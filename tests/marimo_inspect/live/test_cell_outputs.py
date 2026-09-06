"""Live integration tests for the cell_outputs template.

Against a freshly-created headless session (cells not yet executed), every
fixture cell is reported with the documented output keys and empty/null
output. This is the honest contract for a non-instantiated session.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_outputs import TEMPLATE_CELL_OUTPUTS

EXPECTED_TOTAL_CELLS = 6
OUTPUT_KEYS = {
    "visual_output",
    "visual_mimetype",
    "stdout",
    "stderr",
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
