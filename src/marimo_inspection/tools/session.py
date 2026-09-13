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

Validation before binding (T17)
-------------------------------
``set_active_session`` used to bind any string, so an invented session id
reported ``status: OK`` and every later call failed with "Session not found".
It now validates the id against *live* sessions (``GET /api/sessions``) before
it writes any state: with an explicit ``server_url`` that server must report
the exact id; without one, healthy registry servers are searched and the id
must match on exactly one of them. Exactly-duplicate discovered URLs are
queried once (a registry can hold several entries for one server), while
distinct endpoint strings — ``localhost`` vs ``127.0.0.1`` — are deliberately
*not* canonicalized: an id live on both is genuinely ambiguous. Refusals carry
a machine-readable ``reason`` (``invalid_session_id`` / ``session_not_found`` /
``session_ambiguous`` / ``server_unreachable`` / ``server_query_failed``) and
never create a binding; a validated id with no MCP context to bind refuses
with ``binding_context_unavailable`` rather than reporting success.
See ``lookup_session`` for the read-only validator the tool builds on.
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from fastmcp import Context

from marimo_inspection.client import MarimoClient, SessionInfo
from marimo_inspection.discovery import discover_servers
from marimo_inspection.tools.access import (
    ACCESS_REASONS,
    AccessProbe,
    access_next_steps,
    access_refusal_message,
    classify_denied_census,
    http_error_body,
    probe_evidence,
)

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Validation before binding (T17)
# ---------------------------------------------------------------------------

# Public, shared refusal-reason vocabulary for the targeting tools (Wave A).
# `session_not_found` / `server_unreachable` / `server_query_failed` are reused
# by the bind validator below (they are the same classes there); the private
# aliases keep the existing internal names without a second source of truth.
REASON_SESSION_REQUIRED = "session_required"
REASON_SESSION_NOT_FOUND = "session_not_found"
REASON_SERVER_UNREACHABLE = "server_unreachable"
REASON_SERVER_QUERY_FAILED = "server_query_failed"

_REASON_INVALID_SESSION_ID = "invalid_session_id"
_REASON_SESSION_NOT_FOUND = REASON_SESSION_NOT_FOUND
_REASON_SESSION_AMBIGUOUS = "session_ambiguous"
_REASON_SERVER_UNREACHABLE = REASON_SERVER_UNREACHABLE
_REASON_SERVER_QUERY_FAILED = REASON_SERVER_QUERY_FAILED
_REASON_BINDING_CONTEXT_UNAVAILABLE = "binding_context_unavailable"


@dataclass(frozen=True)
class _ServerQuery:
    """One server's answer to "which sessions are live on you?"."""

    url: str
    session_ids: tuple[str, ...] = ()
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SessionLookup:
    """Result of validating a session_id against live servers.

    ``server_url`` is set only when exactly one live server reported the id —
    the sole outcome that may be bound. Every other outcome carries a
    machine-readable ``reason`` and no ``server_url``.
    """

    server_url: str = ""
    source: str = ""  # "explicit" | "discovered"
    reason: str = ""
    matches: tuple[str, ...] = ()  # server urls that reported the id
    queried: tuple[str, ...] = ()  # server urls that answered
    considered: int = 0  # servers the search was supposed to cover
    failures: tuple[tuple[str, str], ...] = ()  # (url, failure reason)
    available: tuple[tuple[str, str], ...] = ()  # (server_url, session_id)
    detail: str = ""


async def _query_server(url: str) -> _ServerQuery:
    """Ask one server for its live sessions, classifying any failure.

    Read-only: nothing is written on either outcome. A transport failure is
    reported as ``server_unreachable`` and a non-auth HTTP/parse failure as
    ``server_query_failed``, so the caller can distinguish "server is down"
    from "server answered badly". A 401/403 is **not** read as a generic query
    failure: a census denial needs the read-scope probe to say whether the
    server is auth-gated or merely missing edit scope, so it is classified by
    ``classify_denied_census`` (``auth_required`` / ``edit_scope_required`` /
    ``session_census_denied``).
    """
    client = MarimoClient(url)
    try:
        sessions = await client.list_sessions()
    except httpx2.HTTPStatusError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", 0)
        if status in (401, 403):
            probe = await classify_denied_census(
                client,
                url,
                denied_status=status,
                denied_detail=http_error_body(exc),
            )
            return _ServerQuery(url=url, reason=probe.reason, detail=probe.detail)
        return _ServerQuery(
            url=url, reason=_REASON_SERVER_QUERY_FAILED, detail=str(exc)
        )
    except (httpx2.TransportError, OSError) as exc:
        return _ServerQuery(url=url, reason=_REASON_SERVER_UNREACHABLE, detail=str(exc))
    except (httpx2.HTTPError, ValueError, AttributeError, TypeError) as exc:
        return _ServerQuery(
            url=url, reason=_REASON_SERVER_QUERY_FAILED, detail=str(exc)
        )
    finally:
        await client.close()
    return _ServerQuery(url=url, session_ids=tuple(s.session_id for s in sessions))


