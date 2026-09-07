"""Tool handler for notebook linting.

Lints a notebook file directly on the server side by reading the notebook's
source and running marimo's static lint engine — the same engine behind
``marimo check``. This is deliberate: linting is static source analysis and
does not need the kernel scratchpad, whose ``AsyncCodeModeContext`` exposes no
notebook IR (``ctx.notebook`` does not exist), so the old scratchpad-template
approach could never work at runtime.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = ["breaking", "runtime", "formatting", "wasm"]


async def _lint_source(contents: str, filepath: str) -> dict:
    """Run marimo's static lint engine on notebook source.

    Args:
        contents: Full notebook source (the marimo ``.py`` file text).
        filepath: Path used for the ``NotebookSerialization``.

    Returns:
        A dict of the form ``{"summary": {...}, "diagnostics": [...]}``.
    """
    from marimo._ast.parse import parse_notebook
    from marimo._lint.rule_engine import RuleEngine

    notebook = parse_notebook(contents, filepath=filepath)
    if notebook is None:
        return {
            "summary": {
                "total_issues": 0,
                "breaking_issues": 0,
                "runtime_issues": 0,
                "formatting_issues": 0,
                "wasm_issues": 0,
            },
            "diagnostics": [],
            "error": "Could not parse notebook source",
        }

    # Use the async entry point directly (check_notebook_sync wraps it in
    # asyncio.run(), which fails inside the MCP server's event loop).
    diagnostics = await RuleEngine.create_default().check_notebook(notebook)

    counts = {sev: 0 for sev in _SEVERITY_ORDER}
    results = []
    for diag in diagnostics:
        sev = str(diag.severity).lower() if diag.severity is not None else "runtime"
        # severity strings look like "Severity.BREAKING" or "breaking"
        sev = sev.split(".")[-1]
        if sev in counts:
            counts[sev] += 1
        else:
            counts["runtime"] += 1

        # diag.cell_id/line/column can be lists; normalize to first value
        def _first(value):
            if value is None:
                return None
            return value[0] if isinstance(value, list) and value else value

        results.append(
            {
                "rule": diag.code,
                "name": diag.name,
                "severity": sev,
                "message": diag.message,
                "cell_id": _first(diag.cell_id),
                "line": _first(diag.line),
                "column": _first(diag.column),
                "filename": diag.filename,
            }
        )

    total = sum(counts.values())
    return {
        "summary": {
            "total_issues": total,
            "breaking_issues": counts["breaking"],
            "runtime_issues": counts["runtime"],
            "formatting_issues": counts["formatting"],
            "wasm_issues": counts["wasm"],
        },
        "diagnostics": results,
    }


async def lint_notebook(
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Lint a marimo notebook to check for issues.

    Uses marimo's internal linting engine (the same one behind
    ``marimo check``) to check for:
    - Breaking issues: Problems that prevent the notebook from running
    - Runtime issues: Problems that may cause unexpected behavior
    - Formatting issues: Code style and formatting problems

    Args:
        session_id: Session ID from list_active_notebooks.
            Optional if an active session is bound.
        server_url: Optional server URL override. Optional if an active
            server_url is bound.

    Returns:
        Dictionary with lint diagnostics and summary.
    """
    sid = await resolve_session_id(session_id, ctx)
    if ctx:
        await ctx.info("Linting notebook...")

    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)

    file_path = session.file
    if not file_path:
        return {
            "session_id": session.session_id,
            "error": "Cannot lint an unsaved notebook (no file path on session)",
        }

    path = Path(file_path)
    if not path.is_file():
        return {
            "session_id": session.session_id,
            "error": f"Notebook file not found: {file_path}",
        }

    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "session_id": session.session_id,
            "error": f"Failed to read notebook file {file_path}: {exc}",
        }

    data = await _lint_source(contents, file_path)
    summary = data.get("summary", {})

    next_steps = []
    if summary.get("breaking_issues", 0) > 0:
        next_steps.append(
            "Fix breaking issue(s) that prevent the notebook from running"
        )
    if summary.get("runtime_issues", 0) > 0:
        next_steps.append("Address runtime issue(s) that may cause unexpected behavior")
    if summary.get("formatting_issues", 0) > 0:
        next_steps.append("Optionally fix formatting issue(s) for better code style")
    if summary.get("total_issues", 0) == 0:
        next_steps.append("No issues found - notebook is healthy!")

    result = {
        "session_id": session.session_id,
        "summary": summary,
        "diagnostics": data.get("diagnostics", []),
        "next_steps": next_steps,
    }
    if data.get("error"):
        result["error"] = data["error"]
    return result


async def _get_client(
    server_url: str,
    ctx: Context | None = None,
) -> MarimoClient:
    """Create a MarimoClient, using explicit server_url or the bound one."""
    url = await resolve_server_url(server_url, ctx)
    return MarimoClient(url)
