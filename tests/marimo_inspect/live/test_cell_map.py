"""Live integration tests for the cell_map template.

Behavioral assertions against the deterministic fixture notebook
(notebooks/test_marimo.py): the map must report the notebook's real cells,
including the hidden setup cell and the intentionally-failing cell.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_map import TEMPLATE_CELL_MAP

# The fixture notebook has exactly 6 cells (1 hidden setup + 5 visible).
EXPECTED_TOTAL_CELLS = 6


@pytest.mark.live
async def test_cell_map_reports_all_notebook_cells(live_client, live_session):
    """The live notebook's real cell count is reported."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert data["total_cells"] == EXPECTED_TOTAL_CELLS
    assert len(data["cells"]) == EXPECTED_TOTAL_CELLS
    assert isinstance(data["total_cells"], int)


@pytest.mark.live
async def test_cell_map_contains_hidden_setup_cell(live_client, live_session):
    """The hidden setup cell is reported, with its helper code as preview."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    setup_cells = [c for c in data["cells"] if c["name"] == "setup"]
    assert setup_cells, "Expected a setup cell in the cell map"
    setup = setup_cells[0]
    # The setup cell defines _double; its preview should show that.
    assert "def _double(x)" in setup["preview"]
    assert setup["line_count"] > 0


@pytest.mark.live
async def test_cell_map_includes_error_cell(live_client, live_session):
    """The intentionally-failing cell is present in the map."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    # Match by preview: the error cell contains the raise statement.
    error_cells = [c for c in data["cells"] if "integration_test_error" in c["preview"]]
    assert error_cells, "Expected the intentional error cell in the map"


@pytest.mark.live
async def test_cell_map_preview_is_truncated(live_client, live_session):
    """Preview is truncated to the configured default (3 lines)."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    data = json.loads(result.stdout[0])

    for cell in data["cells"]:
        preview_line_count = cell["preview"].count("\n") + 1
        assert preview_line_count <= 3, (
            f"Preview has {preview_line_count} lines but max is 3"
        )


@pytest.mark.live
async def test_cell_map_truthful_flag_schema(live_client, live_session):
    """Every cell reports the truthfulness flags (bool or None, never fake)."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert data["cells"], "Expected at least one cell in the map"
    for cell in data["cells"]:
        for key in ("has_output", "has_console_output", "has_errors"):
            assert key in cell, f"cell {cell['cell_id']} missing {key}"
            assert cell[key] is None or isinstance(cell[key], bool), (
                f"cell {cell['cell_id']} {key}={cell[key]!r} must be bool or None"
            )
