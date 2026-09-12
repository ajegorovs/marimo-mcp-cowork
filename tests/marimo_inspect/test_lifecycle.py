"""Tests for the `restart_kernel` lifecycle tool (Wave 3).

Two tiers live here:

* the **client primitives** (`MarimoClient.skew_protection_token`,
  `.restart_session`, `.materialize_session`) exercised against a real httpx
  stack through `httpx2.MockTransport` — every classification row of the
  verified marimo 0.24 protocol is pinned;
* the **tool guard rails** of `restart_kernel` with the client replaced by a
  fake, so each refusal path is asserted without a kernel: a sessionless
  server, an unknown id, an unknown file, a failed restart, a failed
  re-materialization, a foreign single session that must never be adopted, the
  tracker reset, and the structured `session_required` refusal.

Protocol (marimo 0.24) described inline, not by pointer:
`POST /api/kernel/restart_session` needs both `Marimo-Session-Id` and
`Marimo-Server-Token` and only **closes** a session — a replacement kernel
exists only after a client reconnects through `GET /sse` until `kernel-ready`.
It answers 200 `{"success": true}`; 401 `Missing server token` / `Invalid
server token`; 500 `Invalid session id: …` / `Missing Marimo-Session-Id
header`; 403 when the endpoint is not served in this mode (`edit` only). A
transport error leaves the outcome unknown. The skew token is rendered into the
page HTML as `<marimo-server-token data-token="…">`.
"""

from __future__ import annotations

import httpx2
from fastmcp.client import Client

from marimo_inspection.client import (
    MarimoClient,
    RestartOutcome,
    SessionInfo,
    SkewTokenUnavailable,
)

SERVER = "http://stub"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(handler, url: str = SERVER) -> MarimoClient:
    """A real MarimoClient whose transport is a MockTransport."""
    client = MarimoClient(url)
    client._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return client


def _noop_handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
    return httpx2.Response(404)


class _RestartHandler:
    """Scripted marimo stub: an index page plus queued restart responses."""

    def __init__(
        self,
        *,
        token: str = "tok-abc123",
        restart_responses: list[tuple[int, dict | str]] | None = None,
        index_status: int = 200,
    ) -> None:
        self.token = token
        self.queue = list(restart_responses or [])
        self.index_status = index_status
        self.gets: list[str] = []
        self.posts: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            self.gets.append(str(request.url))
            if self.index_status != 200:
                return httpx2.Response(
                    self.index_status, headers={"location": "/auth/login?next=%2F"}
                )
            return httpx2.Response(
                200,
                text=(
                    f'<html><marimo-server-token data-token="{self.token}" hidden>'
                    "</html>"
                ),
            )
        self.posts.append(dict(request.headers))
        status, body = self.queue.pop(0) if self.queue else (200, {"success": True})
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)


# ---------------------------------------------------------------------------
# Client: skew-protection token
# ---------------------------------------------------------------------------


