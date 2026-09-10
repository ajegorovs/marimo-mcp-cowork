"""Hermetic live regression for the `set_ui_value` widget tool.

The plan assumed widget behaviour could only be validated against a
browser-instantiated session, because the shared live fixture's notebook cells
never execute (the `/sse`-created session cannot be instantiated without the
skew token — see AGENTS.md). That assumption is too conservative: a cell
*created through the MCP write tools* runs fine, so a widget can be
materialized in-kernel and driven end to end here.

Proven live (2026-09-10): `set_ui_value("gate_slider", 7)` moved the element's
value 3 -> 7 AND reactively re-ran the dependent cell (its derived global went
103 -> 107), with both cells ending `idle`. This test locks that behaviour in.
"""

from __future__ import annotations

import pytest

from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

# Bare name: marimo treats a leading underscore as cell-private, so a widget
# target must be a plain top-level assignment.
_WIDGET = "gate_slider"

_WIDGET_SOURCE = (
    "import marimo as mo\n"
    f"{_WIDGET} = mo.ui.slider(0, 10, value=3, label='gate')\n"
    f"{_WIDGET}"  # final expression -> the control is visible to the user
)

# A cell cannot read the .value of a UI element it created, so the value read
# lives in its own dependent cell — which is also what proves reactivity.
_READER_SOURCE = f"gate_readback = int({_WIDGET}.value) + 100"


def _inner_value(payload: dict, name: str):
    """Extract a UI element's current selection from get_variables output.

    A widget's value is nested: ``{name: {"value": {"value": ..., "datatype":
    ...}}}`` — the outer entry is the serialized element, the inner ``value``
    is the selection.
    """
    node = payload.get("variables", {}).get(name)
    if isinstance(node, dict):
        inner = node.get("value")
        if isinstance(inner, dict):
            return inner.get("value")
        return inner
    return None


@pytest.mark.live
async def test_set_ui_value_changes_widget_and_reruns_dependents(mutation_server):
    """Set a live widget value; the change and the reactive rerun are real."""
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        widget_cell = await create_cell(
            _WIDGET_SOURCE, session_id=session_id, server_url=server_url
        )
        assert widget_cell["status"] == "ok", widget_cell
        created.append(widget_cell["cell_id"])
        assert (
            await run_cell(
                widget_cell["cell_id"], session_id=session_id, server_url=server_url
            )
        )["status"] == "ok"

        reader_cell = await create_cell(
            _READER_SOURCE, session_id=session_id, server_url=server_url
        )
        assert reader_cell["status"] == "ok", reader_cell
        created.append(reader_cell["cell_id"])
        assert (
            await run_cell(
                reader_cell["cell_id"], session_id=session_id, server_url=server_url
            )
        )["status"] == "ok"

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _WIDGET) == "3", before
        assert _inner_value(before, "gate_readback") == "103", before

        result = await set_ui_value(
            _WIDGET, 7, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["variable_name"] == _WIDGET
        assert result["next_steps"], "success must direct verification"

        after = await get_variables(session_id=session_id, server_url=server_url)
        # The widget itself took the new value...
        assert _inner_value(after, _WIDGET) == "7", after
        # ...and the dependent cell re-ran against it (this is the reactivity
        # claim: 3+100 -> 7+100).
        assert _inner_value(after, "gate_readback") == "107", after

        # Both cells settle idle: the reactive run completed, not wedged.
        states = await get_cell_data(
            cell_ids=created, session_id=session_id, server_url=server_url
        )
        by_id = {row["cell_id"]: row["runtime_state"] for row in states["data"]}
        assert by_id[widget_cell["cell_id"]] == "idle", states
        assert by_id[reader_cell["cell_id"]] == "idle", states
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_rejects_missing_and_non_ui_names(mutation_server):
    """A missing global and a non-UI global are refused with clear payloads."""
    _manager, server_url, session_id, _notebook_copy = mutation_server

    missing = await set_ui_value(
        "definitely_not_a_global", 1, session_id=session_id, server_url=server_url
    )
    assert missing["status"] == "error", missing
    assert "not a live kernel global" in missing["message"], missing

    cell = await create_cell(
        "gate_plain_value = 42", session_id=session_id, server_url=server_url
    )
    cell_id = cell["cell_id"]
    try:
        await run_cell(cell_id, session_id=session_id, server_url=server_url)
        non_ui = await set_ui_value(
            "gate_plain_value", 1, session_id=session_id, server_url=server_url
        )
        assert non_ui["status"] == "error", non_ui
        assert "not a marimo UI element" in non_ui["message"], non_ui
        assert non_ui["datatype"] == "int", non_ui
    finally:
        await delete_cell(cell_id, session_id=session_id, server_url=server_url)
