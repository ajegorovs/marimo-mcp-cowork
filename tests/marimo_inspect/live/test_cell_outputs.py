"""Integration tests for cell_outputs template against a live marimo kernel."""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_outputs import TEMPLATE_CELL_OUTPUTS


@pytest.mark.live
async def test_cell_outputs_returns_structure(live_client, live_session):
    """Verify cell_outputs template returns valid structure."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    # The response should contain output information
    assert "cells" in data or "data" in data


@pytest.mark.live
async def test_cell_outputs_structure(live_client, live_session):
    """The cell_outputs response should always have the expected structure."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_OUTPUTS)
    data = json.loads(result.stdout[0])

    # Verify the response structure
    assert "cells" in data
    assert isinstance(data["cells"], list)
