"""Hermetic live regression for the `set_ui_value` widget tool.

The plan assumed widget behaviour could only be validated against a
browser-instantiated session, because the shared live fixture's notebook cells
never execute (the `/sse`-created session cannot be instantiated without the
skew token — see AGENTS.md). That assumption is too conservative: a cell
*created through the MCP write tools* runs fine, so a widget can be
materialized in-kernel and driven end to end here.

The behaviours below are locked in against a real marimo 0.24 kernel:

* **Reactivity** — `set_ui_value("gate_slider", 7)` moves the element 3 -> 7 AND
  reactively re-runs its dependent cell (derived global 103 -> 107), both cells
  ending `idle`.
* **The swallow trap** — marimo catches an exception raised while applying a
  UI-element value and only writes it to the kernel's stderr, so a rejected
  update used to come back as `status: ok` with an unchanged widget. A scalar
  sent to a dropdown trips exactly that path (``assert len(value) == 1`` inside
  ``dropdown._convert_value``), an unknown option key trips option validation,
  and both must now surface as errors with the widget unmoved.
* **The transport key is the string form** — a dropdown built from numeric
  options (`options=[1, 2, 3, 4]`) is keyed by the strings `"1"`..`"4"` and
  stores the number. The scalar `4` is therefore refused with `did_you_mean
  == ["4"]` (never `[4]`), and that exact correction applies the numeric 4.
* **T20 — a button's value is not the click.** `mo.ui.button` exposes the
  `on_click` return as `value` and a click counter as its frontend value, so a
  side-effect-only handler leaves `value` unchanged while the click landed. The
  counter (0 is the initialization sentinel) is the delivery evidence:
  `handler_invoked` is `true` when the counter moves to the submitted value,
  `false` for the sentinel, and `null` when a repeated counter does not change
  (unknown — even though marimo's runtime does call the handler again). A
  raising `on_click` is `on_click_failed`, and the handler's partial side
  effects are acknowledged.
"""

from __future__ import annotations

import pytest

from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.errors import get_errors
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

# A slider whose on_change handler raises: marimo assigns the new value and
# only THEN calls the handler, so the value moves while the callback fails.
_BOOM = "gate_boom"
_BOOM_SOURCE = (
    "import marimo as mo\n"
    "def _boom(value):\n"
    "    raise ValueError('boom from on_change')\n"
    f"{_BOOM} = mo.ui.slider(0, 10, value=1, on_change=_boom, label='boom')\n"
    f"{_BOOM}"
)

# A dropdown whose options are NUMBERS: marimo keys a dropdown by the string
# form of each option, so `options=[1, 2, 3, 4]` accepts the transport key
# "4" and stores the number 4. Initial selection is the numeric 1.
_NUMERIC_DROPDOWN = "gate_number_dropdown"
_NUMERIC_DROPDOWN_SOURCE = (
    "import marimo as mo\n"
    f"{_NUMERIC_DROPDOWN} = mo.ui.dropdown(options=[1, 2, 3, 4], value=1, "
    "label='number')\n"
    f"{_NUMERIC_DROPDOWN}"
)

# Reading .value back in a dependent cell proves the stored value is the
# NUMBER 4: int 4 * 2 == 8, whereas the string "4" * 2 would be "44".
_NUMERIC_READER_SOURCE = f"gate_number_readback = {_NUMERIC_DROPDOWN}.value * 2"

# --- T20: button click evidence -------------------------------------------
#
# A button whose `on_click` only sets state — the consumer's step-button shape.
# The element's own value is the handler's return (None here), so it never
# moves; the frontend click counter is the only evidence the click landed.
_BUTTON = "gate_button"
_BUTTON_READER = "gate_button_clicks"
_BUTTON_SOURCE = (
    "import marimo as mo\n"
    "gate_clicks, gate_set_clicks = mo.state(0)\n"
    "\n"
    "def _on_click(_value):\n"
    "    gate_set_clicks(gate_clicks() + 1)\n"
    "\n"
    f"{_BUTTON} = mo.ui.button(on_click=_on_click, label='go')\n"
    f"{_BUTTON}"
)
_BUTTON_READER_SOURCE = f"{_BUTTON_READER} = gate_clicks()"

