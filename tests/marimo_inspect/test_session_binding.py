"""Tests for the composite (session_id + server_url) auto-bind.

Covers the T2 fix: `list_active_notebooks(server_url=…)` must bind the
server_url together with the session_id so that later tool calls can omit
both, and an explicit `server_url` on a later call still wins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    assert "now optional for other tools" not in message
    assert "for this MCP session" in message
    assert result["binding_scope"] == "this MCP session (server-side state)"


async def test_missing_binding_error_names_the_condition():
    """The refusal tells the caller why an earlier binding may be invisible."""
    from marimo_inspection.tools.session import resolve_session_id

    with pytest.raises(ValueError) as excinfo:
        await resolve_session_id("", None)

    assert "share one MCP session" in str(excinfo.value)


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
    """Pins the documented limitation the resource §1 names.

    If this ever stops raising, fastmcp's Client began keeping one MCP session
    and the conditional wording in co-work-loop.md / the tool docstrings must be
    relaxed again.
    """
    from fastmcp import Client

    async with Client(_spawn(["--transport", "stdio"])) as client:
        await client.call_tool(
            "set_active_session",
            {"session_id": _PROBE_SESSION, "server_url": _PROBE_URL},
        )
        with pytest.raises(Exception, match="no active session bound"):
            await client.call_tool("get_cell_map", {})
