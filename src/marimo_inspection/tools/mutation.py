"""MCP tool handlers for mutating notebook cells.

Provides the unified write surface: `create_cell`, `edit_cell`, `run_cell`,
and `delete_cell`. These wrap marimo's `marimo._code_mode` API (the same layer
the pairing scripts use) behind single, validated tool contracts.

`edit_cell` carries a **staleness guard**: before mutating, it compares the
cell's live source hash against the agent's last-read snapshot (tracked by the
change tracker). If the cell changed since the agent last read it, the edit is
REFUSED and the agent is told to re-read — impossible silent stomps during
simultaneous co-work. This mirrors Hermes' file-edit guard (`check_stale`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.change_tracking import (
    CellFingerprint,
    get_tracker,
)
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)


async def _get_client(
    server_url: str,
    ctx: Context | None = None,
) -> MarimoClient:
    """Create a MarimoClient, using explicit server_url or the bound one."""
    url = await resolve_server_url(server_url, ctx)
    return MarimoClient(url)


async def _execute_json(client: MarimoClient, sid: str, code: str) -> dict:
    """Run a mutation/read snippet and return its parsed JSON payload.

    marimo may interleave its own status lines (e.g. "created cell 'x'") with
    our JSON result on stdout, so we scan the emitted lines and return the
    first/last one that decodes as a JSON object.
    """
    result = await client.execute(sid, code)
    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": "\n".join(result.stderr or []),
        }
    all_lines = "\n".join(result.stdout or []).split("\n")
    for line in reversed(all_lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return {
        "error": "Failed to parse mutation result",
        "raw_output": "\n".join(result.stdout or []),
        "stderr": "\n".join(result.stderr or []),
    }


async def _refresh_snapshot(
    client: MarimoClient,
    sid: str,
    *,
    record: Iterable[str] = (),
    forget: Iterable[str] = (),
) -> dict[str, CellFingerprint] | None:
    """Re-read live hashes; commit ONLY the cells this mutation touched.

    After a successful mutation the agent's baseline for the cell it just wrote
    is stale; refreshing that one cell (an implicit read, like Hermes'
    ``note_write``) keeps change-detection coherent so our own write does not
    reappear as an external change.

    It must NOT commit the whole session. ``ChangeTracker.commit`` replaces the
    snapshot, which forges a last-read baseline for every OTHER cell — a write
    to any cell would then disable the ``edit_cell`` guard for all of them
    (every foreign edit silently stops reporting ``conflict``, and a never-read
    cell stops reporting ``needs_read``). That is H7 in
    ``docs/agenda-bug-hunt-1.md``.

    Returns the fresh fingerprint map (the caller reports the post-write hash
    from it), or ``None`` if the hashes payload was an error — in which case
    the tracker is left UNTOUCHED. Callers must surface a warning on ``None``
    rather than crashing.
    """
    from marimo_inspection.templates.mutation import build_cell_hashes_template

    data = await _execute_json(client, sid, build_cell_hashes_template())
    if "error" in data:
        logger.warning(
            "Snapshot refresh failed for session %s: %s", sid, data.get("error")
        )
        return None
    hashes = {k: v for k, v in data.items() if isinstance(v, str)}
    fps = {cid: CellFingerprint(code_hash=h) for cid, h in hashes.items()}
    tracker = get_tracker()
    touched = {cid: fps[cid] for cid in record if cid in fps}
    if touched:
        tracker.record_cells(sid, touched)
    if forget:
        tracker.forget_cells(sid, forget)
    return fps


async def create_cell(
    source: str,
    *,
    name: str | None = None,
    hide_code: bool = False,
    after: str | None = None,
    before: str | None = None,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Create a new cell in the notebook.

    Created cells are visible in the UI by default (hide_code=False); pass
    hide_code=True explicitly for setup/implementation cells you want hidden.

    Args:
        source: Source code for the new cell.
        name: Optional cell name.
        hide_code: Whether the code is hidden in the UI (default False).
        after: Optional cell_id to place this cell after.
        before: Optional cell_id to place this cell before.
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with status and the created cell_id.
    """
    if not source.strip():
        return {"error": "source must not be empty", "status": "error"}

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Creating cell in session {sid}...")

    from marimo_inspection.templates.mutation import build_create_cell_template

    code = build_create_cell_template(
        source, name=name, hide_code=hide_code, after=after, before=before
    )
    data = await _execute_json(client, sid, code)
    if "error" in data:
        return data

    # Record the NEW cell's baseline (deliberate: the caller authored its
    # source, so the documented create → run → edit flow must not force a
    # re-read of a cell the agent just wrote). Only that cell is touched —
    # never the whole session (H7).
    new_cell_id = data.get("cell_id")
    fps = await _refresh_snapshot(
        client, sid, record=[new_cell_id] if new_cell_id else ()
    )
    response = {
        "status": "ok",
        "cell_id": new_cell_id,
        "session_id": sid,
        "next_steps": ["Use run_cell to execute it, or get_cell_map to see it."],
    }
    if fps is None:
        response["warning"] = (
            "Cell created, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    return response


async def edit_cell(
    cell_id: str,
    source: str,
    *,
    name: str | None = None,
    hide_code: bool | None = None,
    check_fresh: bool = True,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Edit an existing cell's source code.

    Includes a staleness guard: unless ``check_fresh=False``, refuses to edit
    a cell whose source changed since the agent last read it (as recorded by
    the change tracker). This prevents silently overwriting a concurrent edit.

    Args:
        cell_id: Target cell id.
        source: New source code.
        name: Optional new cell name.
        hide_code: Optional new hide_code value.
        check_fresh: Refuse to edit a cell that changed since last read.
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with status. On a guard refusal, status is 'conflict'/'needs_read'.
    """
    if not cell_id:
        return {"error": "cell_id is required", "status": "error"}
    if not source.strip():
        return {"error": "source must not be empty", "status": "error"}

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id

    from marimo_inspection.templates.mutation import build_cell_hashes_template

    # Fresh live hashes (does NOT touch the tracker — we need the agent's own
    # earlier snapshot for the comparison, not the current state).
    live = await _execute_json(client, sid, build_cell_hashes_template())
    if "error" in live:
        return live
    if cell_id not in live:
        # Genuinely absent from the session (not just a None hash): the
        # staleness guard is meaningless for a nonexistent cell. Refuse
        # BEFORE mutating.
        return {
            "status": "error",
            "cell_id": cell_id,
            "message": (
                f"Cell {cell_id} not found in session {sid}. "
                "Use get_cell_map to list the current cell ids, then retry."
            ),
        }
    live_hash = live.get(cell_id)

    tracker = get_tracker()
    prev = tracker.get_cell_fingerprint(sid, cell_id)

    if check_fresh:
        if prev is None:
            # No baseline for this cell — the agent has never read it, so
            # freshness cannot be proven. This holds even when the session
            # has no snapshot at all: a first edit of a never-read cell must
            # never silently bypass the guard.
            return {
                "status": "needs_read",
                "cell_id": cell_id,
                "message": (
                    f"Cell {cell_id} was never read by this agent. "
                    "get_cell_map/get_cell_data first so you edit against "
                    "a known baseline, then retry edit_cell."
                ),
            }
        if live_hash is not None and live_hash != prev.code_hash:
            return {
                "status": "conflict",
                "cell_id": cell_id,
                "message": (
                    f"Cell {cell_id} was modified since the agent last read it "
                    "(source hash changed). Re-read it (get_cell_data/get_cell_map) "
                    "to avoid overwriting a concurrent edit, then retry edit_cell. "
                    "Or pass check_fresh=False to force."
                ),
            }

    if ctx:
        await ctx.info(f"Editing cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_edit_cell_template

    code = build_edit_cell_template(cell_id, source, name=name, hide_code=hide_code)
    data = await _execute_json(client, sid, code)
    if "error" in data:
        return data

    # The template's own hash (if any) is computed inside the edit context,
    # BEFORE the context-exit applies the queued edit — i.e. stale. Always
    # report the POST-context-exit hash from the fresh snapshot instead.
    # Record ONLY this cell's baseline: an unrelated cell's baseline must
    # survive this write (H7).
    fps = await _refresh_snapshot(client, sid, record=[cell_id])
    response = {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
        "next_steps": ["Use run_cell to execute the edited cell."],
    }
    if fps is None:
        response["warning"] = (
            "Edit applied, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    else:
        fp = fps.get(cell_id)
        if fp is not None:
            response["code_hash"] = fp.code_hash
        elif fps:
            response["warning"] = (
                "Edit applied, but the refreshed snapshot does not contain "
                f"cell {cell_id}. Run get_cell_data to re-establish the baseline."
            )
        # fps == {} (a genuinely cell-free session) — leave the hash out.
    return response


async def run_cell(
    cell_id: str,
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Run (execute) an existing cell.

    Args:
        cell_id: Target cell id.
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with status.
    """
    if not cell_id:
        return {"error": "cell_id is required", "status": "error"}

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Running cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_run_cell_template

    data = await _execute_json(client, sid, build_run_cell_template(cell_id))
    if "error" in data:
        return data

    return {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
        "next_steps": ["Use get_errors/get_cell_outputs to check the run."],
    }


async def delete_cell(
    cell_id: str,
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Delete an existing cell from the notebook.

    Args:
        cell_id: Target cell id.
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with status.
    """
    if not cell_id:
        return {"error": "cell_id is required", "status": "error"}

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Deleting cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_delete_cell_template

    data = await _execute_json(client, sid, build_delete_cell_template(cell_id))
    if "error" in data:
        return data

    # The cell is gone, so there is nothing to record: drop its fingerprint
    # without touching any other cell's baseline (H7). A re-used id would
    # then report `needs_read` — the safe direction.
    fps = await _refresh_snapshot(client, sid, forget=[cell_id])
    response = {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
    }
    if fps is None:
        response["warning"] = (
            "Cell deleted, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    return response
