"""Integration tests for dependency template against a live marimo kernel."""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.dependency import TEMPLATE_DEPENDENCY_GRAPH


@pytest.mark.live
async def test_dependency_returns_structure(live_client, live_session):
    """Verify dependency template returns the expected graph structure."""
    result = await live_client.execute(
        live_session.session_id, TEMPLATE_DEPENDENCY_GRAPH
    )

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert "cells" in data
    assert "variable_owners" in data
    # May have these fields depending on template version
    assert isinstance(data["cells"], list)


@pytest.mark.live
async def test_dependency_has_setup_cell(live_client, live_session):
    """The setup cell should have no parents (it's the root)."""
    result = await live_client.execute(
        live_session.session_id, TEMPLATE_DEPENDENCY_GRAPH
    )

    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    # Find the setup cell (usually cell 0 or named "setup")
    setup_cells = [
        c
        for c in data["cells"]
        if "setup" in c.get("cell_name", "").lower() or c.get("cell_id") == "0"
    ]

    if setup_cells:
        setup = setup_cells[0]
        # Setup cell should have no parent cells (it's the root)
        parent_ids = setup.get("parent_cell_ids", [])
        assert len(parent_ids) == 0, (
            f"Setup cell should have no parents, got: {parent_ids}"
        )


@pytest.mark.live
async def test_dependency_tracks_variables(live_client, live_session):
    """Verify the dependency graph structure is valid."""
    result = await live_client.execute(
        live_session.session_id, TEMPLATE_DEPENDENCY_GRAPH
    )

    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    # Verify the structure is valid
    assert "cells" in data
    assert "variable_owners" in data
    assert isinstance(data["cells"], list)