# A button whose on_click applies a side effect and THEN raises: a partial side
# effect that the error report must acknowledge.
_BOOM_BUTTON = "gate_boom_button"
_BOOM_BUTTON_READER = "gate_boom_button_clicks"
_BOOM_BUTTON_SOURCE = (
    "import marimo as mo\n"
    "gate_boom_clicks, gate_set_boom_clicks = mo.state(0)\n"
    "\n"
    "def _on_click_boom(_value):\n"
    "    gate_set_boom_clicks(gate_boom_clicks() + 1)\n"
    "    raise ValueError('boom from on_click')\n"
    "\n"
    f"{_BOOM_BUTTON} = mo.ui.button(on_click=_on_click_boom, label='boom')\n"
    f"{_BOOM_BUTTON}"
)
_BOOM_BUTTON_READER_SOURCE = f"{_BOOM_BUTTON_READER} = gate_boom_clicks()"

# A run_button reuses the button component; its frontend value is a counter too.
_RUN_BUTTON = "gate_run_button"


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
        # not merely hidden from the tool. The run fails with a NameError, and
        # run_cell reports it truthfully: the run call carries the kernel's
        # traceback in `execution_error`/`stderr` (T15) while the per-cell
        # report names the failing cell and its terminal state.
        reader = await create_cell(
            "private_readback = int(_private_slider.value) + 100",
            session_id=session_id,
            server_url=server_url,
        )
        created.append(reader["cell_id"])
        run = await run_cell(
            reader["cell_id"], session_id=session_id, server_url=server_url
        )
        assert run["status"] == "partial", run
        assert run["execution_error"], run
        assert "_private_slider" in run.get("stderr", ""), run
        assert run["failed_cell_ids"] == [reader["cell_id"]], run
        failed_row = next(
            row for row in run["cells"] if row["cell_id"] == reader["cell_id"]
        )
        assert failed_row["runtime_state"] == "exception", failed_row

        # The STRUCTURED channel stays silent about this failure class: the
        # cell ends `exception` with an EMPTY `cell.errors`, so `has_errors` is
        # false and nothing is counted as a structured error. The CONSOLE
        # channel does carry the traceback (the same one the run payload above
        # holds) — pinned for agenda T11/T12; see co-work-loop.md §6.
        errors = await get_errors(session_id=session_id, server_url=server_url)
        assert errors["has_errors"] is False, errors
        assert errors["total_structured_errors"] == 0, errors
        flagged = {cell["cell_id"]: cell for cell in errors["cells"]}
        assert flagged[reader["cell_id"]]["structured_errors"] == [], errors
        assert flagged[reader["cell_id"]]["console_stderr"], errors
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
        # A rejected conversion never moved the element: `applied` stays false.
        assert result["applied"] is False, result
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


