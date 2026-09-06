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

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.change_tracking import (
    CellFingerprint,
    get_tracker,
)
from marimo_inspection.tools.session import resolve_session_id

logger = logging.getLogger(__name__)


def _get_client(server_url: str) -> MarimoClient:
    """Create a MarimoClient from server_url (same fallback as other tools)."""
    if not server_url:
        raise ValueError(
            "server_url is required. Use list_active_notebooks to discover "
            "servers, then pass the server_url to subsequent tools."
        )
    return MarimoClient(server_url)


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
    client: MarimoClient, sid: str
) -> dict[str, CellFingerprint]:
    """Re-read live hashes and commit them to the change tracker.

    After a successful mutation the agent's snapshot is stale; refreshing it
    (an implicit read, like Hermes' `note_write`) keeps change-detection
    coherent so our own writes don't reappear as external changes.
    """
    from marimo_inspection.templates.mutation import build_cell_hashes_template

    data = await _execute_json(client, sid, build_cell_hashes_template())
    hashes = {k: v for k, v in data.items() if isinstance(v, str)}
    fps = {cid: CellFingerprint(code_hash=h) for cid, h in hashes.items()}
    get_tracker().commit(sid, fps)
    return fps


async def create_cell(
    source: str,
    *,
    name: str | None = None,
    hide_code: bool = True,
    after: str | None = None,
    before: str | None = None,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Create a new cell in the notebook.

    Args:
        source: Source code for the new cell.
        name: Optional cell name.
        hide_code: Whether the code is hidden in the UI (default True).
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
    client = _get_client(server_url)
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

    await _refresh_snapshot(client, sid)
    return {
        "status": "ok",
        "cell_id": data.get("cell_id"),
        "session_id": sid,
        "next_steps": ["Use run_cell to execute it, or get_cell_map to see it."],
    }


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
    client = _get_client(server_url)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id

    from marimo_inspection.templates.mutation import build_cell_hashes_template

    # Fresh live hashes (does NOT touch the tracker — we need the agent's own
    # earlier snapshot for the comparison, not the current state).
    live = await _execute_json(client, sid, build_cell_hashes_template())
    if "error" in live:
        return live
    live_hash = live.get(cell_id)

    tracker = get_tracker()
    prev = tracker.get_cell_fingerprint(sid, cell_id)

    if check_fresh:
        if prev is None:
            # No baseline for this cell — but if the session has been read at
            # all, this cell was never among the reads, so we cannot prove
            # freshness. Refuse; the agent must read first.
            if tracker.has_snapshot(sid):
                return {
                    "status": "needs_read",
                    "cell_id": cell_id,
                    "message": (
                        f"Cell {cell_id} was never read by this agent. "
                        "get_cell_map/get_cell_data first so you edit against "
                        "a known baseline, then retry edit_cell."
                    ),
                }
        elif live_hash is not None and live_hash != prev.code_hash:
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

    await _refresh_snapshot(client, sid)
    return {
        "status": "ok",
        "cell_id": cell_id,
        "code_hash": data.get("code_hash"),
        "session_id": sid,
        "next_steps": ["Use run_cell to execute the edited cell."],
    }


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
    client = _get_client(server_url)
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
    client = _get_client(server_url)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Deleting cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_delete_cell_template

    data = await _execute_json(client, sid, build_delete_cell_template(cell_id))
    if "error" in data:
        return data

    await _refresh_snapshot(client, sid)
    return {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
    }
