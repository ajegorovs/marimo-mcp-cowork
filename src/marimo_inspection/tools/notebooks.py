"""Tool handlers for notebook listing and discovery."""

from __future__ import annotations

import logging

import httpx2
from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import bind_active_session

logger = logging.getLogger(__name__)


async def list_active_notebooks(
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """List currently active marimo notebooks.

    Returns all active sessions with their file paths and session IDs.
    The first discovered session is automatically bound as the active
    session, so later calls that share this MCP session can omit both
    `session_id` and `server_url`.

    The binding lives in two places: this MCP session's server-side state, and
    a **process-global fallback** consulted only when that state has nothing
    bound. Over stdio one process serves exactly one client, so the fallback
    carries the binding to every later call — argument-less calls keep working
    across later or fresh MCP sessions. Over HTTP/SSE one process serves many
    clients, so the fallback is served only while the process has seen a single
    client session; once a second client session appears, argument-less calls
    are refused with `reason: binding_ambiguous` (fail-closed — never guessed).
    An explicit `session_id`/`server_url` always wins over the binding.

    Args:
        server_url: Optional explicit server URL.
            If not provided, discovers servers from the marimo registry.

    Returns:
        Dictionary with summary and list of active notebooks.
    """
    if ctx:
        await ctx.info("Listing active notebooks...")

    from marimo_inspection.discovery import discover_servers

    if server_url:
        servers = [server_url]
    else:
        discovered = await discover_servers()
        servers = [s.url for s in discovered if s.healthy]

    if not servers:
        return {
            "summary": {
                "total_notebooks": 0,
                "active_connections": 0,
                "servers_discovered": 0,
            },
            "notebooks": [],
            "next_steps": [
                "Start a marimo server with: marimo edit notebooks/your-notebook.py --no-token",
            ],
        }

    # Query each server for sessions
    all_notebooks = []
    total_connections = 0

    for url in servers:
        client = MarimoClient(url)
        try:
            sessions = await client.list_sessions()
            for session in sessions:
                all_notebooks.append(
                    {
                        "name": session.basename or "untitled",
                        "path": session.file or "unsaved notebook",
                        "session_id": session.session_id,
                        "server_url": url,
                    }
                )
            total_connections += len(sessions)
        except (httpx2.HTTPError, OSError, ValueError) as exc:
            logger.warning("Failed to query server %s: %s", url, exc)
            all_notebooks.append(
                {
                    "name": "connection failed",
                    "path": url,
                    "session_id": "error",
                    "server_url": url,
                    "error": str(exc),
                }
            )

    # Auto-bind the first discovered session as active
    if all_notebooks and ctx:
        first = all_notebooks[0]
        sid = first.get("session_id")
        if sid and sid != "error":
            url = first.get("server_url", "")
            await bind_active_session(sid, ctx, server_url=url)
            await ctx.info(f"Auto-bound session {sid} as active session")

    return {
        "summary": {
            "total_notebooks": len(all_notebooks),
            "active_connections": total_connections,
            "servers_discovered": len(servers),
        },
        "notebooks": all_notebooks,
        "next_steps": [
            "Use get_cell_map to get the structure of a notebook (session_id and server_url are omittable over stdio; over HTTP/SSE only while this process has served one client session — otherwise pass them explicitly, see set_active_session)",
            "Use get_errors to debug errors",
            "Pass session_id explicitly to target a different notebook",
        ],
    }
