"""Live integration tests for the errors template.

Note on scope: a headless session created via /sse is not instantiated, so no
cell has run and no execution error has been recorded yet
(instantiation requires the token-gated /api/kernel/instantiate endpoint).
The verifiable live contract is that the template runs against a real kernel
and reports a well-formed, consistent error summary (0 errors in a fresh,
non-instantiated session). Instantiation-dependent error detection belongs to
a future instantiate-enabled phase.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.errors import TEMPLATE_ERRORS


@pytest.mark.live
async def test_errors_template_structure(live_client, live_session):
    """The errors template returns the documented fields."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert isinstance(data["has_errors"], bool)
    assert isinstance(data["total_errors"], int)
    # Backward-compatible totals are the STRUCTURED-only counts.
    assert isinstance(data["total_structured_errors"], int)
    assert data["total_structured_errors"] == data["total_errors"]
    assert data["total_errors"] >= 0
    assert isinstance(data["total_cells_with_errors"], int)
    assert isinstance(data["has_console_exception"], bool)
    assert isinstance(data["total_console_exception_cells"], int)
    assert isinstance(data["cells"], list)
    assert data["has_errors"] == (data["total_errors"] > 0)
    for cell in data["cells"]:
        assert "structured_errors" in cell
        assert "console_stderr" in cell
        assert "has_console_exception" in cell


@pytest.mark.live
async def test_errors_consistent_fresh_session(live_client, live_session):
    """A fresh, non-instantiated session reports a self-consistent summary."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    # Structured and console-exception cells are both subsets of `cells`
    # (a single cell may carry both channels).
    assert data["total_cells_with_errors"] <= len(data["cells"])
    assert data["total_console_exception_cells"] <= len(data["cells"])
    if data["has_errors"]:
        assert data["total_cells_with_errors"] > 0
    if data["has_console_exception"]:
        assert data["total_console_exception_cells"] > 0


@pytest.mark.live
async def test_errors_template_stable_across_runs(live_client, live_session):
    """Repeated executions are stable and do not crash the kernel."""
    for _ in range(3):
        result = await live_client.execute(live_session.session_id, TEMPLATE_ERRORS)
        assert result.status == "ok", f"Template failed: stderr={result.stderr}"
        data = json.loads(result.stdout[0])
        assert "has_errors" in data
        assert "total_errors" in data
