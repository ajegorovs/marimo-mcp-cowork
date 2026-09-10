"""MCP tool handlers for live marimo UI-element interaction.

A single, deliberately narrow write tool: ``set_ui_value``. Unlike the cell
mutation tools it takes NO source code — only a ``variable_name`` (a live
kernel global resolving to a marimo UI element) and a ``value``. Arbitrary
code execution is out of scope by construction: the tool cannot be handed a
snippet to run.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp import Context

from marimo_inspection.client import MarimoClient
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
    """Run a snippet and return its parsed JSON payload.

    marimo may interleave its own status lines with our JSON result on
    stdout, so we scan the emitted lines and return the last one that decodes
    as a JSON object. A structured ``status: error`` payload from the template
    is surfaced verbatim (it is a valid dict, not an execution failure).
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
        "error": "Failed to parse set_ui_value result",
        "raw_output": "\n".join(result.stdout or []),
        "stderr": "\n".join(result.stderr or []),
    }


async def set_ui_value(
    variable_name: str,
    value: Any,
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Set the value of a live marimo UI element, by its variable name.

    The kernel global named by ``variable_name`` must resolve to a marimo UI
    element (e.g. ``mo.ui.slider``, ``mo.ui.dropdown``, ``mo.ui.text``). Its
    value is replaced with ``value``, triggering reactive re-execution the
    same way a user interaction would. The value's JSON shape is preserved
    exactly — a scalar dropdown key stays a scalar, a multiselect list stays a
    list.

    This tool accepts NO source code: it exists for widget interaction only,
    not for arbitrary code execution. The update is flushed on code-mode
    context exit; the kernel then re-runs dependent cells.

    Args:
        variable_name: Name of the live kernel global holding the UI element.
        value: New value for the element (shape must match the widget type:
            slider/text take scalars, dropdown takes a key, multiselect takes
            a list of keys).
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with status. On a missing or non-UI variable the kernel template
        returns a structured error explaining exactly what went wrong.
        On success, status is ``ok`` and ``next_steps`` directs verification
        of the reactive effect — this call does NOT wait for downstream
        re-runs to finish.
    """
    if not variable_name:
        return {
            "status": "error",
            "error": "variable_name is required",
        }

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Setting UI element '{variable_name}' in session {sid}...")

    from marimo_inspection.templates.ui import build_set_ui_value_template

    data = await _execute_json(
        client, sid, build_set_ui_value_template(variable_name, value)
    )
    if "error" in data:
        # Execution failure or JSON parse failure — pass verbatim.
        return data
    if data.get("status") == "error":
        # Structured kernel rejection (missing / non-UI variable).
        return data

    return {
        "status": "ok",
        "variable_name": variable_name,
        "session_id": sid,
        "next_steps": [
            (
                "The value update is queued and flushes on code-mode context "
                "exit; the kernel then triggers reactive re-runs. This call "
                "does NOT await downstream completion. Verify with "
                "get_variables (the element's .value) and get_cell_outputs / "
                "get_errors before declaring the interaction complete."
            ),
        ],
    }


__all__ = ["set_ui_value"]
