"""Shared session resolution for MCP tools.

Provides helpers for resolving session_id — either explicitly provided or
from the active session bound in FastMCP state — and a
`set_active_session` tool for explicit binding.
"""

from __future__ import annotations

from fastmcp import Context

_SESSION_KEY = "active_session_id"


async def resolve_session_id(
    session_id: str,
    ctx: Context | None = None,
) -> str:
    """Resolve session_id from explicit value or bound state.

    Returns the session_id to use. Raises ValueError if neither
    a non-empty session_id nor a bound active session is available.
    """
    if session_id:
        return session_id

    if ctx is not None:
        bound = await ctx.get_state(_SESSION_KEY)
        if bound:
            return bound

    raise ValueError(
        "No session_id provided and no active session bound. "
        "Run list_active_notebooks() first to discover and auto-bind a session, "
        "or use set_active_session(session_id) to bind one explicitly."
    )


async def bind_active_session(
    session_id: str,
    ctx: Context,
) -> None:
    """Bind a session_id as the active session for subsequent calls."""
    await ctx.set_state(_SESSION_KEY, session_id)


async def set_active_session(
    session_id: str,
    ctx: Context | None = None,
) -> dict:
    """Set the active notebook session for subsequent tool calls.

    Binds a session_id so tools like get_cell_map, get_cell_data, etc.
    can omit the `session_id` parameter. Use `list_active_notebooks`
    first to discover available sessions and their IDs.

    Args:
        session_id: The session ID to set as active.

    Returns:
        Confirmation with the bound session ID.
    """
    if not session_id:
        return {
            "error": "session_id is required",
            "help": "Use list_active_notebooks to discover available sessions.",
        }

    if ctx:
        await bind_active_session(session_id, ctx)
        await ctx.info(f"Active session set to {session_id}")

    return {
        "status": "OK",
        "active_session_id": session_id,
        "message": f"Active session bound to {session_id}. Session_id is now optional for other tools.",
    }