@pytest.mark.live
async def test_set_ui_value_reports_an_on_change_failure_as_applied(mutation_server):
    """Agenda T13: a raising on_change handler is not a "value not applied".

    marimo assigns the element's new value and *then* calls ``on_change``, so a
    handler that raises leaves the value moved and only the callback failed.
    The kernel's stderr marker is identical for both failure points, but the
    tool's own read-back is not fooled: a rejection whose read-back moved is
    reported as ``on_change_failed`` with ``applied: true`` plus the before and
    after values, and no field claims the element was unchanged.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        widget_cell_id = await _make_cell(_BOOM_SOURCE, server_url, session_id)
        created.append(widget_cell_id)

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _BOOM) == "1", before

        result = await set_ui_value(
            _BOOM, 5, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "on_change_failed", result
        assert result["applied"] is True, result
        assert result["value_before"] == 1, result
        assert result["value_after"] == 5, result
        assert result["kernel_message"] == "ValueError: boom from on_change", result
        assert "boom from on_change" in result["message"], result
        # Self-consistency: the value moved, so nothing may claim otherwise.
        assert "NOT changed" not in result["message"], result
        assert result["next_steps"], result

        # Confirmed moved by an independent read of the live kernel global.
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _BOOM) == "5", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_on_change_failure_on_an_already_held_value(
    mutation_server,
):
    """T13 residual: the handler ran on a value that was already held.

    The widget is created with ``value=1`` (see `_BOOM_SOURCE`), so submitting
    1 again moves nothing — yet ``_update`` assigns the value with no equality
    shortcut and *still* calls the raising handler. The read-back is therefore
    unmoved, which must not be reported as ``value_not_applied``: nothing was
    rejected, the value was accepted and the callback failed. Only the kernel
    traceback's call site ("self._on_change(self._value)") separates this from
    a rejected conversion — marimo writes the same notice for both.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        widget_cell_id = await _make_cell(_BOOM_SOURCE, server_url, session_id)
        created.append(widget_cell_id)

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _BOOM) == "1", before

        result = await set_ui_value(
            _BOOM, 1, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "on_change_failed", result
        assert result["applied"] is False, result
        assert result["no_change"] is True, result
        assert result["handler_ran"] is True, result
        assert result["value_before"] == 1 and result["value_after"] == 1, result
        assert result["kernel_message"] == "ValueError: boom from on_change", result
        assert "boom from on_change" in result["message"], result
        # Nothing was rejected, so no next step may tell the caller to re-send
        # the value in the element's accepted shape.
        assert "Re-send" not in " ".join(result["next_steps"]), result

        # Confirmed unmoved by an independent read of the live kernel global —
        # which is exactly why the read-back alone cannot classify this case.
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _BOOM) == "1", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_numeric_dropdown_correction_uses_the_string_key(
    mutation_server,
):
    """T-V1: a numeric dropdown option is addressed by its STRING transport key.

    marimo keys a dropdown by the string form of each option, so
    ``options=[1, 2, 3, 4]`` accepts the key ``"4"`` and stores the number 4.
    The scalar ``4`` must be refused with ``did_you_mean == ["4"]`` (never
    ``[4]`` — following that verbatim would be refused again by the kernel), and
    applying the exact correction must move the element to the numeric 4.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        dropdown_cell_id = await _make_cell(
            _NUMERIC_DROPDOWN_SOURCE, server_url, session_id
        )
        created.append(dropdown_cell_id)
        reader_cell_id = await _make_cell(
            _NUMERIC_READER_SOURCE, server_url, session_id
        )
        created.append(reader_cell_id)

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _NUMERIC_DROPDOWN) == "1", before
        assert _inner_value(before, "gate_number_readback") == "2", before

        mismatch = await set_ui_value(
            _NUMERIC_DROPDOWN, 4, session_id=session_id, server_url=server_url
        )
        assert mismatch["status"] == "error", mismatch
        assert mismatch["reason"] == "value_shape_mismatch", mismatch
        assert mismatch["element_type"] == "dropdown", mismatch
        assert mismatch["accepted_shape"] == "list[str]", mismatch
        assert mismatch["submitted_value"] == 4, mismatch
        # The correction is the element's own STRING key, not the int sent.
        assert mismatch["did_you_mean"] == ["4"], mismatch

        # The refusal happened before anything was queued: nothing moved.
        untouched = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(untouched, _NUMERIC_DROPDOWN) == "1", untouched

        # The negative half: the int key the caller sent is NOT the option key,
        # so a naive `[4]` correction is refused by the kernel and the element
        # stays put. Only the string "4" addresses this option.
        int_key = await set_ui_value(
            _NUMERIC_DROPDOWN, [4], session_id=session_id, server_url=server_url
        )
        assert int_key["status"] == "error", int_key
        assert int_key["reason"] == "value_not_applied", int_key
        assert int_key["value_before"] == 1, int_key
        assert int_key["value_after"] == 1, int_key

        # Applying that exact correction succeeds and is verified by read-back.
        result = await set_ui_value(
            _NUMERIC_DROPDOWN, ["4"], session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["verified"] is True, result
        assert result["applied"] is True, result
        assert result["value_before"] == 1, result
        assert result["value_after"] == 4, result

        # Independent read-back: the stored value is the NUMBER 4 (int 4 * 2 ==
        # 8; the string "4" * 2 would be "44").
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _NUMERIC_DROPDOWN) == "4", after
        assert _inner_value(after, "gate_number_readback") == "8", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_reports_a_side_effect_only_button_click(mutation_server):
    """T20: the click landed and its side effect applied, though `.value` did not.

    A consumer step button's ``on_click`` only writes a ``mo.state`` value, so
    the element's own value (the handler's ``None`` return) never moves. The
    frontend click counter is the delivery evidence: 0 -> 1 proves the update
    reached the element and marimo invoked the handler, and the side effect is
    confirmed independently by the dependent reader cell.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        created.append(await _make_cell(_BUTTON_SOURCE, server_url, session_id))
        created.append(await _make_cell(_BUTTON_READER_SOURCE, server_url, session_id))

        before = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(before, _BUTTON_READER) == "0", before

        result = await set_ui_value(
            _BUTTON, 1, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["element_type"] == "button", result
        # The element's own value is the handler's return (None) — unchanged...
        assert result["applied"] is False, result
        assert result["value_before"] is None, result
        assert result["value_after"] is None, result
        # ...but the click is confirmed by the frontend counter, which the old
        # payload never reported (the T20 misread).
        assert result["frontend_value_before"] == 0, result
        assert result["frontend_value_after"] == 1, result
        assert result["click_delivered"] is True, result
        assert result["handler_invoked"] is True, result
        assert result["side_effects_verified"] is False, result
        # A button no-change report is NOT "already held this value".
        assert result.get("no_change") is None, result
        assert "already held" not in result["message"].lower(), result
        assert result["next_steps"], result

        after = await get_variables(session_id=session_id, server_url=server_url)
        # The handler's side effect is confirmed by an independent read.
        assert _inner_value(after, _BUTTON_READER) == "1", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_zero_counter_does_not_click_a_button(mutation_server):
    """T20: submitting 0 is the initialization sentinel — on_click never runs."""
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        created.append(await _make_cell(_BUTTON_SOURCE, server_url, session_id))
        created.append(await _make_cell(_BUTTON_READER_SOURCE, server_url, session_id))

        result = await set_ui_value(
            _BUTTON, 0, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["handler_invoked"] is False, result
        assert result["click_delivered"] is False, result
        assert result["side_effects_verified"] is False, result
        assert "warning" in result, result
        assert "sentinel" in result["message"].lower(), result

        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _BUTTON_READER) == "0", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_repeated_button_counter_is_unknown(mutation_server):
    """T20: a repeated nonzero counter cannot be verified from the read-back.

    marimo's runtime does invoke the handler again (the side effect grows), but
    the frontend counter is unchanged, so the tool must report
    ``handler_invoked: null`` — never ``true`` and never ``false``.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        created.append(await _make_cell(_BUTTON_SOURCE, server_url, session_id))
        created.append(await _make_cell(_BUTTON_READER_SOURCE, server_url, session_id))

        first = await set_ui_value(
            _BUTTON, 1, session_id=session_id, server_url=server_url
        )
        assert first["handler_invoked"] is True, first
        after_first = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after_first, _BUTTON_READER) == "1", after_first

        # Replay the SAME counter: the frontend value does not change.
        repeat = await set_ui_value(
            _BUTTON, 1, session_id=session_id, server_url=server_url
        )
        assert repeat["status"] == "ok", repeat
        assert repeat["handler_invoked"] is None, repeat
        assert repeat.get("click_delivered") is None, repeat
        assert repeat.get("no_change") is None, repeat
        assert "warning" in repeat, repeat
        assert repeat["side_effects_verified"] is False, repeat

        # The runtime DID invoke the handler again — the point is that the tool
        # declines to claim it, not that the handler was skipped.
        after_repeat = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after_repeat, _BUTTON_READER) == "2", after_repeat
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_set_ui_value_reports_a_raising_on_click_as_an_error(mutation_server):
    """T20: a button whose on_click raises is an error, and partial effects count.

    marimo catches the exception inside the button's own conversion and writes
    a distinct stderr marker, so the call would otherwise look like a success.
    The handler ran (the partial side effect proves it) and raised, so the
    payload must say so and must not tell the caller to re-send the counter.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        created.append(await _make_cell(_BOOM_BUTTON_SOURCE, server_url, session_id))
        created.append(
            await _make_cell(_BOOM_BUTTON_READER_SOURCE, server_url, session_id)
        )

        result = await set_ui_value(
            _BOOM_BUTTON, 1, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "error", result
        assert result["reason"] == "on_click_failed", result
        assert result["handler_ran"] is True, result
        assert result["handler_invoked"] is True, result
        assert result["side_effects_verified"] is False, result
        assert "boom from on_click" in result["message"], result
        # Partial side effects must be acknowledged, not denied.
        assert "partial" in result["message"].lower(), result
        assert "Re-send" not in " ".join(result["next_steps"]), result
        assert "already held" not in result["message"].lower(), result

        # The handler applied its side effect before raising.
        after = await get_variables(session_id=session_id, server_url=server_url)
        assert _inner_value(after, _BOOM_BUTTON_READER) == "1", after
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted


@pytest.mark.parametrize("counter", [1, 2])
@pytest.mark.live
async def test_set_ui_value_reports_run_button_click_evidence(mutation_server, counter):
    """T20: run_button carries the same frontend click-counter semantics.

    marimo reuses the button component for `run_button`, so a nonzero counter
    is delivered and invokes the conversion exactly like `button`; the payload
    must report the same counter evidence instead of a bare no-change.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        source = (
            "import marimo as mo\n"
            f"{_RUN_BUTTON} = mo.ui.run_button(label='run')\n"
            f"{_RUN_BUTTON}"
        )
        created.append(await _make_cell(source, server_url, session_id))

        result = await set_ui_value(
            _RUN_BUTTON, counter, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["element_type"] == "run_button", result
        assert result["frontend_value_before"] == 0, result
        assert result["frontend_value_after"] == counter, result
        assert result["click_delivered"] is True, result
        assert result["handler_invoked"] is True, result
        assert result["side_effects_verified"] is False, result
        assert result["next_steps"], result
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted
