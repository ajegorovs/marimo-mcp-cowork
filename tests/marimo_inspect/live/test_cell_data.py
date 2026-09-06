"""Live integration tests for the cell_data template.

Behavioral assertions: code retrieved from the live kernel round-trips the
fixture notebook's actual cell source.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_data import TEMPLATE_CELL_DATA

EXPECTED_TOTAL_CELLS = 6


@pytest.mark.live
async def test_cell_data_returns_all_cells(live_client, live_session):
    """cell_data (empty list = all cells) returns every fixture cell."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])
    assert len(data["data"]) == EXPECTED_TOTAL_CELLS


@pytest.mark.live
async def test_cell_data_roundtrips_known_code(live_client, live_session):
    """The computed-value cell's source round-trips exactly."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])

    code_by_id = {c["cell_id"]: c["code"] for c in data["data"]}
    # The fixtures' value_a/value_b/value_c cell.
    matching = [
        code
        for code in code_by_id.values()
        if "value_a = 1" in code and "value_c = value_a + value_b**2" in code
    ]
    assert matching, (
        f"No cell with computed-value source; got: {list(code_by_id.values())}"
    )


@pytest.mark.live
async def test_cell_data_roundtrips_error_cell(live_client, live_session):
    """The intentional error cell's source is retrievable."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])

    error_codes = [
        c["code"] for c in data["data"] if "integration_test_error" in c["code"]
    ]
    assert error_codes, "Error cell source should be present via cell_data"


@pytest.mark.live
async def test_cell_data_multiple_cells_matches_cell_map(live_client, live_session):
    """cell_data count agrees with the cell map total."""
    from marimo_inspection.templates.cell_map import TEMPLATE_CELL_MAP

    map_result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    map_data = json.loads(map_result.stdout[0])

    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])
    assert len(data["data"]) == map_data["total_cells"]
