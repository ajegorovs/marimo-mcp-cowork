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
    session — subsequent tools can omit `session_id`.

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
            await bind_active_session(sid, ctx)
            await ctx.info(f"Auto-bound session {sid} as active session")

    return {
        "summary": {
            "total_notebooks": len(all_notebooks),
            "active_connections": total_connections,
            "servers_discovered": len(servers),
        },
        "notebooks": all_notebooks,
        "next_steps": [
            "Use get_cell_map to get the structure of a notebook (session_id is now optional)",
            "Use get_errors to debug errors",
            "Pass session_id explicitly to target a different notebook",
        ],
    }
