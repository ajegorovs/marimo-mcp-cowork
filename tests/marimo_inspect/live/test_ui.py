"""Hermetic live regression for the `set_ui_value` widget tool.

The plan assumed widget behaviour could only be validated against a
browser-instantiated session, because the shared live fixture's notebook cells
never execute (the `/sse`-created session cannot be instantiated without the
skew token — see AGENTS.md). That assumption is too conservative: a cell
*created through the MCP write tools* runs fine, so a widget can be
materialized in-kernel and driven end to end here.

Two behaviours are locked in against a real marimo 0.24 kernel:

* **Reactivity** — `set_ui_value("gate_slider", 7)` moves the element 3 -> 7 AND
  reactively re-runs its dependent cell (derived global 103 -> 107), both cells
  ending `idle`.
* **The swallow trap** — marimo catches an exception raised while applying a
  UI-element value and only writes it to the kernel's stderr, so a rejected
  update used to come back as `status: ok` with an unchanged widget. A scalar
  sent to a dropdown trips exactly that path (``assert len(value) == 1`` inside
  ``dropdown._convert_value``), an unknown option key trips option validation,
  and both must now surface as errors with the widget unmoved.
"""

from __future__ import annotations

import pytest

from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

# Bare names: marimo keeps a leading-underscore name cell-private, so a widget
# target must be a plain top-level assignment (locked in by
# test_set_ui_value_cannot_address_a_cell_private_widget below).
_WIDGET = "gate_slider"
_DROPDOWN = "gate_dropdown"

_WIDGET_SOURCE = (
    "import marimo as mo\n"
    f"{_WIDGET} = mo.ui.slider(0, 10, value=3, label='gate')\n"
    f"{_WIDGET}"  # final expression -> the control is visible to the user
)

# A cell cannot read the .value of a UI element it created, so the value read
# lives in its own dependent cell — which is also what proves reactivity.
_READER_SOURCE = f"gate_readback = int({_WIDGET}.value) + 100"

_DROPDOWN_SOURCE = (
    "import marimo as mo\n"
    f"{_DROPDOWN} = mo.ui.dropdown(options=['alpha', 'beta'], value='alpha', "
    "label='gate')\n"
    f"{_DROPDOWN}"
)

_DROPDOWN_READER_SOURCE = f"gate_dropdown_readback = str({_DROPDOWN}.value) + '!'"


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


async def _make_cell(source: str, server_url: str, session_id: str) -> str:
    """Create + run a cell, returning its id."""
    cell = await create_cell(source, session_id=session_id, server_url=server_url)
    assert cell["status"] == "ok", cell
    run = await run_cell(cell["cell_id"], session_id=session_id, server_url=server_url)
    assert run["status"] == "ok", run
    return cell["cell_id"]


