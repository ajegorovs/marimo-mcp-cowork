"""Tool handler for error aggregation."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_session_id

logger = logging.getLogger(__name__)


async def get_errors(
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get all errors in the notebook session, organized by cell.

    Returns runtime errors grouped by cell, including error types, messages,
    and tracebacks.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        server_url: Optional server URL override.

    Returns:
        Dictionary with error information organized by cell.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        await ctx.info("Getting notebook errors...")

    client = _get_client(server_url)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.errors import build_errors_template

    code = build_errors_template()
    result = await client.execute(session.session_id, code)

    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": result.stderr,
        }

    stdout_text = "\n".join(result.stdout)
    try:
        data = json.loads(stdout_text)
        has_errors = data.get("has_errors", False)
        total_errors = data.get("total_errors", 0)

        next_steps = []
        if has_errors:
            next_steps.append("Use get_cell_data to inspect impacted cells")
            next_steps.append("Re-run the notebook after addressing errors")
        else:
            next_steps.append("No errors detected")

        return {
            "session_id": session.session_id,
            "has_errors": has_errors,
            "total_errors": total_errors,
            "total_cells_with_errors": data.get("total_cells_with_errors", 0),
            "cells": data.get("cells", []),
            "next_steps": next_steps,
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse errors result",
            "raw_output": stdout_text,
            "stderr": result.stderr,
        }


def _get_client(server_url: str) -> MarimoClient:
    """Create a MarimoClient."""
    if not server_url:
        raise ValueError(
            "server_url is required. Use list_active_notebooks to discover servers."
        )
    return MarimoClient(server_url)
