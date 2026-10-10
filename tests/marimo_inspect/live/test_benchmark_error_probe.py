"""Characterize the wrapper error-channel split for a raising benchmark cell.

Companion to ``test_benchmark_fixture.py``: boots the disposable
``mission_clean.py`` copy, runs the original cells, then adds the SAME raising
source the A/B wrapper uses (``raise RuntimeError("a4 fault marker")``) and
executes it through the REAL handlers. The point is to measure — not assume —
which channel carries the failure: marimo's structured error records vs the
serialized ``console_stderr`` stream.

Observed on 0.24.x: a plain ``raise`` in a document cell populates BOTH
channels (unlike the widget ``on_change`` path, whose structured channel stays
silent). ``run_cell`` reports it per-cell as ``runtime_state: exception`` with a
structured ``errors`` entry and a top-level ``Execution failed`` + traceback
``stderr``; ``get_errors`` and ``get_cell_data(include_errors=True)`` agree.
"""

from pathlib import Path

import pytest

from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.mutation import create_cell, run_cell

FIXTURE = Path(__file__).parents[3] / "benchmark" / "notebooks" / "mission_clean.py"

FAULT_SOURCE = 'raise RuntimeError("a4 fault marker")'
FAULT_MARKER = "a4 fault marker"


@pytest.mark.live
async def test_wrapper_error_channel_split_for_raising_cell(notebook_server):
    """Measure how a plain cell raise surfaces across the two error channels."""
    original = FIXTURE.read_bytes()
    _manager, url, sid, copy = await notebook_server(
        FIXTURE.read_text(encoding="utf-8"), name="mission_error_probe.py"
    )

    # Original cells first (register the document, healthy baseline).
    boot = await run_cell(mode="all", session_id=sid, server_url=url)
    assert boot["status"] == "ok", boot

    created = await create_cell(FAULT_SOURCE, session_id=sid, server_url=url)
    assert created.get("status") == "ok", created
    cell_id = created["cell_id"]

    executed = await run_cell(cell_id, mode="cell", session_id=sid, server_url=url)
    errors = await get_errors(session_id=sid, server_url=url)
    data = await get_cell_data(
        cell_ids=[cell_id], include_errors=True, session_id=sid, server_url=url
    )

    # --- run_cell: per-cell structured failure + top-level traceback ------------
    assert executed["status"] == "partial", executed
    assert executed["failed_cell_ids"] == [cell_id], executed
    assert executed["succeeded_cell_ids"] == [], executed
    ex_row = next(c for c in executed["cells"] if c["cell_id"] == cell_id)
    assert ex_row["runtime_state"] == "exception", ex_row
    # run_cell's per-cell errors use the marimo CellError shape: kind + message.
    assert any(
        e.get("kind") == "runtime" and FAULT_MARKER in str(e.get("message", ""))
        for e in (ex_row["errors"] or [])
    ), ex_row
    # Backward-compatible top-level failure envelope carries the raw traceback.
    assert executed["error"] == "Execution failed", executed
    assert FAULT_MARKER in executed["stderr"], executed

    # --- get_errors: structured channel AND stderr channel both populated -------
    assert errors["has_errors"] is True, errors
    assert errors["total_structured_errors"] == 1, errors
    assert errors["total_cells_with_errors"] == 1, errors
    cell_entry = next(c for c in errors["cells"] if c["cell_id"] == cell_id)
    structured = cell_entry["structured_errors"]
    assert [e["kind"] for e in structured] == ["runtime"], structured
    assert any(
        FAULT_MARKER in str(e.get("msg", ""))
        or FAULT_MARKER in str(e.get("exception", ""))
        for e in structured
    ), structured
    # Same cell raise is ALSO recorded on the console channel as a traceback.
    assert cell_entry["console_stderr"], cell_entry
    stderr_text = "".join(str(o.get("data", "")) for o in cell_entry["console_stderr"])
    assert FAULT_MARKER in stderr_text, cell_entry
    assert "Traceback (most recent call last)" in stderr_text, cell_entry
    assert cell_entry["has_console_exception"] is True, cell_entry
    assert cell_entry["console_exception_evidence"] == "traceback", cell_entry

    # --- get_cell_data(include_errors=True) mirrors the same two channels -------
    row = next(r for r in data["data"] if r["cell_id"] == cell_id)
    assert row["runtime_state"] == "exception", row
    assert [e["kind"] for e in row["structured_errors"]] == ["runtime"], row
    assert row["console_stderr"], row
    assert row["has_console_exception"] is True, row
    assert row["console_exception_evidence"] == "traceback", row

    # The hermetic copy matches the same raising source, not a stale document.
    assert FAULT_SOURCE in copy.read_text(encoding="utf-8")
    assert FIXTURE.read_bytes() == original
