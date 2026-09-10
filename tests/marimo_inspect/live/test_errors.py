"""Live integration tests for the errors template.

Note on scope: a headless session created via /sse is not instantiated, so no
cell has run and no execution error has been recorded yet
(instantiation requires the token-gated /api/kernel/instantiate endpoint).
The verifiable live contract is that the template runs against a real kernel
and reports a well-formed, consistent error summary (0 errors in a fresh,
non-instantiated session). Instantiation-dependent error detection belongs to
a future instantiate-enabled phase.

The console-channel regressions below are behavioural: they need cells that
actually executed, so they use the isolated ``mutation_server`` fixture and
materialize their subjects by *creating* the cell through the MCP write tools
— cells created that way DO run, with no browser (see docs/live-tests.md).
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.errors import TEMPLATE_ERRORS
from marimo_inspection.tools.cells import get_cell_outputs
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

# A widget whose on_change handler raises: marimo assigns the new value and
# THEN runs the handler, so the value moves while the traceback lands on the
# cell's console stderr and nothing reaches marimo's structured error records.
_BOOM_WIDGET = "gate_boom"

_BOOM_WIDGET_SOURCE = (
    "import marimo as mo\n"
    "def _boom(value):\n"
    "    raise ValueError('boom from on_change')\n"
    f"{_BOOM_WIDGET} = mo.ui.slider(0, 10, value=1, on_change=_boom, label='boom')\n"
    f"{_BOOM_WIDGET}"  # final expression -> the control is rendered
)


async def _make_cell(source: str, server_url: str, session_id: str) -> str:
    """Create + run a cell, returning its id."""
    cell = await create_cell(source, session_id=session_id, server_url=server_url)
    assert cell["status"] == "ok", cell
    run = await run_cell(cell["cell_id"], session_id=session_id, server_url=server_url)
    assert run["status"] == "ok", run
    return cell["cell_id"]


async def _delete_cells(cell_ids: list[str], server_url: str, session_id: str) -> None:
    """Delete every created cell (newest first) and assert each delete."""
    for cell_id in reversed(cell_ids):
        deleted = await delete_cell(
            cell_id, session_id=session_id, server_url=server_url
        )
        assert deleted.get("status") == "ok", deleted


@pytest.mark.live
async def test_errors_template_structure(live_client, live_session):
    """The errors template returns the documented fields."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert isinstance(data["has_errors"], bool)
    assert isinstance(data["total_errors"], int)
    # Backward-compatible totals are the STRUCTURED-only counts.
    assert isinstance(data["total_structured_errors"], int)
    assert data["total_structured_errors"] == data["total_errors"]
    assert data["total_errors"] >= 0
    assert isinstance(data["total_cells_with_errors"], int)
    assert isinstance(data["has_console_exception"], bool)
    assert isinstance(data["total_console_exception_cells"], int)
    assert isinstance(data["cells"], list)
    assert data["has_errors"] == (data["total_errors"] > 0)
    for cell in data["cells"]:
        assert "structured_errors" in cell
        assert "console_stderr" in cell
        assert "has_console_exception" in cell


@pytest.mark.live
async def test_errors_consistent_fresh_session(live_client, live_session):
    """A fresh, non-instantiated session reports a self-consistent summary."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    # Structured and console-exception cells are both subsets of `cells`
    # (a single cell may carry both channels).
    assert data["total_cells_with_errors"] <= len(data["cells"])
    assert data["total_console_exception_cells"] <= len(data["cells"])
    if data["has_errors"]:
        assert data["total_cells_with_errors"] > 0
    if data["has_console_exception"]:
        assert data["total_console_exception_cells"] > 0


@pytest.mark.live
async def test_errors_template_stable_across_runs(live_client, live_session):
    """Repeated executions are stable and do not crash the kernel."""
    for _ in range(3):
        result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
        assert result.status == "ok", f"Template failed: stderr={result.stderr}"
        data = json.loads(result.stdout[0])
        assert "has_errors" in data
        assert "total_errors" in data


@pytest.mark.live
async def test_console_stderr_flags_a_console_only_ui_handler_exception(
    mutation_server,
):
    """Agenda T12: get_errors must flag the cell behind a UI-handler traceback.

    A raising ``on_change`` handler is invisible to marimo's structured error
    records — only the cell's console stderr carries it. The channel filter
    compared marimo's str-mixin ``CellChannel`` enum unnormalized (``str()``
    reads ``"CellChannel.STDERR"``), so it never matched and the cell the
    frontend shows the traceback for was reported nowhere.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        cell_id = await _make_cell(_BOOM_WIDGET_SOURCE, server_url, session_id)
        created.append(cell_id)

        # Trigger the handler. The value moves (the handler runs after the
        # assignment); the traceback goes to the cell's console stderr.
        update = await set_ui_value(
            _BOOM_WIDGET, 5, session_id=session_id, server_url=server_url
        )
        assert update["status"] == "error", update
        variables = await get_variables(session_id=session_id, server_url=server_url)
        assert variables["variables"][_BOOM_WIDGET]["value"]["value"] == "5", variables

        errors = await get_errors(session_id=session_id, server_url=server_url)
        # The structured channel stays silent for this failure class...
        assert errors["has_errors"] is False, errors
        # ...and the console channel carries it.
        assert errors["has_console_exception"] is True, errors
        assert errors["total_console_exception_cells"] >= 1, errors
        flagged = {cell["cell_id"]: cell for cell in errors["cells"]}
        assert cell_id in flagged, errors
        entry = flagged[cell_id]
        assert entry["has_console_exception"] is True, entry
        assert entry["console_stderr"], entry
        assert entry["console_stderr"][0]["channel"] == "stderr", entry
        joined = "".join(event["data"] for event in entry["console_stderr"])
        assert "Traceback" in joined, entry
        assert "ValueError: boom from on_change" in joined, entry
    finally:
        await _delete_cells(created, server_url, session_id)


@pytest.mark.live
async def test_print_output_lands_in_the_stdout_channel(mutation_server):
    """Agenda T12: a print() must appear in get_cell_outputs.stdout.

    ``console_events`` always carried it; the stdout/stderr lists filtered the
    unnormalized channel, so a consumer reading them saw an empty stream.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        cell_id = await _make_cell(
            'print("console-stdout-probe")', server_url, session_id
        )
        created.append(cell_id)

        outputs = await get_cell_outputs(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        cell = outputs["cells"][0]
        assert cell["cell_id"] == cell_id, outputs
        assert cell["stdout"], cell
        assert cell["stdout"][0]["channel"] == "stdout", cell
        assert "console-stdout-probe" in cell["stdout"][0]["data"], cell
        assert cell["stderr"] == [], cell
    finally:
        await _delete_cells(created, server_url, session_id)
