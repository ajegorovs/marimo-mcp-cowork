"""Tests for the composite (session_id + server_url) auto-bind.

Covers the T2 fix: `list_active_notebooks(server_url=…)` must bind the
server_url together with the session_id so that later tool calls can omit
both, and an explicit `server_url` on a later call still wins.
"""

from __future__ import annotations

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
