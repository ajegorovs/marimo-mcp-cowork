"""Hermetic live regressions for `run_cell`'s execution modes (T15).

These tests drive the REAL MCP handler (`marimo_inspection.tools.mutation.run_cell`)
against a real marimo 0.24 kernel on a **purpose-built** notebook written into
``tmp_path`` (the ``notebook_server`` factory in ``live/conftest.py``). That
matters here: the repo fixture carries a deliberate ``ValueError`` cell, so a
whole-notebook run on it can never be all-idle and could not prove the happy
path. The purpose-built documents hold only valid cells, so ``mode="all"``
either executes every document cell or the test fails.

What is pinned (the T15 contract):

1. **``mode="all"`` is the run-all fix.** On a fresh, never-instantiated `/sse`
   session every document cell is queued and executed, and an unreferenced
   widget leaf — unreachable via ``set_ui_value`` before, because nothing ever
   ran it — becomes registered and drivable afterwards.
2. **``mode="descendants"`` never silently degrades.** On a fresh session the
   kernel graph is empty, so an unregistered target is refused with
   ``reason: graph_unpopulated`` and *nothing runs*; once ``mode="all"`` has
   populated the graph, the same call queues the target plus its graph
   descendants.
3. **Unknown ids and names, and ``mode="all"`` + a non-empty ``cell_id`` abort
   before any execution.** marimo raises at queue time and discards the whole
   batch, so validation must precede the run.
4. **A mixed runtime failure is reported per cell, truthfully.** marimo
   discards the run payload when any target raises and the in-context snapshot
   is frozen, so the tool takes a separate post-run report: an ``exception``
   cell and a ``cancelled`` dependent are both reported as failures while the
   healthy cells are reported as succeeded.
5. **A cell NAME resolves like a cell id** (the pre-modes ``run_cell``
   forwarded its target to ``ctx.run_cell``, which accepts either), for
   ``cell`` and for ``descendants`` — the payload echoes the name and reports
   the resolved id, and the run queues that id.

Isolation: one isolated server per test on a disposable notebook; teardown
re-checks that ``notebooks/test_marimo.py`` is byte-identical to what it was at
boot, so no repo fixture is touched.
"""

from __future__ import annotations

import pytest

from marimo_inspection.tools.cells import get_cell_data, get_cell_map
from marimo_inspection.tools.mutation import create_cell, run_cell
from marimo_inspection.tools.ui import set_ui_value

# ── purpose-built notebooks ─────────────────────────────────────────────────
#
# A: root (defines `seed`)
# B: descendant of A (edge A -> B)
# C: UNREFERENCED widget leaf — nothing depends on it, so only a direct run (or
#    a whole-notebook run) ever registers `mode_slider`.

MODES_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    seed = 1
    return (seed,)


@app.cell
def _(seed):
    doubled = seed * 2
    return (doubled,)


@app.cell
def _():
    import marimo as mo

    mode_slider = mo.ui.slider(0, 10, value=3)
    mode_slider
    return (mode_slider,)
"""

# A: root, B: descendant, RAISER: runtime NameError, DEPENDENT: reads RAISER's
# name so the kernel cancels it instead of running it.
FAILING_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    seed = 1
    return (seed,)


@app.cell
def _(seed):
    doubled = seed * 2
    return (doubled,)


@app.cell
def _(seed):
    boom = seed + missing_name_on_purpose
    return (boom,)


@app.cell
def _(boom):
    after = boom + 1
    return (after,)
"""

_ROLES = {
    "root": "seed = 1",
    "descendant": "doubled = seed * 2",
    "widget": "mode_slider = mo.ui.slider",
    "raiser": "missing_name_on_purpose",
    "dependent": "after = boom + 1",
}


async def _roles(server_url: str, session_id: str) -> tuple[dict[str, str], dict]:
    """Map role -> cell id (and return the full cell map) for a live session.

    Identification uses the FULL source (`get_cell_data`), not the 3-line
    `get_cell_map` preview, so it cannot depend on preview truncation.
    """
    data = await get_cell_data(session_id=session_id, server_url=server_url)
    roles: dict[str, str] = {}
    for row in data["data"]:
        for role, marker in _ROLES.items():
            if marker in (row.get("code") or ""):
                roles[role] = row["cell_id"]
    cell_map = await get_cell_map(session_id=session_id, server_url=server_url)
    return roles, cell_map


