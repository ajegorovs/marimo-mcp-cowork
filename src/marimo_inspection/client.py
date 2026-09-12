"""HTTP client for marimo's API.

Handles communication with a running marimo server via its HTTP API:
- GET /api/sessions (list active sessions)
- POST /api/kernel/execute (scratchpad execution)
- GET /sse (the frontend handshake that materializes a kernel session)
- POST /api/kernel/restart_session (kernel restart; skew-token protected)
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
Marimo-Session-Id header` for a bad or missing session header. A 403 (the
endpoint is served in `edit` mode only) is classified `edit_required`. A
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

    async def _post_restart(self, session_id: str, token: str) -> RestartOutcome:
        """One POST to the restart endpoint, classified from its status/body.

        Verified rows (marimo 0.24.0): 200 `{"success": true}`; 401
        `Missing server token` / `Invalid server token`; 500
        `Invalid session id: …` / `Missing Marimo-Session-Id header`; 403
        (`edit_required` — the endpoint is served in `edit` mode only); any
        other status is `restart_failed`; a transport error is
        `server_unreachable`. A 500 kills that HTTP connection, so the caller
        must verify on a fresh client.
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
        fresh client. A 403 is classified `edit_required` (the endpoint is
        served in `edit` mode only) and is not retried.

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