def _failure_headline(failures: tuple[tuple[str, str], ...]) -> str:
    """Pick the reason that describes a run where nothing answered.

    When every surveyed server refused its census, an access denial is the
    headline rather than a generic query failure — but only when they *agree*:
    a mix of access and transport/query outcomes stays ``server_query_failed``
    so the reason is never picked from a subset.
    """
    reasons = {reason for _, reason in failures}
    if len(reasons) == 1:
        only = next(iter(reasons))
        if only in ACCESS_REASONS:
            return only
    if reasons == {_REASON_SERVER_UNREACHABLE}:
        return _REASON_SERVER_UNREACHABLE
    return _REASON_SERVER_QUERY_FAILED


def _dedupe_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Drop exactly-duplicate URLs, keeping first-seen order.

    Marimo's registry is one JSON file per server process, so one endpoint can
    legitimately appear several times. Querying it once keeps a single live
    server from being counted as several "matches" (which would loop a
    perfectly unambiguous bind into a false ``session_ambiguous``).

    Distinct strings are deliberately *not* canonicalized: ``localhost`` and
    ``127.0.0.1`` name different endpoints, and an id reported by both is
    genuinely ambiguous — the fail-closed answer is right there.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return tuple(unique)


async def lookup_session(session_id: str, server_url: str = "") -> SessionLookup:
    """Find which live marimo server(s) report ``session_id`` (read-only).

    With an explicit ``server_url`` only that server is queried. Otherwise the
    healthy servers discovered from the marimo registry are queried. Never
    binds, stores or mutates anything.

    Args:
        session_id: The session id to look for.
        server_url: Optional explicit server to query.

    Returns:
        A ``SessionLookup`` whose ``server_url`` is set when exactly one
        server reported the id, and whose ``reason`` explains any other
        outcome (``session_not_found``, ``session_ambiguous``,
        ``server_unreachable``, ``server_query_failed``).
    """
    source = "explicit" if server_url else "discovered"

    if server_url:
        urls: tuple[str, ...] = (server_url.rstrip("/"),)
    else:
        discovered = await discover_servers()
        urls = _dedupe_urls(
            tuple(server.url for server in discovered if server.healthy)
        )
        if not urls:
            return SessionLookup(
                source=source,
                reason=_REASON_SESSION_NOT_FOUND,
                detail=(
                    "no healthy marimo server was discovered, so the id could "
                    "not be validated against any live session"
                ),
            )

    queries = [await _query_server(url) for url in urls]
    matches = tuple(q.url for q in queries if session_id in q.session_ids)
    queried = tuple(q.url for q in queries if not q.reason)
    failures = tuple((q.url, q.reason) for q in queries if q.reason)
    available = tuple((q.url, sid) for q in queries for sid in q.session_ids)
    common: dict[str, Any] = {
        "source": source,
        "matches": matches,
        "queried": queried,
        "considered": len(urls),
        "failures": failures,
        "available": available,
    }

    if len(matches) == 1:
        return SessionLookup(server_url=matches[0], **common)
    if len(matches) > 1:
        return SessionLookup(
            reason=_REASON_SESSION_AMBIGUOUS,
            detail=(
                f"{len(matches)} servers report session id {session_id!r}: "
                f"{', '.join(matches)}"
            ),
            **common,
        )
    if not queried and failures:
        # Nothing answered: the query failure is the headline, not "not found".
        return SessionLookup(
            reason=_failure_headline(failures),
            detail=(
                f"{len(failures)} server(s) could not be queried, so the id "
                f"could not be validated: "
                + "; ".join(f"{url} ({reason})" for url, reason in failures)
            ),
            **common,
        )
    return SessionLookup(
        reason=_REASON_SESSION_NOT_FOUND,
        detail=(
            f"no live session on the {len(queried)} queried server(s) "
            f"reports session id {session_id!r}"
        ),
        **common,
    )