class TestSkewTokenScrape:
    """The token is read from the server's own page HTML (probe C5)."""

    async def test_reads_marker_from_index_html(self):
        handler = _RestartHandler(token="AAAA-testtoken-AAAAAA")
        client = _client(handler)
        try:
            token = await client.skew_protection_token()
        finally:
            await client.close()

        assert token == "AAAA-testtoken-AAAAAA"
        # One GET, follow_redirects=False so an auth redirect stays visible.
        assert handler.gets == [f"{SERVER}/"]

    async def test_caches_until_refresh(self):
        handler = _RestartHandler()
        client = _client(handler)
        try:
            first = await client.skew_protection_token()
            second = await client.skew_protection_token()
            refreshed = await client.skew_protection_token(refresh=True)
        finally:
            await client.close()

        assert first == second == refreshed
        # 1 initial scrape, 0 for the cached call, 1 for the explicit refresh.
        assert len(handler.gets) == 2

    async def test_missing_marker_is_unavailable(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, text="<html>no marker here</html>")

        client = _client(handler)
        try:
            try:
                await client.skew_protection_token()
            except SkewTokenUnavailable as exc:
                assert "token" in str(exc)
            else:  # pragma: no cover - the assertion below is the contract
                raise AssertionError("an empty page must not yield a token")
        finally:
            await client.close()

    async def test_empty_data_token_is_unavailable(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, text='<marimo-server-token data-token="" hidden>'
            )

        client = _client(handler)
        try:
            try:
                await client.skew_protection_token()
            except SkewTokenUnavailable:
                pass
            else:  # pragma: no cover
                raise AssertionError("an empty token must never be sent")
        finally:
            await client.close()

    async def test_auth_redirect_is_unavailable(self):
        """A non-200 page yields no token (never followed into a login page)."""
        handler = _RestartHandler(index_status=303)
        client = _client(handler)
        try:
            try:
                await client.skew_protection_token()
            except SkewTokenUnavailable as exc:
                assert "303" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("a redirect carries no token")
        finally:
            await client.close()
        # The redirect was seen, never followed into an auth page.
        assert handler.gets == [f"{SERVER}/"]

    async def test_fingerprint_is_opaque_and_stable(self):
        handler = _RestartHandler(token="AAAA-testtoken-AAAAAA")
        client = _client(handler)
        try:
            fingerprint = await client.skew_token_fingerprint()
        finally:
            await client.close()

        assert fingerprint
        assert "AAAA-testtoken-AAAAAA" not in fingerprint
        assert fingerprint == await _fingerprint_of("AAAA-testtoken-AAAAAA")


async def _fingerprint_of(token: str) -> str:
    """The fingerprint a second client computes for the same token."""
    handler = _RestartHandler(token=token)
    client = _client(handler)
    try:
        return await client.skew_token_fingerprint()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Client: restart_session classification
# ---------------------------------------------------------------------------


