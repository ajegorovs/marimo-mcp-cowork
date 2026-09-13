"""HTTP client for marimo's API.

Handles communication with a running marimo server via its HTTP API:
- GET /api/sessions (list active sessions)
- POST /api/kernel/execute (scratchpad execution)
- GET /sse (the frontend handshake that materializes a kernel session)
- POST /api/kernel/restart_session (kernel restart; skew-token protected)
- POST /api/kernel/instantiate (original-cell execution; skew-token protected)
- SSE stream parsing (execution output, kernel-ready)

Skew protection (the `Marimo-Server-Token` header)
--------------------------------------------------
marimo mounts a skew-protection middleware that requires
`Marimo-Server-Token == session_manager.skew_protection_token` on every POST
except `/auth/login`, `/api/kernel/execute` and `/ws*`. The middleware is
installed by default and is switched off when marimo runs with `--mcp` or
`--no-skew-protection`; `--no-token` disables **auth**, not skew protection.
The token is rendered into the app HTML
(`<marimo-server-token data-token="…">`), so an unauthenticated client can read
it with one `GET /` — the same value the frontend sends as
`Marimo-Server-Token`. Verified in-protocol against marimo 0.24.0: a 200
`GET /` carries the marker; `POST /api/kernel/restart_session` answers 200
`{"success": true}` with both headers, 401 `Missing server token` / `Invalid
server token` without a valid one, and 500 `Invalid session id: …` / `Missing
`Marimo-Session-Id header` for a bad or missing session header. A literal 403
(the endpoint is served in `edit` mode only) is classified `edit_required` —
kept defensively, because measured marimo 0.24.0 converts an API 403 into the
401 auth body rather than serving the 403, and a run-mode server is caught
earlier with `edit_scope_required` (see `marimo_inspection.tools.access`). A
restart only **closes** the session: a replacement kernel exists only after a
client reconnects through `GET /sse` (see
:meth:`MarimoClient.materialize_session`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Self

import httpx2 as httpx

logger = logging.getLogger(__name__)

#: The page marker marimo renders the skew-protection token into.
_SKEW_TOKEN_RE = re.compile(r'<marimo-server-token data-token="([^"]*)"')

#: The two headers `POST /api/kernel/restart_session` requires.
SKEW_TOKEN_HEADER = "Marimo-Server-Token"
SESSION_HEADER = "Marimo-Session-Id"

#: The kernel-restart endpoint (skew-protected; `edit` mode only).
RESTART_PATH = "/api/kernel/restart_session"

#: The frontend handshake that materializes (or reconnects) a session.
SSE_PATH = "/sse"

#: The original-cell execution endpoint. `/sse` materializes a session but runs
#: no cell; this POST is the second half of the frontend's connect sequence
#: (skew-token protected, `edit` scope only).
INSTANTIATE_PATH = "/api/kernel/instantiate"

#: The unauthenticated read-scope endpoint the auth/scope classifier probes
#: (see ``marimo_inspection.tools.access``). Measured on marimo 0.24.0: it
#: answers 200 on a run-mode server whose ``/api/sessions`` census is denied for
#: lack of edit scope, and 401 with the same auth body when the server is
#: auth-gated — which is what separates a scope denial from an auth gate.
VERSION_PATH = "/api/version"


class SkewTokenUnavailable(RuntimeError):
    """The server exposed no skew-protection token over HTTP.

    Raised when the served page carries no `<marimo-server-token data-token=…>`
    marker, or when it answers with a non-200 that is never followed. An auth
    gate can produce that, but this client does not assume every authed server
    redirects the page — it only reports that no marker arrived. Only meaningful
    when the middleware is mounted at all: with `--mcp` or
    `--no-skew-protection` the header is ignored.
    """


def _skew_token_fingerprint(token: str) -> str:
    """An opaque, comparable fingerprint of a skew-protection token.

    The raw token is a server-process secret. This digest is used only inside
    the client to compare a token across an operation (rotation); it is never
    put in a tool payload, a log line, or a refusal message — the payload
    carries the *measured* ``skew_token_rotated`` boolean instead.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def page_carries_skew_token(text: str) -> bool:
    """True when served HTML carries marimo's app-shell token marker.

    The marker (``<marimo-server-token data-token="…">``) is a *semantic* page
    fact: it is served by the notebook app and absent from the login page, so
    the auth/scope classifier can tell the two apart without guessing from a
    response header or a byte count.
    """
    return _SKEW_TOKEN_RE.search(text) is not None


