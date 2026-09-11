"""Shared session resolution for MCP tools.

Provides helpers for resolving session_id and server_url — either explicitly
provided or from the active session bound in FastMCP state — and a
`set_active_session` tool for explicit binding.

Process-global fallback binding (H11)
-------------------------------------
FastMCP keys session state by the MCP session identity the client negotiates
(``Context.session_id`` caches a state prefix on the SDK connection, and
``_make_state_key`` prefixes every stored key with it). A client that starts a
fresh MCP session per request — fastmcp's own ``Client`` on the pinned fastmcp
4.0.3, over stdio and HTTP alike — therefore writes and reads a binding under
different keys, so it never sees it. To keep argument-less calls working for
that client shape, every binding is *also* stored process-globally (the
fallback below) and consulted only when the MCP-session state has nothing
bound.

Isolation argument — why this cannot leak:

* **stdio** — one server process serves exactly one client for its whole
  lifetime, so a process-global binding is connection-global: the only client
  that can read it is the one that wrote it.
* **HTTP / SSE** — one process serves many clients, so an unscoped fallback
  would let a client that never bound pick up another client's session and
  mutate the wrong notebook. The fallback is therefore **scoped**: it is
  served only while the process has observed a *single* client session. The
  first time a second distinct client session appears on the process, the
  fallback is withheld and argument-less calls refuse with
  ``reason: binding_ambiguous`` (fails closed — an ambiguous binding is never
  guessed). A stdio process is single-client by construction and is never
  tracked, so its argument-less calls keep working regardless of how many MCP
  sessions the client rotates through.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from fastmcp import Context

_SESSION_KEY = "active_session_id"
_SERVER_URL_KEY = "active_server_url"

# Transports that serve exactly one client per server process. For these the
# process-global fallback is connection-global and cannot leak, so the
# single-client scoping below does not apply (and their per-request session
# identities are deliberately not tracked).
_SINGLE_CLIENT_TRANSPORTS = frozenset({"stdio"})

_FALLBACK_LOCK = threading.Lock()
_fallback_session_id = ""
_fallback_server_url = ""
# Client sessions observed on this process; only consulted for multi-client
# transports (see the module docstring).
_seen_client_sessions: set[str] = set()


class SessionBindingError(ValueError):
    """A session-resolution refusal with a machine-readable ``reason``.

    The message already names the reason; the attribute lets a caller (or a
    test) read it structurally instead of parsing the string.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FallbackDecision:
    """Outcome of consulting the process-global fallback binding.

    ``reason`` is empty when the fallback is served *or* when nothing is
    bound at all; it names why the fallback was withheld otherwise.
    """

    session_id: str = ""
    server_url: str = ""
    reason: str = ""


def _is_single_client_transport(transport: str | None) -> bool:
    """True when one process serves exactly one client over this transport."""
    return transport in _SINGLE_CLIENT_TRANSPORTS


def _transport_of(ctx: Context | None) -> str | None:
    """Read the transport name off a context (None when unavailable)."""
    transport = getattr(ctx, "transport", None)
    return transport if isinstance(transport, str) else None


def _session_of(ctx: Context | None) -> str | None:
    """Read this call's MCP session identity (None when unavailable)."""
    try:
        session_id = ctx.session_id if ctx is not None else None
    except (AttributeError, RuntimeError):
        session_id = None
    return session_id if isinstance(session_id, str) else None


def record_client_session(
    transport: str | None,
    session_id: str | None,
) -> None:
    """Record one client session observed by this process (scoping input).

    Single-client transports are a no-op: one process serves one client
    there, so there is nothing to disambiguate and the rotating session
    identities of a session-per-request client must not count as many clients.
    """
    if _is_single_client_transport(transport) or not session_id:
        return
    with _FALLBACK_LOCK:
        _seen_client_sessions.add(session_id)


def store_fallback_binding(session_id: str, server_url: str = "") -> None:
    """Store the process-global fallback binding for argument-less calls.

    Mirrors ``bind_active_session``: the session_id always replaces the
    previous fallback, the server_url only when one was supplied.
    """
    global _fallback_session_id, _fallback_server_url
    with _FALLBACK_LOCK:
        _fallback_session_id = session_id
        if server_url:
            _fallback_server_url = server_url


def fallback_decision(
    transport: str | None,
    session_id: str | None,
) -> FallbackDecision:
    """Record this call's client session, then scope the fallback binding.

    The real predicate used in production (see ``resolve_session_id`` /
    ``resolve_server_url``). Returns the binding to serve, or an empty decision
    carrying ``reason="binding_ambiguous"`` when a binding exists but the
    process has already served more than one client session.
    """
    record_client_session(transport, session_id)
    with _FALLBACK_LOCK:
        if not _fallback_session_id:
            return FallbackDecision()
        if (
            not _is_single_client_transport(transport)
            and len(_seen_client_sessions) > 1
        ):
            return FallbackDecision(reason="binding_ambiguous")
        return FallbackDecision(
            session_id=_fallback_session_id,
            server_url=_fallback_server_url,
        )


def reset_fallback_state() -> None:
    """Clear the process-global fallback (test isolation only)."""
    global _fallback_session_id, _fallback_server_url
    with _FALLBACK_LOCK:
        _fallback_session_id = ""
        _fallback_server_url = ""
        _seen_client_sessions.clear()