@pytest.mark.live
async def test_set_ui_value_changes_widget_and_reruns_dependents(mutation_server):
    """Set a live widget value; the change and the reactive rerun are real."""
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        widget_cell_id = await _make_cell(_WIDGET_SOURCE, server_url, session_id)
        created.append(widget_cell_id)
        reader_cell_id = await _make_cell(_READER_SOURCE, server_url, session_id)
        created.append(reader_cell_id)

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _WIDGET) == "3", before
        assert _inner_value(before, "gate_readback") == "103", before

        result = await set_ui_value(
            _WIDGET, 7, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["variable_name"] == _WIDGET
        # A scalar is accepted by the element's declared shape (int | float)...
        assert result["accepted_shape"] == "int | float", result
        # ...and the read-back confirms the element actually moved.
        assert result["verified"] is True, result
        assert result["applied"] is True, result
        assert result["value_before"] == 3, result
        assert result["value_after"] == 7, result
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
        assert by_id[widget_cell_id] == "idle", states
        assert by_id[reader_cell_id] == "idle", states
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
    assert missing["reason"] == "unknown_variable", missing
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
        assert non_ui["reason"] == "not_a_ui_element", non_ui
        assert "not a marimo UI element" in non_ui["message"], non_ui
        assert non_ui["datatype"] == "int", non_ui
    finally:
        await delete_cell(cell_id, session_id=session_id, server_url=server_url)


@pytest.mark.live
async def test_set_ui_value_cannot_address_a_cell_private_widget(mutation_server):
    """A leading underscore makes a widget unreachable — bind a bare name.

    marimo keeps underscore-prefixed names cell-private: no other cell can see
    them, so they never reach `ctx.globals` and `set_ui_value` cannot address
    them. This is why every other test here uses a bare widget name.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        widget_cell_id = await _make_cell(
            "import marimo as mo\n"
            "_private_slider = mo.ui.slider(0, 10, value=3, label='priv')\n"
            "_private_slider",
            server_url,
            session_id,
        )
        created.append(widget_cell_id)

        result = await set_ui_value(
            "_private_slider", 5, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "unknown_variable", result
        assert "not a live kernel global" in result["message"], result

        # And a dependent cell cannot see the name either: it is cell-private,
        # not merely hidden from the tool. The run fails with a NameError and
        # run_cell surfaces the kernel's traceback.
        reader = await create_cell(
            "private_readback = int(_private_slider.value) + 100",
            session_id=session_id,
            server_url=server_url,
        )
        created.append(reader["cell_id"])
        run = await run_cell(
            reader["cell_id"], session_id=session_id, server_url=server_url
        )
        assert "error" in run, run
        assert "_private_slider" in run.get("stderr", ""), run
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_scalar_to_dropdown_is_refused_not_swallowed(
    mutation_server,
):
    """The consumer's T10 bug: a scalar dropdown key must not be a silent no-op.

    A scalar trips ``assert len(value) == 1`` inside ``dropdown._convert_value``;
    marimo swallows that, so before the shape guard this returned `ok` with the
    widget unchanged.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        dropdown_cell_id = await _make_cell(_DROPDOWN_SOURCE, server_url, session_id)
        created.append(dropdown_cell_id)
        reader_cell_id = await _make_cell(
            _DROPDOWN_READER_SOURCE, server_url, session_id
        )
        created.append(reader_cell_id)

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _DROPDOWN) == "alpha", before
        assert _inner_value(before, "gate_dropdown_readback") == "alpha!", before

        result = await set_ui_value(
            _DROPDOWN, "beta", session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "value_shape_mismatch", result
        assert result["element_type"] == "dropdown", result
        assert result["accepted_shape"] == "list[str]", result
        assert result["did_you_mean"] == ["beta"], result
        assert result["submitted_value"] == "beta", result
        assert "Nothing was changed" in result["message"], result

        # The widget (and everything downstream of it) is untouched.
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _DROPDOWN) == "alpha", after
        assert _inner_value(after, "gate_dropdown_readback") == "alpha!", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_dropdown_list_applies_and_reruns_dependents(
    mutation_server,
):
    """The corrected form applies, is verified by read-back, and reruns."""
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        dropdown_cell_id = await _make_cell(_DROPDOWN_SOURCE, server_url, session_id)
        created.append(dropdown_cell_id)
        reader_cell_id = await _make_cell(
            _DROPDOWN_READER_SOURCE, server_url, session_id
        )
        created.append(reader_cell_id)

        result = await set_ui_value(
            _DROPDOWN, ["beta"], session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["verified"] is True, result
        assert result["applied"] is True, result
        assert result["value_before"] == "alpha", result
        assert result["value_after"] == "beta", result

        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _DROPDOWN) == "beta", after
        assert _inner_value(after, "gate_dropdown_readback") == "beta!", after

        # A repeat with the same value is a verified no-op, not a new mutation.
        repeat = await set_ui_value(
            _DROPDOWN, ["beta"], session_id=session_id, server_url=server_url
        )
        assert repeat["status"] == "ok", repeat
        assert repeat["verified"] is True, repeat
        assert repeat["applied"] is False, repeat
        assert repeat["no_change"] is True, repeat
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_invalid_dropdown_key_is_an_error_not_an_ok(
    mutation_server,
):
    """marimo's own rejection (unknown option) must not be reported as success.

    The kernel writes the ValueError to stderr and drops the update; the tool
    scans that stderr and returns the kernel's message, which already lists the
    valid options.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        dropdown_cell_id = await _make_cell(_DROPDOWN_SOURCE, server_url, session_id)
        created.append(dropdown_cell_id)

        result = await set_ui_value(
            _DROPDOWN, ["nope"], session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "value_not_applied", result
        assert result["element_type"] == "dropdown", result
        assert result["accepted_shape"] == "list[str]", result
        assert result["submitted_value"] == ["nope"], result
        assert "not a valid option" in result["kernel_message"], result
        assert "alpha" in result["kernel_message"], result
        assert result["value_before"] == "alpha", result
        assert result["value_after"] == "alpha", result
        assert result["next_steps"], result

        # Confirmed unmoved by an independent read.
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _DROPDOWN) == "alpha", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted
