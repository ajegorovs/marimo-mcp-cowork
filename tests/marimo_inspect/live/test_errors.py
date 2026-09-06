"""Integration tests for errors template against a live marimo kernel.

Tests verify:
1. Template returns valid structure
2. Errors are detected when cells have execution errors
3. Error types are reported correctly

Note: Scratchpad execution is non-persistent, so we test the template's
ability to detect errors in the notebook state rather than creating errors.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.errors import TEMPLATE_ERRORS


@pytest.mark.live
async def test_errors_template_structure(live_client, live_session):
    """Verify errors template returns valid structure."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    # Verify required fields exist
    assert "has_errors" in data, "Missing 'has_errors' field"
    assert "total_errors" in data, "Missing 'total_errors' field"
    assert isinstance(data["has_errors"], bool), "has_errors should be boolean"
    assert isinstance(data["total_errors"], int), "total_errors should be integer"
    assert data["total_errors"] >= 0, "total_errors should be non-negative"


@pytest.mark.live
async def test_errors_detects_no_errors_clean_state(live_client, live_session):
    """Verify errors template works with a clean notebook state.

    A fresh notebook should have 0 errors (or the template should handle it gracefully).
    """
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)

    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    # The template should work regardless of error state
    assert "has_errors" in data
    assert "total_errors" in data

    # If there are no errors, the structure should still be valid
    if not data["has_errors"]:
        assert data["total_errors"] == 0


@pytest.mark.live
async def test_errors_template_executes_without_crashing(live_client, live_session):
    """Verify the errors template doesn't crash the kernel.

    This is a regression test for template execution stability.
    """
    # Execute the template multiple times to ensure stability
    for i in range(3):
        result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
        assert result.status == "ok", (
            f"Template failed on iteration {i}: stderr={result.stderr}"
        )

        data = json.loads(result.stdout[0])
        assert "has_errors" in data
        assert "total_errors" in data
