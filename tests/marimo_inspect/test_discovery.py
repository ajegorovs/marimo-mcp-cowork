"""Tests for server discovery (discovery.py).

Mocks the registry directory and HTTP health checks to avoid needing
real marimo servers. Verifies:
- Registry file parsing
- Health check logic
- Server sorting (healthy first)
- Edge cases (empty registry, stale PIDs, network errors)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# -------------------------------------------------------------------
# DiscoveredServer dataclass tests
# -------------------------------------------------------------------


class TestDiscoveredServer:
    """Test the DiscoveredServer dataclass."""

    def test_create_server(self):
        """Create a discovered server."""
        from marimo_inspection.discovery import DiscoveredServer

        server = DiscoveredServer(url="http://127.0.0.1:8090")
        assert server.url == "http://127.0.0.1:8090"
        assert server.healthy is False
        assert server.pid is None

    def test_create_with_pid(self):
        """Create with PID."""
        from marimo_inspection.discovery import DiscoveredServer

        server = DiscoveredServer(url="http://127.0.0.1:8090", pid=12345, healthy=True)
        assert server.pid == 12345
        assert server.healthy is True


# -------------------------------------------------------------------
# discover_servers() tests
# -------------------------------------------------------------------


class TestDiscoverServers:
    """Test discover_servers() with mocked filesystem/HTTP."""

    async def test_empty_registry(self, mock_http_check):
        """Returns empty list when no registry directory exists."""
        from marimo_inspection.discovery import discover_servers

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_dir.return_value = mock_path

            servers = await discover_servers()
            assert servers == []

    async def test_no_json_files(self):
        """Returns empty list when registry exists but has no JSON."""
        from marimo_inspection.discovery import discover_servers

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.glob.return_value = []
            mock_dir.return_value = mock_path

            servers = await discover_servers()
            assert servers == []

    async def test_discover_healthy_server(self):
        """Returns healthy server when registry file is valid."""
        from marimo_inspection.discovery import DiscoveredServer, discover_servers

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"url": "http://127.0.0.1:8090", "pid": 9999}, f)
            tmp_path = Path(f.name)

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.glob.return_value = [tmp_path]
            mock_dir.return_value = mock_path

            # Mock _check_server_file to return a healthy server
            with patch("marimo_inspection.discovery._check_server_file") as mock_check:
                mock_check.return_value = DiscoveredServer(
                    url="http://127.0.0.1:8090",
                    pid=9999,
                    healthy=True,
                )

                servers = await discover_servers()
                assert len(servers) == 1
                assert servers[0].url == "http://127.0.0.1:8090"

        # Cleanup
        tmp_path.unlink()

    async def test_discover_unhealthy_server(self):
        """Returns empty list when health check fails."""
        from marimo_inspection.discovery import discover_servers

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"url": "http://127.0.0.1:9999", "pid": 8888}, f)
            tmp_path = Path(f.name)

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.glob.return_value = [tmp_path]
            mock_dir.return_value = mock_path

            with patch(
                "marimo_inspection.discovery.httpx.AsyncClient"
            ) as mock_client_cls:
                instance = MagicMock()
                mock_response = MagicMock()
                mock_response.status_code = 500
                instance.get = AsyncMock(return_value=mock_response)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = instance

                servers = await discover_servers()
                assert servers == []

        tmp_path.unlink()

    async def test_discover_with_base_url(self):
        """base_url override bypasses registry."""
        from marimo_inspection.discovery import discover_servers

        with patch("marimo_inspection.discovery._check_server") as mock_check:
            from marimo_inspection.discovery import DiscoveredServer

            mock_check.return_value = DiscoveredServer(
                url="http://override:8090", healthy=True
            )
            servers = await discover_servers(base_url="http://override:8090")
            assert len(servers) == 1
            assert servers[0].url == "http://override:8090"

    async def test_healthy_servers_first(self):
        """Healthy servers are sorted first."""
        from marimo_inspection.discovery import discover_servers

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_dir.return_value = mock_path

            servers = await discover_servers()
            # All should be healthy when registry is empty
            assert servers == []

    async def test_invalid_json_file(self):
        """Ignores corrupt JSON registry files."""
        from marimo_inspection.discovery import discover_servers

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json {{{")
            tmp_path = Path(f.name)

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.glob.return_value = [tmp_path]
            mock_dir.return_value = mock_path

            servers = await discover_servers()
            assert servers == []

        tmp_path.unlink()

    async def test_json_file_no_url(self):
        """Ignores registry files without URL."""
        from marimo_inspection.discovery import discover_servers

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"pid": 1234}, f)
            tmp_path = Path(f.name)

        with patch("marimo_inspection.discovery._get_registry_dir") as mock_dir:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path.glob.return_value = [tmp_path]
            mock_dir.return_value = mock_path

            servers = await discover_servers()
            assert servers == []

        tmp_path.unlink()


# -------------------------------------------------------------------
# _check_server() helper tests
# -------------------------------------------------------------------


class TestCheckServerHelper:
    """Test _check_server() internal helper."""

    async def test_healthy_server(self):
        """Returns server when health check succeeds."""
        from marimo_inspection.discovery import _check_server

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            server = await _check_server("http://127.0.0.1:8090")
            assert server is not None
            assert server.healthy is True

    async def test_unhealthy_server(self):
        """Returns None when health check fails."""
        from marimo_inspection.discovery import _check_server

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            server = await _check_server("http://127.0.0.1:8090")
            assert server is None

    async def test_run_mode_401_census_is_not_discoverable(self):
        """A census refused for lack of edit scope drops the server (T18).

        ``marimo run`` registers in the marimo server registry like ``edit``
        does under ``--no-token``, but its ``GET /api/sessions`` answers 401
        (the endpoint requires edit scope). The health check accepts only 200,
        so a run-mode server is never returned by discovery and never counted
        by ``servers_discovered`` on the discovery path.
        """
        from marimo_inspection.discovery import _check_server

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            assert await _check_server("http://127.0.0.1:8090") is None

    async def test_network_error(self):
        """Returns None when health check throws."""
        from marimo_inspection.discovery import _check_server

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            instance.get = AsyncMock(side_effect=ConnectionError)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            server = await _check_server("http://127.0.0.1:8090")
            assert server is None


# -------------------------------------------------------------------
# _get_registry_dir() tests
# -------------------------------------------------------------------


class TestGetRegistryDir:
    """Test _get_registry_dir() internal."""

    def test_registry_dir_path(self):
        """Returns correct directory structure."""
        from marimo_inspection.discovery import _get_registry_dir

        path = _get_registry_dir()
        assert path.name == "servers"

    def test_registry_dir_absolute(self):
        """Returns absolute path."""
        from marimo_inspection.discovery import _get_registry_dir

        path = _get_registry_dir()
        assert path.is_absolute()

    @patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/xdg-test"}, clear=False)
    def test_posix_uses_xdg_state_home(self):
        """POSIX honors XDG_STATE_HOME."""
        from marimo_inspection.discovery import _get_registry_dir

        path = _get_registry_dir()
        assert path == Path("/tmp/xdg-test/marimo/servers")

    @patch.dict(os.environ, {}, clear=True)
    def test_posix_defaults_to_local_state(self):
        """POSIX without XDG_STATE_HOME defaults to ~/.local/state."""
        from marimo_inspection.discovery import _get_registry_dir

        path = _get_registry_dir()
        assert path == Path.home() / ".local" / "state" / "marimo" / "servers"

    @patch("marimo_inspection.discovery.os.name", "nt")
    @patch("marimo_inspection.discovery.Path.home")
    def test_windows_uses_dotmarimo(self, mock_home):
        """Windows uses ~/.marimo/servers."""
        mock_home.return_value = PurePosixPath("/Users/test")
        from marimo_inspection.discovery import _get_registry_dir

        path = _get_registry_dir()
        assert path.parts == ("/", "Users", "test", ".marimo", "servers")


# -------------------------------------------------------------------
# list_sessions() tests
# -------------------------------------------------------------------


class TestListSessions:
    """Test list_sessions() from discovery module."""

    async def test_list_sessions_success(self):
        """Returns session data on success."""
        from marimo_inspection.discovery import list_sessions

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "running_notebooks": [{"id": "abc123", "file": "/test.py"}]
            }
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            result = await list_sessions("http://127.0.0.1:8090")
            assert "running_notebooks" in result
            assert len(result["running_notebooks"]) == 1

    async def test_list_sessions_error(self):
        """Raises on HTTP error."""
        from marimo_inspection.discovery import list_sessions

        with patch("marimo_inspection.discovery.httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.raise_for_status.side_effect = Exception("error")
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            with pytest.raises(Exception, match="error"):
                await list_sessions("http://127.0.0.1:8090")
