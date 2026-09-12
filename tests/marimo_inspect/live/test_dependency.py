"""Live integration tests for the dependency template.

Note on scope: the shared fixture session is created by the /sse handshake and
is never instantiated (instantiation requires the token-gated
/api/kernel/instantiate endpoint out of scope for the suite), so against it
`ctx.graph` reports no executed cells. Two tiers therefore exist here:

- Structure tests against the shared session: the template runs against a live
  kernel, returns the documented structure, and does not raise.
- Cell-completeness tests against the per-test `mutation_server`: cells created
  through the write tools execute, so `get_dependency_graph` can be compared,
  through the real handlers, with `get_cell_map` over real live cell state
  (the T-V3 regression).
"""

from __future__ import annotations

import json
import sys

import pytest

from marimo_inspection.templates.dependency import TEMPLATE_DEPENDENCY_GRAPH
from marimo_inspection.tools.cells import get_cell_map
from marimo_inspection.tools.dependency import get_dependency_graph
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell


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


@pytest.mark.live
async def test_dependency_graph_covers_every_live_cell(mutation_server):
    """T-V3: one dependency entry per live notebook cell, with real edges.

    Pre-fix the template iterated ``ctx.graph.cells`` only. A live notebook cell
    the kernel's dependency graph does not know about still exists — on marimo
    0.24.x that is every cell of the never-instantiated fixture notebook,
    including its import-only cell (``import marimo as mo`` …) — so
    ``get_dependency_graph`` returned fewer ids than ``get_cell_map``. The set
    equality below fails against the pre-fix template with the missing ids
    named; the import-only fixture cell has no entry at all there.

    The created pair also exercises a REAL edge rather than an isolated cell:
    the dependent cell references the name the import cell defines, so the
    import cell's ``child_cell_ids`` must contain the dependent id and the
    dependent cell's ``parent_cell_ids`` must contain the import id.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        # Import cell: defines `np`, references nothing.
        importer = await create_cell(
            "import numpy as np", session_id=session_id, server_url=server_url
        )
        assert importer["status"] == "ok", importer
        created.append(importer["cell_id"])
        ran = await run_cell(
            importer["cell_id"], session_id=session_id, server_url=server_url
        )
        assert ran["status"] == "ok", ran

        # Dependent cell, created after the import cell: references `np`.
        dependent = await create_cell(
            "dep = np.array([1, 2, 3]).sum()",
            session_id=session_id,
            server_url=server_url,
        )
        assert dependent["status"] == "ok", dependent
        created.append(dependent["cell_id"])
        ran = await run_cell(
            dependent["cell_id"], session_id=session_id, server_url=server_url
        )
        assert ran["status"] == "ok", ran

        cell_map = await get_cell_map(session_id=session_id, server_url=server_url)
        graph = await get_dependency_graph(
            session_id=session_id, server_url=server_url
        )

        map_ids = [c["cell_id"] for c in cell_map["cells"]]
        cells = graph["cells"]
        graph_ids = [c["cell_id"] for c in cells]
        assert len(graph_ids) == len(set(graph_ids)), graph_ids
        assert set(graph_ids) == set(map_ids), (
            "get_dependency_graph omitted live cells: "
            f"missing={sorted(set(map_ids) - set(graph_ids))} "
            f"(cell_map={len(map_ids)} ids, graph={len(graph_ids)} ids)"
        )

        by_id = {c["cell_id"]: c for c in cells}

        # The real edge, reported from both sides. The pre-fix template had no
        # dependent cell here to exercise at all.
        importer_row = by_id[importer["cell_id"]]
        dependent_row = by_id[dependent["cell_id"]]
        assert [d["name"] for d in importer_row["defs"]] == ["np"], importer_row
        assert importer_row["refs"] == [], importer_row
        assert importer_row["parent_cell_ids"] == [], importer_row
        assert dependent["cell_id"] in importer_row["child_cell_ids"], importer_row
        assert dependent_row["refs"] == ["np"], dependent_row
        assert importer["cell_id"] in dependent_row["parent_cell_ids"], dependent_row
        assert dependent_row["child_cell_ids"] == [], dependent_row

        # cell_name agrees with get_cell_map's `name` for EVERY cell — a cell
        # the graph omits must not come back with a blank or wrong name.
        names = {c["cell_id"]: c["name"] for c in cell_map["cells"]}
        for cell in cells:
            assert cell["cell_name"] == names[cell["cell_id"]], cell

        # The exact class T-V3 found: the fixture's import-only cell is absent
        # from `ctx.graph.cells` on marimo 0.24.x (never instantiated), so
        # pre-fix it had no entry at all. It is now present as a valid cell with
        # the complete shape and empty graph-derived lists (no invented edges).
        # The emptiness pins the marimo-side property observed on 0.24.x, in the
        # spirit of the H10 invariants: if a marimo bump starts registering this
        # cell, the assertion reports it.
        fixture_rows = [
            c
            for c in cell_map["cells"]
            if "import marimo as mo" in (c.get("preview") or "")
        ]
        assert len(fixture_rows) == 1, (
            "fixture cell lookup failed: expected exactly one import-only "
            f"fixture cell in cell_map, got "
            f"{[c['cell_id'] for c in fixture_rows]}"
        )
        fixture_row = fixture_rows[0]
        placeholder = by_id[fixture_row["cell_id"]]
        assert placeholder["cell_name"] == fixture_row["name"], placeholder
        assert placeholder["defs"] == [], placeholder
        assert placeholder["refs"] == [], placeholder
        assert placeholder["parent_cell_ids"] == [], placeholder
        assert placeholder["child_cell_ids"] == [], placeholder

        # The rest of the payload is untouched by the reconciliation.
        assert isinstance(graph["variable_owners"], dict)
        assert isinstance(graph["multiply_defined"], list)
        assert isinstance(graph["cycles"], list)
        assert graph["variable_owners"].get("np") == [importer["cell_id"]], graph[
            "variable_owners"
        ]
        assert graph["variable_owners"].get("dep") == [dependent["cell_id"]], graph[
            "variable_owners"
        ]
    finally:
        # Cleanup failures must be visible, but must never replace the primary
        # failure already propagating out of the body (only one exception can
        # leave a `finally`).
        cleanup_failures = []
        for cid in created:
            deleted = await delete_cell(
                cid, session_id=session_id, server_url=server_url
            )
            if deleted.get("status") != "ok":
                cleanup_failures.append((cid, deleted))
        if cleanup_failures:
            if sys.exc_info()[0] is None:
                pytest.fail(f"delete_cell cleanup failed: {cleanup_failures}")
            print(f"[cleanup] delete_cell failed: {cleanup_failures}")
