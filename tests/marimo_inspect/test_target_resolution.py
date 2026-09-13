"""Structured target resolution (Wave A) — fail-before contract tests.

Twelve handlers used to resolve ``session_id``/``server_url`` locally via
``resolve_session_id`` / ``resolve_server_url`` and then call
``MarimoClient.resolve_session`` directly. A mismatched explicit pair raised a
bare ``ValueError``, a missing target raised ``ValueError``, and transport/query
failures escaped as ``httpx2`` errors — none of them the structured refusal the
rest of the surface already speaks (``restart_kernel`` /
``set_active_session``). These regressions pin one shared contract for every one
of the twelve:

* a missing explicit/bound target is ``session_required``;
* ``SessionBindingError.reason`` passes through untouched
  (``binding_ambiguous`` included);
* an id absent from the selected server is ``session_not_found`` together with
  the ``available_sessions`` that *same* server truthfully reports;
* ``available_sessions_readable`` states whether that census was actually read:
  true for a successful census (an empty one included), false for every refusal
  built without one — so an empty list is never mistaken for "no sessions";
* an unreadable census (truncated JSON, invalid UTF-8, a non-object body, a
  malformed row) is ``server_query_failed``, **never** ``session_not_found``:
  absence is only ever concluded from a census that was successfully read;
* a transport failure is ``server_unreachable``;
* a non-auth HTTP/parse/type query failure is ``server_query_failed``;
* HTTP 401/403 is **classified** by the shared read-scope probe (Task 01), not
  propagated and not folded into ``server_query_failed``:
  ``edit_scope_required`` when ``GET /api/version`` is readable (the run-mode
  census denial), ``auth_required`` when the read-scope probe is denied with
  the same auth body, and ``session_census_denied`` when the probes cannot tell
  the two apart — each carrying ``read_scope_status_code``/``page_kind`` as its
  evidence and a false ``available_sessions_readable`` (a denied census was
  never read);
* no refusal runs a read or a write (``operation_ran: false``, and the real
  stub sees no POST / the mocked execute seam is never reached).

The session/server cases drive the real handler against a loopback HTTP stub
through the real ``MarimoClient`` (no mocks below the tool boundary); the
binding and mocked cases pin the resolver's own branches and the untouched
execution seam. The shared *pre-resolution* matrices (``session_required``,
``binding_ambiguous``) run one read and one mutation handler because the
inventory test proves all twelve take the same path; the access-denial matrix
keeps the same representative pair (the classification itself is fanned out in
``test_access.py``).
"""

from __future__ import annotations

import http.server
import importlib
import inspect
import json
import socket
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# The twelve affected handlers, with the required arguments for a valid call.
# ---------------------------------------------------------------------------

HANDLERS: list[tuple[str, str, dict]] = [
    ("get_cell_map", "marimo_inspection.tools.cells", {}),
    ("get_cell_data", "marimo_inspection.tools.cells", {}),
    ("get_cell_outputs", "marimo_inspection.tools.cells", {}),
    ("get_errors", "marimo_inspection.tools.errors", {}),
    ("get_variables", "marimo_inspection.tools.variables", {}),
    ("get_dependency_graph", "marimo_inspection.tools.dependency", {}),
    ("lint_notebook", "marimo_inspection.tools.lint", {}),
    ("create_cell", "marimo_inspection.tools.mutation", {"source": "x = 1"}),
    (
        "edit_cell",
        "marimo_inspection.tools.mutation",
        {"cell_id": "c1", "source": "x = 2"},
    ),
    ("run_cell", "marimo_inspection.tools.mutation", {"cell_id": "c1"}),
    ("delete_cell", "marimo_inspection.tools.mutation", {"cell_id": "c1"}),
    (
        "set_ui_value",
        "marimo_inspection.tools.ui",
        {"variable_name": "slider", "value": 1},
    ),
]

HANDLER_IDS = [name for name, _, _ in HANDLERS]

#: One read and one mutation stand in for the twelve where the refusal is
#: produced entirely *before* ``MarimoClient.resolve_session`` runs — by the
#: shared binding resolver (``session_required``, ``binding_ambiguous``) or by
#: the census status code (401/403). The inventory test below proves every one
#: of the twelve calls the same resolver, so fanning those matrices out twelve
#: ways buys the same coverage for ten times the loopback servers. The cases
#: that exercise ``resolve_session`` itself — mismatch/not-found, unreachable,
#: query failure, the mocked execution seam — keep full fan-out.
REPRESENTATIVE: list[tuple[str, str, dict]] = [
    ("get_cell_data", "marimo_inspection.tools.cells", {}),
    ("create_cell", "marimo_inspection.tools.mutation", {"source": "x = 1"}),
]

