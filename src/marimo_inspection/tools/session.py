"""Shared session resolution for MCP tools.

Provides helpers for resolving session_id and server_url — either explicitly
provided or from the active session bound in FastMCP state — and a
`set_active_session` tool for explicit binding.
"""

from __future__ import annotations

from fastmcp import Context

_SESSION_KEY = "active_session_id"
_SERVER_URL_KEY = "active_server_url"


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
        "No session_id provided and no active session bound for this call. "
        "Run list_active_notebooks() first to discover and auto-bind a session, "
        "or use set_active_session(session_id, server_url=...) to bind one "
        "explicitly. A binding made earlier is invisible to a client whose "
        "requests do not share one MCP session (fastmcp's own Client does not) "
        "— pass session_id and server_url explicitly there."
    )


async def resolve_server_url(
    server_url: str,
    ctx: Context | None = None,
) -> str:
    """Resolve server_url from explicit value or bound state.

    Returns the server_url to use. Raises ValueError if neither an explicit
    server_url nor a bound active server_url is available.
    """
    if server_url:
        return server_url

    if ctx is not None:
        bound = await ctx.get_state(_SERVER_URL_KEY)
        if bound:
            return bound

    raise ValueError(
        "server_url is required. Use list_active_notebooks to discover servers "
        "and auto-bind one, or pass server_url explicitly."
    )


async def bind_active_session(
    session_id: str,
    ctx: Context,
    server_url: str = "",
) -> None:
    """Bind a session_id (and optionally its server_url) as the active session."""
    await ctx.set_state(_SESSION_KEY, session_id)
    if server_url:
        await ctx.set_state(_SERVER_URL_KEY, server_url)


async def set_active_session(
    session_id: str,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Set the active notebook session for subsequent tool calls.

    Binds a session_id (and optionally its server_url) so later calls can omit
    both `session_id` and `server_url` — **only where those later calls share
    this call's MCP session**. Session state is keyed by the MCP session
    identity the client negotiates, so a binding is visible to the next call
    only when the client keeps one session for the connection (an
    `mcp`-SDK-based client does). fastmcp's own `Client` starts a fresh MCP
    session per request on the pinned fastmcp 4.0.3, so the binding does not
    reach later calls there: pass both arguments explicitly instead. Use
    `list_active_notebooks` first to discover available sessions and their IDs.

    Args:
        session_id: The session ID to set as active.
        server_url: Optional server URL to bind with the session.

    Returns:
        Confirmation with the bound session ID.
    """
    if not session_id:
        return {
            "error": "session_id is required",
            "help": "Use list_active_notebooks to discover available sessions.",
        }

    if ctx:
        await bind_active_session(session_id, ctx, server_url=server_url)
        await ctx.info(f"Active session set to {session_id}")

    return {
        "status": "OK",
        "active_session_id": session_id,
        "binding_scope": "this MCP session (server-side state)",
        "message": (
            f"Active session bound to {session_id} for this MCP session. Calls "
            "that reuse the same MCP session may now omit session_id and "
            "server_url; a client that starts a new MCP session per request "
            "(fastmcp's own Client does) cannot see the binding — pass both "
            "arguments explicitly there."
        ),
    }