def _row(run_result: dict, cell_id: str) -> dict:
    for row in run_result["cells"]:
        if row["cell_id"] == cell_id:
            return row
    raise AssertionError(f"cell {cell_id} missing from run payload: {run_result}")


@pytest.mark.live
async def test_all_mode_runs_every_document_cell_and_registers_the_widget(
    notebook_server,
):
    """T15: `mode="all"` on a fresh session executes every cell and registers
    the unreferenced widget leaf (which `set_ui_value` cannot reach before).

    The happy path needs a document with no failing cell, hence the
    purpose-built notebook rather than the repo fixture (which carries a
    deliberate ``ValueError``): all requested targets must come back ``idle``.
    """
    _manager, server_url, session_id, _notebook = await notebook_server(MODES_NOTEBOOK)
    roles, fresh = await _roles(server_url, session_id)
    assert set(roles) == {"root", "descendant", "widget"}, roles
    assert all(c["runtime_state"] == "stale" for c in fresh["cells"]), fresh

    # Before: the leaf's widget is not a kernel global — the consumer's T15
    # symptom, reproduced.
    before = await set_ui_value(
        "mode_slider", 7, session_id=session_id, server_url=server_url
    )
    assert before["status"] == "error", before
    assert before["reason"] == "unknown_variable", before

    result = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert result["status"] == "ok", result
    assert result["mode"] == "all", result
    assert set(result["requested_cell_ids"]) == set(roles.values()), result
    assert result["failed_cell_ids"] == [], result
    assert result["not_run_cell_ids"] == [], result
    assert set(result["succeeded_cell_ids"]) == set(roles.values()), result
    assert result["counts"] == {
        "requested": len(roles),
        "succeeded": len(roles),
        "failed": 0,
        "not_run": 0,
    }, result
    for cell in result["cells"]:
        assert cell["runtime_state"] == "idle", cell
        assert cell["output_stale"] is False, cell
        assert cell["errors"] == [], cell

    after_map = await get_cell_map(session_id=session_id, server_url=server_url)
    assert all(c["runtime_state"] == "idle" for c in after_map["cells"]), after_map

    # After: the once-unreferenced widget now exists in the kernel. `mode="all"`
    # registered it by executing its cell — no widget-specific code involved.
    after = await set_ui_value(
        "mode_slider", 7, session_id=session_id, server_url=server_url
    )
    assert after["status"] == "ok", after
    assert after["verified"] is True, after
    assert after["applied"] is True, after
    assert after["value_after"] == 7, after


@pytest.mark.live
async def test_descendants_mode_refuses_unregistered_then_works_once_populated(
    notebook_server,
):
    """T15: `mode="descendants"` refuses `graph_unpopulated` on a fresh session
    (nothing runs) and resolves the target + its descendants once registered.
    """
    _manager, server_url, session_id, _notebook = await notebook_server(MODES_NOTEBOOK)
    roles, _fresh = await _roles(server_url, session_id)

    # A fresh `/sse` session has an EMPTY kernel graph: the root is a document
    # cell but not a registered one, so descendants cannot be computed.
    refused = await run_cell(
        roles["root"], mode="descendants", session_id=session_id, server_url=server_url
    )
    assert refused["status"] == "error", refused
    assert refused["reason"] == "graph_unpopulated", refused
    # It must not silently degrade to a single-cell run.
    assert refused["requested_cell_ids"] == [roles["root"]], refused
    assert "mode='all'" in refused["message"], refused
    assert any("mode='all'" in step for step in refused.get("next_steps", [])), refused

    # The unreferenced leaf is equally unregistered — same refusal.
    leaf_refused = await run_cell(
        roles["widget"],
        mode="descendants",
        session_id=session_id,
        server_url=server_url,
    )
    assert leaf_refused["reason"] == "graph_unpopulated", leaf_refused

    # Nothing ran: every cell is still stale.
    still_fresh = await get_cell_map(session_id=session_id, server_url=server_url)
    assert all(c["runtime_state"] == "stale" for c in still_fresh["cells"]), still_fresh

    # Populate the graph the documented way.
    populated = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert populated["status"] == "ok", populated

    # Now the mode resolves: target + its graph descendants only. The widget
    # leaf is NOT a descendant of the root and must not be queued.
    resolved = await run_cell(
        roles["root"], mode="descendants", session_id=session_id, server_url=server_url
    )
    assert resolved["status"] == "ok", resolved
    assert resolved["mode"] == "descendants", resolved
    assert set(resolved["requested_cell_ids"]) == {
        roles["root"],
        roles["descendant"],
    }, resolved
    assert set(resolved["succeeded_cell_ids"]) == {
        roles["root"],
        roles["descendant"],
    }, resolved
    assert roles["widget"] not in resolved["requested_cell_ids"], resolved


