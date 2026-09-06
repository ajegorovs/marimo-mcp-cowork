"""Tool handlers for cell inspection."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_session_id

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

    client = _get_client(server_url)
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
    cell_ids: list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get full runtime data for one or more cells.

    Includes source code, errors, and variable information.
    If cell_ids is empty, returns data for all cells.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        cell_ids: Cell IDs from get_cell_map. Empty = all cells.
        server_url: Optional server URL override.

    Returns:
        Dictionary with cell runtime data.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        target = f"cells {cell_ids}" if cell_ids else "all cells"
        await ctx.info(f"Getting cell data for {target}...")

    client = _get_client(server_url)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.cell_data import build_cell_data_template

    code = build_cell_data_template(cell_ids=cell_ids or [])
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
            "data": data.get("data", []),
            "next_steps": [
                "Review cell code for implementation details",
                "Check errors for execution issues",
                "Examine variables to understand cell state",
            ],
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse cell data result",
            "raw_output": stdout_text,
            "stderr": result.stderr,
        }


async def get_cell_outputs(
    session_id: str = "",
    cell_ids: list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get cell execution outputs including visual display and console streams.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        cell_ids: Cell IDs from get_cell_map. Empty = all cells.
        server_url: Optional server URL override.

    Returns:
        Dictionary with cell outputs and console streams.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        target = f"cells {cell_ids}" if cell_ids else "all cells"
        await ctx.info(f"Getting cell outputs for {target}...")

    client = _get_client(server_url)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.cell_outputs import build_cell_outputs_template

    code = build_cell_outputs_template(cell_ids=cell_ids or [])
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


def _get_client(server_url: str) -> MarimoClient:
    """Create a MarimoClient, using server_url or auto-discovery."""
    if server_url:
        return MarimoClient(server_url)

    # Default discovery will be handled in the tool
    # This is a fallback; typically the caller provides server_url

    # Use a placeholder; actual discovery happens in list_active_notebooks
    raise ValueError(
        "server_url is required. Use list_active_notebooks to discover servers, "
        "then pass the server_url to subsequent tools."
    )
