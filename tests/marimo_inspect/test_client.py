"""Tests for MarimoClient (client.py).

Mocks the HTTP layer to test:
- Session listing and parsing
- Session resolution by ID and file path
- Scratchpad execution and SSE parsing
- Error handling (network failures, invalid responses)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# -------------------------------------------------------------------
# MarimoClient initialization tests
# -------------------------------------------------------------------


class TestMarimoClientInit:
    """Test MarimoClient constructor."""

    def test_create_client(self):
        """Create client with server URL."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        assert client._url == "http://127.0.0.1:8090"

    def test_url_trailing_slash_removed(self):
        """Trailing slash is stripped from URL."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090/")
        assert client._url == "http://127.0.0.1:8090"

    def test_url_multiple_trailing_slashes(self):
        """Multiple trailing slashes are stripped."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090///")
        assert client._url == "http://127.0.0.1:8090"


# -------------------------------------------------------------------
# list_sessions() tests
# -------------------------------------------------------------------


class TestListSessions:
    """Test MarimoClient.list_sessions()."""

    async def test_list_sessions(self):
        """Returns parsed session list."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {
                "path": "/home/user/notebooks/test.py",
                "filename": "test.py",
            }
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            sessions = await client.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].session_id == "abc123"
            assert sessions[0].file == "/home/user/notebooks/test.py"
            assert sessions[0].basename == "test.py"

    async def test_list_sessions_empty(self):
        """Returns empty list when no running notebooks."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {}

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            sessions = await client.list_sessions()
            assert sessions == []

    async def test_list_sessions_no_file(self):
        """Handles sessions without file path."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"def456": {}}

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            sessions = await client.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].session_id == "def456"
            assert sessions[0].file is None

    async def test_list_sessions_http_error(self):
        """Raises on HTTP error."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("connection refused")

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            with pytest.raises(Exception, match="connection refused"):
                await client.list_sessions()


# -------------------------------------------------------------------
# resolve_session() tests
# -------------------------------------------------------------------


class TestResolveSession:
    """Test MarimoClient.resolve_session()."""

    async def test_resolve_by_session_id(self):
        """Resolves session by ID."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/test.py", "filename": "test.py"},
            "def456": {"path": "/other.py", "filename": "other.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            session = await client.resolve_session(session_id="abc123")
            assert session.session_id == "abc123"
            assert session.file == "/test.py"

    async def test_resolve_by_file_path(self):
        """Resolves session by file path."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/test.py", "filename": "test.py"},
            "def456": {"path": "/other.py", "filename": "other.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            session = await client.resolve_session(file_path="/test.py")
            assert session.session_id == "abc123"

    async def test_resolve_by_basename(self):
        """Resolves session by basename when full path not matched."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/other/test.py", "filename": "test.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            session = await client.resolve_session(file_path="/different/path/test.py")
            assert session.session_id == "abc123"

    async def test_resolve_single_session(self):
        """Returns single session when only one exists."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/test.py", "filename": "test.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            session = await client.resolve_session()
            assert session.session_id == "abc123"

    async def test_resolve_no_sessions(self):
        """Raises when no sessions exist."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {}

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            with pytest.raises(ValueError, match="No active sessions"):
                await client.resolve_session()

    async def test_resolve_multiple_sessions(self):
        """Raises when multiple sessions and no discriminator."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/test1.py", "filename": "test1.py"},
            "def456": {"path": "/test2.py", "filename": "test2.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            with pytest.raises(ValueError, match="Multiple sessions found"):
                await client.resolve_session()

    async def test_resolve_session_not_found(self):
        """Raises when session ID does not match."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "abc123": {"path": "/test.py", "filename": "test.py"},
        }

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            with pytest.raises(ValueError, match="not found"):
                await client.resolve_session(session_id="nonexistent")


# -------------------------------------------------------------------
# execute() tests
# -------------------------------------------------------------------


class TestExecute:
    """Test MarimoClient.execute()."""

    async def test_execute_success(self):
        """Returns ExecuteResult on successful execution."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event: stdout
data: {"data": "hello"}
event: done
data: {"success": true, "output": {"mimetype": "text/plain", "data": "hello"}}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        # Simulate aiter_lines
        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines

        # Properly mock the async context manager for stream
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="print('test')")
            assert result.status == "ok"
            assert "hello" in result.stdout

    async def test_execute_error(self):
        """Returns error status on execution failure."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event: stderr
data: {"data": "NameError: x"}
event: done
data: {"success": false, "output": {"mimetype": "text/plain", "data": ""}}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines

        # Properly mock the async context manager for stream
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="undefined_var")
            assert result.status == "error"
            assert "NameError: x" in result.stderr

    async def test_execute_http_error(self):
        """Raises on HTTP error."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.aread = AsyncMock(return_value="Internal error")

        # Properly mock the async context manager for stream
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            with pytest.raises(RuntimeError, match="Execution failed"):
                await client.execute(session_id="abc123", code="print('test')")

    async def test_execute_output_event(self):
        """Captures output from the done event."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event: done
data: {"success": true, "output": {"mimetype": "text/html", "data": "<b>chart</b>"}}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines

        # Properly mock the async context manager for stream
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="plt.show()")
            assert result.status == "ok"
            assert result.output == {"mimetype": "text/html", "data": "<b>chart</b>"}

    async def test_execute_event_without_space(self):
        """Handles SSE 'event:' without a space after the colon."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event:stdout
