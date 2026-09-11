"""Tool handler for dependency graph inspection."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)


async def get_dependency_graph(
    session_id: str = "",
    cell_id: str = "",
    depth: int = 0,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get the cell dependency graph showing variable relationships.

    Reveals which variables each cell defines and references, parent/child
    relationships between cells, variable ownership, and dependency issues
    like multiply-defined variables or cycles. The graph is always the FULL
    notebook graph.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        cell_id: NOT IMPLEMENTED — supplying it is refused (``reason:
            unsupported_argument``) instead of silently ignored.
        depth: NOT IMPLEMENTED — supplying a non-zero value is refused for the
            same reason.
        server_url: Optional server URL override. Optional if an active
            server_url is bound.

    Returns:
        Dictionary with dependency graph information (``cells`` carry
        ``cell_name``, matching get_cell_map's names), or a structured refusal
        when ``cell_id``/``depth`` are supplied. Nothing is read from the
        notebook in that case.
    """
    unsupported = []
    if cell_id:
        unsupported.append("cell_id")
    if depth:
        unsupported.append("depth")
    if unsupported:
        return {
            "status": "error",
            "reason": "unsupported_argument",
            "error": (
                "get_dependency_graph does not implement "
                + " or ".join(unsupported)
                + ": it always returns the full notebook graph. Nothing was read."
            ),
            "unsupported_arguments": unsupported,
            "next_steps": [
                "Call get_dependency_graph without cell_id/depth",
                "Walk cells[].parent_cell_ids / cells[].child_cell_ids for a neighbourhood",
                "Use get_cell_data(cell_ids=[...]) to read one cell's source",
            ],
        }

    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        await ctx.info("Getting full dependency graph...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.dependency import build_dependency_graph_template

    code = build_dependency_graph_template()
    result = await client.execute(session.session_id, code)

    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": result.stderr,
        }

    stdout_text = "\n".join(result.stdout)
    try:
        data = json.loads(stdout_text)
        multiply_defined = data.get("multiply_defined", [])
        cycles = data.get("cycles", [])

        next_steps = []
        if multiply_defined:
            names = ", ".join(multiply_defined[:5])
            suffix = "..." if len(multiply_defined) > 5 else ""
            next_steps.append(
                f"Fix {len(multiply_defined)} multiply-defined variable(s): {names}{suffix}"
            )
        if cycles:
            next_steps.append(
                f"Resolve {len(cycles)} dependency cycle(s) for correct execution order"
            )
        next_steps.append(
            "Walk cells[].parent_cell_ids / cells[].child_cell_ids for a "
            "neighbourhood (cell_id/depth are not implemented)"
        )

        return {
            "session_id": session.session_id,
            "cells": data.get("cells", []),
            "variable_owners": data.get("variable_owners", {}),
            "multiply_defined": multiply_defined,
            "cycles": cycles,
            "next_steps": next_steps,
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse dependency graph result",
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