def _access_refusal(
    probe: AccessProbe,
    *,
    session_id: str,
    server_url: str,
) -> dict[str, Any]:
    """Build the shared target refusal for a classified access denial.

    Same envelope as every other target refusal (``target_resolved: false``,
    ``operation_ran: false``, ``state_changed: false``) — plus the probe
    evidence (``read_scope_status_code`` / ``page_kind``) so the reason is
    auditable. The census was denied, so it is not readable:
    ``available_sessions`` is empty and ``available_sessions_readable`` is
    false, never an empty list pretending to be a real census.
    """
    return _target_refusal(
        probe.reason,
        (
            f"{access_refusal_message(probe.reason, server_url, detail=probe.detail)} "
            f"{_OPERATION_NOT_RUN}"
        ),
        session_id=session_id,
        server_url=server_url,
        available_sessions=[],
        available_sessions_readable=False,
        next_steps=access_next_steps(probe.reason),
        **probe_evidence(probe),
    )


def _session_refusal(
    session_id: str,
    requested_url: str,
    lookup: SessionLookup,
) -> dict[str, Any]:
    """Build the structured, fail-closed refusal for an unvalidated bind."""
    if lookup.reason == _REASON_SESSION_AMBIGUOUS:
        message = (
            f"reason: {_REASON_SESSION_AMBIGUOUS} — session id {session_id!r} "
            f"is live on more than one server ({', '.join(lookup.matches)}), so "
            "the bind was refused: which one is meant cannot be guessed. Call "
            f"set_active_session({session_id!r}, server_url=<the one you "
            "mean>)."
        )
        next_steps = [
            (
                "Re-call set_active_session with an explicit server_url from "
                "`matching_servers`."
            ),
            "Use list_active_notebooks to see each session's notebook path.",
        ]
    elif lookup.reason in ACCESS_REASONS:
        message = access_refusal_message(
            lookup.reason, requested_url, detail=lookup.detail
        )
        next_steps = access_next_steps(lookup.reason)
    elif lookup.reason in (
        _REASON_SERVER_UNREACHABLE,
        _REASON_SERVER_QUERY_FAILED,
    ):
        message = (
            f"reason: {lookup.reason} — the session id {session_id!r} could "
            f"not be validated: {lookup.detail or 'the server did not answer'}. "
            "Nothing was bound."
        )
        next_steps = [
            "Check the server is running and reachable at `server_url`.",
            "Use list_active_notebooks to see which servers answer.",
        ]
    else:
        message = (
            f"reason: {_REASON_SESSION_NOT_FOUND} — no live marimo session "
            f"reports session id {session_id!r}, so nothing was bound. {lookup.detail}"
        )
        next_steps = [
            (
                "Use list_active_notebooks to list live session ids and their "
                "server_urls."
            ),
            (
                "Pass an id exactly as that listing reports it — never an "
                "invented one; a marimo `edit` server allows exactly one "
                "session per server."
            ),
        ]

    return {
        "status": "error",
        "reason": lookup.reason,
        "error": message,
        "message": message,
        "session_id": session_id,
        "server_url": requested_url,
        "bound": False,
        "state_changed": False,
        "matching_servers": list(lookup.matches),
        "servers_discovered": lookup.considered,
        "servers_queried": list(lookup.queried),
        "servers_failed": [
            {"server_url": url, "reason": reason} for url, reason in lookup.failures
        ],
        "available_sessions": [
            {"server_url": url, "session_id": sid} for url, sid in lookup.available
        ],
        "next_steps": next_steps,
    }