@dataclass(frozen=True)
class ProbeResponse:
    """One read-scope probe's answer, never an exception.

    ``status_code`` is 0 when the request never produced an HTTP answer
    (``transport_failed`` true); otherwise the server's status. ``detail`` is
    the response body (truncated), which is what the classifier compares for
    the marimo API auth body.
    """

    status_code: int = 0
    detail: str = ""
    transport_failed: bool = False


@dataclass(frozen=True)
class RootPageResponse:
    """A raw ``GET /`` observation for the auth/scope classifier.

    Deliberately raw: the classifier reads the status, the (unfollowed)
    redirect ``location`` and the body markers. The redirect is *not* followed,
    because the 303's ``location`` is itself the evidence — following it would
    replace the marker with a login page and hide which of the two it was.
    ``detail`` carries the transport failure text when there was no answer.
    """

    status_code: int = 0
    body: str = ""
    location: str = ""
    transport_failed: bool = False
    detail: str = ""


@dataclass(frozen=True)
class RestartOutcome:
    """The classified result of `POST /api/kernel/restart_session`.

    ``reason`` is empty exactly when ``ok``. ``token_fingerprint`` (an opaque
    digest, never the token value) lets the caller measure token rotation after
    the restart without handling the secret; it stays inside this module and is
    never serialized into a tool payload.
    """

    ok: bool
    reason: str = ""
    status_code: int = 0
    detail: str = ""
    token_used: bool = False
    token_refreshed: bool = False
    token_fingerprint: str = ""


@dataclass(frozen=True)
class InstantiateOutcome:
    """The classified result of `POST /api/kernel/instantiate`.

    ``reason`` is empty exactly when ``ok``. A 200 is only ``ok`` when the
    server's own body reports ``success`` — the status alone is not taken as
    proof. ``token_used`` records whether a scraped skew-protection token was
    sent, ``token_refreshed`` whether the single retry after an
    `Invalid server token` used a re-scraped one.
    """

    ok: bool
    reason: str = ""
    status_code: int = 0
    detail: str = ""
    token_used: bool = False
    token_refreshed: bool = False


@dataclass
class SessionInfo:
    """Information about an active marimo session."""

    session_id: str
    file: str | None = None
    basename: str | None = None
    running_notebooks: int = 0


@dataclass
class ExecuteResult:
    """Result from scratchpad execution."""

    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    output: Any = None
    execution_count: int | None = None
    status: str = ""  # "ok" | "error"


