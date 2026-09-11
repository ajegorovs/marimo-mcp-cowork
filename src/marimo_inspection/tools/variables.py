"""Tool handlers for variable and table inspection."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.args import normalize_list_arg
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)


async def get_variables(
    session_id: str = "",
    variable_names: str | list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get tables and variables information in the session.

    Returns information about kernel variables and DataFrames.
    If variable_names is empty, returns all variables — meaning the notebook's
    own session names, with the inspection template's scaffolding (its imports
    and helpers) excluded, since the scratchpad shares the kernel namespace.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        variable_names: Specific variables to inspect. Empty = all. Accepts a
            single name, a native array, or a JSON-encoded array — a harness
            may deliver either of the latter two as a string.
        server_url: Optional server URL override.

    Returns:
        Dictionary with tables and variables information.
    """
    variable_names = normalize_list_arg(variable_names)
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        target = f"variables {variable_names}" if variable_names else "all variables"
        await ctx.info(f"Inspecting {target}...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    from marimo_inspection.templates.variables import build_variables_template

    code = build_variables_template(variable_names=variable_names)
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
            "tables": data.get("tables", {}),
            "variables": data.get("variables", {}),
            "next_steps": [
                "Review table columns and row counts for DataFrames",
                "Check variable values for scalar types",
                "Use variable names in subsequent code execution",
            ],
        }
    except json.JSONDecodeError:
        return {
            "error": "Failed to parse variables result",
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
