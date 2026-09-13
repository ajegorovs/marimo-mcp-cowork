"""Tool handlers for variable and table inspection."""

from __future__ import annotations

import json
import logging

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.args import normalize_list_arg
from marimo_inspection.tools.session import resolve_target

logger = logging.getLogger(__name__)


async def get_variables(
    session_id: str = "",
    variable_names: str | list[str] | None = None,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Get tables and variables information in the session.

    Returns information about kernel variables and DataFrames.

    If variable_names is empty — including an empty or whitespace-only string —
    returns all variables, meaning the executed
    public names defined by notebook cells. Kernel-injected globals, the
    inspection template's scaffolding, private (leading-underscore) names, and
    definitions from cells that have not executed are excluded, because the
    scratchpad shares the kernel namespace. Pass variable_names to inspect
    specific names that are visible in that kernel namespace, including an
    explicitly named kernel-injected global such as ``input``. A filtered
    lookup does not apply the unfiltered exclusions, but it still cannot surface
    a name the kernel does not expose: a leading-underscore private name reports
    an empty result.

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        variable_names: Specific variables to inspect. Empty (or a blank string)
            = all. Accepts a single name, a native array, or a JSON-encoded
            array — a harness may deliver either of the latter two as a string.
        server_url: Optional server URL override.

    Returns:
        Dictionary with tables and variables information.
    """
    variable_names = normalize_list_arg(variable_names)
    resolved = await resolve_target(
        session_id, server_url, ctx=ctx, client_factory=MarimoClient
    )
    if resolved.refusal is not None:
        return resolved.refusal
    client, session = resolved.unwrap()
    if ctx:
        target = f"variables {variable_names}" if variable_names else "all variables"
        await ctx.info(f"Inspecting {target}...")

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
