"""Tool handler for error aggregation."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)


async def get_errors(
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get all errors in the notebook session, organized by cell.

    Per-cell channels are reported under ``cells[]`` (one entry per cell) and
    are never conflated:

    - ``cells[].structured_errors``: marimo's structured CellError records
      (kind graph|runtime, msg, exception).
    - ``cells[].console_stderr``: serialized stderr console events (same shape
      as get_cell_outputs), so UI-handler exception tracebacks are visible even
      when the structured channel is empty.
    - ``cells[].console_exception_evidence``: present on each console-flagged
      cell, naming the marker that matched.

    Top-level error flags and totals are summaries: ``has_errors`` /
    ``total_errors`` / ``total_cells_with_errors`` cover the structured channel
    (backward-compatible), while ``has_console_exception`` /
    ``total_console_exception_cells`` summarize the per-cell console channel.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        server_url: Optional server URL override. Optional if an active
            server_url is bound.

    Returns:
        Dictionary with error information organized by cell.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        await ctx.info("Getting notebook errors...")

    client = await _get_client(server_url, ctx)
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
        has_console_exception = data.get("has_console_exception", False)

        next_steps = []
        if has_errors:
            next_steps.append("Use get_cell_data to inspect impacted cells")
            next_steps.append("Re-run the notebook after addressing errors")
        elif has_console_exception:
            evidence = {
                c.get("console_exception_evidence")
                for c in data.get("cells", [])
                if c.get("console_exception_evidence")
            }
            seen = (
                " and ".join(kind.replace("_", " ") for kind in sorted(evidence))
                or "exception evidence"
            )
            next_steps.append(
                f"Console stderr carries {seen} in "
                f"{data.get('total_console_exception_cells', 0)} cell(s) while the "
                "structured channel is empty — read those cell's console_stderr "
                "and inspect the affected cell"
            )
        else:
            next_steps.append("No errors detected")

        return {
            "session_id": session.session_id,
            "has_errors": has_errors,
            "total_errors": total_errors,
            "total_structured_errors": data.get(
                "total_structured_errors", total_errors
            ),
            "total_cells_with_errors": data.get("total_cells_with_errors", 0),
            "has_console_exception": has_console_exception,
            "total_console_exception_cells": data.get(
                "total_console_exception_cells", 0
            ),
            "cells": data.get("cells", []),
            "next_steps": next_steps,
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse errors result",
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