async def resolve_session_id(
    session_id: str,
    ctx: Context | None = None,
) -> str:
    """Resolve session_id from explicit value or bound state.

    Falls back to the process-global binding only when the MCP-session state
    has nothing bound and the fallback's scope allows it (see the module
    docstring). Returns the session_id to use. Raises ``SessionBindingError``
    with ``reason="binding_ambiguous"`` when a binding exists but this process
    has served more than one client, and ``ValueError`` when nothing is bound.
    """
    if session_id:
        record_client_session(_transport_of(ctx), _session_of(ctx))
        return session_id

    if ctx is not None:
        record_client_session(_transport_of(ctx), _session_of(ctx))
        bound = await ctx.get_state(_SESSION_KEY)
        if bound:
            return bound

        decision = fallback_decision(_transport_of(ctx), _session_of(ctx))
        if decision.session_id:
            return decision.session_id
        if decision.reason:
            raise SessionBindingError(
                f"reason: {decision.reason} — a session was bound earlier, but "
                "this server process has served more than one client session, "
                "so the process-global fallback was withheld: a client that "
                "never bound must not inherit another client's notebook. Pass "
                "session_id and server_url explicitly on this client, or run "
                "the server over stdio where one process serves one client.",
                reason=decision.reason,
            )

    raise ValueError(
        "No session_id provided and no active session bound for this call. "
        "Run list_active_notebooks() first to discover and auto-bind a session, "
        "or use set_active_session(session_id, server_url=...) to bind one "
        "explicitly. A binding made earlier reaches a later call either through "
        "this MCP session's state (for a client that keeps one MCP session "
        "across calls — an mcp-SDK client does) or, while this server process "
        "serves a single client, through the process-global fallback. Over "
        "HTTP/SSE, once a second client session is observed, that fallback is "
        "withheld and argument-less calls fail with reason: binding_ambiguous."
    )


async def resolve_server_url(
    server_url: str,
    ctx: Context | None = None,
) -> str:
    """Resolve server_url from explicit value or bound state.

    Also consults the process-global fallback under the same scope as
    ``resolve_session_id``. Returns the server_url to use. Raises
    ``SessionBindingError`` with ``reason="binding_ambiguous"`` when a binding
    exists but this process has served more than one client, and ``ValueError``
    when no server_url is available.
    """
    if server_url:
        record_client_session(_transport_of(ctx), _session_of(ctx))
        return server_url

    if ctx is not None:
        record_client_session(_transport_of(ctx), _session_of(ctx))
        bound = await ctx.get_state(_SERVER_URL_KEY)
        if bound:
            return bound

        decision = fallback_decision(_transport_of(ctx), _session_of(ctx))
        if decision.server_url:
            return decision.server_url
        if decision.reason:
            raise SessionBindingError(
                f"reason: {decision.reason} — a server_url was bound earlier, "
                "but this server process has served more than one client "
                "session, so the process-global fallback was withheld. Pass "
                "session_id and server_url explicitly on this client.",
                reason=decision.reason,
            )

    raise ValueError(
        "server_url is required. Use list_active_notebooks to discover servers "
        "and auto-bind one, or pass server_url explicitly."
    )


async def bind_active_session(
    session_id: str,
    ctx: Context,
    server_url: str = "",
) -> None:
    """Bind a session_id (and optionally its server_url) as the active session.

    Writes both the MCP-session state and the process-global fallback (see the
    module docstring for the isolation argument). The binder's own client
    session is recorded first so a later *different* client on a multi-client
    transport is detected and refused instead of inheriting this binding.
    """
    await ctx.set_state(_SESSION_KEY, session_id)
    if server_url:
        await ctx.set_state(_SERVER_URL_KEY, server_url)
    record_client_session(_transport_of(ctx), _session_of(ctx))
    store_fallback_binding(session_id, server_url=server_url)


async def set_active_session(
    session_id: str,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Set the active notebook session for subsequent tool calls.

    Binds a session_id (and optionally its server_url) so later calls can omit
    both `session_id` and `server_url`. The binding is written to two places:
    this call's MCP-session state (visible to a client that keeps one MCP
    session across calls — an `mcp`-SDK-based client does) and a
    **process-global fallback** consulted only when the MCP-session state has
    nothing bound. Over stdio one process serves exactly one client, so the
    fallback carries the binding to every later call, fastmcp's own `Client`
    (a fresh MCP session per request on the pinned fastmcp 4.0.3) included.
    Over HTTP/SSE one process serves many clients, so the fallback is served
    only while the process has seen a single client session; once a second
    distinct client session appears, argument-less calls are refused with
    `reason: binding_ambiguous` rather than risk handing one client another
    client's notebook — pass both arguments explicitly there. Use
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
        "binding_scope": (
            "this MCP session, plus a process-global fallback served only "
            "while this server process sees a single client"
        ),
        "message": (
            f"Active session bound to {session_id}. Later calls that share this "
            "MCP session may omit session_id and server_url. On stdio (one "
            "client per server process) the process-global fallback carries the "
            "binding to every later call — a client that starts a new MCP "
            "session per request (fastmcp's own Client does) included. Over "
            "HTTP/SSE the fallback is served only while this process has seen "
            "one client session; after a second client appears, argument-less "
            "calls are refused with reason: binding_ambiguous — pass both "
            "arguments explicitly on such a client."
        ),
    }
