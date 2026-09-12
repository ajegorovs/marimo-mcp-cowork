"""Live integration tests for the cell_outputs template.

Against a freshly-created headless session (cells not yet executed), every
fixture cell is reported with the documented output keys and empty/null
output. This is the honest contract for a non-instantiated session.

The restored-output regression at the bottom drives the REAL MCP handlers
against an isolated server: it pins that a cell edited without re-running
keeps its prior rendering visible while being marked stale (T21).
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_outputs import TEMPLATE_CELL_OUTPUTS
from marimo_inspection.tools.cells import get_cell_data, get_cell_outputs
from marimo_inspection.tools.mutation import (
    create_cell,
    delete_cell,
    edit_cell,
    run_cell,
)

EXPECTED_TOTAL_CELLS = 6
OUTPUT_KEYS = {
    "visual_output",
    "visual_mimetype",
    "stdout",
    "stderr",
    # Runtime truthfulness (T21): the live kernel state and the derived
    # stale-output signal are part of every row's documented shape.
    "runtime_state",
    "output_stale",
}


@pytest.mark.live
async def test_cell_outputs_returns_all_cells(live_client, live_session):
    """cell_outputs covers every fixture cell."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])
    assert len(data["cells"]) == EXPECTED_TOTAL_CELLS


@pytest.mark.live
async def test_cell_outputs_structure_per_cell(live_client, live_session):
    """Each cell has the documented output keys."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)
    data = json.loads(result.stdout[0])

    assert isinstance(data["cells"], list)
    for cell in data["cells"]:
        assert OUTPUT_KEYS.issubset(cell.keys()), (
            f"Missing output keys; got {sorted(cell.keys())}"
        )


@pytest.mark.live
async def test_runtime_state_and_stale_flag_are_typed(live_client, live_session):
    """runtime_state is a string-or-None and output_stale mirrors it exactly.

    On this shared session nothing has been executed, so the honest reading is
    the kernel's: every cell is stale and its (empty) output is flagged stale
    rather than passed off as current.
    """
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)
    data = json.loads(result.stdout[0])

    for cell in data["cells"]:
        state = cell["runtime_state"]
        assert state is None or isinstance(state, str), cell
        assert isinstance(cell["output_stale"], bool), cell
        assert cell["output_stale"] is (state == "stale"), cell


@pytest.mark.live
async def test_cell_outputs_include_error_cell(live_client, live_session):
    """The intentional error cell is listed in outputs."""
    from marimo_inspection.templates.cell_data import TEMPLATE_CELL_DATA

    celldata = json.loads(
        (await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)).stdout[
            0
        ]
    )
    error_cell_ids = {
        c["cell_id"] for c in celldata["data"] if "integration_test_error" in c["code"]
    }

    outputs = json.loads(
        (
            await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)
        ).stdout[0]
    )
    output_ids = {c["cell_id"] for c in outputs["cells"]}
    assert error_cell_ids.issubset(output_ids)


# -------------------------------------------------------------------
# Restored-output / staleness regression (T21) — real kernel, real handlers
# -------------------------------------------------------------------


def _row(payload: dict, cell_id: str) -> dict:
    """Return the cells[] row for ``cell_id`` or fail loudly."""
    for row in payload["cells"]:
        if row["cell_id"] == cell_id:
            return row
    raise AssertionError(f"cell {cell_id} missing from payload: {payload}")


def _visible_source(marker: int) -> str:
    """A cell whose final expression renders a visible output."""
    return f'import marimo as mo\nmo.md("t21-probe-{marker}")'


@pytest.mark.live
async def test_restored_output_is_flagged_stale_until_rerun(mutation_server):
    """T21: an un-rerun edit leaves the prior output visible but STALE.

    create+run a cell with visible output → the read is current
    (``runtime_state: "idle"``, ``output_stale: false``). Edit it WITHOUT
    running → the same prior rendering is still reported, now flagged
    ``runtime_state: "stale"`` / ``output_stale: true`` — visible, but not
    passed off as current. Run → the new rendering is current again.

    Pre-fix the rows carried no ``runtime_state``/``output_stale`` at all, so
    this fails on the first stale assertion (KeyError on the missing key).
    """
    _manager, server_url, session_id, _copy = mutation_server
    cell_id: str | None = None
    try:
        created = await create_cell(
            _visible_source(1), session_id=session_id, server_url=server_url
        )
        assert created["status"] == "ok", created
        cell_id = str(created["cell_id"])

        # A full-source read arms the edit guard (the recovery path).
        await get_cell_data(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )

        ran = await run_cell(cell_id, session_id=session_id, server_url=server_url)
        assert ran["status"] == "ok", ran

        current = await get_cell_outputs(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        current_row = _row(current, cell_id)
        assert current_row["runtime_state"] == "idle", current_row
        assert current_row["output_stale"] is False, current_row
        assert current_row["visual_output"] is not None, current_row
        first_render = current_row["visual_output"]

        # Edit WITHOUT running: the cell goes stale, output stays as-is.
        edited = await edit_cell(
            cell_id, _visible_source(2), session_id=session_id, server_url=server_url
        )
        assert edited["status"] == "ok", edited

        stale = await get_cell_outputs(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        stale_row = _row(stale, cell_id)
        assert stale_row["runtime_state"] == "stale", stale_row
        assert stale_row["output_stale"] is True, stale_row
        # The prior rendering is still visible (restored, not erased) — it just
        # cannot masquerade as current.
        assert stale_row["visual_output"] is not None, stale_row
        assert stale_row["visual_output"] == first_render, (
            "expected the prior rendering to remain visible while stale; "
            f"got {stale_row['visual_output']!r}"
        )

        rerun = await run_cell(cell_id, session_id=session_id, server_url=server_url)
        assert rerun["status"] == "ok", rerun

        final = await get_cell_outputs(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        final_row = _row(final, cell_id)
        assert final_row["runtime_state"] == "idle", final_row
        assert final_row["output_stale"] is False, final_row
        assert final_row["visual_output"] is not None, final_row
        assert final_row["visual_output"] != first_render, (
            "the rerun must render the new output, not the restored one; "
            f"got {final_row['visual_output']!r}"
        )
    finally:
        if cell_id is not None:
            await delete_cell(cell_id, session_id=session_id, server_url=server_url)