def _invalid_session_id_response(session_id: str, requested_url: str) -> dict[str, Any]:
    """Reject an empty session_id in the same structured shape as a refusal.

    An empty id can never name a live session, so it is refused up front —
    ``status: error`` / ``reason: invalid_session_id`` — instead of returning a
    bare error object the caller has to special-case.
    """
    message = (
        f"reason: {_REASON_INVALID_SESSION_ID} — session_id is required and "
        "must be a live session id; nothing was bound."
    )
    return {
        "status": "error",
        "reason": _REASON_INVALID_SESSION_ID,
        "error": "session_id is required",
        "message": message,
        "help": "Use list_active_notebooks to discover available sessions.",
        "session_id": session_id,
        "server_url": requested_url,
        "bound": False,
        "state_changed": False,
        "next_steps": [
            (
                "Use list_active_notebooks to list live session ids and their "
                "server_urls, then pass one of them."
            ),
        ],
    }


def _context_unavailable_refusal(session_id: str, validated_url: str) -> dict[str, Any]:
    """Refuse to claim a bind when there is no MCP session to write it to.

    The id was validated live (so it and its server are known), but without a
    ``Context`` there is no MCP-session state to store the binding in. Reporting
    ``status: OK`` here would tell the caller a binding exists that does not,
    which is exactly the T17 defect in a different guise — so it refuses.
    """
    message = (
        f"reason: {_REASON_BINDING_CONTEXT_UNAVAILABLE} — session id "
        f"{session_id!r} was validated live on {validated_url}, but this call "
        "has no MCP session context to hold the binding, so nothing was bound. "
        "Call set_active_session through the MCP server (where a context is "
        "provided), or pass session_id and server_url explicitly on every "
        "later call."
    )
    return {
        "status": "error",
        "reason": _REASON_BINDING_CONTEXT_UNAVAILABLE,
        "error": message,
        "message": message,
        "session_id": session_id,
        "server_url": validated_url,
        "validated": True,
        "bound": False,
        "state_changed": False,
        "next_steps": [
            (
                "Call set_active_session from an MCP tool call so it can write "
                "the binding."
            ),
            "Or pass session_id and server_url explicitly on each later call.",
        ],
    }


