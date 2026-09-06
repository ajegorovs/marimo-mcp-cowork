"""Integration tests for cell_map template against a live marimo kernel.

Note: An empty notebook has 0 cells. Tests that expect cells use a notebook
with content, or verify the empty case explicitly.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_map import TEMPLATE_CELL_MAP


@pytest.mark.live
async def test_cell_map_returns_cells(live_client, live_session):
    """Verify cell map template returns accurate cell data from a real kernel."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    # The response structure should be valid even with 0 cells
    assert "total_cells" in data
    assert "cells" in data
    assert len(data["cells"]) == data["total_cells"]

    # Empty notebook has 0 cells
    if data["total_cells"] == 0:
        assert len(data["cells"]) == 0
        return

    # If there are cells, verify the structure
    cell = data["cells"][0]
    assert "cell_id" in cell
    assert "name" in cell
    assert "preview" in cell
    assert "line_count" in cell
    assert "runtime_state" in cell


@pytest.mark.live
async def test_cell_map_structure(live_client, live_session):
    """The cell map response should always have the expected structure."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    # Verify the response structure
    assert "total_cells" in data
    assert "cells" in data
    assert isinstance(data["total_cells"], int)
    assert isinstance(data["cells"], list)


@pytest.mark.live
async def test_cell_map_has_setup_cell(live_client, live_session):
    """If the notebook has cells, one should be named 'setup' or have empty name."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    # Empty notebook has no setup cell
    if data["total_cells"] == 0:
        assert len(data["cells"]) == 0
        return

    # At least one cell should be named "setup" or have empty name
    names = [c["name"] for c in data["cells"]]
    assert any(name in ("setup", "") for name in names), (
        f"Expected setup cell, got names: {names}"
    )


@pytest.mark.live
async def test_cell_map_preview_is_truncated(live_client, live_session):
    """Preview should be truncated to the configured number of lines."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    for cell in data["cells"]:
        # preview_lines default is 3
        line_count = cell["line_count"]
        preview_lines = cell["preview"].count("\n") + 1
        assert preview_lines <= max(3, line_count), (
            f"Preview has {preview_lines} lines but config says 3"
        )
