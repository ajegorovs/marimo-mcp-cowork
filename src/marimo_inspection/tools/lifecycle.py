"""MCP tool handler for restarting a live marimo kernel (`restart_kernel`).

A kernel restart is the right instrument when the *kernel* is the problem and a
server relaunch is not: an imported package's source changed (module cache), a
new dependency was installed, the kernel is wedged, or globals are poisoned. It
preserves the server process — and therefore its skew-protection token, any
open frontend page and its websocket — whereas killing and relaunching the
server rotates that token and 401s every already-open page until a hard reload.

Verified marimo 0.24 protocol this handler builds on:

* ``POST /api/kernel/restart_session`` requires **both** ``Marimo-Session-Id``
  and ``Marimo-Server-Token``, and only **closes** the session: there is no
  replacement kernel until a client reconnects through the ``/sse`` handshake
  (a 403 means the endpoint is not served in this mode — it exists in ``edit``
  mode only). The endpoint alone answers 200 ``{"success": true}`` and leaves
  ``/api/sessions`` empty, so reporting success from the 200 alone is the
  documented failure mode; re-materialization is part of the contract here.
* The skew token is rendered into the page HTML as
  ``<marimo-server-token data-token="…">`` for an unauthenticated client; it is
  stable across a kernel restart and rotated only by a server relaunch. A server
  that gates the page (e.g. auth enabled) yields no token, so the restart is
  refused with ``reason: skew_token_unavailable`` and **nothing is closed**.
* A restart resets execution state: every cell is stale, kernel globals are
  gone, widget values are back at their constructor defaults, and a cell
  created in-session can come back under a **different cell id**.
* A ``kernel-ready`` event on ``/sse`` proves a live session, not a *new*
  kernel; the reset contract is the POST above **plus** that handshake and the
  census. Even then the result is point-in-time: a later browser reconnect can
  re-key the session, and a configured session TTL can reap an orphaned one.

The handler therefore never reports success it did not verify: the zero-session
guard runs before the POST, a session with the **expected id** must be live in
``/api/sessions`` after it (a foreign id is never adopted), and the local change
tracker is cleared because cell ids and hashes from before the restart are not
a valid basis for anything.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx2
from fastmcp import Context

from marimo_inspection.client import (
    MarimoClient,
    RestartOutcome,
    SessionInfo,
    SkewTokenUnavailable,
)
from marimo_inspection.tools.change_tracking import get_tracker
from marimo_inspection.tools.session import (
    SessionBindingError,
    resolve_server_url,
    resolve_session_id,
)

logger = logging.getLogger(__name__)

#: Re-materialization poll. The `/sse` handshake already waits for
#: `kernel-ready`; the census poll is the independent confirmation that the
#: server reports the session again before anything is called a success.
REMATERIALIZE_POLL_ATTEMPTS = 10
REMATERIALIZE_POLL_DELAY = 0.5

#: Failure-reason vocabulary (Wave 3).
REASON_SESSION_REQUIRED = "session_required"
REASON_SESSION_NOT_FOUND = "session_not_found"
REASON_SESSION_FILE_UNKNOWN = "session_file_unknown"
REASON_AUTH_REQUIRED = "auth_required"
REASON_SKEW_TOKEN_UNAVAILABLE = "skew_token_unavailable"
REASON_SKEW_TOKEN_INVALID = "skew_token_invalid"
REASON_EDIT_REQUIRED = "edit_required"
REASON_SERVER_UNREACHABLE = "server_unreachable"
REASON_RESTART_FAILED = "restart_failed"
REASON_SESSION_NOT_REMATERIALIZED = "session_not_rematerialized"
REASON_SERVER_SESSIONLESS = "server_sessionless"

_SUCCESS_MESSAGE = (
    "The kernel is new: every cell is stale until it is re-run, kernel "
    "globals are gone, widget values are back at their constructor defaults, "
    "and a cell created in-session may have come back under a different cell "
    "id. The notebook file survived. This result is point-in-time — a later "
    "browser reconnect can re-key the session and a configured session TTL "
    "can reap it — so re-read the notebook before acting on any cached id, "
    "and re-run list_active_notebooks if a later call answers "
    "`Invalid session id`."
)

_SUCCESS_NEXT_STEPS = [
    (
        "Re-read the notebook with get_cell_map: every cell is stale until "
        "re-run, and a cell created in-session may have been re-keyed to a "
        "different cell id — cached ids and code hashes are not valid now."
    ),
    (
        'Re-run what you need with run_cell; run_cell(mode="all") re-executes '
        "the whole document."
    ),
    (
        "Re-apply widget values with set_ui_value — they reset to their "
        "constructor defaults."
    ),
    (
        "Treat this success as point-in-time: a later browser reconnect can "
        "re-key the session and a configured session TTL can reap it. If a "
        "later call answers `Invalid session id`, re-run "
        "list_active_notebooks and re-bind before continuing."
    ),
    "The notebook file survived the restart; kernel globals did not.",
]

_NOT_STARTED = "the kernel was NOT restarted: nothing was closed and no state changed"


def _failure(
    reason: str,
    message: str,
    *,
    session_id: str = "",
    server_url: str = "",
    state_changed: bool | None = False,
    restarted: bool | None = False,
    re_materialized: bool = False,
    sessions_before: int | None = None,
    sessions_after: int | None = None,
    next_steps: list[str] | None = None,
    **extra: Any,
) -> dict:
    """Build the structured refusal every guard rail returns.

    The repo's shape is kept: a top-level ``error`` string *and* the structured
    ``status``/``reason`` fields, plus the session/server identity and an
    explicit ``state_changed`` so a caller can never read a refusal as a reset.
    ``state_changed``/``restarted`` are ``None`` when the outcome is genuinely
    unknown (a transport failure on the POST — the request may or may not have
    reached the server), never ``False`` by default in that case.
    """
    payload: dict[str, Any] = {
        "status": "error",
        "reason": reason,
        "error": message,
        "message": message,
        "session_id": session_id,
        "server_url": server_url,
        "restarted": restarted,
        "re_materialized": re_materialized,
        "state_changed": state_changed,
        "sessions_before": sessions_before,
        "sessions_after": sessions_after,
    }
    payload.update(extra)
    payload["next_steps"] = list(next_steps or [])
    return payload


def _closed_without_live_session(
    reason: str,
    message: str,
    *,
    sid: str,
    url: str,
    sessions_before: list[SessionInfo],
    sessions_after: list[SessionInfo],
) -> dict:
    """Refuse a restart that closed the kernel but confirmed no live session.

    Every such refusal is identical except for its reason/message: the state
    *was* changed (`restarted`/`state_changed` true, `re_materialized` false),
    the caller is shown what is actually live, and it is told how to recover.
    """
    return _failure(
        reason,
        message,
        session_id=sid,
        server_url=url,
        state_changed=True,
        restarted=True,
        sessions_before=len(sessions_before),
        sessions_after=len(sessions_after),
        available_sessions=[
            {"session_id": s.session_id, "file": s.file} for s in sessions_after
        ],
        next_steps=[
            "Re-materialize by opening the notebook in a browser (or re-running the /sse handshake).",
            "Then call list_active_notebooks and re-read the notebook before acting.",
        ],
    )


async def _poll_for_live_session(
    client: MarimoClient,
    expected_id: str,
    *,
    attempts: int,
    delay: float,
) -> tuple[list[SessionInfo], str]:
    """Poll `/api/sessions` until the **expected** session id is live.

    Returns ``(sessions, live_session_id)``. ``live_session_id`` is the expected
    id exactly when it is live; otherwise it is empty and the caller must report
    a failure, never a success. A *different* live id is never adopted: this
    tool cannot prove a foreign single session is the one it re-materialized,
    and silently re-binding onto another client's session is the failure mode
    being refused here.
    """
    sessions: list[SessionInfo] = []
    for attempt in range(attempts):
        try:
            sessions = await client.list_sessions()
        except (httpx2.HTTPError, OSError, ValueError) as exc:
            logger.warning("Post-restart session census failed: %s", exc)
            sessions = []
        if expected_id in [s.session_id for s in sessions]:
            return sessions, expected_id
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    return sessions, ""


async def _measure_token_rotation(
    client: MarimoClient, outcome: RestartOutcome
) -> tuple[bool | None, str]:
    """Measure whether the skew token rotated (never infer it).

    Returns ``(rotated, warning)``. ``rotated`` is ``True``/``False`` only when
    a token was actually used *and* a fresh fingerprint could be read; when skew
    protection was off (no token was sent) or the re-scrape failed, it is
    ``None`` — unmeasured, not "unchanged". A ``rotation`` of ``True`` means the
    server process was relaunched, i.e. not preserved.
    """
    if not outcome.token_fingerprint:
        return None, ""
    try:
        fingerprint = await client.skew_token_fingerprint(refresh=True)
    except SkewTokenUnavailable as exc:
        return None, f"skew-token rotation could not be measured: {exc}"
    return fingerprint != outcome.token_fingerprint, ""


async def restart_kernel(
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Restart the live kernel of the active session, preserving the server.

    Closes the session's kernel (`POST /api/kernel/restart_session`) and
    **re-materializes** a replacement through the frontend's `/sse` handshake,
    so the window in which the server is at zero sessions is closed again. Use
    it only when the kernel itself is the problem: an imported package's source
    changed, a new dependency was installed, the kernel is wedged, or globals
    are poisoned. A cell edit never needs one — `edit_cell` + `run_cell` apply
    live, and `edit_cell` already serializes the edit back to the `.py`.

    What it costs (stated, not implied): all execution state is discarded —
    kernel globals, imported-module caches, and widget values (back to their
    constructor defaults) — every cell becomes `stale` until re-run, and a cell
    created in-session may come back under a **different cell id**. The local
    change tracker is cleared, so every cell reports `needs_read` again until
    re-read (`get_cell_data`), and `cell_ids_stable` is `false` in the payload
    because cached ids must not be reused. The notebook file on disk survives.

    A success is **point-in-time**, not a durable guarantee: `session_id_stable`
    is `false` and `session_verification` is `point_in_time`. The tool opens its
    own `/sse` stream, verifies the expected id in `/api/sessions`, and closes
    that stream — the re-materialized session is then an ordinary orphan, so a
    later browser reconnect can re-key it to a new id and the server's
    configured session TTL can reap it. If a later call answers
    `Invalid session id`, re-run `list_active_notebooks` and re-bind.

    The token the endpoint requires is read from the server's own page
    (`<marimo-server-token data-token="…">`, the same value the frontend sends)
    and is never exposed in the payload — only the *measured*
    `skew_token_rotated` boolean is. When the census itself is refused with 401
    the call is refused with `reason: auth_required` (an authenticated server
    may gate the page so this unauthenticated client cannot read a token either)
    and **nothing is closed**. A server whose API answers but whose page carries
    no token is refused with `reason: skew_token_unavailable`; a POST refused
    401 is reported with the same reason and states whether a token was tried.
    A token that became invalid (the server was relaunched) is re-scraped once
    and retried. A 403 from the endpoint is `reason: edit_required` (the
    endpoint is served in `edit` mode only).

    Failure contract — every refusal is `status: error` with a machine-readable
    `reason`, a top-level `error` string, and `state_changed`:

    - `session_required` / `binding_ambiguous` — no usable session to act on.
    - `session_not_found` — the id is not live (or the server has no session);
      nothing was closed. This is also the guard that refuses to run over a
      sessionless server.
    - `session_file_unknown` — the live session reports no notebook path, so
      the `/sse` handshake has no `file` to hand back; nothing was closed.
    - `auth_required` — the server requires marimo auth, so even the session
      census is refused; nothing was closed.
    - `skew_token_unavailable` / `skew_token_invalid` / `edit_required` /
      `restart_failed` — the restart POST was refused and did not close the
      kernel.
    - `server_unreachable` — the POST's transport failed, so `state_changed` and
      `restarted` are `null`: the request may or may not have reached the server
      and a kernel may or may not have been closed. Verify before retrying.
    - `session_not_rematerialized` / `server_sessionless` — the kernel *was*
      closed (`state_changed: true`) but a live session with the expected id
      could not be confirmed afterwards. A different live id is **never**
      adopted, so this is never reported as success; re-materialize a session
      (open the notebook in a browser) before calling other tools.

    On success the payload reports `restarted`, `re_materialized`,
    `live_session_id` (always the requested id — a different one is refused),
    `sessions_before` / `sessions_after`, `skew_token_source` (`page_html` or
    `not_required`), the measured `skew_token_rotated` (`null` when
    unmeasurable) and `server_process_preserved` (`null` when unmeasurable;
    `false` when the token was observed to rotate), `execution_state_reset`,
    `widget_values_reset`, `cell_ids_stable: false`, `session_id_stable: false`,
    `session_verification: "point_in_time"`, `change_tracking_cleared`, plus
    `next_steps`.

    Args:
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        The success payload above, or the structured refusal described above.
    """
    try:
        sid = await resolve_session_id(session_id, ctx)
        url = await resolve_server_url(server_url, ctx)
    except SessionBindingError as exc:
        return _failure(
            exc.reason, str(exc), session_id=session_id, server_url=server_url
        )
    except ValueError as exc:
        return _failure(
            REASON_SESSION_REQUIRED,
            f"reason: {REASON_SESSION_REQUIRED} — {exc}",
            session_id=session_id,
            server_url=server_url,
            next_steps=[
                "Use list_active_notebooks to discover and bind a live session.",
                "Or pass session_id and server_url explicitly.",
            ],
        )

    if ctx:
        await ctx.info(f"Restarting kernel of session {sid} on {url}...")

    # ── 1. Pre-flight: the zero-session guard. A sessionless server (or an
    # unknown id) is refused here so the 500 the endpoint would answer with is
    # never provoked, and no session is closed.
    client = MarimoClient(url)
    try:
        try:
            sessions_before = await client.list_sessions()
        except (httpx2.TransportError, OSError) as exc:
            return _failure(
                REASON_SERVER_UNREACHABLE,
                f"reason: {REASON_SERVER_UNREACHABLE} — {url} did not answer "
                f"GET /api/sessions ({exc}), so {_NOT_STARTED}.",
                session_id=sid,
                server_url=url,
                next_steps=[
                    "Check the marimo server is running and reachable at `server_url`.",
                    "Use list_active_notebooks to see which servers answer.",
                ],
            )
        except (httpx2.HTTPError, ValueError, AttributeError, TypeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                # marimo auth is on: an unauthenticated client cannot even
                # read the session census, so nothing here can work. Whether a
                # token is also unreachable depends on the server's page config,
                # so it is not asserted here.
                return _failure(
                    REASON_AUTH_REQUIRED,
                    f"reason: {REASON_AUTH_REQUIRED} — {url} answered "
                    f"GET /api/sessions with {status}: marimo auth is enabled, "
                    "so this unauthenticated client cannot enumerate the "
                    f"session. {_NOT_STARTED}.",
                    session_id=sid,
                    server_url=url,
                    next_steps=[
                        (
                            "Run the marimo server without auth (the "
                            "documented headless recipe: `marimo edit "
                            "<notebook> --no-token --headless`) so agent tooling "
                            "can reach it, then retry."
                        ),
                        "Or restart the server manually if auth must stay on.",
                    ],
                )
            return _failure(
                REASON_RESTART_FAILED,
                f"reason: {REASON_RESTART_FAILED} — {url} answered "
                f"GET /api/sessions with an error ({exc}), so {_NOT_STARTED}.",
                session_id=sid,
                server_url=url,
                next_steps=["Check the marimo server's health, then retry."],
            )

        target = next((s for s in sessions_before if s.session_id == sid), None)
        available = [
            {"session_id": s.session_id, "file": s.file} for s in sessions_before
        ]
        if target is None:
            if not sessions_before:
                message = (
                    f"reason: {REASON_SESSION_NOT_FOUND} — {url} reports no live "
                    f"session at all (0 sessions), so there is no kernel to "
                    f"restart and {_NOT_STARTED}."
                )
            else:
                message = (
                    f"reason: {REASON_SESSION_NOT_FOUND} — no live session on "
                    f"{url} reports session id {sid!r}, so {_NOT_STARTED}. "
                    f"Live sessions: {available}."
                )
            return _failure(
                REASON_SESSION_NOT_FOUND,
                message,
                session_id=sid,
                server_url=url,
                sessions_before=len(sessions_before),
                sessions_after=len(sessions_before),
                available_sessions=available,
                next_steps=[
                    "Use list_active_notebooks to list live session ids and their server_urls.",
                    (
                        "Pass an id exactly as that listing reports it — a "
                        "marimo `edit` server allows exactly one session per "
                        "server."
                    ),
                ],
            )

        file = target.file or ""
        if not file:
            return _failure(
                REASON_SESSION_FILE_UNKNOWN,
                f"reason: {REASON_SESSION_FILE_UNKNOWN} — session {sid!r} is "
                "live but reports no notebook path, and the /sse handshake "
                "needs that path to re-materialize the same document (without "
                f"it the default notebook would open), so {_NOT_STARTED}.",
                session_id=sid,
                server_url=url,
                sessions_before=len(sessions_before),
                sessions_after=len(sessions_before),
                next_steps=[
                    "Restart the server manually, or reconnect a frontend to the notebook.",
                ],
            )

        # ── 2. The restart POST. The outcome is classified, never guessed.
        outcome = await client.restart_session(sid)
    finally:
        await client.close()

    if not outcome.ok:
        return _restart_failure(outcome, sid, url, len(sessions_before), file)

    # ── 3. Re-materialize on a FRESH client: a 500 poisons the connection the
    # restart was attempted on, and the census must be read independently.
    fresh = MarimoClient(url)
    try:
        ready = await fresh.materialize_session(sid, file)
        sessions_after, live_id = await _poll_for_live_session(
            fresh,
            sid,
            attempts=REMATERIALIZE_POLL_ATTEMPTS,
            delay=REMATERIALIZE_POLL_DELAY,
        )
        rotated, rotation_warning = await _measure_token_rotation(fresh, outcome)
    finally:
        await fresh.close()

    if not ready:
        return _closed_without_live_session(
            REASON_SESSION_NOT_REMATERIALIZED,
            (
                f"reason: {REASON_SESSION_NOT_REMATERIALIZED} — the kernel of "
                f"session {sid!r} WAS closed (state_changed: true) but the "
                "re-materialization handshake (GET /sse) never reported "
                "`kernel-ready`, so no working kernel could be confirmed. This "
                "is NOT a success. The server may be at "
                f"{len(sessions_after)} session(s)."
            ),
            sid=sid,
            url=url,
            sessions_before=sessions_before,
            sessions_after=sessions_after,
        )

    if not live_id:
        if not sessions_after:
            reason = REASON_SERVER_SESSIONLESS
            message = (
                f"reason: {REASON_SERVER_SESSIONLESS} — the kernel was closed "
                "and NO session came back: the server is at zero sessions, so "
                "every later tool call on this session will fail with "
                "`Invalid session id`. This is NOT a success."
            )
        else:
            reason = REASON_SESSION_NOT_REMATERIALIZED
            message = (
                f"reason: {REASON_SESSION_NOT_REMATERIALIZED} — the kernel was "
                f"closed, but the server reports {len(sessions_after)} live "
                f"session(s) and none of them has the expected id {sid!r}, so "
                "the re-materialized session cannot be identified. This is NOT "
                "a success."
            )
        return _closed_without_live_session(
            reason,
            message,
            sid=sid,
            url=url,
            sessions_before=sessions_before,
            sessions_after=sessions_after,
        )

    # ── 4. Local state reset. Cell ids and code hashes from before the restart
    # are not a valid basis for change detection or the edit guard, and a cell
    # created in-session can be re-keyed, so dropping the session's entries is
    # the only safe direction (every cell then reports `needs_read`).
    tracker = get_tracker()
    tracker.clear_session(sid)

    # ── 5. Point-in-time, not durable. The expected id was live at census time,
    # but this tool then closes its own /sse stream, leaving the session an
    # ordinary orphan: a later browser reconnect can re-key it, and the server's
    # configured session TTL can reap it. The payload reports the id as it was
    # at verification time and says so, rather than promising durability.
    preserved = None if rotated is None else (not rotated)

    payload: dict[str, Any] = {
        "status": "ok",
        "reason": "",
        "session_id": sid,
        "live_session_id": live_id,
        "server_url": url,
        "session_file": file,
        "restarted": True,
        "re_materialized": True,
        "state_changed": True,
        "sessions_before": len(sessions_before),
        "sessions_after": len(sessions_after),
        "skew_token_source": "page_html" if outcome.token_used else "not_required",
        "skew_token_rotated": rotated,
        "execution_state_reset": True,
        "widget_values_reset": True,
        "cell_ids_stable": False,
        "session_id_stable": False,
        "session_verification": "point_in_time",
        "change_tracking_cleared": True,
        "server_process_preserved": preserved,
        "message": _SUCCESS_MESSAGE,
        "next_steps": list(_SUCCESS_NEXT_STEPS),
    }
    if rotation_warning:
        payload["warning"] = rotation_warning
    if ctx:
        await ctx.info(f"Kernel restarted; session {live_id} is live again")
    return payload


def _restart_failure(
    outcome: RestartOutcome,
    sid: str,
    url: str,
    sessions_before: int,
    file: str,
) -> dict:
    """Map a non-ok `RestartOutcome` onto the tool's refusal contract.

    A refusal proves *nothing was closed* only where the server answered and
    refused. A transport failure leaves the outcome unknown, so
    ``state_changed``/``restarted`` are returned as ``None`` there rather than
    a false "nothing was closed".
    """
    reason = outcome.reason or REASON_RESTART_FAILED
    status = outcome.status_code or "n/a"
    state_changed: bool | None = False

    if reason == REASON_SKEW_TOKEN_UNAVAILABLE:
        if outcome.token_used:
            auth_phrase = (
                "a skew token was read from the server's page HTML and sent, "
                "but the endpoint accepted no usable token (401)"
            )
        else:
            auth_phrase = (
                "no usable skew token was available: none could be read from "
                "the server's page HTML (the page must carry a "
                "`<marimo-server-token data-token=…>` marker, which a server "
                "that gates the page can deny an unauthenticated client)"
            )
        message = (
            f"reason: {REASON_SKEW_TOKEN_UNAVAILABLE} — {auth_phrase}, and the "
            "restart endpoint requires the `Marimo-Server-Token` header. "
            f"Nothing was closed; {_NOT_STARTED}."
        )
        next_steps = [
            (
                "Run the marimo server without auth (the documented headless "
                "recipe: `marimo edit <notebook> --no-token --headless`) so "
                "the page carries the token, then retry."
            ),
            "Or restart the server manually if auth must stay on.",
        ]
    elif reason == REASON_SKEW_TOKEN_INVALID:
        message = (
            f"reason: {REASON_SKEW_TOKEN_INVALID} — the server rejected the "
            "skew token even after re-reading it from the page HTML. That "
            "happens when the server process was relaunched underneath this "
            f"call (a relaunch rotates the token). Nothing was closed; "
            f"{_NOT_STARTED}."
        )
        next_steps = [
            "Use list_active_notebooks to rediscover the server and session ids.",
            "Retry restart_kernel against the current server.",
        ]
    elif reason == REASON_EDIT_REQUIRED:
        message = (
            f"reason: {REASON_EDIT_REQUIRED} — POST /api/kernel/restart_session "
            f"returned 403 for session {sid!r}. That endpoint is served only in "
            f"`edit` mode. Nothing was closed; {_NOT_STARTED}."
        )
        next_steps = [
            "Run the marimo server in `edit` mode so the restart endpoint is available.",
            "Use list_active_notebooks to confirm the session, then retry.",
        ]
    elif reason == REASON_SERVER_UNREACHABLE:
        state_changed = None
        message = (
            f"reason: {REASON_SERVER_UNREACHABLE} — {url} did not answer the "
            f"restart request ({outcome.detail}), so the outcome is UNKNOWN: "
            "the request may or may not have reached the server and a kernel "
            "may or may not have been closed. Verify with list_active_notebooks "
            "before retrying."
        )
        next_steps = [
            "Check the marimo server is running and reachable at `server_url`.",
            (
                "Use list_active_notebooks to see which server and session are "
                "live — do not assume nothing was closed."
            ),
        ]
    else:
        message = (
            f"reason: {reason} — POST /api/kernel/restart_session returned "
            f"{status} for session {sid!r} ({outcome.detail}). "
            f"Nothing was closed; {_NOT_STARTED}."
        )
        next_steps = [
            "Check the marimo server's health and the session id, then retry.",
            "Use list_active_notebooks to confirm the session is still live.",
        ]

    return _failure(
        reason,
        message,
        session_id=sid,
        server_url=url,
        state_changed=state_changed,
        restarted=state_changed,
        sessions_before=sessions_before,
        sessions_after=None if state_changed is None else sessions_before,
        session_file=file,
        next_steps=next_steps,
    )