async def set_active_session(
    session_id: str,
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Set the active notebook session for subsequent tool calls.

    The id is **validated against live sessions before anything is bound**, so
    a successful call means the binding can actually reach the session — an
    invented id (one no server reports) is refused instead of being reported
    as bound, and every refusal leaves binding state untouched.

    Validation:

    * With an explicit `server_url`, that server's live sessions are queried
      (`GET /api/sessions`) and the id must match one of them exactly. This is
      the deterministic path: one validation request, then the bind.
    * Without `server_url`, healthy servers are discovered from the marimo
      server registry and their live sessions searched. Exactly one match binds
      both the id and that server's URL. No match returns `status: error` with
      `reason: session_not_found`. The same id live on more than one server
      returns `reason: session_ambiguous` and asks for an explicit `server_url`
      (which server is meant is never guessed).
    * A server that cannot be reached (`reason: server_unreachable`) or that
      answers with an error (`reason: server_query_failed`) is reported
      explicitly — a query failure is never treated as "not found", and no
      binding is created in any of these cases.
    * A census denied with HTTP 401/403 is classified, not blurred: the id
      cannot be validated, so the bind is refused with `reason:
      edit_scope_required` when a read-scope endpoint proves the server is
      readable (the run-mode case), `reason: auth_required` when the read-scope
      probe is denied with the same auth body, and `reason:
      session_census_denied` when the probes cannot tell the two apart — the
      same reasons the targeting tools and `restart_kernel` report.
    * A refusal reports `bound: false`, `state_changed: false`, the servers it
      queried (`servers_queried`) or failed on (`servers_failed`), and the
      sessions it did see (`available_sessions`), plus `next_steps`. An empty
      `session_id` is `reason: invalid_session_id`; a validated id with no MCP
      session context to bind to is `reason: binding_context_unavailable` —
      the tool never reports a binding it did not write.

    On success the response carries `active_session_id`, `active_server_url`
    (the URL that was queried and matched — never a guess), `server_url_source`
    (`explicit` or `discovered`), `validated: true`, and the discovery limits
    (`servers_discovered` / `servers_queried` / `servers_failed`) so a
    validation that could not cover every endpoint is visible.

    The binding itself is written to two places: this call's MCP-session state
    (visible to a client that keeps one MCP session across calls — an
    `mcp`-SDK-based client does) and a **process-global fallback** consulted
    only when the MCP-session state has nothing bound. Over stdio one process
    serves exactly one client, so the fallback carries the binding to every
    later call, fastmcp's own `Client` (a fresh MCP session per request on the
    pinned fastmcp 4.0.3) included. Over HTTP/SSE one process serves many
    clients, so the fallback is served only while the process has seen a single
    client session; once a second distinct client session appears,
    argument-less calls are refused with `reason: binding_ambiguous` rather
    than risk handing one client another client's notebook — pass both
    arguments explicitly there. Use `list_active_notebooks` first to discover
    available sessions and their IDs.

    Args:
        session_id: The session ID to set as active (must be reported by a
            live server's `GET /api/sessions`).
        server_url: Optional server URL. When given, only that server is
            queried and bound; when omitted, the URL is the one the discovered
            server that reported the id is reachable at.

    Returns:
        `status: "OK"` with the bound session ID and server URL, or
        `status: "error"` with a machine-readable `reason` and no state
        changed.
    """
    if not session_id:
        return _invalid_session_id_response(session_id, server_url)

    lookup = await lookup_session(session_id, server_url=server_url)
    if lookup.reason:
        if ctx:
            await ctx.info(
                f"set_active_session refused ({lookup.reason}); nothing bound"
            )
        return _session_refusal(session_id, server_url, lookup)

    resolved_url = lookup.server_url
    if ctx is None:
        # The id is live, but there is no MCP session state to bind it to.
        return _context_unavailable_refusal(session_id, resolved_url)

    await bind_active_session(session_id, ctx, server_url=resolved_url)
    await ctx.info(f"Active session set to {session_id}")

    return {
        "status": "OK",
        "active_session_id": session_id,
        "active_server_url": resolved_url,
        "server_url_source": lookup.source,
        "validated": True,
        "servers_discovered": lookup.considered,
        "servers_queried": list(lookup.queried),
        "servers_failed": [
            {"server_url": url, "reason": reason} for url, reason in lookup.failures
        ],
        "binding_scope": (
            "this MCP session, plus a process-global fallback served only "
            "while this server process sees a single client"
        ),
        "message": (
            f"Active session bound to {session_id} on {resolved_url}: the "
            "server's live sessions were queried first and the id matched "
            "exactly (an invented id is refused, never bound). Later calls "
            "that share this MCP session may omit session_id and server_url. "
            "On stdio (one client per server process) the process-global "
            "fallback carries the binding to every later call — a client that "
            "starts a new MCP session per request (fastmcp's own Client does) "
            "included. Over HTTP/SSE the fallback is served only while this "
            "process has seen one client session; after a second client "
            "appears, argument-less calls are refused with reason: "
            "binding_ambiguous — pass both arguments explicitly on such a "
            "client."
        ),
    }


# ---------------------------------------------------------------------------
# Structured target resolution for the targeting tools (Wave A)
# ---------------------------------------------------------------------------
#
# Every cell/session-targeting tool used to resolve session_id/server_url and
# then call MarimoClient.resolve_session itself. A mismatched explicit pair
# raised a bare ValueError, a missing target raised ValueError, and
# transport/query failures escaped as httpx2 errors — none of them the
# structured refusal the rest of the surface already speaks (`restart_kernel`,
# `set_active_session`). `resolve_target` centralizes exactly that step: it
# returns either the resolved (client, session) pair or one common refusal
# payload, and **every refusal is built before any read or write operation**.
#
# HTTP 401/403 is classified, not propagated and not read as a generic query
# failure. Pinned marimo 0.24.0 serves an API 403 as 401
# `{"detail":"Authorization header required"}` and strips WWW-Authenticate, so
# a denied census cannot say by itself whether the server is auth-gated or the
# census merely needs edit scope. `classify_denied_census` (tools/access.py)
# resolves it with a read-scope probe, and the refusal reason is
# `auth_required`, `edit_scope_required` or `session_census_denied`.


@dataclass(frozen=True)
class TargetResolution:
    """Outcome of resolving a tool's session/server target.

    Exactly one of ``refusal`` / (``client``, ``session``) is set: a non-None
    ``refusal`` means nothing was read or written and the caller must return it
    verbatim; otherwise the caller owns ``client`` and must use ``session``.
    """

    client: MarimoClient | None = None
    session: SessionInfo | None = None
    refusal: dict[str, Any] | None = None

    @property
    def refused(self) -> bool:
        """True when the caller must return ``refusal`` and do nothing else."""
        return self.refusal is not None

    def unwrap(self) -> tuple[MarimoClient, SessionInfo]:
        """The resolved ``(client, session)`` pair, or raise on a refusal.

        Callers must have already returned ``refusal``; this exists so the happy
        path is a plain non-optional unpack for both readers and type checkers.
        """
        if self.client is None or self.session is None:
            raise RuntimeError("resolve_target refused; return the refusal instead")
        return self.client, self.session


_OPERATION_NOT_RUN = "Nothing was read or written."


def _target_refusal(
    reason: str,
    message: str,
    *,
    session_id: str,
    server_url: str,
    available_sessions: list[dict[str, str]] | None = None,
    available_sessions_readable: bool = False,
    next_steps: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the one refusal payload every targeting tool returns.

    The envelope (``status``/``reason``/``target_resolved``/``operation_ran``/
    ``state_changed``/``next_steps``) follows the same structured-refusal
    conventions as ``restart_kernel``'s refusals, but the rows are this
    resolver's own ``{server_url, session_id}`` pair — ``restart_kernel``
    reports ``{session_id, file}`` rows for the kernel it just handled, so the
    two must not be read as one shape.

    ``available_sessions_readable`` is the truthfulness flag for the census:
    true **only** when this refusal followed a census that was successfully
    read (a successful but empty census included), false when there is no
    census at all (no target, a withheld binding, a transport or query
    failure). An empty ``available_sessions`` therefore means "this server
    reports no live sessions" exactly when this flag is true, and
    "unknown/unreadable" when it is false.
    """
    payload: dict[str, Any] = {
        "status": "error",
        "reason": reason,
        "error": message,
        "message": message,
        "session_id": session_id,
        "server_url": server_url,
        "target_resolved": False,
        "operation_ran": False,
        "state_changed": False,
        "available_sessions": list(available_sessions or []),
        "available_sessions_readable": available_sessions_readable,
        "next_steps": list(next_steps or []),
    }
    payload.update(extra)
    return payload


async def _close_quietly(client: MarimoClient) -> None:
    """Best-effort close of a client the resolver refused to hand off.

    A refusal must not leak the HTTP client it built. The close is tolerant on
    purpose: a test double's ``close`` may not be awaitable, and closing must
    never replace the refusal with a new exception.
    """
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:  # a close failure never masks the refusal
        logger.debug("Failed to close a refused client", exc_info=True)


async def _census_of(
    client: MarimoClient, url: str
) -> tuple[list[dict[str, str]], str]:
    """Read *that* client's server census, only to report it truthfully.

    Called when ``resolve_session`` reports the requested id absent: the same
    client/server is asked which sessions it does report, so the refusal carries
    the truth instead of a guess. Its outcome is also the discriminator between
    a real absence (the census reads fine and simply does not contain the id)
    and an unreadable census (the read fails — including a JSON decode error or
    invalid UTF-8, which ``resolve_session`` surfaces as the same ``ValueError``
    it raises for a missing id, so the type alone cannot tell them apart).

    Returns ``(rows, "")`` on a successful read — an empty census included —
    and ``([], error)`` on a failed one: never a different server and never an
    invented id.
    """
    try:
        sessions = await client.list_sessions()
    except (httpx2.HTTPError, OSError, ValueError, AttributeError, TypeError) as exc:
        return [], str(exc)
    return (
        [{"server_url": url, "session_id": s.session_id} for s in sessions],
        "",
    )


def _missing_target_refusal(
    session_id: str, server_url: str, message: str
) -> TargetResolution:
    """A no-target refusal: session_required, nothing attempted."""
    return TargetResolution(
        refusal=_target_refusal(
            REASON_SESSION_REQUIRED,
            message,
            session_id=session_id,
            server_url=server_url,
            next_steps=[
                "Use list_active_notebooks to discover and bind a live session.",
                "Or pass session_id and server_url explicitly on this call.",
            ],
        )
    )


async def resolve_target(
    session_id: str,
    server_url: str,
    *,
    ctx: Context | None = None,
    client_factory: Callable[[str], MarimoClient] = MarimoClient,
) -> TargetResolution:
    """Resolve a tool's target session/server, or return a common refusal.

    The single choke point for every cell/session-targeting tool. It resolves
    the binding pair (explicit value first, then the bound state / scoped
    process-global fallback), builds the caller-supplied client, and resolves
    the session on it. On any failure it returns a structured refusal instead of
    raising, so a target error is a payload rather than a tool-level exception.

    Refusal classes (all `status: error`, `operation_ran: false`):

    - `session_required` — no explicit or bound `session_id`/`server_url`.
    - `binding_ambiguous` (or any other `SessionBindingError.reason`) — passed
      through unchanged from the binding resolver.
    - `session_not_found` — the requested id is not live on the selected
      server; the same server's `available_sessions` census is included, with
      `available_sessions_readable: true` because that census was read.
    - `server_unreachable` — the session census transport failed.
    - `server_query_failed` — the census answered with a non-auth HTTP error
      (a 500 included) or could not be read: an unreadable body (truncated
      JSON, invalid UTF-8, a non-object body) or a failing discriminating
      re-read. A census that cannot be read is **never** reported as "the
      session is absent": absence is concluded only from a census that was
      successfully read, so a malformed census body is a query failure, not
      `session_not_found`.
    - HTTP 401/403 is classified with a read-scope probe, never propagated and
      never folded into `server_query_failed`: `edit_scope_required` when
      `GET /api/version` is readable (the run-mode census denial — the user
      cannot fix it by authenticating), `auth_required` when the read-scope
      probe is denied with the same body, and `session_census_denied` when the
      probes cannot tell the two apart. All three carry
      `read_scope_status_code` and `page_kind` as their evidence.

    Every refusal carries `available_sessions` plus
    `available_sessions_readable`, which is true only when a census was
    successfully read (an empty successful census included) and false when the
    list is empty because nothing was read.

    The `client_factory` is a parameter so each tool module can keep passing its
    own `MarimoClient` import — the per-module patch seam existing tests rely on
    — rather than the resolver hard-coding one construction path.

    Args:
        session_id: The caller's explicit session id (empty to use the binding).
        server_url: The caller's explicit server URL (empty to use the binding).
        ctx: The FastMCP context carrying the binding state, if any.
        client_factory: Callable building the client for a resolved URL.

    Returns:
        A `TargetResolution`: `refusal` set (return it verbatim), or `client`
        and `session` set for the caller to use.
    """
    try:
        sid = await resolve_session_id(session_id, ctx)
    except SessionBindingError as exc:
        return TargetResolution(
            refusal=_target_refusal(
                exc.reason,
                f"{exc} {_OPERATION_NOT_RUN}",
                session_id=session_id,
                server_url=server_url,
                next_steps=[
                    "Pass session_id and server_url explicitly on this call.",
                    (
                        "Or run the server over stdio, where one process serves "
                        "one client and the process-global fallback is never "
                        "withheld."
                    ),
                ],
            )
        )
    except ValueError as exc:
        return _missing_target_refusal(
            session_id,
            server_url,
            (
                f"reason: {REASON_SESSION_REQUIRED} — no target session could "
                f"be resolved: {exc} {_OPERATION_NOT_RUN}"
            ),
        )

    try:
        url = await resolve_server_url(server_url, ctx)
    except SessionBindingError as exc:
        return TargetResolution(
            refusal=_target_refusal(
                exc.reason,
                f"{exc} {_OPERATION_NOT_RUN}",
                session_id=sid,
                server_url=server_url,
                next_steps=[
                    "Pass session_id and server_url explicitly on this call.",
                    (
                        "Or run the server over stdio, where one process serves "
                        "one client and the process-global fallback is never "
                        "withheld."
                    ),
                ],
            )
        )
    except ValueError as exc:
        return _missing_target_refusal(
            sid,
            server_url,
            (
                f"reason: {REASON_SESSION_REQUIRED} — no target session could "
                f"be resolved: {exc} {_OPERATION_NOT_RUN}"
            ),
        )

    client = client_factory(url)
    try:
        session = await client.resolve_session(session_id=sid)
    except httpx2.HTTPStatusError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", 0)
        if status in (401, 403):
            # A denied census cannot classify itself: pinned marimo 0.24.0
            # serves an API 403 as 401 with one auth body and strips
            # WWW-Authenticate, so a run-mode scope denial and a true auth gate
            # look identical here. The read-scope probe tells them apart before
            # the client is closed.
            probe = await classify_denied_census(
                client,
                url,
                denied_status=status,
                denied_detail=http_error_body(exc),
            )
            await _close_quietly(client)
            return TargetResolution(
                refusal=_access_refusal(probe, session_id=sid, server_url=url)
            )
        await _close_quietly(client)
        return TargetResolution(
            refusal=_target_refusal(
                REASON_SERVER_QUERY_FAILED,
                (
                    f"reason: {REASON_SERVER_QUERY_FAILED} — {url} answered "
                    f"GET /api/sessions with {status} ({exc}), so the requested "
                    f"session could not be resolved. {_OPERATION_NOT_RUN}"
                ),
                session_id=sid,
                server_url=url,
                next_steps=[
                    "Check the marimo server's health, then retry.",
                    "Use list_active_notebooks to see which servers answer.",
                ],
            )
        )
    except (httpx2.TransportError, OSError) as exc:
        await _close_quietly(client)
        return TargetResolution(
            refusal=_target_refusal(
                REASON_SERVER_UNREACHABLE,
                (
                    f"reason: {REASON_SERVER_UNREACHABLE} — {url} did not answer "
                    f"GET /api/sessions ({exc}), so the requested session could "
                    f"not be resolved. {_OPERATION_NOT_RUN}"
                ),
                session_id=sid,
                server_url=url,
                next_steps=[
                    "Check the marimo server is running and reachable at `server_url`.",
                    "Use list_active_notebooks to see which servers answer.",
                ],
            )
        )
    except ValueError as exc:
        # `resolve_session(session_id=…)` raises ValueError when the id is not
        # in this server's census — but an unreadable census raises ValueError
        # too (a JSON decode error *is* one, and so is invalid UTF-8). So the
        # absence may not be concluded here: the same client re-reads its own
        # census, and only a *successful* read may classify the miss.
        #   * census read -> the id really is absent from it: session_not_found
        #     with this server's real sessions (never another server's, never a
        #     guess).
        #   * census unreadable -> absence is unknown, so this is a query
        #     failure; an empty list is never reported as "no sessions". A
        #     transport failure of this second read lands in the same
        #     query-failure class (the census could not be read at all) and its
        #     error text is carried in the message.
        available, census_error = await _census_of(client, url)
        await _close_quietly(client)
        if census_error:
            return TargetResolution(
                refusal=_target_refusal(
                    REASON_SERVER_QUERY_FAILED,
                    (
                        f"reason: {REASON_SERVER_QUERY_FAILED} — the session "
                        f"census at {url} could not be read ({census_error}), so "
                        "whether the requested session is live there is "
                        "unknown: absence is never inferred from an unreadable "
                        f"census. {_OPERATION_NOT_RUN}"
                    ),
                    session_id=sid,
                    server_url=url,
                    next_steps=[
                        "Check the marimo server's health, then retry.",
                        "Use list_active_notebooks to see which servers answer.",
                    ],
                )
            )
        return TargetResolution(
            refusal=_target_refusal(
                REASON_SESSION_NOT_FOUND,
                (
                    f"reason: {REASON_SESSION_NOT_FOUND} — no live session on "
                    f"{url} reports session id {sid!r} ({exc}). {_OPERATION_NOT_RUN}"
                ),
                session_id=sid,
                server_url=url,
                available_sessions=available,
                available_sessions_readable=True,
                next_steps=[
                    (
                        "Use list_active_notebooks to list live session ids and "
                        "their server_urls."
                    ),
                    (
                        "Pass an id exactly as that listing reports it — never "
                        "an invented one."
                    ),
                ],
            )
        )
    except (httpx2.HTTPError, AttributeError, TypeError) as exc:
        await _close_quietly(client)
        return TargetResolution(
            refusal=_target_refusal(
                REASON_SERVER_QUERY_FAILED,
                (
                    f"reason: {REASON_SERVER_QUERY_FAILED} — the session census "
                    f"at {url} could not be used ({exc}), so the requested "
                    f"session could not be resolved. {_OPERATION_NOT_RUN}"
                ),
                session_id=sid,
                server_url=url,
                next_steps=[
                    "Check the marimo server's health, then retry.",
                    "Use list_active_notebooks to see which servers answer.",
                ],
            )
        )

    return TargetResolution(client=client, session=session)