@pytest.mark.live
async def test_mixed_runtime_failure_reports_truthful_partial_per_cell(
    notebook_server,
):
    """T15: a batch containing an `exception` cell and a `cancelled` dependent
    reports per-cell outcomes even though the run call itself returned an error.

    marimo discards the run payload when a target raises and freezes the
    in-context snapshot, so the tool's separate post-run report is what makes
    the partial verdict truthful. A single batch-level ok/error, or the kernel's
    own ``re-ran cell …`` lines, would misreport both.
    """
    _manager, server_url, session_id, _notebook = await notebook_server(
        FAILING_NOTEBOOK
    )
    roles, fresh = await _roles(server_url, session_id)
    assert set(roles) == {"root", "descendant", "raiser", "dependent"}, roles
    assert all(c["runtime_state"] == "stale" for c in fresh["cells"]), fresh

    result = await run_cell(mode="all", session_id=session_id, server_url=server_url)

    # The run call failed as a whole...
    assert result["status"] == "partial", result
    assert result["execution_error"], result
    assert "missing_name_on_purpose" in result["stderr"], result

    # ...but each requested cell is reported by its own terminal state.
    assert set(result["requested_cell_ids"]) == set(roles.values()), result
    assert set(result["succeeded_cell_ids"]) == {
        roles["root"],
        roles["descendant"],
    }, result
    raiser_row = _row(result, roles["raiser"])
    assert raiser_row["runtime_state"] == "exception", raiser_row
    assert [e["kind"] for e in raiser_row["errors"]] == ["runtime"], raiser_row
    assert "missing_name_on_purpose" in raiser_row["errors"][0]["message"], raiser_row

    dependent_row = _row(result, roles["dependent"])
    # The dependent never executed: the kernel cancels a cell whose ancestor
    # raised. It must never be reported as succeeded, and it stays in the
    # failed set rather than being waved through as "not run".
    assert dependent_row["runtime_state"] != "idle", dependent_row
    assert dependent_row["runtime_state"] in {
        "cancelled",
        "interrupted",
        "exception",
    }, dependent_row
    assert set(result["failed_cell_ids"]) == {
        roles["raiser"],
        roles["dependent"],
    }, result
    assert result["not_run_cell_ids"] == [], result
    assert result["counts"] == {
        "requested": 4,
        "succeeded": 2,
        "failed": 2,
        "not_run": 0,
    }, result


@pytest.mark.live
async def test_invalid_modes_abort_before_running_and_cell_mode_stays_single(
    notebook_server,
):
    """T15: validation failures run nothing; the default stays a single cell.

    One unknown id aborts the whole batch at queue time in marimo (raising
    before anything executes), so the tool validates the plan first. ``all``
    with a non-empty ``cell_id`` is a caller error, not something to ignore.
    """
    _manager, server_url, session_id, _notebook = await notebook_server(MODES_NOTEBOOK)
    roles, _fresh = await _roles(server_url, session_id)

    unknown = await run_cell(
        "no_such_cell_on_purpose", session_id=session_id, server_url=server_url
    )
    assert unknown["status"] == "error", unknown
    assert unknown["reason"] == "unknown_cell_ids", unknown
    assert unknown["unknown_cell_ids"] == ["no_such_cell_on_purpose"], unknown

    mixed = await run_cell(
        roles["root"], mode="all", session_id=session_id, server_url=server_url
    )
    assert mixed["status"] == "error", mixed
    assert mixed["reason"] == "cell_id_not_allowed", mixed

    # Neither refusal executed anything.
    untouched = await get_cell_map(session_id=session_id, server_url=server_url)
    assert all(c["runtime_state"] == "stale" for c in untouched["cells"]), untouched

    # The default mode keeps the documented single-cell behavior: the requested
    # cell runs, and the independent unreferenced leaf does not.
    single = await run_cell(roles["root"], session_id=session_id, server_url=server_url)
    assert single["status"] == "ok", single
    assert single["mode"] == "cell", single
    assert single["cell_id"] == roles["root"], single
    assert single["requested_cell_ids"] == [roles["root"]], single
    assert single["succeeded_cell_ids"] == [roles["root"]], single
    assert single["not_run_cell_ids"] == [], single
    after_map = await get_cell_map(session_id=session_id, server_url=server_url)
    states = {c["cell_id"]: c["runtime_state"] for c in after_map["cells"]}
    assert states[roles["root"]] == "idle", states
    assert states[roles["widget"]] == "stale", states