REPRESENTATIVE_IDS = [name for name, _, _ in REPRESENTATIVE]

#: Handlers deliberately NOT routed through the shared resolver in this wave.
EXCLUDED = ("list_active_notebooks", "set_active_session", "restart_kernel")

#: The advertised tool surface is fixed; this wave must not change it.
EXPECTED_TOOL_COUNT = 15


def _handler(name: str, module_path: str):
    """Import the handler function from its defining module."""
    return getattr(importlib.import_module(module_path), name)


async def _call(name: str, module_path: str, kwargs: dict, **overrides):
    """Call one handler with its valid required args plus the overrides."""
    handler = _handler(name, module_path)
    return await handler(**{**kwargs, **overrides})


# ---------------------------------------------------------------------------
# A real loopback HTTP stub for marimo's /api/sessions.
#
# Not a mock: the handler runs the real MarimoClient against this socket. The
# stub counts POSTs so a test can prove no kernel/execute call happened.
# ---------------------------------------------------------------------------


#: The exact body real marimo 0.24.0 answers for both an auth gate and an API
#: 403 (edit-scope) denial.
AUTH_BODY = b'{"detail":"Authorization header required"}'

#: A served app shell: its skew-token marker proves the page is the notebook app.
APP_SHELL_HTML = b'<html><marimo-server-token data-token="tok" hidden></html>'

#: The measured auth-on `GET /` answer: a 303 to the login form.
LOGIN_LOCATION = "/auth/login?next=%2F"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Serve GET /api/sessions, /api/version and / from a fixed recipe.

    Every POST is counted and refused, so a test can prove no kernel/execute
    call happened on a refusal.
    """

    @property
    def _stub(self) -> StubMarimoServer:
        return self.server  # type: ignore[return-value]

    def _respond(
        self,
        code: int,
        body: bytes,
        content_type: str,
        *,
        location: str = "",
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/sessions":
            self._stub.sessions_calls += 1
            if self._stub.sessions_status != 200:
                self._respond(
                    self._stub.sessions_status,
                    self._stub.sessions_status_body,
                    "application/json",
                )
                return
            self._respond(200, self._stub.sessions_payload(), "application/json")
            return
        if path == "/api/version":
            self._stub.version_calls += 1
            self._respond(
                self._stub.version_status,
                self._stub.version_body,
                "application/json"
                if self._stub.version_status == 200
                else "text/plain",
            )
            return
        if path == "/":
            self._stub.page_calls += 1
            self._respond(
                self._stub.page_status,
                self._stub.page_body,
                "text/html",
                location=self._stub.page_location,
            )
            return
        self._respond(404, b"stub: no such endpoint", "text/plain")

    def do_POST(self) -> None:
        # Any POST is a read (execute) or a write (mutation/UI) that must not
        # happen on a refusal.
        self._stub.post_calls += 1
        self._respond(500, b"STUB-POST-REACHED", "text/plain")

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request stderr noise."""


class StubMarimoServer(http.server.ThreadingHTTPServer):
    """A loopback stub serving the census plus its read-scope probes.

    One stub covers every census shape a test needs — status code plus either
    an advertised id set or a raw body — and, for the access-denial matrix, the
    two probes the classifier reads: ``GET /api/version`` (read scope) and
    ``GET /`` (semantic page markers).
    """

    daemon_threads = True

    def __init__(
        self,
        session_ids: tuple[str, ...] = (),
        sessions_status: int = 200,
        sessions_body: bytes | None = None,
        sessions_status_body: bytes = AUTH_BODY,
        version_status: int = 200,
        version_body: bytes = b"0.24.0",
        page_status: int = 404,
        page_body: bytes = b"stub: no such endpoint",
        page_location: str = "",
    ) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.session_ids = session_ids
        self.sessions_status = sessions_status
        #: Raw bytes for the census; ``None`` builds the documented JSON object
        #: from ``session_ids``. Lets a test serve a truncated, non-UTF-8 or
        #: non-object body at HTTP 200.
        self.sessions_body = sessions_body
        #: Body for a non-200 census (defaults to marimo's real denial body).
        self.sessions_status_body = sessions_status_body
        self.version_status = version_status
        self.version_body = version_body
        self.page_status = page_status
        self.page_body = page_body
        self.page_location = page_location
        self.sessions_calls = 0
        self.version_calls = 0
        self.page_calls = 0
        self.post_calls = 0

    def sessions_payload(self) -> bytes:
        """The exact ``/api/sessions`` body this stub answers with."""
        if self.sessions_body is not None:
            return self.sessions_body
        payload = {
            sid: {"path": f"/tmp/{sid}.py", "filename": f"{sid}.py"}
            for sid in self.session_ids
        }
        return json.dumps(payload).encode()

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"


