"""Live integration tests for the dependency template.

Note on scope: the dependency graph reflects *executed* cell state. Against a
headless session created by the /sse handshake, cells are not instantiated
(instantiation requires the token-gated /api/kernel/instantiate endpoint out
of scope for the suite), so `ctx.graph` reports no executed cells. These
tests therefore assert the contract that is genuinely true here: the template
runs against a live kernel, returns the documented structure, and does not
raise. Deeper graph-content assertions require an instantiated session and
belong to a future instantiate-enabled phase.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.dependency import TEMPLATE_DEPENDENCY_GRAPH


@pytest.mark.live
async def test_dependency_executes_against_live_kernel(live_client, live_session):
    """The dependency template runs successfully on a live kernel."""
    result = await live_client.execute(
        live_session.session_id, TEMPLATE_DEPENDENCY_GRAPH
    )
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    for key in ("cells", "variable_owners", "multiply_defined", "cycles"):
        assert key in data, f"Missing '{key}' in dependency graph response"


@pytest.mark.live
async def test_dependency_structure_types(live_client, live_session):
    """The graph response fields have the documented types."""
    result = await live_client.execute(
        live_session.session_id, TEMPLATE_DEPENDENCY_GRAPH
    )
    data = json.loads(result.stdout[0])

    assert isinstance(data["cells"], list)
    assert isinstance(data["variable_owners"], dict)
    assert isinstance(data["multiply_defined"], list)
    assert isinstance(data["cycles"], list)
