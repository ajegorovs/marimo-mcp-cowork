"""Tool handlers for cell inspection."""

from __future__ import annotations

import hashlib
import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.args import normalize_list_arg
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)


async def get_cell_map(
    session_id: str = "",
    preview_lines: int = 3,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get a lightweight map of cells showing previews.

    Returns cell IDs, names, code previews, and runtime state.
    This is the starting point for navigating a notebook.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        preview_lines: Number of lines to show per cell (default: 3).
        server_url: Optional server URL override.

    Returns:
        Dictionary with cell map and navigation info.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        await ctx.info(f"Getting cell map for session {sid}...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.cell_map import build_cell_map_template

    code = build_cell_map_template(preview_lines=preview_lines)
    result = await client.execute(session.session_id, code)

    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": result.stderr,
        }

    # Parse stdout for JSON result
    stdout_text = "\n".join(result.stdout)
    try:
        data = json.loads(stdout_text)
        cells = data.get("cells", [])

        from marimo_inspection.tools.change_tracking import (
            CellFingerprint,
            get_tracker,
        )

        tracker = get_tracker()
        fingerprints = {
            c["cell_id"]: CellFingerprint(
                code_hash=c.get("code_hash", ""),
                state=c.get("runtime_state"),
            )
            for c in cells
        }

        # First observation for this session = baseline (no diff reported).
        had_baseline = tracker.has_snapshot(session.session_id)
        change = tracker.diff(session.session_id, fingerprints)
        tracker.commit(session.session_id, fingerprints)

        response = {
            "session_id": session.session_id,
            "notebook_name": session.basename,
            "cells": cells,
            "total_cells": data.get("total_cells", 0),
            "preview_lines": preview_lines,
            "next_steps": [
                "Use cell_id to get full cell content via get_cell_data",
                "Identify key sections based on cell previews",
                "Focus on import cells first to understand dependencies",
            ],
        }
        # Expose change detection only once a baseline exists and there is
        # actually something worth reporting.
        if had_baseline and change.has_changes:
            response["changes_since_last"] = change.to_dict()
        return response
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse cell map result",
            "raw_output": stdout_text,
            "stderr": result.stderr,
        }


async def get_cell_data(
    session_id: str = "",
    cell_ids: str | list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get full runtime data for one or more cells.

    Includes source code, errors, and variable information.
    If cell_ids is empty, returns data for all cells.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        cell_ids: Cell IDs from get_cell_map. Empty = all cells. Accepts a
            single ID, a native array, or a JSON-encoded array — a harness
            may deliver either of the latter two as a string.
        server_url: Optional server URL override.

    Returns:
        Dictionary with cell runtime data.
    """
    cell_ids = normalize_list_arg(cell_ids)
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        target = f"cells {cell_ids}" if cell_ids else "all cells"
        await ctx.info(f"Getting cell data for {target}...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.cell_data import build_cell_data_template

    code = build_cell_data_template(cell_ids=cell_ids)
    result = await client.execute(session.session_id, code)

    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": result.stderr,
        }

    stdout_text = "\n".join(result.stdout)
    try:
        data = json.loads(stdout_text)
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse cell data result",
            "raw_output": stdout_text,
            "stderr": result.stderr,
        }

    # A read IS the freshness event: record the exact returned source hash
    # and runtime state for each returned cell into the change tracker
    # (merge-only — never erases fingerprints of unread cells). Skipped on
    # execution error or JSON parse failure above, so a failed read cannot
    # corrupt the agent's baseline.
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    rows = data.get("data", [])
    fingerprints: dict[str, CellFingerprint] = {}
    for row in rows:
        cell_id = row.get("cell_id")
        code = row.get("code") or ""
        if not cell_id:
            continue
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        fingerprints[cell_id] = CellFingerprint(
            code_hash=code_hash,
            state=row.get("runtime_state"),
        )
    get_tracker().record_cells(session.session_id, fingerprints)

    return {
        "session_id": session.session_id,
        "data": rows,
        "next_steps": [
            "Review cell code for implementation details",
            "Check errors for execution issues",
            "Examine variables to understand cell state",
        ],
    }


async def get_cell_outputs(
    session_id: str = "",
    cell_ids: str | list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get cell execution outputs including visual display and console streams.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        cell_ids: Cell IDs from get_cell_map. Empty = all cells. Accepts a
            single ID, a native array, or a JSON-encoded array — a harness
            may deliver either of the latter two as a string.
        server_url: Optional server URL override.

    Returns:
        Dictionary with cell outputs and console streams.
    """
    cell_ids = normalize_list_arg(cell_ids)
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        target = f"cells {cell_ids}" if cell_ids else "all cells"
        await ctx.info(f"Getting cell outputs for {target}...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.cell_outputs import build_cell_outputs_template

    code = build_cell_outputs_template(cell_ids=cell_ids)
    result = await client.execute(session.session_id, code)

    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": result.stderr,
        }

    stdout_text = "\n".join(result.stdout)
    try:
        data = json.loads(stdout_text)
        return {
            "session_id": session.session_id,
            "cells": data.get("cells", []),
            "next_steps": [
                "Review visual_output for displayed content",
                "Check stdout/stderr for print statements and warnings",
            ],
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse cell outputs result",
            "raw_output": stdout_text,
            "stderr": result.stderr,
        }


async def _get_client(
    server_url: str,
    ctx: Context | None = None,
) -> MarimoClient:
    """Create a MarimoClient, using explicit server_url or the bound one."""
    url = await resolve_server_url(server_url, ctx)
    return MarimoClient(url)
