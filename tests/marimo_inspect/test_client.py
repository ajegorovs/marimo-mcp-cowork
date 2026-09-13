"""Tests for MarimoClient (client.py).

Mocks the HTTP layer to test:
- Session listing and parsing
- Session resolution by ID and file path
- Scratchpad execution and SSE parsing
- The frontend instantiate POST (`/api/kernel/instantiate`)
- Error handling (network failures, invalid responses)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from marimo_inspection.client import MarimoClient

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


# -------------------------------------------------------------------
# instantiate_notebook() tests — the frontend original-cell POST
#
# The protocol below is the measured marimo 0.24.0 shape:
# `GET /` carries `<marimo-server-token data-token="…">`, and
# `POST /api/kernel/instantiate` requires both `Marimo-Server-Token` and
# `Marimo-Session-Id` plus a body whose `objectIds`/`values` are required and
# whose auto-run wire key is the camelCase `autoRun`.
# -------------------------------------------------------------------

SERVER = "http://stub"


class _InstantiateHandler:
    """Scripted marimo stub: a token-bearing index page plus queued POST rows."""

    def __init__(
        self,
        *,
        token: str = "tok-abc123",
        refreshed_token: str = "tok-refreshed",
        responses: list[tuple[int, dict | str]] | None = None,
        index_status: int = 200,
        transport_error: Exception | None = None,
    ) -> None:
        self.token = token
        self.refreshed_token = refreshed_token
        self.queue = list(responses or [])
        self.index_status = index_status
        self.transport_error = transport_error
        self.gets: list[str] = []
        self.posts: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            self.gets.append(str(request.url))
            if self.index_status != 200:
                return httpx2.Response(
                    self.index_status,
                    headers={"location": "/auth/login?next=%2F"},
                )
            # The first GET is the initial scrape; a later GET is a `refresh`.
            token = self.token if len(self.gets) == 1 else self.refreshed_token
            return httpx2.Response(
                200,
                text=(
                    f'<html><marimo-server-token data-token="{token}" hidden></html>'
                ),
            )
        if self.transport_error is not None:
            raise self.transport_error
        self.posts.append(request)
        status, body = self.queue.pop(0) if self.queue else (200, {"success": True})
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)


def _instantiate_client(handler) -> MarimoClient:
    """A real MarimoClient whose transport is a MockTransport."""
    client = MarimoClient(SERVER)
    client._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return client


def _posted_body(request: httpx2.Request) -> dict:
    return json.loads(request.content)


class TestInstantiateNotebook:
    """`MarimoClient.instantiate_notebook` — headers, body, classification."""

    async def test_sends_both_headers_and_the_exact_body(self):
        """200 `{"success": true}` with both headers and the camelCase body."""
        handler = _InstantiateHandler()
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is True
        assert outcome.reason == ""
        assert outcome.status_code == 200
        assert outcome.token_used is True
        assert outcome.token_refreshed is False

        assert len(handler.posts) == 1
        request = handler.posts[0]
        assert str(request.url) == f"{SERVER}/api/kernel/instantiate"
        assert request.headers["Marimo-Server-Token"] == "tok-abc123"
        assert request.headers["Marimo-Session-Id"] == "session-1"
        assert _posted_body(request) == {
            "objectIds": [],
            "values": [],
            "autoRun": True,
        }

    async def test_no_sse_handshake_is_performed(self):
        """The session must already exist: this call never materializes one."""
        handler = _InstantiateHandler()
        client = _instantiate_client(handler)
        try:
            await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        # Exactly one GET — the token scrape — and never `/sse`.
        assert handler.gets == [f"{SERVER}/"]
        assert all("/sse" not in url for url in handler.gets)

    async def test_auto_run_false_is_camel_encoded(self):
        """`auto_run` is transmitted as marimo's `autoRun` wire key."""
        handler = _InstantiateHandler()
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1", auto_run=False)
        finally:
            await client.close()

        assert outcome.ok is True
        assert _posted_body(handler.posts[0])["autoRun"] is False
        # The snake_case spelling marimo silently ignores is never sent.
        assert "auto_run" not in _posted_body(handler.posts[0])

    async def test_object_ids_and_values_are_forwarded(self):
        """Caller-supplied UI wiring reaches the endpoint unchanged."""
        handler = _InstantiateHandler()
        client = _instantiate_client(handler)
        try:
            await client.instantiate_notebook(
                "session-1", object_ids=["slider-1"], values=[7]
            )
        finally:
            await client.close()

        assert _posted_body(handler.posts[0]) == {
            "objectIds": ["slider-1"],
            "values": [7],
            "autoRun": True,
        }

    async def test_missing_server_token_is_unavailable(self):
        """A 401 `Missing server token` is the skew-protection refusal."""
        handler = _InstantiateHandler(
            responses=[(401, {"error": "Missing server token"})]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "skew_token_unavailable"
        assert outcome.status_code == 401

    async def test_invalid_server_token_refreshes_once_then_succeeds(self):
        """A rotated token costs exactly one re-scrape and one retry."""
        handler = _InstantiateHandler(
            responses=[
                (401, {"error": "Invalid server token"}),
                (200, {"success": True}),
            ]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is True
        assert outcome.token_refreshed is True
        # The retry used the re-scraped token, not the cached one.
        assert len(handler.gets) == 2
        assert handler.posts[1].headers["Marimo-Server-Token"] == "tok-refreshed"

    async def test_invalid_server_token_twice_is_reported_invalid(self):
        """The retry is not a loop: a second refusal is the final answer."""
        handler = _InstantiateHandler(
            responses=[
                (401, {"error": "Invalid server token"}),
                (401, {"error": "Invalid server token"}),
            ]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "skew_token_invalid"
        assert outcome.token_refreshed is True
        assert len(handler.posts) == 2

    async def test_bad_session_id_is_session_not_found(self):
        """A 500 naming the session id is classified, never retried."""
        handler = _InstantiateHandler(
            responses=[(500, {"detail": "Invalid session id: nope"})]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("nope")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "session_not_found"
        assert outcome.status_code == 500
        assert len(handler.posts) == 1

    async def test_missing_session_header_is_classified(self):
        """The other 500 row: the session header itself never arrived."""
        handler = _InstantiateHandler(
            responses=[(500, {"detail": "Missing Marimo-Session-Id header"})]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.reason == "session_header_missing"

    async def test_edit_required_on_403(self):
        """A literal 403 is `edit_required` (defensive row)."""
        handler = _InstantiateHandler(responses=[(403, {"detail": "no"})])
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.reason == "edit_required"
        assert outcome.status_code == 403

    async def test_missing_required_field_is_instantiate_failed(self):
        """A 400 for a body field the caller cannot omit is still classified."""
        handler = _InstantiateHandler(
            responses=[(400, {"detail": "Object missing required field `objectIds`"})]
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "instantiate_failed"
        assert "objectIds" in outcome.detail

    async def test_200_without_success_body_is_not_ok(self):
        """The status alone is never proof: the body must report success."""
        handler = _InstantiateHandler(responses=[(200, {"success": False})])
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "instantiate_failed"
        assert outcome.status_code == 200

    async def test_transport_error_is_server_unreachable(self):
        """A dead connection leaves the outcome unknown, never a success."""
        handler = _InstantiateHandler(
            transport_error=httpx2.ConnectError("connection refused")
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "server_unreachable"
        assert outcome.token_used is False

    async def test_unscrapable_token_still_posts_and_reports_the_scrape_error(self):
        """A gated page yields no token; the POST is attempted and classified."""
        handler = _InstantiateHandler(
            index_status=303,
            responses=[(401, {"error": "Missing server token"})],
        )
        client = _instantiate_client(handler)
        try:
            outcome = await client.instantiate_notebook("session-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "skew_token_unavailable"
        assert outcome.token_used is False
        # The refusal carries why no token could be read.
        assert "303" in outcome.detail
        assert "Marimo-Server-Token" not in handler.posts[0].headers