#: A short poll interval: the default 0.5s would be paid by every test's
#: ``shutdown()``, which is the bulk of this module's wall time.
_STUB_POLL_INTERVAL = 0.05


@pytest.fixture
def stub_server():
    """Factory fixture: start one stub marimo server per call, stop after test."""
    started: list[StubMarimoServer] = []

    def _start(
        *session_ids: str,
        sessions_status: int = 200,
        sessions_body: bytes | None = None,
        **kwargs: object,
    ) -> StubMarimoServer:
        server = StubMarimoServer(
            tuple(session_ids),
            sessions_status=sessions_status,
            sessions_body=sessions_body,
            **kwargs,  # type: ignore[arg-type]
        )
        threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": _STUB_POLL_INTERVAL},
            daemon=True,
        ).start()
        started.append(server)
        return server

    yield _start

    for server in started:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    """Ask the OS for a free loopback port (nothing listens there)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Fake context for the binding-ambiguity branch (no real MCP transport here).
# ---------------------------------------------------------------------------


class ScopedContext:
    """Minimal context reporting the two scoping inputs the resolver reads."""

    def __init__(self, transport: str, session_id: str) -> None:
        self.transport = transport
        self.session_id = session_id
        self._state: dict[str, object] = {}
        self.infos: list[str] = []

    async def get_state(self, key: str) -> object | None:
        return self._state.get(key)

    async def set_state(
        self, key: str, value: object, serializable: bool = True
    ) -> None:
        self._state[key] = value

    async def info(self, message: str) -> None:
        self.infos.append(message)


@pytest.fixture(autouse=True)
def _reset_process_fallback():
    """Keep the process-global fallback from leaking between tests."""
    from marimo_inspection.tools.session import reset_fallback_state

    reset_fallback_state()
    yield
    reset_fallback_state()


def _assert_refusal(result: dict, reason: str, *, readable: bool = False) -> None:
    """Pin the common refusal shape for any structured target refusal.

    ``readable`` is the truthfulness flag for ``available_sessions``: it must be
    true only where a census was actually read (a successful empty census
    included) and false everywhere a refusal is built without one.
    """
    assert isinstance(result, dict), result
    assert result["status"] == "error", result
    assert result["reason"] == reason, result
    assert result["error"], result
    assert result["message"], result
    assert "nothing was read or written" in result["message"].lower(), result
    assert result["target_resolved"] is False, result
    assert result["operation_ran"] is False, result
    assert result["state_changed"] is False, result
    assert isinstance(result["available_sessions"], list), result
    assert result["available_sessions_readable"] is readable, result
    assert result["next_steps"], result


# ---------------------------------------------------------------------------
# Coverage: exactly the twelve handlers, and only those.
# ---------------------------------------------------------------------------


async def test_inventory_routes_exactly_the_twelve_handlers():
    """Every listed handler calls the shared resolver; the surface stays 15.

    Programmatic proof of coverage: the set of registered tools whose source
    calls the shared resolver must equal the twelve intended handlers, the
    three excluded tools must not call it, and the advertised surface must
    still be exactly 15 tools.
    """
    from marimo_inspection.server import create_server

    routed = set()
    for name, module_path, _ in HANDLERS:
        source = inspect.getsource(_handler(name, module_path))
        assert "resolve_target(" in source, f"{name} is not routed through the resolver"
        routed.add(name)

    assert routed == set(HANDLER_IDS)

    # The excluded tools keep their own paths.
    excluded_pairs = [
        ("list_active_notebooks", "marimo_inspection.tools.notebooks"),
        ("set_active_session", "marimo_inspection.tools.session"),
        ("restart_kernel", "marimo_inspection.tools.lifecycle"),
    ]
    for name, module_path in excluded_pairs:
        source = inspect.getsource(_handler(name, module_path))
        assert "resolve_target(" not in source, f"{name} must not be routed"

    from fastmcp.client import Client

    async with Client(transport=create_server()) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert len(names) == EXPECTED_TOOL_COUNT, sorted(names)
    assert set(HANDLER_IDS) <= names, sorted(names)


# ---------------------------------------------------------------------------
# 1. An id absent from the selected server -> session_not_found + census.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "module_path", "kwargs"), HANDLERS, ids=HANDLER_IDS)
async def test_mismatched_explicit_session_is_refused_with_truthful_census(
    name, module_path, kwargs, stub_server
):
    """A requested id the server does not report refuses truthfully.

    The stub advertises ``s_live``; every handler called with
    ``session_id="s_missing"`` must return ``session_not_found`` plus that
    server's real census (``available_sessions_readable: true``, because the
    census was actually read), and must not POST (no read/write ran).
    """
    stub = stub_server("s_live")

    result = await _call(
        name, module_path, kwargs, session_id="s_missing", server_url=stub.url
    )

    _assert_refusal(result, "session_not_found", readable=True)
    assert result["session_id"] == "s_missing", result
    assert result["server_url"] == stub.url, result
    assert result["available_sessions"] == [
        {"server_url": stub.url, "session_id": "s_live"}
    ], result
    assert stub.sessions_calls >= 1
    assert stub.post_calls == 0, f"{name} POSTed on a refusal"


# ---------------------------------------------------------------------------
# 2. No target at all -> session_required (never a bare ValueError).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "module_path", "kwargs"), REPRESENTATIVE, ids=REPRESENTATIVE_IDS
)
@pytest.mark.parametrize(
    ("session_id", "server_url"),
    [("", ""), ("s_x", "")],
    ids=["nothing-given", "server-url-missing"],
)
async def test_missing_target_is_session_required(
    name, module_path, kwargs, session_id, server_url
):
    """No explicit/bound target returns session_required, not ValueError."""
    result = await _call(
        name, module_path, kwargs, session_id=session_id, server_url=server_url
    )

    _assert_refusal(result, "session_required")
    assert "session_required" in result["message"]


# ---------------------------------------------------------------------------
# 3. Binding ambiguity passes through from SessionBindingError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "module_path", "kwargs"), REPRESENTATIVE, ids=REPRESENTATIVE_IDS
)
async def test_binding_ambiguity_reason_passes_through(name, module_path, kwargs):
    """reason: binding_ambiguous survives as the refusal reason."""
    from marimo_inspection.tools.session import (
        record_client_session,
        store_fallback_binding,
    )

    store_fallback_binding("s_bound", "http://127.0.0.1:9000")
    record_client_session("streamable-http", "session-a")
    ctx = ScopedContext("streamable-http", "session-b")

    result = await _call(
        name, module_path, kwargs, session_id="", server_url="", ctx=ctx
    )

    _assert_refusal(result, "binding_ambiguous")


# ---------------------------------------------------------------------------
# 4. Transport failure -> server_unreachable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "module_path", "kwargs"), HANDLERS, ids=HANDLER_IDS)
async def test_unreachable_server_is_server_unreachable(name, module_path, kwargs):
    """A server that does not answer is server_unreachable, not a raw error."""
    url = f"http://127.0.0.1:{_free_port()}"

    result = await _call(name, module_path, kwargs, session_id="s_x", server_url=url)

    _assert_refusal(result, "server_unreachable")
    assert result["server_url"] == url, result


# ---------------------------------------------------------------------------
# 5. Non-auth query failure -> server_query_failed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "module_path", "kwargs"), HANDLERS, ids=HANDLER_IDS)
async def test_query_failure_is_server_query_failed(
    name, module_path, kwargs, stub_server
):
    """An HTTP 500 on the session census is server_query_failed."""
    stub = stub_server("s_live", sessions_status=500)

    result = await _call(
        name, module_path, kwargs, session_id="s_live", server_url=stub.url
    )

    _assert_refusal(result, "server_query_failed")
    assert stub.post_calls == 0, f"{name} POSTed on a refusal"


# ---------------------------------------------------------------------------
# 6. Access denials are classified by a read-scope probe (Task 01), not raw.
#
# Pinned marimo 0.24.0 serves an API 403 as 401 {"detail":"Authorization
# header required"} and strips WWW-Authenticate, so a denied census cannot say
# by itself whether the server is auth-gated or the census merely needs edit
# scope. Every row below is a real server shape; the classifier rows are driven
# end-to-end through the real handler against the stub.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            "run_mode_readable",
            "edit_scope_required",
            id="run-mode-version-200",
        ),
        pytest.param(
            "auth_gate_same_body",
            "auth_required",
            id="auth-on-version-401-same-body",
        ),
        pytest.param(
            "ambiguous_app_shell",
            "edit_scope_required",
            id="ambiguous-page-app-shell",
        ),
        pytest.param(
            "ambiguous_login_page",
            "auth_required",
            id="ambiguous-page-login",
        ),
        pytest.param(
            "ambiguous_unreadable",
            "session_census_denied",
            id="ambiguous-both-probes-fail",
        ),
    ],
)
async def test_access_denials_are_classified_by_probe(row, expected, stub_server):
    """Each real denial shape maps to its classified reason, with no operation.

    The census is denied (401), the probe recipe decides the reason, and the
    refusal must be the common target-refusal envelope: the census was never
    read (`available_sessions` empty, `available_sessions_readable: false`),
    the probe evidence is reported, and nothing was POSTed.
    """
    recipes = {
        "run_mode_readable": {"version_status": 200},
        "auth_gate_same_body": {
            "version_status": 401,
            "version_body": AUTH_BODY,
        },
        "ambiguous_app_shell": {
            "version_status": 404,
            "page_status": 200,
            "page_body": APP_SHELL_HTML,
        },
        "ambiguous_login_page": {
            "version_status": 404,
            "page_status": 303,
            "page_location": LOGIN_LOCATION,
        },
        "ambiguous_unreadable": {"version_status": 404},
    }
    stub = stub_server(sessions_status=401, **recipes[row])

    for name, module_path, kwargs in REPRESENTATIVE:
        result = await _call(
            name, module_path, kwargs, session_id="s_live", server_url=stub.url
        )
        _assert_refusal(result, expected)
        assert result["server_url"] == stub.url, result
        assert result["available_sessions"] == [], result
        assert result["available_sessions_readable"] is False, result
        assert result["read_scope_status_code"] == recipes[row]["version_status"], (
            result
        )
        assert result["target_resolved"] is False, result
        # The reason must be actionable: edit-scope refusals name edit mode,
        # auth refusals name auth.
        if expected == "edit_scope_required":
            assert "edit" in result["message"].lower(), result
        if expected == "auth_required":
            assert "auth" in result["message"].lower(), result

    assert stub.post_calls == 0, "a refusal POSTed"
    # One denied census read per handler call, and no discriminating re-read:
    # the denial is classified from the probes, never by rereading the census.
    assert stub.sessions_calls == len(REPRESENTATIVE), stub.sessions_calls


async def test_run_mode_denial_is_not_reported_as_auth_required(stub_server):
    """The bug this task fixes: run mode must not be blamed on auth.

    A run-mode server answers the census 401 with the *same* auth body an
    auth-on server uses; only the readable read-scope endpoint separates them.
    Reporting ``auth_required`` there tells the user to authenticate, which
    cannot fix a missing edit scope.
    """
    stub = stub_server(sessions_status=401, version_status=200)

    result = await _call(
        "get_cell_data",
        "marimo_inspection.tools.cells",
        {},
        session_id="s_live",
        server_url=stub.url,
    )

    assert result["reason"] == "edit_scope_required", result
    assert result["reason"] != "auth_required"
    assert stub.version_calls == 1, stub.version_calls
    assert stub.page_calls == 0, "the readable probe already decided"


async def test_parameterized_denied_status_is_classified_not_propagated(stub_server):
    """A denied census is a payload now — no HTTPStatusError escapes a tool."""
    stub = stub_server(sessions_status=403, version_status=200)

    result = await _call(
        "edit_cell",
        "marimo_inspection.tools.mutation",
        {"cell_id": "c1", "source": "x = 2"},
        session_id="s_live",
        server_url=stub.url,
    )

    _assert_refusal(result, "edit_scope_required")
    assert result["error"], result
    assert stub.post_calls == 0


# ---------------------------------------------------------------------------
# 7. The execution seam is never reached on a refusal (mocked client).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "module_path", "kwargs"), HANDLERS, ids=HANDLER_IDS)
async def test_refusal_never_reaches_the_execution_seam(name, module_path, kwargs):
    """resolve_session's not-found failure refuses without calling execute.

    The mocked client is the per-module patch seam, so this proves the refusal
    happens before any scratchpad/mutation call for every handler, and that the
    census is read from the *same* client/server that refused.
    """
    module = importlib.import_module(module_path)
    url = "http://127.0.0.1:8090"
    instance = MagicMock(
        resolve_session=AsyncMock(
            side_effect=ValueError("Session s_missing not found")
        ),
        list_sessions=AsyncMock(
            return_value=[MagicMock(session_id="s_live", file="/t.py")]
        ),
        execute=AsyncMock(),
    )

    with patch.object(module, "MarimoClient", return_value=instance):
        result = await _call(
            name, module_path, kwargs, session_id="s_missing", server_url=url
        )

    _assert_refusal(result, "session_not_found", readable=True)
    assert result["available_sessions"] == [
        {"server_url": url, "session_id": "s_live"}
    ], result
    assert instance.execute.call_count == 0, f"{name} executed on a refusal"
    assert instance.resolve_session.await_count == 1


# ---------------------------------------------------------------------------
# 8. An unreadable census can never mean "the id is absent".
# ---------------------------------------------------------------------------

#: HTTP 200 census bodies that cannot be turned into sessions. A truncated JSON
#: body and non-UTF-8 bytes surface as ``ValueError`` from ``list_sessions`` —
#: the same type ``resolve_session`` raises for an absent id — while a valid but
#: non-object body and a non-mapping row surface as ``AttributeError``.
MALFORMED_CENSUS_BODIES = [
    pytest.param(b'{"s_live": {"path": "/tmp/s_live.py"', 2, id="truncated-json"),
    pytest.param(b'["not", "a", "session", "object"]', 1, id="non-object-json"),
    pytest.param(b'{"s_live": "not-a-row"}', 1, id="malformed-session-row"),
    pytest.param(b'\xff\xfe{"s_live": {}}', 2, id="invalid-utf8"),
]


@pytest.mark.parametrize(
    ("name", "module_path", "kwargs"), REPRESENTATIVE, ids=REPRESENTATIVE_IDS
)
@pytest.mark.parametrize(("body", "census_reads"), MALFORMED_CENSUS_BODIES)
async def test_unreadable_census_is_query_failure_not_not_found(
    name, module_path, kwargs, stub_server, body, census_reads
):
    """An unreadable census is server_query_failed, never session_not_found.

    ``resolve_session`` raises ``ValueError`` both for an absent id and for a
    body that cannot even be decoded, so the resolver must not read the second
    case as the first: it re-reads the *same* server (the ``census_reads``
    count includes that discriminating read) and, when that read fails too,
    refuses with ``server_query_failed`` and an explicitly unreadable census.
    """
    stub = stub_server("s_live", sessions_body=body)

    result = await _call(
        name, module_path, kwargs, session_id="s_missing", server_url=stub.url
    )

    _assert_refusal(result, "server_query_failed")
    assert result["server_url"] == stub.url, result
    assert result["available_sessions"] == [], result
    assert result["available_sessions_readable"] is False, result
    assert stub.sessions_calls == census_reads, result
    assert stub.post_calls == 0, f"{name} POSTed on a refusal"


# ---------------------------------------------------------------------------
# 9. A readable-but-empty census is still an honest not-found.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "module_path", "kwargs"), REPRESENTATIVE, ids=REPRESENTATIVE_IDS
)
async def test_empty_census_is_readable_and_still_not_found(
    name, module_path, kwargs, stub_server
):
    """A live server with zero sessions: readable census, honest absence.

    The census *was* read — ``available_sessions_readable`` is true — and it
    genuinely holds no sessions, so the requested id is absent (never a guess)
    and the census is an empty list rather than an unknown one.
    """
    stub = stub_server()

    result = await _call(
        name, module_path, kwargs, session_id="s_missing", server_url=stub.url
    )

    _assert_refusal(result, "session_not_found", readable=True)
    assert result["available_sessions"] == [], result
    assert stub.post_calls == 0, f"{name} POSTed on a refusal"
