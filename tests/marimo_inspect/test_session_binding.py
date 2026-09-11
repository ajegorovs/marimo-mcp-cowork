"""Tests for the composite (session_id + server_url) auto-bind.

Covers the T2 fix: `list_active_notebooks(server_url=…)` must bind the
server_url together with the session_id so that later tool calls can omit
both, and an explicit `server_url` on a later call still wins.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_process_fallback():
    """Keep the process-global fallback from leaking between tests."""
    from marimo_inspection.tools.session import reset_fallback_state

    reset_fallback_state()
    yield
    reset_fallback_state()


class FakeContext:
    """In-memory stand-in for fastmcp.Context's state store."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}
        self.infos: list[str] = []

    async def set_state(
        self, key: str, value: object, serializable: bool = True
    ) -> None:
        self._state[key] = value

    async def get_state(self, key: str) -> object | None:
        return self._state.get(key)

    async def info(self, message: str) -> None:
        self.infos.append(message)


class ScopedContext(FakeContext):
    """FakeContext that also reports the transport and MCP session identity.

    These are the two scoping inputs the production resolvers read, so a test
    using this context exercises the real predicate rather than a fork of it.
    """

    def __init__(self, transport: str, session_id: str) -> None:
        super().__init__()
        self.transport = transport
        self.session_id = session_id


def _make_session(session_id: str = "abc123"):
    return MagicMock(
        session_id=session_id,
        file="/test.py",
        basename="test.py",
    )


async def _bind_via_list(server_url: str) -> FakeContext:
    from marimo_inspection.tools.notebooks import list_active_notebooks

    ctx = FakeContext()
    with patch("marimo_inspection.tools.notebooks.MarimoClient") as list_cls:
        instance = MagicMock()
        instance.list_sessions = AsyncMock(return_value=[_make_session()])
        list_cls.return_value = instance
        result = await list_active_notebooks(server_url=server_url, ctx=ctx)
    assert result["summary"]["total_notebooks"] == 1
    return ctx


async def test_list_binds_server_url_for_later_omitted_call():
    """A tool call with neither session_id nor server_url uses the bound URL."""
    from marimo_inspection.tools.cells import get_cell_map

    ctx = await _bind_via_list("http://127.0.0.1:9000")

    with patch("marimo_inspection.tools.cells.MarimoClient") as cells_cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": [], "total_cells": 0}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cells_cls.return_value = instance

        result = await get_cell_map(ctx=ctx)

        assert "cells" in result
        cells_cls.assert_called_once_with("http://127.0.0.1:9000")


async def test_explicit_server_url_overrides_bound():
    """A later explicit server_url wins over the auto-bound one."""
    from marimo_inspection.tools.cells import get_cell_map

    ctx = await _bind_via_list("http://127.0.0.1:9000")

    with patch("marimo_inspection.tools.cells.MarimoClient") as cells_cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": [], "total_cells": 0}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cells_cls.return_value = instance

        result = await get_cell_map(server_url="http://127.0.0.1:9999", ctx=ctx)

        assert "cells" in result
        cells_cls.assert_called_once_with("http://127.0.0.1:9999")


async def test_server_url_still_required_without_binding():
    """With no bind and no explicit server_url, tools still raise."""
    from marimo_inspection.tools.cells import get_cell_map

    with pytest.raises(ValueError, match="server_url is required"):
        await get_cell_map(session_id="abc123", ctx=None)


async def test_set_active_session_binds_both():
    """set_active_session(server_url=…) stores the URL too."""
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        set_active_session,
    )

    ctx = FakeContext()
    result = await set_active_session(
        session_id="abc123",
        server_url="http://127.0.0.1:9000",
        ctx=ctx,
    )

    assert result["status"] == "OK"
    assert ctx._state[_SESSION_KEY] == "abc123"
    assert ctx._state[_SERVER_URL_KEY] == "http://127.0.0.1:9000"


async def test_bare_string_variable_names_normalized():
    """A harness-mangled string variable_names is treated as a one-element list."""
    from marimo_inspection.tools.variables import get_variables

    with patch("marimo_inspection.tools.variables.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"variables": {}, "tables": {}}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_variables(
            session_id="abc123",
            variable_names="x",
            server_url="http://127.0.0.1:8090",
        )

        assert "variables" in result
        code = instance.execute.await_args.args[1]
        assert '"x"' in code
        assert '["x"]' in code


async def test_bare_string_cell_ids_normalized_for_cell_data():
    """A harness-mangled string cell_ids is treated as a one-element list."""
    from marimo_inspection.tools.cells import get_cell_data

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"data": []}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_cell_data(
            session_id="abc123",
            cell_ids="5",
            server_url="http://127.0.0.1:8090",
        )

        assert "data" in result
        code = instance.execute.await_args.args[1]
        assert '["5"]' in code


async def test_bare_string_cell_ids_normalized_for_cell_outputs():
    """A harness-mangled string cell_ids is treated as a one-element list."""
    from marimo_inspection.tools.cells import get_cell_outputs

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": []}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_cell_outputs(
            session_id="abc123",
            cell_ids="5",
            server_url="http://127.0.0.1:8090",
        )

        assert "cells" in result
        code = instance.execute.await_args.args[1]
        assert '["5"]' in code


