"""Tool handlers for notebook listing and discovery."""

from __future__ import annotations

import logging

import httpx2
from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import bind_active_session

logger = logging.getLogger(__name__)

#: The honest placeholder for a field marimo does not publish. A session's
#: *provenance* (how it came to exist) and *owner* (which client holds it) are
#: not exposed by marimo 0.24's public API, so the tool reports them as unknown
#: rather than inferring them from the MCP binding. See the tool docstring.
_UNKNOWN = "unknown"


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

    **Binding is not ownership.** Every *session* entry in `notebooks` carries
    `provenance: "unknown"` and `owner: "unknown"`. marimo 0.24's public API
    (`GET /api/sessions`) exposes only each session's filename/path — no
    creator, no owning client, no creation time, and no per-session consumer
    count — so these values are placeholders that say "not knowable here", not
    claims that a session is unowned. Binding a session never makes the caller
    its owner, and a bound session may well be held by a human's browser page.

    **Browser-first for human co-work.** Launch the notebook and open it in a
    browser first, then call this tool and bind the session the page already
    holds. In marimo 0.24 **edit mode without an explicit `--session-ttl`** the
    page **becomes/holds the main consumer connection** for that session; the
    payload `owner` still reads `"unknown"` either way, because the public API
    does not publish who holds that role. Materializing a session yourself with
    the `/sse` handshake is for **headless, agent-only** work: without an
    explicit `--session-ttl` closing that stream leaves the session an **orphan**
    (it outlives the stream) until a later connection takes it over — though a
    **configured `--session-ttl` can reap that orphan**. A human who opens the
    page later must **take over** the session and **re-run the notebook** before
    its widgets respond. One session per server in that mode, so a second
    distinct client does not get its own kernel: it joins the same kernel as a
    **non-main, read-only consumer**, and a later reconnect can **re-key** the
    session id. The re-key behavior is real and separate from a page-vs-session
    divergence observed once; the divergence's cause is **not diagnosed**, and
    **neither the re-key nor any read-path explanation is confirmed as its
    cause** — treat a divergence as unexplained and prefer the browser-first
    order rather than assuming a mechanism. Run mode is out of scope here.

    Counting semantics for `summary`:
      - `total_notebooks` — the number of real sessions, i.e. `session_count`.
        A connection failure adds a sentinel row (see below) but is not a
        notebook, so it is deliberately **not** counted here. This is a
        **failure-path correction** only; on the normal path every row is a
        session and the value is unchanged.
      - `session_count` — sessions the queried servers report via
        `GET /api/sessions` (one per live kernel session there); always equal
        to `total_notebooks`.
      - `result_row_count` — `len(notebooks)`: every row, **including** a
        connection-failure sentinel. Compare it with `session_count` to see how
        many rows are sentinels.
      - `attached_client_count` — always `null`. marimo publishes no
        per-session client count, and the server-wide
        `/api/status/connections.active` value counts **sessions with an open
        main consumer**, not attached clients — so the tool reports
        unavailability rather than a wrong number.
      - `active_connections` — **DEPRECATED** compatibility alias equal to
        `session_count`. It was never a count of attached clients; read
        `session_count` instead. Kept so older callers keep working.
      - `servers_discovered` — how many servers were queried.

    A server that cannot be queried contributes one **sentinel row** in
    `notebooks` — its keys are exactly `name`, `path`, `session_id` (the literal
    `"error"`), `server_url`, and `error`. It is **not a session**, so it
    deliberately carries **no** `provenance`/`owner` (those describe a session),
    and it is counted only by `result_row_count`.

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
                "session_count": 0,
                "result_row_count": 0,
                "attached_client_count": None,
                "servers_discovered": 0,
                # DEPRECATED alias for `session_count` (never a client count).
                "active_connections": 0,
            },
            "notebooks": [],
            "next_steps": [
                "Start a marimo server with: marimo edit notebooks/your-notebook.py --no-token",
                "Open the notebook in a browser so the page creates the session and holds its main consumer connection, then call list_active_notebooks again (/sse materialization is for headless agent-only work)",
            ],
        }

    # Query each server for sessions
    all_notebooks = []
    session_count = 0

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
                        # marimo publishes neither field: "unknown" states that
                        # the answer is not available, not that the session is
                        # unowned. Binding is not ownership.
                        "provenance": _UNKNOWN,
                        "owner": _UNKNOWN,
                    }
                )
            session_count += len(sessions)
        except (httpx2.HTTPError, OSError, ValueError) as exc:
            logger.warning("Failed to query server %s: %s", url, exc)
            # NOT a session: a bare sentinel recording the failed query. It
            # deliberately omits `provenance`/`owner` (those describe a
            # session), is excluded from `session_count`/`total_notebooks`,
            # and is counted only by `result_row_count`.
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
            # Truthful session count; a connection-failure sentinel is a row,
            # not a notebook, so it never inflates this (failure-path fix).
            "total_notebooks": session_count,
            "session_count": session_count,
            # Rows in `notebooks`, including any failure sentinel.
            "result_row_count": len(all_notebooks),
            "attached_client_count": None,
            "servers_discovered": len(servers),
            # DEPRECATED compatibility alias for `session_count`; it has never
            # been an attached-client count, so read `session_count`.
            "active_connections": session_count,
        },
        "notebooks": all_notebooks,
        "next_steps": [
            "For human co-work, open the notebook in a browser first so the page becomes/holds the session's main consumer connection, then bind it here — /sse materialization is for headless agent-only work, and a human must take over and re-run such a session (a configured --session-ttl can reap an orphan)",
            "Use get_cell_map to get the structure of a notebook (session_id and server_url are omittable over stdio; over HTTP/SSE only while this process has served one client session — otherwise pass them explicitly, see set_active_session)",
            "Use get_errors to debug errors",
            "Pass session_id explicitly to target a different notebook",
        ],
    }