data: {"data": "no-space-event"}
event:done
data: {"success": true, "output": null}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="print('x')")
            assert result.status == "ok"
            assert "no-space-event" in result.stdout

    async def test_execute_standalone_output_event(self):
        """Captures output from a separate 'event: output' frame."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event: output
data: {"output": {"mimetype": "text/plain", "data": "42"}}
event: done
data: {"success": true, "output": {"mimetype": "text/plain", "data": "42"}}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="42")
            assert result.status == "ok"
            assert result.output == {"mimetype": "text/plain", "data": "42"}

    async def test_execute_ignores_malformed_data_lines(self):
        """Non-JSON data lines are skipped without crashing."""
        from marimo_inspection.client import MarimoClient

        client = MarimoClient("http://127.0.0.1:8090")
        sse_data = """event: stdout
data: +OK ready
event: done
data: {"success": true, "output": null}
"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aread = AsyncMock(return_value="")

        async def aiter_lines():
            for line in sse_data.strip().split("\n"):
                yield line

        mock_response.aiter_lines = aiter_lines
        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_response)
        async_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            client, "_get_client", new_callable=AsyncMock
        ) as mock_get_client:
            mock_http = MagicMock()
            mock_http.stream = MagicMock(return_value=async_cm)
            mock_get_client.return_value = mock_http

            result = await client.execute(session_id="abc123", code="x")
            assert result.status == "ok"


# -------------------------------------------------------------------
# ExecuteResult dataclass tests
# -------------------------------------------------------------------


class TestExecuteResult:
    """Test the ExecuteResult dataclass."""

    def test_default_values(self):
        """Default values are correct."""
        from marimo_inspection.client import ExecuteResult

        result = ExecuteResult()
        assert result.stdout == []
        assert result.stderr == []
        assert result.output is None
        assert result.execution_count is None
        assert result.status == ""

    def test_custom_values(self):
        """Custom values are preserved."""
        from marimo_inspection.client import ExecuteResult

        result = ExecuteResult(
            stdout=["line1"],
            stderr=["error"],
            output={"data": 1},
            execution_count=42,
            status="ok",
        )
        assert result.execution_count == 42
        assert result.status == "ok"


# -------------------------------------------------------------------
# SessionInfo dataclass tests
# -------------------------------------------------------------------


class TestSessionInfo:
    """Test the SessionInfo dataclass."""

    def test_create_session_info(self):
        """Create session info with minimal data."""
        from marimo_inspection.client import SessionInfo

        session = SessionInfo(session_id="abc123")
        assert session.session_id == "abc123"
        assert session.file is None
        assert session.basename is None

    def test_full_session_info(self):
        """Create with all fields."""
        from marimo_inspection.client import SessionInfo

        session = SessionInfo(
            session_id="abc123",
            file="/home/user/test.py",
            basename="test.py",
        )
        assert session.basename == "test.py"