class TestRestartSessionClassification:
    """Every verified status/body pair maps to its documented reason."""

    async def test_success(self):
        handler = _RestartHandler(restart_responses=[(200, {"success": True})])
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is True
        assert outcome.reason == ""
        assert outcome.status_code == 200
        assert outcome.token_used is True
        assert outcome.token_fingerprint
        # Both required headers were sent.
        headers = {k.lower(): v for k, v in handler.posts[0].items()}
        assert headers["marimo-session-id"] == "sid-1"
        assert headers["marimo-server-token"] == "tok-abc123"

    async def test_missing_token_refused(self):
        """No scrapable token and a 401 → skew_token_unavailable."""
        handler = _RestartHandler(
            restart_responses=[(401, {"error": "Missing server token"})]
        )
        handler.token = ""  # page carries an empty data-token
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "skew_token_unavailable"
        assert outcome.status_code == 401
        assert outcome.token_used is False

    async def test_invalid_token_refreshes_once_then_succeeds(self):
        handler = _RestartHandler(
            restart_responses=[
                (401, {"error": "Invalid server token"}),
                (200, {"success": True}),
            ]
        )
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is True
        assert outcome.token_refreshed is True
        assert len(handler.posts) == 2
        # The retry re-scraped: two index GETs for this restart.
        assert len(handler.gets) >= 2

    async def test_invalid_token_twice_is_refused(self):
        handler = _RestartHandler(
            restart_responses=[
                (401, {"error": "Invalid server token"}),
                (401, {"error": "Invalid server token"}),
            ]
        )
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "skew_token_invalid"
        assert outcome.token_refreshed is True
        # Exactly one retry — never a loop.
        assert len(handler.posts) == 2

    async def test_invalid_session_id_maps_to_not_found_without_retry(self):
        handler = _RestartHandler(
            restart_responses=[(500, {"detail": "Invalid session id: bogus"})]
        )
        client = _client(handler)
        try:
            outcome = await client.restart_session("bogus")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "session_not_found"
        assert outcome.status_code == 500
        # A 500 poisons that connection (probe C3) — never retried here.
        assert len(handler.posts) == 1

    async def test_missing_session_header_maps_to_its_own_reason(self):
        handler = _RestartHandler(
            restart_responses=[(500, {"detail": "Missing Marimo-Session-Id header"})]
        )
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.reason == "session_header_missing"

    async def test_unexpected_status_maps_to_restart_failed(self):
        handler = _RestartHandler(restart_responses=[(502, "bad gateway")])
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.reason == "restart_failed"
        assert outcome.status_code == 502

    async def test_forbidden_maps_to_edit_required(self):
        """A 403 means the endpoint is not served in this mode (`edit` only)."""
        handler = _RestartHandler(restart_responses=[(403, {"detail": "Forbidden"})])
        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "edit_required"
        assert outcome.status_code == 403
        # No retry: the mode is the problem, not the token.
        assert len(handler.posts) == 1

    async def test_transport_error_maps_to_unreachable(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.method == "GET":
                return httpx2.Response(200, text="<html></html>")
            raise httpx2.ConnectError("connection refused")

        client = _client(handler)
        try:
            outcome = await client.restart_session("sid-1")
        finally:
            await client.close()

        assert outcome.ok is False
        assert outcome.reason == "server_unreachable"


# ---------------------------------------------------------------------------
# Client: materialize_session
# ---------------------------------------------------------------------------


class TestMaterializeSession:
    """The `/sse` handshake is what actually spawns the replacement kernel."""

    async def test_kernel_ready_is_true(self):
        seen: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request.url.params.get("session_id", ""))
            return httpx2.Response(
                200,
                text=('event: kernel-ready\ndata: {"op": "kernel-ready"}\n\n'),
            )

        client = _client(handler)
        try:
            ok = await client.materialize_session("sid-1", "/tmp/nb.py")
        finally:
            await client.close()

        assert ok is True
        assert seen == ["sid-1"]

    async def test_non_200_is_false(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(500, text="nope")

        client = _client(handler)
        try:
            ok = await client.materialize_session("sid-1", "/tmp/nb.py")
        finally:
            await client.close()

        assert ok is False

    async def test_stream_without_kernel_ready_is_false(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, text="event: other\ndata: {}\n\n")

        client = _client(handler)
        try:
            ok = await client.materialize_session("sid-1", "/tmp/nb.py")
        finally:
            await client.close()

        assert ok is False


# ---------------------------------------------------------------------------
# Tool guard rails
# ---------------------------------------------------------------------------


class FakeContext:
    """In-memory stand-in for fastmcp.Context's state store."""

    def __init__(self, transport: str = "stdio", session_id: str = "mcp-1") -> None:
        self._state: dict[str, object] = {}
        self.transport = transport
        self.session_id = session_id
        self.infos: list[str] = []

    async def set_state(self, key: str, value: object, serializable: bool = True):
        self._state[key] = value

    async def get_state(self, key: str):
        return self._state.get(key)

    async def info(self, message: str) -> None:
        self.infos.append(message)


class FakeClient:
    """Scripted MarimoClient for the handler's two client phases."""

    def __init__(
        self,
        sessions: list[SessionInfo] | None = None,
        *,
        outcome: RestartOutcome | None = None,
        ready: bool = True,
        token_fingerprint: str = "fp-before",
        list_error: Exception | None = None,
        fingerprint_error: Exception | None = None,
    ) -> None:
        self._sessions = list(sessions or [])
        self.outcome = outcome or RestartOutcome(
            ok=True,
            status_code=200,
            token_used=True,
            token_fingerprint="fp-before",
        )
        self.ready = ready
        self.token_fingerprint = token_fingerprint
        self.list_error = list_error
        self.fingerprint_error = fingerprint_error
        self.restart_calls: list[str] = []
        self.materialized: list[tuple[str, str]] = []
        self.closed = False

    async def list_sessions(self) -> list[SessionInfo]:
        if self.list_error is not None:
            raise self.list_error
        return list(self._sessions)

    async def restart_session(self, session_id: str) -> RestartOutcome:
        self.restart_calls.append(session_id)
        return self.outcome

    async def materialize_session(self, session_id: str, file: str, **kwargs) -> bool:
        self.materialized.append((session_id, file))
        return self.ready

    async def skew_token_fingerprint(self, *, refresh: bool = False) -> str:
        if self.fingerprint_error is not None:
            raise self.fingerprint_error
        return self.token_fingerprint

    async def close(self) -> None:
        self.closed = True


def _session(session_id: str = "sid-1", file: str | None = "/tmp/nb.py") -> SessionInfo:
    return SessionInfo(session_id=session_id, file=file, basename="nb.py")


def _fast_poll(monkeypatch, attempts: int = 2, delay: float = 0.01) -> None:
    from marimo_inspection.tools import lifecycle

    monkeypatch.setattr(lifecycle, "REMATERIALIZE_POLL_ATTEMPTS", attempts)
    monkeypatch.setattr(lifecycle, "REMATERIALIZE_POLL_DELAY", delay)


def _patch_clients(pre: FakeClient, post: FakeClient):
    from unittest.mock import patch

    return patch(
        "marimo_inspection.tools.lifecycle.MarimoClient",
        side_effect=[pre, post],
    )


class TestRestartKernelGuardRails:
    """No refusal may report success or claim a state change it did not make."""

    async def test_no_session_id_and_no_binding_is_structured(self):
        from marimo_inspection.tools.lifecycle import restart_kernel

        result = await restart_kernel()

        assert result["status"] == "error"
        assert result["reason"] == "session_required"
        assert result["state_changed"] is False
        assert result["error"]

    async def test_binding_ambiguous_passes_through(self, monkeypatch):
        from marimo_inspection.tools import lifecycle
        from marimo_inspection.tools.session import SessionBindingError

        async def _raise(*args, **kwargs):
            raise SessionBindingError(
                "reason: binding_ambiguous", reason="binding_ambiguous"
            )

        monkeypatch.setattr(lifecycle, "resolve_session_id", _raise)

        result = await lifecycle.restart_kernel()

        assert result["status"] == "error"
        assert result["reason"] == "binding_ambiguous"
        assert result["state_changed"] is False

    async def test_sessionless_server_refuses(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([])
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "session_not_found"
        assert result["state_changed"] is False
        assert result["sessions_before"] == 0
        assert result["available_sessions"] == []
        # The restart endpoint was never called: nothing to close.
        assert pre.restart_calls == []

    async def test_unknown_session_id_refuses_and_lists_what_is_live(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session("other-id")])
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "session_not_found"
        assert result["state_changed"] is False
        assert result["sessions_before"] == 1
        assert result["available_sessions"] == [
            {"session_id": "other-id", "file": "/tmp/nb.py"}
        ]
        assert pre.restart_calls == []

    async def test_session_without_a_file_refuses(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session("sid-1", file=None)])
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "session_file_unknown"
        assert result["state_changed"] is False
        # The file is the /sse param; a guess would materialize the wrong notebook.
        assert pre.restart_calls == []

    async def test_unreachable_server_refuses(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(list_error=httpx2.ConnectError("refused"))
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "server_unreachable"
        assert result["state_changed"] is False
        assert result["error"]

    @staticmethod
    def _status_error(status: int) -> httpx2.HTTPStatusError:
        request = httpx2.Request("GET", f"{SERVER}/api/sessions")
        return httpx2.HTTPStatusError(
            f"{status} error", request=request, response=httpx2.Response(status)
        )

    async def test_auth_enabled_is_named_not_generic(self, monkeypatch):
        """marimo auth refuses the census itself — name that cause.

        Measured against a real auth-on server: `GET /api/sessions` answers 401
        to an unauthenticated client, so the pre-flight is where an
        auth-protected server is stopped. A generic `restart_failed` would hide
        the only actionable cause. (Whether the page is also gated so no token
        can be read is server-config dependent and is deliberately not asserted
        in the refusal.)
        """
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(list_error=self._status_error(401))
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "auth_required"
        assert result["state_changed"] is False
        assert result["restarted"] is False
        assert pre.restart_calls == []
        assert any("auth" in step.lower() for step in result["next_steps"])

    async def test_other_census_errors_stay_generic(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(list_error=self._status_error(503))
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "restart_failed"
        assert result["state_changed"] is False

    async def test_skew_token_unavailable_reports_guidance(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(
            [_session()],
            outcome=RestartOutcome(
                ok=False, reason="skew_token_unavailable", status_code=401
            ),
        )
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "skew_token_unavailable"
        assert result["state_changed"] is False
        assert result["restarted"] is False
        # The session was not closed: nothing to re-materialize.
        assert post.materialized == []
        assert any("auth" in step.lower() for step in result["next_steps"])

    async def test_failed_rematerialization_is_never_ok(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        post = FakeClient([], ready=False)
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "session_not_rematerialized"
        assert result["state_changed"] is True
        assert result["restarted"] is True
        assert result["re_materialized"] is False
        # The handshake was attempted with the session's own file.
        assert post.materialized == [("sid-1", "/tmp/nb.py")]
        assert result["sessions_after"] == 0

    async def test_server_left_sessionless_is_reported_as_such(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        post = FakeClient([], ready=True)
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "server_sessionless"
        assert result["status"] == "error"
        assert result["state_changed"] is True
        assert result["sessions_after"] == 0

    async def test_multiple_live_sessions_without_ours_is_not_rematerialized(
        self, monkeypatch
    ):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        post = FakeClient([_session("a"), _session("b")])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["reason"] == "session_not_rematerialized"
        assert result["sessions_after"] == 2

    async def test_foreign_single_session_is_never_adopted(self, monkeypatch):
        """A lone *different* id after the restart is not proof of ours.

        The tool re-materialized `sid-1`; if the census shows only `other-id`,
        that could be another client's session, so it must refuse — never adopt
        it, never report success, never re-bind onto it.
        """
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        post = FakeClient([_session("other-id")], ready=True)
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "session_not_rematerialized"
        assert result["re_materialized"] is False
        assert result["state_changed"] is True
        assert result["sessions_after"] == 1
        # The foreign id is not surfaced as a live handle to use.
        assert result.get("live_session_id") is None
        assert result["available_sessions"] == [
            {"session_id": "other-id", "file": "/tmp/nb.py"}
        ]

    async def test_post_transport_failure_is_unknown_not_unclosed(self, monkeypatch):
        """A failed POST has an unknown outcome — never "nothing was closed"."""
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(
            [_session()],
            outcome=RestartOutcome(
                ok=False, reason="server_unreachable", detail="connection refused"
            ),
        )
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "server_unreachable"
        # Unknown, not False: the request may have reached the server.
        assert result["state_changed"] is None
        assert result["restarted"] is None
        assert result["sessions_after"] is None
        assert "nothing was closed" not in result["message"].lower()
        assert "unknown" in result["message"].lower()
        # The kernel was never confirmed closed, so nothing is re-materialized.
        assert post.materialized == []

    async def test_edit_required_refusal_names_the_mode(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(
            [_session()],
            outcome=RestartOutcome(
                ok=False, reason="edit_required", status_code=403
            ),
        )
        post = FakeClient([])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "error"
        assert result["reason"] == "edit_required"
        assert result["state_changed"] is False
        assert result["restarted"] is False
        assert "edit" in result["message"].lower()
        assert post.materialized == []


class TestRestartKernelSuccess:
    """The success payload states exactly what happened, and what it cost."""

    async def _run_success(
        self, monkeypatch, post: FakeClient, ctx: FakeContext | None = None
    ):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        with _patch_clients(pre, post):
            return await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER, ctx=ctx
            ), pre

    async def test_payload_shape(self, monkeypatch):
        result, pre = await self._run_success(monkeypatch, FakeClient([_session()]))

        assert result["status"] == "ok"
        assert result["reason"] == ""
        assert result["session_id"] == "sid-1"
        assert result["live_session_id"] == "sid-1"
        assert result["server_url"] == SERVER
        assert result["session_file"] == "/tmp/nb.py"
        assert result["restarted"] is True
        assert result["re_materialized"] is True
        assert result["sessions_before"] == 1
        assert result["sessions_after"] == 1
        assert result["state_changed"] is True
        assert result["skew_token_source"] == "page_html"
        assert result["skew_token_rotated"] is False
        assert result["execution_state_reset"] is True
        assert result["widget_values_reset"] is True
        assert result["cell_ids_stable"] is False
        # The session id is point-in-time, and the payload says so.
        assert result["session_id_stable"] is False
        assert result["session_verification"] == "point_in_time"
        assert result["change_tracking_cleared"] is True
        assert result["server_process_preserved"] is True
        assert pre.restart_calls == ["sid-1"]
        assert result["next_steps"]

    async def test_payload_never_exposes_the_token(self, monkeypatch):
        post = FakeClient([_session()], token_fingerprint="fp-secret-digest")
        result, _ = await self._run_success(monkeypatch, post)

        blob = repr(result)
        assert "fp-secret-digest" not in blob
        assert "tok-" not in blob

    async def test_message_states_the_cost_and_point_in_time(self, monkeypatch):
        result, _ = await self._run_success(monkeypatch, FakeClient([_session()]))
        message = result["message"].lower()
        assert "kernel is new" in message
        assert "stale" in message
        assert "widget" in message
        # No durable promise: the id can be re-keyed / reaped later.
        assert "point-in-time" in message
        assert "ttl" in message
        assert any("point-in-time" in s.lower() for s in result["next_steps"])

    async def test_token_rotation_is_measured(self, monkeypatch):
        post = FakeClient([_session()], token_fingerprint="fp-rotated")
        result, _ = await self._run_success(monkeypatch, post)
        assert result["skew_token_rotated"] is True
        # A rotated token means the server process was NOT preserved.
        assert result["server_process_preserved"] is False

    async def test_token_not_required_is_unmeasured(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient(
            [_session()],
            outcome=RestartOutcome(ok=True, status_code=200, token_used=False),
        )
        post = FakeClient([_session()])
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "ok"
        assert result["skew_token_source"] == "not_required"
        # Skew was off: rotation/preservation were never measured.
        assert result["skew_token_rotated"] is None
        assert result["server_process_preserved"] is None

    async def test_unmeasurable_rotation_is_null_with_warning(self, monkeypatch):
        from marimo_inspection.tools import lifecycle

        _fast_poll(monkeypatch)
        pre = FakeClient([_session()])
        post = FakeClient(
            [_session()], fingerprint_error=SkewTokenUnavailable("page gated")
        )
        with _patch_clients(pre, post):
            result = await lifecycle.restart_kernel(
                session_id="sid-1", server_url=SERVER
            )

        assert result["status"] == "ok"
        assert result["skew_token_rotated"] is None
        assert result["server_process_preserved"] is None
        assert "could not be measured" in result["warning"]

    async def test_change_tracker_is_cleared(self, monkeypatch):
        from marimo_inspection.tools.change_tracking import (
            CellFingerprint,
            get_tracker,
        )

        tracker = get_tracker()
        tracker.record_cells("sid-1", {"cell-a": CellFingerprint(code_hash="h")})
        try:
            result, _ = await self._run_success(monkeypatch, FakeClient([_session()]))
            assert result["change_tracking_cleared"] is True
            # Cell ids are not a stable handle across a restart.
            assert tracker.get_cell_fingerprint("sid-1", "cell-a") is None
        finally:
            tracker.clear_session("sid-1")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRestartKernelRegistration:
    """The tool is advertised with its surface-wide annotations."""

    async def test_registered_with_annotations(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "restart_kernel")

            assert tool.description
            annotations = tool.annotations
            assert annotations is not None
            # Dumped with aliases: the same field names the server registers,
            # without the SDK-v2 deprecation shims for the old attributes.
            hints = annotations.model_dump(by_alias=True)
            assert hints["readOnlyHint"] is False
            assert hints["destructiveHint"] is True
            assert hints["idempotentHint"] is False
            assert hints["openWorldHint"] is False

    async def test_schema_accepts_optional_session_and_url(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "restart_kernel")
            props = tool.input_schema.get("properties", {})
            assert "session_id" in props
            assert "server_url" in props
            assert set(tool.input_schema.get("required", [])) <= {
                "session_id",
                "server_url",
            }

    async def test_description_teaches_trigger_cost_and_failures(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "restart_kernel")
            description = " ".join((tool.description or "").lower().split())

            # When it is warranted, and that a cell edit never needs it.
            assert "kernel" in description
            assert "cell edit" in description
            # What it costs.
            assert "stale" in description
            assert "widget" in description
            assert "needs_read" in description
            # The failure vocabulary, incl. the never-success-over-zero rule.
            assert "session_not_rematerialized" in description
            assert "server_sessionless" in description
            assert "skew_token_unavailable" in description
            assert "edit_required" in description
            # No durable-id promise: the id is point-in-time and may be reaped.
            assert "session_id_stable" in description
            assert "point-in-time" in description
            assert "ttl" in description
