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

from marimo_inspection.tools.cells import get_cell_data
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