# ---------------------------------------------------------------------------
# What the binding promise says vs what a real client sees.
#
# Session state is keyed by the MCP session identity the client negotiates
# (`fastmcp/server/context.py`: session_id is cached on the SDK connection;
# `_make_state_key` prefixes every key with it). A client that starts a new MCP
# session per request therefore writes and reads the binding under different
# keys. These tests pin both sides of that split, and the wording that has to
# stay true for each — see resources/co-work-loop.md §1.
# ---------------------------------------------------------------------------

_SERVER_BIN = Path(sys.executable).parent / "marimo-inspect"
_PROBE_SESSION = "s_probe_not_a_real_session"
_PROBE_URL = "http://127.0.0.1:59999"


async def test_set_active_session_message_is_scoped_to_the_mcp_session():
    """The confirmation must scope the promise instead of calling args optional."""
    from marimo_inspection.tools.session import set_active_session

    result = await set_active_session(
        session_id="abc123", server_url="http://127.0.0.1:9000", ctx=FakeContext()
    )

    message = result["message"]
    assert "now optional" not in message
    assert "process-global fallback" in message
    assert "binding_ambiguous" in message
    assert "single client" in result["binding_scope"]


async def test_missing_binding_error_names_the_condition():
    """The refusal tells the caller why an earlier binding may be invisible."""
    from marimo_inspection.tools.session import resolve_session_id

    with pytest.raises(ValueError) as excinfo:
        await resolve_session_id("", None)

    text = str(excinfo.value)
    assert "process-global fallback" in text
    assert "binding_ambiguous" in text


def _free_port() -> int:
    """Ask the OS for a free loopback port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    """Poll until something accepts connections on the port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        await asyncio.sleep(0.1)
    raise AssertionError(f"marimo-inspect did not start on port {port}")


