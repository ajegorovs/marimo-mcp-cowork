"""Hermetic live regressions for the mutation tools (create/edit/run/delete).

These tests exercise the REAL MCP handler functions
(``marimo_inspection.tools.*``) — not mocks — against a real marimo 0.24
kernel booted by the harness. They run in the same process as the change
tracker singleton, so the staleness guard behaves exactly as it does inside
the MCP server.

Isolation: each test boots its OWN `MarimoServerManager` on a tmp_path copy
of ``notebooks/test_marimo.py`` (see the ``mutation_server`` fixture). The
shared session-suite server is never touched here, and marimo's disk
serialization (if any) lands on the disposable copy.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.tools.cells import get_cell_data, get_cell_map, get_cell_outputs
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.mutation import (
    create_cell,
    delete_cell,
    edit_cell,
    run_cell,
)

# Non-empty, never-executed marker for the regression-A cell; it must never
# appear in the repo fixture or survive on disk after deletion.
_A_MARKER = "t3_regression_a"

_A_INITIAL_SOURCE = f"{_A_MARKER}_value = 1"
_A_EDITED_SOURCE = f"{_A_MARKER}_value = 2"


def _raw_external_edit(cell_id: str, code: str) -> str:
    """Test-only snippet: edit a cell behind the agent's back.

    Deliberately NOT built by ``templates/mutation.py`` — the whole point is
    an external actor (a second co-worker) mutating the notebook out-of-band,
    which must trip the staleness guard on the next guarded `edit_cell`.
    """
    return (
        "import json\n"
        "import marimo._code_mode as cm\n"
        "\n"
        "async def _run():\n"
        "    async with cm.get_context() as ctx:\n"
        f"        ctx.edit_cell({json.dumps(cell_id)}, {json.dumps(code)})\n"
        '        return json.dumps({"status": "ok", "cell_id": '
        f"{json.dumps(cell_id)}}})\n"
        "\n"
        "print(await _run())"
    )


def _rows_by_id(data: dict) -> dict[str, dict]:
    return {row["cell_id"]: row for row in data["data"]}


async def _execute_raw(server_url: str, session_id: str, code: str):
    """Run a raw scratchpad snippet against the live kernel.

    Used to simulate a *second* actor (another co-worker / an out-of-band
    editor) mutating the notebook without going through this package's tools,
    which is exactly the situation the staleness guard exists for.
    """
    from marimo_inspection.client import MarimoClient

    client = MarimoClient(server_url)
    try:
        return await client.execute(session_id, code)
    finally:
        await client.close()


@pytest.mark.live
async def test_create_read_guarded_edit_run_verify_delete(mutation_server):
    """Ordinary create→read→guarded-edit→run→verify→delete round trip.

    Uses the real handlers with default staleness semantics: the read
    establishes the baseline, the guarded edit proceeds (fresh), the run
    actually executes the cell (runtime state stale→idle), and the delete
    removes it from the live session and from anything marimo serialized.
    """
    _manager, server_url, session_id, notebook_copy = mutation_server

    cell_id: str | None = None
    delete_status: str | None = None
    try:
        created = await create_cell(
            _A_INITIAL_SOURCE, session_id=session_id, server_url=server_url
        )
        assert created["status"] == "ok", created
        cell_id = created["cell_id"]
        assert cell_id, created

        # Read the new cell: records the baseline for the staleness guard.
        read = await get_cell_data(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        rows = _rows_by_id(read)
        assert cell_id in rows, read
        assert rows[cell_id]["code"] == _A_INITIAL_SOURCE
        # A freshly created, never-run cell is stale — marimo's marker that
        # it has not executed yet.
        assert rows[cell_id]["runtime_state"] == "stale"

        # Guarded edit against the fresh baseline must proceed.
        edited = await edit_cell(
            cell_id,
            _A_EDITED_SOURCE,
            session_id=session_id,
            server_url=server_url,
        )
        assert edited["status"] == "ok", edited
        assert "code_hash" in edited, "post-exit hash must be reported"

        # Run it: the state transition proves the kernel executed the code.
        ran = await run_cell(cell_id, session_id=session_id, server_url=server_url)
        assert ran["status"] == "ok", ran

        rerun = await get_cell_data(
            cell_ids=[cell_id], session_id=session_id, server_url=server_url
        )
        rows = _rows_by_id(rerun)
        assert rows[cell_id]["code"] == _A_EDITED_SOURCE
        assert rows[cell_id]["runtime_state"] == "idle"

        errors = await get_errors(session_id=session_id, server_url=server_url)
        assert errors["has_errors"] is False, errors
        assert errors["total_errors"] == 0, errors
    finally:
        if cell_id is not None:
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            delete_status = deleted.get("status")

    # Post-delete verification: gone from the live session and from disk.
    assert cell_id is not None, "create_cell did not produce a cell id"
    assert delete_status == "ok"
    after_delete = await get_cell_data(
        cell_ids=[cell_id], session_id=session_id, server_url=server_url
    )
    assert cell_id not in _rows_by_id(after_delete)
    assert _A_MARKER not in notebook_copy.read_text(), (
        "deleted cell source left on disk"
    )


@pytest.mark.live
async def test_external_conflict_reread_recover(mutation_server):
    """Conflict → re-read → recover: the guard refuses a stale edit, an
    external out-of-band mutation is never silently stomped, and a fresh
    re-read re-arms the guard so the retried edit applies.

    The external mutation is driven by a raw scratchpad snippet (NOT the
    package templates) — a second actor editing the notebook out-of-band.
    """
    from marimo_inspection.client import MarimoClient

    _manager, server_url, session_id, _notebook_copy = mutation_server
    client = MarimoClient(server_url)

    # Baseline read: pick a disposable fixture cell (the computed-values
    # cell) — re-reading it records the baseline the conflict is judged on.
    all_data = await get_cell_data(session_id=session_id, server_url=server_url)
    target = next(
        row
        for row in all_data["data"]
        if "value_a" in (row.get("code") or "")
        and "value_b" in (row.get("code") or "")
        and "value_c" in row.get("code", "")
        and "value_a + value_b" in row.get("code", "")
    )
    target_id = target["cell_id"]
    original_code = target["code"]

    external_code = "value_a = 999\nvalue_b = 999\nvalue_c = 999"
    final_code = "value_a = 7\nvalue_b = 7\nvalue_c = 7"

    cleanup_status: str | None = None
    try:
        # External actor edits the cell out-of-band.
        raw = await client.execute(
            session_id, _raw_external_edit(target_id, external_code)
        )
        assert raw.status == "ok", f"raw edit failed: stderr={raw.stderr}"

        # Guarded edit must refuse — and refuse WITHOUT mutating anything.
        first = await edit_cell(
            target_id, final_code, session_id=session_id, server_url=server_url
        )
        assert first["status"] == "conflict", first
        assert "modified since" in first["message"], first

        # The external source is still live (nothing was stomped).
        reread = await get_cell_data(
            cell_ids=[target_id], session_id=session_id, server_url=server_url
        )
        rows = _rows_by_id(reread)
        assert rows[target_id]["code"] == external_code

        # Re-read re-arms the baseline; the retried guarded edit applies.
        retry = await edit_cell(
            target_id, final_code, session_id=session_id, server_url=server_url
        )
        assert retry["status"] == "ok", retry
        assert "code_hash" in retry, retry

        verified = await get_cell_data(
            cell_ids=[target_id], session_id=session_id, server_url=server_url
        )
        rows = _rows_by_id(verified)
        assert rows[target_id]["code"] == final_code
    finally:
        # Cleanup: run the cell (the final source is a harmless assignment);
        # the edited values are consistent with the cell's own definitions.
        cleanup = await run_cell(
            target_id, session_id=session_id, server_url=server_url
        )
        cleanup_status = cleanup.get("status")

    assert cleanup_status == "ok"
    assert original_code != final_code  # sanity: the fixture cell did change
    await client.close()


@pytest.mark.live
async def test_unrelated_write_does_not_disarm_the_guard(mutation_server):
    """H7: a write to ANOTHER cell must not bless this cell's baseline.

    Pre-fix, `_refresh_snapshot` committed a whole-session hash map, so the
    unrelated create_cell below forged a last-read baseline for the externally
    edited cell and this edit_cell returned 'ok' — overwriting a concurrent
    edit with no re-read.
    """
    _manager, server_url, session_id, _copy = mutation_server
    created: list[str] = []
    try:
        target = await create_cell(
            _A_INITIAL_SOURCE, session_id=session_id, server_url=server_url
        )
        target_id = target["cell_id"]
        created.append(target_id)

        # Baseline: the agent reads the cell it just created.
        read = await get_cell_data(
            cell_ids=[target_id], session_id=session_id, server_url=server_url
        )
        assert _rows_by_id(read)[target_id]["code"]

        # A second co-worker edits it out of band.
        raw = await _execute_raw(
            server_url, session_id, _raw_external_edit(target_id, _A_EDITED_SOURCE)
        )
        assert raw.status == "ok", f"raw external edit failed: stderr={raw.stderr}"

        # An UNRELATED write in our own process.
        other = await create_cell(
            "h7_unrelated = 1", session_id=session_id, server_url=server_url
        )
        created.append(other["cell_id"])

        # The guard must still refuse: our baseline predates the foreign edit.
        result = await edit_cell(
            target_id,
            f"{_A_EDITED_SOURCE}  # ours",
            session_id=session_id,
            server_url=server_url,
        )
        assert result["status"] == "conflict", result
    finally:
        for cid in reversed(created):
            await delete_cell(cid, session_id=session_id, server_url=server_url)


@pytest.mark.live
async def test_unrelated_write_does_not_bless_a_never_read_cell(mutation_server):
    """H7: a never-read cell must still report needs_read after another write."""
    _manager, server_url, session_id, _notebook_copy = mutation_server
    created: list[str] = []
    try:
        # Discover a fixture cell id WITHOUT reading its source. get_cell_data
        # would record the read baseline this test proves is absent; get_cell_map
        # no longer does (H9), but it is still avoided here so the assertion
        # does not depend on that. get_cell_outputs reads executed outputs only
        # and records nothing.
        outputs = await get_cell_outputs(session_id=session_id, server_url=server_url)
        fixture_cell = outputs["cells"][0]["cell_id"]

        other = await create_cell(
            "h7_unrelated_2 = 1", session_id=session_id, server_url=server_url
        )
        created.append(other["cell_id"])

        result = await edit_cell(
            fixture_cell, "x = 1", session_id=session_id, server_url=server_url
        )
        assert result["status"] == "needs_read", result
    finally:
        for cid in reversed(created):
            await delete_cell(cid, session_id=session_id, server_url=server_url)


@pytest.mark.live
async def test_preview_read_does_not_bless_a_read_baseline(mutation_server):
    """H9: a `get_cell_map` PREVIEW must not arm the `edit_cell` guard.

    Pre-fix, `get_cell_map` called `ChangeTracker.commit` for every cell, and
    that one snapshot doubled as the read baseline — so a 3-line preview forged
    a full-source read and `edit_cell` overwrote a never-read cell with
    `status: ok`. Post-fix only a full-source read (`get_cell_data`) records
    the baseline; `get_cell_map` keeps feeding only `changes_since_last`.

    This fails against the pre-fix code on the `assert refused["status"] ==
    "needs_read"` line (pre-fix returns `ok`, i.e. the edit is applied).
    """
    _manager, server_url, session_id, _copy = mutation_server

    # Discover a fixture cell id WITHOUT reading any source: get_cell_outputs
    # reads executed outputs only and records nothing.
    outputs = await get_cell_outputs(session_id=session_id, server_url=server_url)
    fixture_cell = outputs["cells"][0]["cell_id"]

    # The documented "start here" preview read — pre-fix this blessed every
    # cell in the notebook.
    cell_map = await get_cell_map(session_id=session_id, server_url=server_url)
    assert any(c["cell_id"] == fixture_cell for c in cell_map["cells"]), cell_map

    refused = await edit_cell(
        fixture_cell, "x = 1", session_id=session_id, server_url=server_url
    )
    assert refused["status"] == "needs_read", refused

    # A real full-source read records the baseline; the same cell is then
    # editable with no further re-read.
    read = await get_cell_data(
        cell_ids=[fixture_cell], session_id=session_id, server_url=server_url
    )
    original = _rows_by_id(read)[fixture_cell]["code"]
    applied = await edit_cell(
        fixture_cell,
        original + "\n# h9_read_baseline",
        session_id=session_id,
        server_url=server_url,
    )
    assert applied["status"] == "ok", applied


@pytest.mark.live
async def test_insert_keeps_pre_existing_code_hashes_unchanged(mutation_server):
    """H10: pins the invariant the H7 guard narrowing rests on.

    `_refresh_snapshot` records ONLY the mutated cell because inserting a cell
    leaves every pre-existing cell's `code_hash` unchanged on marimo 0.24.x
    (measured once by a throwaway probe, now pinned here). If a marimo bump
    broke the property, the guard would start reporting false `conflict`s for
    cells nobody touched and no test would say so.

    Invariant demonstration (no pre-fix code to fail against — the property is
    marimo's): with this assertion perturbed to require the *opposite* — a
    pre-existing cell's hash changed by the insert — the test fails on the
    first pre-existing cell with the "insert changed cell ..." message; the
    perturbation was observed and reverted on 2026-09-11.
    """
    _manager, server_url, session_id, _copy = mutation_server
    created: list[str] = []
    try:
        before = await get_cell_map(session_id=session_id, server_url=server_url)
        before_hashes = {c["cell_id"]: c["code_hash"] for c in before["cells"]}
        assert before_hashes, before

        made = await create_cell(
            "h10_inserted = 1", session_id=session_id, server_url=server_url
        )
        assert made["status"] == "ok", made
        created.append(made["cell_id"])

        after = await get_cell_map(session_id=session_id, server_url=server_url)
        after_hashes = {c["cell_id"]: c["code_hash"] for c in after["cells"]}
        # The insert is visible...
        assert made["cell_id"] in after_hashes, after
        # ...and every pre-existing cell's code hash is byte-identical.
        for cid, h in before_hashes.items():
            assert after_hashes.get(cid) == h, (
                f"insert changed cell {cid}'s code_hash: {h!r} -> "
                f"{after_hashes.get(cid)!r}"
            )
    finally:
        for cid in created:
            await delete_cell(cid, session_id=session_id, server_url=server_url)


@pytest.mark.live
async def test_delete_keeps_pre_existing_code_hashes_unchanged(mutation_server):
    """H10: deleting a cell leaves every OTHER cell's code_hash unchanged.

    Same invariant as the insert case, exercised across a delete (the other
    operation `_refresh_snapshot` narrows to `forget`). See the insert test's
    docstring for the perturbation demonstration.
    """
    _manager, server_url, session_id, _copy = mutation_server
    made = await create_cell(
        "h10_deletable = 1", session_id=session_id, server_url=server_url
    )
    assert made["status"] == "ok", made
    victim = made["cell_id"]

    before = await get_cell_map(session_id=session_id, server_url=server_url)
    before_hashes = {c["cell_id"]: c["code_hash"] for c in before["cells"]}
    assert victim in before_hashes, before

    deleted = await delete_cell(victim, session_id=session_id, server_url=server_url)
    assert deleted["status"] == "ok", deleted

    after = await get_cell_map(session_id=session_id, server_url=server_url)
    after_hashes = {c["cell_id"]: c["code_hash"] for c in after["cells"]}
    assert victim not in after_hashes, after
    for cid, h in before_hashes.items():
        if cid == victim:
            continue
        assert after_hashes.get(cid) == h, (
            f"delete changed cell {cid}'s code_hash: {h!r} -> {after_hashes.get(cid)!r}"
        )


@pytest.mark.live
async def test_cell_map_still_reports_changes_since_last(mutation_server):
    """H9: the tracker split must not disarm change detection.

    `get_cell_map` no longer records the `edit_cell` read baseline, but its
    `commit` must keep feeding `changes_since_last` (the separate
    change-detection snapshot). An out-of-band edit therefore still shows up as
    an edited cell — while the cell itself remains un-editable until a
    `get_cell_data` read.
    """
    _manager, server_url, session_id, _copy = mutation_server
    outputs = await get_cell_outputs(session_id=session_id, server_url=server_url)
    target = outputs["cells"][0]["cell_id"]

    # First observation = baseline; nothing to diff against yet.
    first = await get_cell_map(session_id=session_id, server_url=server_url)
    assert "changes_since_last" not in first, first
    assert target in {c["cell_id"] for c in first["cells"]}, first

    # A second actor edits the cell out of band.
    raw = await _execute_raw(
        server_url, session_id, _raw_external_edit(target, "setup_marker = 1")
    )
    assert raw.status == "ok", f"raw external edit failed: stderr={raw.stderr}"

    second = await get_cell_map(session_id=session_id, server_url=server_url)
    changed = second.get("changes_since_last")
    assert changed is not None, second
    assert target in changed["edited_cells"], changed

    # The change is detected, but the preview still did not bless the cell.
    refused = await edit_cell(
        target, "x = 1", session_id=session_id, server_url=server_url
    )
    assert refused["status"] == "needs_read", refused