class MarimoClient:
    """HTTP client for marimo's API."""

    def __init__(self, server_url: str) -> None:
        """Create a marimo client.

        Args:
            server_url: Base URL of the marimo server (without trailing slash).
        """
        self._url = server_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        #: The scraped skew-protection token (server-process scoped: stable
        #: across a kernel restart, rotated by a server relaunch).
        self._skew_token: str = ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def list_sessions(self) -> list[SessionInfo]:
        """List active sessions on the server."""
        client = await self._get_client()
        response = await client.get(f"{self._url}/api/sessions")
        response.raise_for_status()
        data = response.json()

        sessions: list[SessionInfo] = []
        # marimo /api/sessions returns {session_id: {filename, path, ...}, ...}
        for session_id, session_info in data.items():
            file_path = session_info.get("path", session_info.get("filename"))
            basename = None
            if file_path:
                from pathlib import Path

                basename = Path(file_path).name

            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    file=file_path,
                    basename=basename,
                )
            )
        return sessions

    async def resolve_session(
        self, file_path: str | None = None, session_id: str | None = None
    ) -> SessionInfo:
        """Resolve a session by file path or session ID.

        Args:
            file_path: Absolute path to the notebook file.
            session_id: Explicit session ID.

        Returns:
            SessionInfo for the matched session.

        Raises:
            ValueError: If no matching session is found.
        """
        sessions = await self.list_sessions()

        if session_id:
            for s in sessions:
                if s.session_id == session_id:
                    return s
            raise ValueError(f"Session {session_id} not found")

        if file_path:
            for s in sessions:
                if s.file == file_path:
                    return s
            # Try basename match
            from pathlib import Path

            basename = Path(file_path).name
            for s in sessions:
                if s.basename == basename:
                    return s
            raise ValueError(
                f"No session found for file {file_path}. "
                f"Sessions: {[s.file for s in sessions]}"
            )

        if len(sessions) == 1:
            return sessions[0]

        if not sessions:
            raise ValueError("No active sessions on server")

        raise ValueError(
            f"Multiple sessions found; specify file_path or session_id. "
            f"Sessions: {[(s.session_id, s.file) for s in sessions]}"
        )

    async def execute(
        self,
        session_id: str,
        code: str,
        timeout: float = 30.0,
    ) -> ExecuteResult:
        """Execute code in the marimo scratchpad.

        The scratchpad namespace is a shallow copy of kernel globals.
        New top-level bindings created by the code are discarded after execution.

        Args:
            session_id: The session ID to execute in.
            code: Python code to execute.
            timeout: Timeout in seconds for the execution.

        Returns:
            ExecuteResult with stdout, stderr, and status.
        """
        client = await self._get_client()

        body = {
            "code": code,
            "id": "scratch",
            "session_id": session_id,
        }

        headers = {SESSION_HEADER: session_id}

        # Use SSE streaming for the response
        async with client.stream(
            "POST",
            f"{self._url}/api/kernel/execute",
            json=body,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                text = await response.aread()
                raise RuntimeError(f"Execution failed: {response.status_code} {text}")

            result = ExecuteResult()
            current_event = ""
            async for line in response.aiter_lines():
                line = line.rstrip("\r\n")
                if not line:
                    continue

                # Parse SSE format: "event: <name>" and "data: <json>"
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("event:"):
                    current_event = line[6:]
                elif line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Marimo's execute endpoint uses event names, not type field
                    if current_event == "stdout":
                        result.stdout.append(data.get("data", ""))
                    elif current_event == "stderr":
                        result.stderr.append(data.get("data", ""))
                    elif current_event == "done":
                        result.status = "ok" if data.get("success") else "error"
                        result.output = data.get("output")
                    elif current_event == "output":
                        result.output = data.get("output")
                elif line.startswith("data:"):
                    data_str = line[5:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if current_event == "stdout":
                        result.stdout.append(data.get("data", ""))
                    elif current_event == "stderr":
                        result.stderr.append(data.get("data", ""))
                    elif current_event == "done":
                        result.status = "ok" if data.get("success") else "error"
                        result.output = data.get("output")
                    elif current_event == "output":
                        result.output = data.get("output")

            return result

    async def skew_protection_token(self, *, refresh: bool = False) -> str:
        """Scrape the server's skew-protection token from its page HTML.

        marimo renders ``<marimo-server-token data-token="…" hidden>`` into the
        served page, so an unauthenticated client can read it with one
        ``GET /`` — the same value the frontend sends as
        ``Marimo-Server-Token``. The token is server-process scoped: a kernel
        restart leaves it unchanged, a server relaunch rotates it, so it is
        cached per client and re-scraped on demand (``refresh=True``).

        Args:
            refresh: Re-scrape even when a token is already cached.

        Returns:
            The 22-character token string.

        Raises:
            SkewTokenUnavailable: The response was not 200, the page carried no
                marker, the marker was empty, or the server did not answer.
        """
        if self._skew_token and not refresh:
            return self._skew_token

        client = await self._get_client()
        try:
            # follow_redirects=False: an auth redirect must be visible as a
            # non-200 rather than silently followed into a login page.
            response = await client.get(f"{self._url}/", follow_redirects=False)
        except (httpx.TransportError, OSError) as exc:
            raise SkewTokenUnavailable(
                f"could not read the server page at {self._url}/: {exc}"
            ) from exc

        if response.status_code != 200:
            raise SkewTokenUnavailable(
                f"GET / answered {response.status_code}, not 200, so the page "
                "(and any token it carries) never arrived"
            )

        match = _SKEW_TOKEN_RE.search(response.text)
        if match is None or not match.group(1):
            raise SkewTokenUnavailable(
                "the served page carried no <marimo-server-token data-token=…> "
                "marker, so no token could be read"
            )

        self._skew_token = match.group(1)
        return self._skew_token

    async def skew_token_fingerprint(self, *, refresh: bool = False) -> str:
        """An opaque fingerprint of the current skew-protection token.

        Use it to compare the token before and after an operation (rotation)
        without the raw value ever entering a payload or a log.
        """
        return _skew_token_fingerprint(
            await self.skew_protection_token(refresh=refresh)
        )

    async def probe_version(self) -> ProbeResponse:
        """Read the unauthenticated read-scope endpoint, never raising.

        ``GET /api/version`` is the probe the auth/scope classifier builds on:
        on a run-mode server the session census is denied for lack of edit
        scope while this answers 200, so a 200 here proves the server is
        reachable and readable without auth. An auth-gated server denies it
        with the same body it uses for the census (401
        ``{"detail":"Authorization header required"}``). A 404/500 or a
        transport failure is reported as-is, never guessed at.
        """
        client = await self._get_client()
        try:
            response = await client.get(f"{self._url}{VERSION_PATH}")
        except (httpx.TransportError, OSError) as exc:
            return ProbeResponse(transport_failed=True, detail=str(exc))
        return ProbeResponse(
            status_code=response.status_code, detail=response.text[:400]
        )

    async def probe_root_page(self) -> RootPageResponse:
        """Fetch the served landing page for semantic classification.

        ``follow_redirects=False`` on purpose: an auth-enabled server answers
        ``303`` with ``location: /auth/login?…`` (measured on 0.24.0), and that
        redirect *is* the login-page evidence — following it would swap the
        app-shell marker for a login form and lose the distinction. A
        transport failure is reported, never raised.
        """
        client = await self._get_client()
        try:
            response = await client.get(f"{self._url}/", follow_redirects=False)
        except (httpx.TransportError, OSError) as exc:
            return RootPageResponse(transport_failed=True, detail=str(exc))
        return RootPageResponse(
            status_code=response.status_code,
            body=response.text[:20000],
            location=response.headers.get("location", ""),
        )

    async def _post_restart(self, session_id: str, token: str) -> RestartOutcome:
        """One POST to the restart endpoint, classified from its status/body.

        Verified rows (marimo 0.24.0): 200 `{"success": true}`; 401
        `Missing server token` / `Invalid server token`; 500
        `Invalid session id: …` / `Missing Marimo-Session-Id header`; a literal
        403 (`edit_required` — defensive: 0.24.0 converts an API 403 into the
        401 auth body); any other status is `restart_failed`; a transport error
        is `server_unreachable`. A 500 kills that HTTP connection, so the
        caller must verify on a fresh client.
        """
        headers = {SESSION_HEADER: session_id}
        if token:
            headers[SKEW_TOKEN_HEADER] = token

        client = await self._get_client()
        try:
            response = await client.post(
                f"{self._url}{RESTART_PATH}", json={}, headers=headers
            )
        except (httpx.TransportError, OSError) as exc:
            return RestartOutcome(
                ok=False, reason="server_unreachable", detail=str(exc)
            )

        fingerprint = _skew_token_fingerprint(token) if token else ""
        if response.status_code == 200:
            return RestartOutcome(
                ok=True,
                status_code=200,
                detail=response.text[:200],
                token_used=bool(token),
                token_fingerprint=fingerprint,
            )

        detail = response.text[:400]
        if response.status_code == 401:
            reason = (
                "skew_token_invalid"
                if "Invalid server token" in detail
                else "skew_token_unavailable"
            )
        elif response.status_code == 403:
            reason = "edit_required"
        elif response.status_code == 500:
            if "Invalid session id" in detail:
                reason = "session_not_found"
            elif "Missing Marimo-Session-Id header" in detail:
                reason = "session_header_missing"
            else:
                reason = "restart_failed"
        else:
            reason = "restart_failed"

        return RestartOutcome(
            ok=False,
            reason=reason,
            status_code=response.status_code,
            detail=detail,
            token_used=bool(token),
            token_fingerprint=fingerprint,
        )

    async def restart_session(self, session_id: str) -> RestartOutcome:
        """Close the session's kernel via `POST /api/kernel/restart_session`.

        Sends both required headers. The token is scraped first; if it cannot
        be read, the POST is still attempted without it (a server running with
        the middleware off accepts that) and a `401 Missing server token` is
        then classified as `skew_token_unavailable`. A `401 Invalid server
        token` (the server was relaunched and the token rotated) triggers
        exactly one re-scrape and retry. A 500 is classified rather than
        retried, because marimo poisons that HTTP connection — verify on a
        fresh client. A literal 403 is classified `edit_required` (defensive:
        the endpoint is served in `edit` mode only, but 0.24.0 serves an API
        403 as the 401 auth body instead, and a run-mode server is normally
        refused earlier with `edit_scope_required`) and is not retried.

        This endpoint only **closes** the session: no replacement kernel is
        spawned until a client reconnects through `/sse`, so a caller must
        re-materialize before reporting success.
        """
        token = ""
        scrape_error = ""
        try:
            token = await self.skew_protection_token()
        except SkewTokenUnavailable as exc:
            scrape_error = str(exc)

        outcome = await self._post_restart(session_id, token)
        if outcome.ok:
            return outcome

        if outcome.reason == "skew_token_invalid":
            try:
                token = await self.skew_protection_token(refresh=True)
            except SkewTokenUnavailable as exc:
                return replace(
                    outcome,
                    reason="skew_token_unavailable",
                    detail=f"{outcome.detail} (re-scrape failed: {exc})",
                    token_used=False,
                    token_fingerprint="",
                )
            retry = await self._post_restart(session_id, token)
            return replace(retry, token_refreshed=True)

        if outcome.reason == "skew_token_unavailable" and scrape_error:
            return replace(outcome, detail=f"{outcome.detail} ({scrape_error})")
        return outcome

    async def _post_instantiate(
        self,
        session_id: str,
        token: str,
        *,
        object_ids: list[str],
        values: list[Any],
        auto_run: bool,
    ) -> InstantiateOutcome:
        """One POST to the instantiate endpoint, classified from status/body.

        Verified rows (marimo 0.24.0, `POST /api/kernel/instantiate`): 200
        `{"success": true}`; 401 `{"error": "Missing server token"}` /
        `{"error": "Invalid server token"}` (the skew-protection middleware);
        500 `{"detail": "Invalid session id: …"}` /
        `{"detail": "Missing Marimo-Session-Id header"}`; 400
        `{"detail": "Object missing required field ``objectIds``"}` when the
        body omits a required field; a literal 403 is `edit_required`
        (defensive — the endpoint is `edit` scope only and measured 0.24.0
        serves an API 403 as the 401 auth body); any other status is
        `instantiate_failed`; a transport error is `server_unreachable`.

        The body uses the request model's camelCase wire names
        (`objectIds`, `values`, `autoRun`): marimo's msgspec struct renames its
        snake_case fields, and an unknown `auto_run` key is silently *ignored*
        — harmless only while marimo's own default for it is `True`.
        """
        headers = {SESSION_HEADER: session_id}
        if token:
            headers[SKEW_TOKEN_HEADER] = token

        body: dict[str, Any] = {
            "objectIds": list(object_ids),
            "values": list(values),
            "autoRun": auto_run,
        }

        client = await self._get_client()
        try:
            response = await client.post(
                f"{self._url}{INSTANTIATE_PATH}", json=body, headers=headers
            )
        except (httpx.TransportError, OSError) as exc:
            return InstantiateOutcome(
                ok=False, reason="server_unreachable", detail=str(exc)
            )

        if response.status_code == 200:
            try:
                success = response.json().get("success") is True
            except (ValueError, UnicodeDecodeError):
                success = False
            if success:
                return InstantiateOutcome(
                    ok=True,
                    status_code=200,
                    detail=response.text[:200],
                    token_used=bool(token),
                )
            return InstantiateOutcome(
                ok=False,
                reason="instantiate_failed",
                status_code=200,
                detail=(
                    "answered 200 but the body was not a success response: "
                    f"{response.text[:200]}"
                ),
                token_used=bool(token),
            )

        detail = response.text[:400]
        if response.status_code == 401:
            reason = (
                "skew_token_invalid"
                if "Invalid server token" in detail
                else "skew_token_unavailable"
            )
        elif response.status_code == 403:
            reason = "edit_required"
        elif response.status_code == 500:
            if "Invalid session id" in detail:
                reason = "session_not_found"
            elif "Missing Marimo-Session-Id header" in detail:
                reason = "session_header_missing"
            else:
                reason = "instantiate_failed"
        else:
            reason = "instantiate_failed"

        return InstantiateOutcome(
            ok=False,
            reason=reason,
            status_code=response.status_code,
            detail=detail,
            token_used=bool(token),
        )

    async def instantiate_notebook(
        self,
        session_id: str,
        *,
        object_ids: list[str] | None = None,
        values: list[Any] | None = None,
        auto_run: bool = True,
    ) -> InstantiateOutcome:
        """Execute a live session's own notebook cells (frontend instantiate).

        `GET /sse` materializes a kernel session but runs **no** cell; a
        frontend performs this second step. It scrapes the skew-protection
        token from `GET /` and POSTs `/api/kernel/instantiate` with that token
        as `Marimo-Server-Token` plus `Marimo-Session-Id`, body
        `{"objectIds": [], "values": [], "autoRun": true}` — `objectIds` and
        `values` are required by the request model (empty and equal length
        here), and `auto_run` defaults to True. Verified against marimo 0.24.0:
        the original file cells then genuinely execute.

        `--no-token` disables **auth**, not skew protection, so the header is
        required on a normal `--no-token` headless server; a server started
        with `--mcp` or `--no-skew-protection` ignores the value. If the token
        cannot be scraped, the POST is still attempted without it and a
        `401 Missing server token` is reported as `skew_token_unavailable`
        (with the scrape failure appended). A `401 Invalid server token`
        (server relaunched, token rotated) triggers exactly one re-scrape and
        retry.

        The session must already exist — call `materialize_session` first; this
        method never creates one. The kernel runs the cells asynchronously, so
        a 200 means only that the instantiate request was *accepted*: a caller
        that needs the executed state must poll the read tools until the cells
        leave `stale`.

        Args:
            session_id: Session id from `list_active_notebooks`.
            object_ids: UI element ids to set at instantiate time; must be the
                same length as `values`. Empty by default.
            values: Values for `object_ids`. Empty by default.
            auto_run: Run the cells after wiring the UI values (marimo's own
                default is True).

        Returns:
            InstantiateOutcome: `ok` only for a 200 whose body reports success.
        """
        object_ids = list(object_ids or [])
        values = list(values or [])

        token = ""
        scrape_error = ""
        try:
            token = await self.skew_protection_token()
        except SkewTokenUnavailable as exc:
            scrape_error = str(exc)

        outcome = await self._post_instantiate(
            session_id,
            token,
            object_ids=object_ids,
            values=values,
            auto_run=auto_run,
        )
        if outcome.ok:
            return outcome

        if outcome.reason == "skew_token_invalid":
            try:
                token = await self.skew_protection_token(refresh=True)
            except SkewTokenUnavailable as exc:
                return replace(
                    outcome,
                    reason="skew_token_unavailable",
                    detail=f"{outcome.detail} (re-scrape failed: {exc})",
                    token_used=False,
                )
            retry = await self._post_instantiate(
                session_id,
                token,
                object_ids=object_ids,
                values=values,
                auto_run=auto_run,
            )
            return replace(retry, token_refreshed=True)

        if outcome.reason == "skew_token_unavailable" and scrape_error:
            return replace(outcome, detail=f"{outcome.detail} ({scrape_error})")
        return outcome

    async def materialize_session(
        self, session_id: str, file: str, *, timeout: float = 15.0
    ) -> bool:
        """Re-materialize a session through the frontend's `/sse` handshake.

        marimo spawns a kernel only when a client connects to
        ``GET /sse?session_id=<id>&file=<path>``; reading the stream until the
        ``kernel-ready`` event therefore proves a **live session** exists for
        that id. It does **not**, by itself, prove the kernel is *new* — the
        same handshake succeeds against a kernel that was never restarted. The
        reset contract is the caller's whole sequence: the ``POST
        /api/kernel/restart_session`` that closed the old kernel, followed by
        this handshake and the ``GET /api/sessions`` census. A ``kernel-ready``
        event checks the first half of a point-in-time condition, not a
        durable one.

        The stream is closed as soon as ``kernel-ready`` is seen; the session
        is left materialized and is subject to the server's configured session
        TTL afterwards.

        Returns:
            True when ``kernel-ready`` was observed, False on any other
            outcome (non-200, a stream that ended first, or a transport
            error). A False is never treated as success by the caller.
        """
        client = await self._get_client()
        params = {"session_id": session_id, "file": file}
        try:
            async with client.stream(
                "GET", f"{self._url}{SSE_PATH}", params=params, timeout=timeout
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    return False
                async for line in response.aiter_lines():
                    if "kernel-ready" in line:
                        return True
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("Session handshake for %s failed: %s", session_id, exc)
            return False
        return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