async def _wait_for_mcp(url: str, timeout: float = 20.0) -> None:
    """Poll until an MCP handshake succeeds (the port may open first)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with (
                streamable_http_client(url) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
            return
        except Exception as exc:  # noqa: BLE001 - transport startup races
            last = exc
            await asyncio.sleep(0.2)
    raise AssertionError(f"MCP server never became ready at {url}: {last!r}")


def _start_http_server(port: int, log_path: Path) -> subprocess.Popen:
    """Launch the console script as a streamable-HTTP server.

    Synchronous on purpose: ``subprocess.Popen`` in an async test trips
    ASYNC220, and the child inherits the log fd after the handle closes here.
    """
    with log_path.open("wb") as handle:
        return subprocess.Popen(
            [str(_SERVER_BIN), "--transport", "http", "--port", str(port)],
            stdout=handle,
            stderr=handle,
        )


def _stop_process(proc: subprocess.Popen) -> None:
    """Terminate the server subprocess, killing it if it will not exit."""
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _spawn(params_args: list[str]) -> dict:
    return {
        "mcpServers": {
            "marimo-inspect": {
                "command": str(_SERVER_BIN),
                "args": params_args,
            }
        }
    }


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_binding_is_visible_to_a_session_stable_sdk_client():
    """An `mcp`-SDK stdio client keeps one MCP session, so the binding holds.

    The bound URL is deliberately dead: reaching the HTTP layer (a connection
    error naming it) *is* the proof the binding was visible, and it fails
    pre-fix with "no active session bound" instead.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(_SERVER_BIN), args=["--transport", "stdio"]
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        bound = await session.call_tool(
            "set_active_session",
            {"session_id": _PROBE_SESSION, "server_url": _PROBE_URL},
        )
        assert json.loads(bound.content[0].text)["status"] == "OK"

        after = await session.call_tool("get_cell_map", {})
        text = after.content[0].text if after.content else ""

    assert "no active session bound" not in text
    assert _PROBE_URL in text or "connect" in text.lower()


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_fastmcp_client_starts_a_new_mcp_session_per_request():
    """A session-per-request client is now covered by the process-global fallback.

    fastmcp's own ``Client`` starts a fresh MCP session per request on the
    pinned fastmcp 4.0.3 (measured — see the H11 entry in
    ``docs/agenda-bug-hunt-1.md``), so the binding written to the MCP-session
    state never reaches the following call. Over stdio one process serves
    exactly one client, so the process-global fallback carries it anyway: the
    argument-less call must reach the bound (deliberately dead) URL rather than
    refuse.

    This test used to pin the opposite — ``no active session bound`` on every
    argument-less call — which would now be pinning the defect H11 fixed. It is
    the pre-fix-failing closure test for the stdio capability.
    """
    from fastmcp import Client

    async with Client(_spawn(["--transport", "stdio"])) as client:
        bound = await client.call_tool(
            "set_active_session",
            {"session_id": _PROBE_SESSION, "server_url": _PROBE_URL},
        )
        assert json.loads(bound.content[0].text)["status"] == "OK"

        after = await client.call_tool("get_cell_map", {}, raise_on_error=False)
        text = " | ".join(getattr(b, "text", "") for b in (after.content or []))

    assert "no active session bound" not in text
    assert _PROBE_URL in text or "connect" in text.lower()


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_two_http_clients_do_not_share_the_fallback_binding(tmp_path):
    """Client B must never inherit client A's binding over HTTP.

    Over ``--transport http`` one process serves many clients, so the
    process-global fallback is scoped: it stops being served as soon as a
    second distinct client session is observed. Client A (a session-stable
    ``mcp``-SDK HTTP client) binds and its own argument-less call is served;
    client B — a different connection that never bound — is then refused with
    the structured ``reason: binding_ambiguous`` instead of being handed A's
    session.

    Pre-fix there was no fallback at all, so B refused with the generic
    "no active session bound" text and the ``binding_ambiguous`` assertion
    failed; a naive *unscoped* fallback would instead let B reach the dead URL
    and fail the ``_PROBE_URL not in text`` assertion.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    proc = _start_http_server(port, tmp_path / "http-server.log")
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        await _wait_for_port(port)
        await _wait_for_mcp(url)

        async with (
            streamable_http_client(url) as (read_a, write_a),
            ClientSession(read_a, write_a) as client_a,
        ):
            await client_a.initialize()
            bound = await client_a.call_tool(
                "set_active_session",
                {"session_id": _PROBE_SESSION, "server_url": _PROBE_URL},
            )
            assert json.loads(bound.content[0].text)["status"] == "OK", (
                "client A could not bind"
            )

            # Positive control: while the process has seen one client session,
            # the fallback is served — the call proceeds to the (dead) bound
            # URL instead of being refused.
            served = await client_a.call_tool("get_cell_map", {})
            served_text = " ".join(
                getattr(block, "text", "") for block in (served.content or [])
            )
            assert "no active session bound" not in served_text
            assert "binding_ambiguous" not in served_text

            # Client B is a second, independent MCP session that never bound
            # anything.
            async with (
                streamable_http_client(url) as (read_b, write_b),
                ClientSession(read_b, write_b) as client_b,
            ):
                await client_b.initialize()
                refused = await client_b.call_tool("get_cell_map", {})
            refused_text = " ".join(
                getattr(block, "text", "") for block in (refused.content or [])
            )
    finally:
        _stop_process(proc)

    assert "binding_ambiguous" in refused_text, (
        "client B was not told why the binding was withheld: " + refused_text
    )
    assert _PROBE_URL not in refused_text


async def test_bind_active_session_stores_the_process_global_fallback():
    """The bind choke point feeds the fallback, not only the MCP-session state.

    Both the explicit ``set_active_session`` and ``list_active_notebooks``'
    auto-bind go through ``bind_active_session``, so this pins the wiring for
    both paths without needing a live marimo server to discover.
    """
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        bind_active_session,
        fallback_decision,
    )

    ctx = ScopedContext("stdio", "rotated-any")
    await bind_active_session("s_new", ctx, server_url="http://127.0.0.1:9000")

    assert ctx._state[_SESSION_KEY] == "s_new"
    assert ctx._state[_SERVER_URL_KEY] == "http://127.0.0.1:9000"

    decision = fallback_decision("stdio", "rotated-other")
    assert decision.session_id == "s_new"
    assert decision.server_url == "http://127.0.0.1:9000"


async def test_fallback_scope_serves_stdio_and_withholds_on_a_second_client():
    """The production predicate: single-client serve, then fail closed.

    Hermetic: drives ``fallback_decision`` — the function the resolvers
    actually call — plus ``resolve_session_id`` through a context that reports
    the real scoping inputs, so this is the shipped predicate and not a fork.
    """
    from marimo_inspection.tools.session import (
        FallbackDecision,
        SessionBindingError,
        fallback_decision,
        resolve_session_id,
        store_fallback_binding,
    )

    store_fallback_binding("s_bound", "http://127.0.0.1:9000")
    served = FallbackDecision(session_id="s_bound", server_url="http://127.0.0.1:9000")

    # stdio is single-client by construction: always served, and the rotating
    # session identities of a session-per-request client are never counted.
    assert fallback_decision("stdio", "rotated-1") == served
    assert fallback_decision("stdio", "rotated-2") == served

    # HTTP: served while one client session has been seen ...
    assert fallback_decision("streamable-http", "session-a") == served

    # ... withheld as soon as a second distinct client session appears.
    withheld = fallback_decision("streamable-http", "session-b")
    assert withheld.session_id == ""
    assert withheld.reason == "binding_ambiguous"

    # The real resolver refuses with the structured reason, not a bare string.
    ctx_b = ScopedContext("streamable-http", "session-c")
    with pytest.raises(SessionBindingError) as excinfo:
        await resolve_session_id("", ctx_b)
    assert excinfo.value.reason == "binding_ambiguous"
    assert "reason: binding_ambiguous" in str(excinfo.value)

    # A single stdio client keeps resolving through the process-global
    # fallback even after many rotated MCP sessions.
    ctx_stdio = ScopedContext("stdio", "rotated-99")
    assert await resolve_session_id("", ctx_stdio) == "s_bound"
