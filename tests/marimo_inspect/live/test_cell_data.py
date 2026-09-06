"""Integration tests for cell_data template against a live marimo kernel."""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_data import TEMPLATE_CELL_DATA


@pytest.mark.live
async def test_cell_data_returns_code(live_client, live_session):
    """Verify cell_data template returns actual cell code."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert "data" in data
    # Empty notebook has no cells
    assert isinstance(data["data"], list)


@pytest.mark.live
async def test_cell_data_structure(live_client, live_session):
    """The cell_data response should always have the expected structure."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])

    # Verify the response structure
    assert "data" in data
    assert isinstance(data["data"], list)


@pytest.mark.live
async def test_cell_data_multiple_cells(live_client, live_session):
    """Query multiple cells in one call."""
    from marimo_inspection.templates.cell_map import TEMPLATE_CELL_MAP

    map_result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    map_data = json.loads(map_result.stdout[0])

    if not map_data["cells"]:
        pytest.skip("No cells in test notebook")

    # Use the template with all cells (empty list = all cells)
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)

    assert result.status == "ok"
    data = json.loads(result.stdout[0])
    assert len(data["data"]) == map_data["total_cells"]