@pytest.mark.live
async def test_cell_names_resolve_like_ids_for_cell_and_descendants(notebook_server):
    """T15 compat: a cell NAME resolves like a cell id (``ctx.cells``).

    The pre-modes ``run_cell`` forwarded its target straight to
    ``ctx.run_cell``, which accepts an id OR a name. A caller that passed a
    name must keep working after the modes landed — for ``cell`` and for
    ``descendants`` — while an unknown name is still refused *before* anything
    runs. The payload echoes the input and reports the resolved id, and the run
    queues that id (queuing the name would raise at queue time).
    """
    _manager, server_url, session_id, _notebook = await notebook_server(MODES_NOTEBOOK)
    _roles_map, fresh = await _roles(server_url, session_id)
    assert all(c["runtime_state"] == "stale" for c in fresh["cells"]), fresh

    root = await create_cell(
        "named_seed = 7",
        name="named_root",
        session_id=session_id,
        server_url=server_url,
    )
    root_id = root["cell_id"]
    dep = await create_cell(
        "named_doubled = named_seed * 2",
        session_id=session_id,
        server_url=server_url,
    )
    dep_id = dep["cell_id"]
    # The name is genuinely distinct from the live id.
    assert root_id != "named_root" and dep_id != root_id

    # An unknown NAME refuses before anything runs, exactly like an unknown id.
    unknown = await run_cell(
        "no_such_name_on_purpose", session_id=session_id, server_url=server_url
    )
    assert unknown["status"] == "error", unknown
    assert unknown["reason"] == "unknown_cell_ids", unknown
    assert unknown["unknown_cell_ids"] == ["no_such_name_on_purpose"], unknown
    assert unknown["cell_id"] == "no_such_name_on_purpose", unknown
    assert unknown["resolved_cell_id"] is None, unknown
    assert unknown["error"], unknown

    untouched = await get_cell_map(session_id=session_id, server_url=server_url)
    assert all(c["runtime_state"] == "stale" for c in untouched["cells"]), untouched

    # Default mode by NAME: resolves to the live id, runs it, reports both.
    single = await run_cell("named_root", session_id=session_id, server_url=server_url)
    assert single["status"] == "ok", single
    assert single["mode"] == "cell", single
    assert single["cell_id"] == "named_root", single
    assert single["resolved_cell_id"] == root_id, single
    assert single["requested_cell_ids"] == [root_id], single
    assert single["succeeded_cell_ids"] == [root_id], single

    # `descendants` by NAME: the resolved target plus its graph descendant.
    resolved = await run_cell(
        "named_root", mode="descendants", session_id=session_id, server_url=server_url
    )
    assert resolved["status"] == "ok", resolved
    assert resolved["resolved_cell_id"] == root_id, resolved
    assert set(resolved["requested_cell_ids"]) == {root_id, dep_id}, resolved
    assert set(resolved["succeeded_cell_ids"]) == {root_id, dep_id}, resolved
    assert resolved["failed_cell_ids"] == [], resolved

    # The name-based runs really executed the cells they resolved to.
    after_map = await get_cell_map(session_id=session_id, server_url=server_url)
    states = {c["cell_id"]: c["runtime_state"] for c in after_map["cells"]}
    assert states[root_id] == "idle", states
    assert states[dep_id] == "idle", states
