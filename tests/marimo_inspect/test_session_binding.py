"""Tests for session resolution, auto-bind and explicit binding.

Covers:

* the T2 fix — `list_active_notebooks(server_url=…)` must bind the server_url
  together with the session_id so that later tool calls can omit both, and an
  explicit `server_url` on a later call still wins;
* the T17 fix — `set_active_session` validates the id against live sessions
  before it mutates any binding state, so an invented id is refused instead of
  reported as bound;
* the H11 process-global fallback and its HTTP single-client scoping.

The T17 cases use a real HTTP stub for marimo's `/api/sessions` (and, for the
discovery path, a real temp server registry), so the tool runs the real
`MarimoClient`, `discover_servers` and socket stack — no mocks below the tool
boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_process_fallback():
    """Keep the process-global fallback from leaking between tests."""
    from marimo_inspection.tools.session import reset_fallback_state

    reset_fallback_state()
    yield
    reset_fallback_state()


class FakeContext:
    """In-memory stand-in for fastmcp.Context's state store."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}
        self.infos: list[str] = []

    async def set_state(
        self, key: str, value: object, serializable: bool = True
    ) -> None:
        self._state[key] = value

    async def get_state(self, key: str) -> object | None:
        return self._state.get(key)

    async def info(self, message: str) -> None:
        self.infos.append(message)


class ScopedContext(FakeContext):
    """FakeContext that also reports the transport and MCP session identity.

    These are the two scoping inputs the production resolvers read, so a test
    using this context exercises the real predicate rather than a fork of it.
    """

    def __init__(self, transport: str, session_id: str) -> None:
        super().__init__()
        self.transport = transport
        self.session_id = session_id


def _make_session(session_id: str = "abc123"):
    return MagicMock(
        session_id=session_id,
        file="/test.py",
        basename="test.py",
    )


# ---------------------------------------------------------------------------
# A real HTTP stub for marimo's /api/sessions (T17 validation)
#
# Not a mock: the tool under test runs the real MarimoClient against this
# socket, and the discovery path runs the real `discover_servers()` registry
# scan + health check against it. Any kernel endpoint answers with a marker so
# a test can prove an argument-less call resolved to THIS server.
# ---------------------------------------------------------------------------


class _StubSessionsHandler(http.server.BaseHTTPRequestHandler):
    """Serve GET /api/sessions; mark every other request with a body token."""

    @property
    def _stub(self) -> StubMarimoServer:
        """The owning stub server (typed accessor for its session list)."""
        return self.server  # type: ignore[return-value]

    def _respond(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/api/sessions":
            self._stub.sessions_calls += 1
            if (
                self._stub.sessions_ok_calls is not None
                and self._stub.sessions_calls > self._stub.sessions_ok_calls
            ):
                # Healthy at discovery, failing by the time the tool queries it
                # — models a server that answered the registry health check and
                # then died before validation.
                self._respond(
                    self._stub.sessions_error_status,
                    b"stub: sessions unavailable",
                    "text/plain",
                )
                return
            if self._stub.sessions_payload is not None:
                self._respond(
                    200,
                    self._stub.sessions_payload.encode(),
                    "application/json",
                )
                return
            if self._stub.sessions_status != 200:
                self._respond(
                    self._stub.sessions_status,
                    b"stub: sessions unavailable",
                    "text/plain",
                )
                return
            payload = {
                sid: {"path": f"/tmp/{sid}.py", "filename": f"{sid}.py"}
                for sid in self._stub.session_ids
            }
            self._respond(200, json.dumps(payload).encode(), "application/json")
            return
        self._respond(404, b"stub: no such endpoint", "text/plain")

    def do_POST(self) -> None:
        # Reaching this proves an argument-less tool call used THIS server's
        # URL: only a resolved binding can get here.
        self._respond(500, b"BOUND-URL-REACHED", "text/plain")

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request stderr noise."""


class StubMarimoServer(http.server.ThreadingHTTPServer):
    """A loopback HTTP stub advertising a fixed set of live session ids."""

    daemon_threads = True

    def __init__(
        self,
        session_ids: tuple[str, ...],
        sessions_status: int = 200,
        sessions_payload: str | None = None,
        sessions_ok_calls: int | None = None,
        sessions_error_status: int = 500,
    ) -> None:
        super().__init__(("127.0.0.1", 0), _StubSessionsHandler)
        self.session_ids = session_ids
        self.sessions_status = sessions_status
        self.sessions_payload = sessions_payload
        # When set, the first N /api/sessions calls answer normally and every
        # later one fails with sessions_error_status (see do_GET).
        self.sessions_ok_calls = sessions_ok_calls
        self.sessions_error_status = sessions_error_status
        self.sessions_calls = 0

    @property
    def url(self) -> str:
        """Base URL of the stub (no trailing slash)."""
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"


@pytest.fixture
def stub_server():
    """Factory fixture: start one stub marimo server per call, stop after test."""
    started: list[StubMarimoServer] = []

    def _start(
        *session_ids: str,
        sessions_status: int = 200,
        sessions_payload: str | None = None,
        sessions_ok_calls: int | None = None,
    ) -> StubMarimoServer:
        server = StubMarimoServer(
            session_ids,
            sessions_status=sessions_status,
            sessions_payload=sessions_payload,
            sessions_ok_calls=sessions_ok_calls,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server

    yield _start

    for server in started:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def _registry(root: Path, *urls: str):
    """Point marimo's server registry at ``root`` containing ``urls``.

    ``discover_servers()`` reads ``$XDG_STATE_HOME/marimo/servers/*.json`` and
    health-checks each entry over HTTP, so this exercises the real discovery
    path against the real stubs instead of patching it.
    """
    registry = root / "marimo" / "servers"
    registry.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(urls):
        (registry / f"stub-{index}.json").write_text(json.dumps({"url": url, "pid": 0}))
    previous = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = previous


async def _bind_via_list(server_url: str) -> FakeContext:
    from marimo_inspection.tools.notebooks import list_active_notebooks

    ctx = FakeContext()
    with patch("marimo_inspection.tools.notebooks.MarimoClient") as list_cls:
        instance = MagicMock()
        instance.list_sessions = AsyncMock(return_value=[_make_session()])
        list_cls.return_value = instance
        result = await list_active_notebooks(server_url=server_url, ctx=ctx)
    assert result["summary"]["total_notebooks"] == 1
    return ctx


async def test_list_binds_server_url_for_later_omitted_call():
    """A tool call with neither session_id nor server_url uses the bound URL."""
    from marimo_inspection.tools.cells import get_cell_map

    ctx = await _bind_via_list("http://127.0.0.1:9000")

    with patch("marimo_inspection.tools.cells.MarimoClient") as cells_cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": [], "total_cells": 0}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cells_cls.return_value = instance

        result = await get_cell_map(ctx=ctx)

        assert "cells" in result
        cells_cls.assert_called_once_with("http://127.0.0.1:9000")


async def test_explicit_server_url_overrides_bound():
    """A later explicit server_url wins over the auto-bound one."""
    from marimo_inspection.tools.cells import get_cell_map

    ctx = await _bind_via_list("http://127.0.0.1:9000")

    with patch("marimo_inspection.tools.cells.MarimoClient") as cells_cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": [], "total_cells": 0}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cells_cls.return_value = instance

        result = await get_cell_map(server_url="http://127.0.0.1:9999", ctx=ctx)

        assert "cells" in result
        cells_cls.assert_called_once_with("http://127.0.0.1:9999")


async def test_server_url_still_required_without_binding():
    """With no bind and no explicit server_url, tools still raise."""
    from marimo_inspection.tools.cells import get_cell_map

    with pytest.raises(ValueError, match="server_url is required"):
        await get_cell_map(session_id="abc123", ctx=None)


async def test_set_active_session_binds_both(stub_server):
    """set_active_session(server_url=…) stores the URL too (T17: real id)."""
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        set_active_session,
    )

    stub = stub_server("s_live")
    ctx = FakeContext()
    result = await set_active_session(
        session_id="s_live",
        server_url=stub.url,
        ctx=ctx,
    )

    assert result["status"] == "OK"
    assert result["active_session_id"] == "s_live"
    assert result["active_server_url"] == stub.url
    assert result["server_url_source"] == "explicit"
    assert result["validated"] is True
    assert ctx._state[_SESSION_KEY] == "s_live"
    assert ctx._state[_SERVER_URL_KEY] == stub.url


async def test_set_active_session_trailing_slash_url_is_normalized(stub_server):
    """A trailing slash on the explicit URL is stored without it."""
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        set_active_session,
    )

    stub = stub_server("s_live")
    ctx = FakeContext()
    result = await set_active_session(
        session_id="s_live", server_url=stub.url + "/", ctx=ctx
    )

    assert result["status"] == "OK"
    assert ctx._state[_SERVER_URL_KEY] == stub.url


async def test_bare_string_variable_names_normalized():
    """A harness-mangled string variable_names is treated as a one-element list."""
    from marimo_inspection.tools.variables import get_variables

    with patch("marimo_inspection.tools.variables.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"variables": {}, "tables": {}}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_variables(
            session_id="abc123",
            variable_names="x",
            server_url="http://127.0.0.1:8090",
        )

        assert "variables" in result
        code = instance.execute.await_args.args[1]
        assert '"x"' in code
        assert '["x"]' in code


async def test_bare_string_cell_ids_normalized_for_cell_data():
    """A harness-mangled string cell_ids is treated as a one-element list."""
    from marimo_inspection.tools.cells import get_cell_data

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"data": []}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_cell_data(
            session_id="abc123",
            cell_ids="5",
            server_url="http://127.0.0.1:8090",
        )

        assert "data" in result
        code = instance.execute.await_args.args[1]
        assert '["5"]' in code


async def test_bare_string_cell_ids_normalized_for_cell_outputs():
    """A harness-mangled string cell_ids is treated as a one-element list."""
    from marimo_inspection.tools.cells import get_cell_outputs

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        exec_result = MagicMock(status="ok", stdout=['{"cells": []}'])
        instance.execute = AsyncMock(return_value=exec_result)
        cls.return_value = instance

        result = await get_cell_outputs(
            session_id="abc123",
            cell_ids="5",
            server_url="http://127.0.0.1:8090",
        )

        assert "cells" in result
        code = instance.execute.await_args.args[1]
        assert '["5"]' in code


# ---------------------------------------------------------------------------
# T17 — validate before binding.
#
# `set_active_session` used to report `status: OK` for any string, so an
# invented id bound successfully and every later call failed with "Session not
# found" (docs/agenda-udv-consumer-findings.md §T17). It must now check the id
# against live sessions first and refuse without touching binding state. The
# servers below are real HTTP stubs reached through the real MarimoClient, so
# these are not mock-satisfied contracts.
# ---------------------------------------------------------------------------


async def test_set_active_session_refuses_an_invented_id(stub_server):
    """An id no live session reports is refused and never bound."""
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        set_active_session,
    )

    stub = stub_server("s_real")
    ctx = FakeContext()

    result = await set_active_session(
        session_id="s_invented", server_url=stub.url, ctx=ctx
    )

    assert result["status"] == "error"
    assert result["reason"] == "session_not_found"
    assert result["bound"] is False
    assert "active_session_id" not in result
    assert result["session_id"] == "s_invented"
    # The refusal says what the server does have, so the caller can correct it.
    assert result["available_sessions"] == [
        {"server_url": stub.url, "session_id": "s_real"}
    ]
    # No binding state was created anywhere.
    assert _SESSION_KEY not in ctx._state
    assert _SERVER_URL_KEY not in ctx._state
    assert set(ctx._state) == set()


async def test_refused_bind_leaves_the_process_global_fallback_unchanged(
    stub_server,
):
    """A refusal must not write the process-global fallback either."""
    from marimo_inspection.tools.session import (
        fallback_decision,
        set_active_session,
        store_fallback_binding,
    )

    stub = stub_server("s_real")
    store_fallback_binding("s_earlier", stub.url)
    ctx = FakeContext()

    result = await set_active_session(
        session_id="s_invented", server_url=stub.url, ctx=ctx
    )

    assert result["reason"] == "session_not_found"
    decision = fallback_decision("stdio", "probe")
    assert decision.session_id == "s_earlier"
    assert decision.server_url == stub.url


async def test_refused_bind_keeps_a_previous_mcp_session_binding(stub_server):
    """A refusal does not disturb the binding an earlier valid bind wrote."""
    from marimo_inspection.tools.session import (
        _SESSION_KEY,
        set_active_session,
    )

    stub = stub_server("s_real")
    ctx = FakeContext()
    ok = await set_active_session(session_id="s_real", server_url=stub.url, ctx=ctx)
    assert ok["status"] == "OK"

    refused = await set_active_session(
        session_id="s_invented", server_url=stub.url, ctx=ctx
    )

    assert refused["reason"] == "session_not_found"
    assert ctx._state[_SESSION_KEY] == "s_real"


async def test_refuses_when_the_server_cannot_be_reached():
    """An explicit server_url that does not answer is an explicit failure."""
    from marimo_inspection.tools.session import set_active_session

    url = f"http://127.0.0.1:{_free_port()}"
    ctx = FakeContext()

    result = await set_active_session(session_id="s_anything", server_url=url, ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "server_unreachable"
    assert result["server_url"] == url
    assert result["servers_queried"] == []
    assert result["servers_failed"] == [
        {"server_url": url, "reason": "server_unreachable"}
    ]
    assert ctx._state == {}


async def test_refuses_when_the_server_query_fails(stub_server):
    """An HTTP error from /api/sessions is reported, not treated as not-found."""
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_real", sessions_status=500)
    ctx = FakeContext()

    result = await set_active_session(session_id="s_real", server_url=stub.url, ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "server_query_failed"
    assert ctx._state == {}


async def test_refuses_a_200_with_an_unusable_body(stub_server):
    """A 200 that is not marimo's session map is a failure, not "found"."""
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server(sessions_payload="[]")
    ctx = FakeContext()

    result = await set_active_session(
        session_id="s_anything", server_url=stub.url, ctx=ctx
    )

    assert result["status"] == "error"
    assert result["reason"] == "server_query_failed"
    assert result["servers_queried"] == []
    assert ctx._state == {}


async def test_without_url_binds_id_and_the_discovered_server_url(
    tmp_path, stub_server
):
    """Discovery finds the id on a healthy server and binds both fields."""
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        set_active_session,
    )

    stub = stub_server("s_discovered")
    ctx = FakeContext()

    with _registry(tmp_path, stub.url):
        result = await set_active_session(session_id="s_discovered", ctx=ctx)

    assert result["status"] == "OK"
    assert result["active_session_id"] == "s_discovered"
    assert result["active_server_url"] == stub.url
    assert result["server_url_source"] == "discovered"
    assert ctx._state[_SESSION_KEY] == "s_discovered"
    assert ctx._state[_SERVER_URL_KEY] == stub.url


async def test_without_url_refuses_an_unknown_id(tmp_path, stub_server):
    """The discovery path refuses an id no discovered server reports."""
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_discovered")
    ctx = FakeContext()

    with _registry(tmp_path, stub.url):
        result = await set_active_session(session_id="s_invented", ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "session_not_found"
    assert result["servers_queried"] == [stub.url]
    assert result["servers_failed"] == []
    assert result["available_sessions"] == [
        {"server_url": stub.url, "session_id": "s_discovered"}
    ]
    assert ctx._state == {}


async def test_without_url_and_no_servers_refuses(tmp_path):
    """With nothing discovered, no id can be validated — and none is bound."""
    from marimo_inspection.tools.session import set_active_session

    ctx = FakeContext()

    with _registry(tmp_path):
        result = await set_active_session(session_id="s_any", ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "session_not_found"
    assert result["servers_discovered"] == 0
    assert result["servers_queried"] == []
    assert ctx._state == {}


async def test_duplicate_id_across_servers_is_ambiguous(tmp_path, stub_server):
    """The same id on two servers binds nothing and asks for server_url."""
    from marimo_inspection.tools.session import (
        fallback_decision,
        set_active_session,
    )

    first = stub_server("s_dup")
    second = stub_server("s_dup")
    ctx = FakeContext()

    with _registry(tmp_path, first.url, second.url):
        result = await set_active_session(session_id="s_dup", ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "session_ambiguous"
    assert result["bound"] is False
    assert sorted(result["matching_servers"]) == sorted([first.url, second.url])
    assert "server_url" in result["message"]
    assert ctx._state == {}
    assert fallback_decision("stdio", "probe").session_id == ""


async def test_ambiguous_refusal_lists_every_matching_session(tmp_path, stub_server):
    """The ambiguity answer lists the matches so the caller can choose."""
    from marimo_inspection.tools.session import set_active_session

    first = stub_server("s_dup", "s_only_first")
    second = stub_server("s_dup")
    ctx = FakeContext()

    with _registry(tmp_path, first.url, second.url):
        result = await set_active_session(session_id="s_dup", ctx=ctx)

    assert result["reason"] == "session_ambiguous"
    listed = {
        (entry["server_url"], entry["session_id"])
        for entry in result["available_sessions"]
    }
    assert listed == {
        (first.url, "s_dup"),
        (first.url, "s_only_first"),
        (second.url, "s_dup"),
    }


async def test_set_active_session_without_context_never_claims_a_bind(stub_server):
    """No ctx means no state to write, so even a live id is not "bound".

    Validation still runs (an invented id is still ``session_not_found``), but
    a validated id with no MCP session context to hold the binding refuses
    with ``binding_context_unavailable`` instead of reporting ``status: OK``
    for a binding that was never written.
    """
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_real")

    refused = await set_active_session(session_id="s_invented", server_url=stub.url)
    assert refused["status"] == "error"
    assert refused["reason"] == "session_not_found"

    result = await set_active_session(session_id="s_real", server_url=stub.url)
    assert result["status"] == "error"
    assert result["reason"] == "binding_context_unavailable"
    assert result["bound"] is False
    assert result["state_changed"] is False
    assert result["validated"] is True
    assert result["server_url"] == stub.url
    assert "active_session_id" not in result


async def test_set_active_session_rejects_an_empty_id(stub_server):
    """An empty session_id is refused in the same structured shape as a bind.

    It must report ``status: error`` / ``reason: invalid_session_id`` plus the
    same ``bound: false`` / ``state_changed: false`` signals the other
    refusals carry — never a bespoke error object the caller has to special-case.
    """
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_real")
    ctx = FakeContext()

    result = await set_active_session(session_id="", server_url=stub.url, ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "invalid_session_id"
    assert result["bound"] is False
    assert result["state_changed"] is False
    assert "error" in result and "help" in result
    assert ctx._state == {}


async def test_duplicate_exact_url_is_queried_once(tmp_path, stub_server):
    """The same registry URL twice is one endpoint, not an ambiguous pair.

    Marimo's registry holds one JSON file per server process, so one endpoint
    can appear in it twice. Counting that as two matches would refuse a
    perfectly unambiguous bind with ``session_ambiguous``; exact duplicates are
    deduped before querying.
    """
    from marimo_inspection.tools.session import (
        _SESSION_KEY,
        set_active_session,
    )

    stub = stub_server("s_unique")
    ctx = FakeContext()

    with _registry(tmp_path, stub.url, stub.url):
        result = await set_active_session(session_id="s_unique", ctx=ctx)

    assert result["status"] == "OK", result
    assert result.get("reason") != "session_ambiguous"
    assert result["active_server_url"] == stub.url
    assert result["servers_discovered"] == 1
    assert result["servers_queried"] == [stub.url]
    assert ctx._state[_SESSION_KEY] == "s_unique"


def test_dedupe_keeps_distinct_endpoint_strings():
    """Dedupe drops only exact duplicates — it does not canonicalize hosts.

    ``localhost`` and ``127.0.0.1`` name different endpoints, so they must
    survive dedupe and stay ambiguous/fail-closed rather than being merged into
    one "match".
    """
    from marimo_inspection.tools.session import _dedupe_urls

    assert _dedupe_urls(("http://127.0.0.1:8000", "http://127.0.0.1:8000")) == (
        "http://127.0.0.1:8000",
    )
    assert _dedupe_urls(
        ("http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:8000")
    ) == ("http://127.0.0.1:8000", "http://localhost:8000")


async def test_distinct_url_strings_for_one_server_stay_ambiguous(
    tmp_path, stub_server
):
    """Two endpoint strings that reach the same server are still ambiguous.

    They are deliberately not canonicalized: the id *is* reported twice, so the
    fail-closed answer stands and the caller must pick the endpoint explicitly.
    """
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_dup")
    port = stub.server_address[1]
    loopback = f"http://127.0.0.1:{port}"
    localhost = f"http://localhost:{port}"
    ctx = FakeContext()

    with _registry(tmp_path, loopback, localhost):
        result = await set_active_session(session_id="s_dup", ctx=ctx)

    assert result["status"] == "error"
    assert result["reason"] == "session_ambiguous"
    assert sorted(result["matching_servers"]) == sorted([loopback, localhost])
    assert ctx._state == {}


async def test_successful_discovery_reports_the_validation_limits(
    tmp_path, stub_server
):
    """A successful bind still reports what it could and could not validate.

    Discovery health-checks each registry entry, but a validation query can
    still fail on an endpoint that answered moments earlier (the server died in
    between). The success payload must carry ``servers_discovered`` /
    ``servers_queried`` / ``servers_failed`` so that limit is visible.
    """
    from marimo_inspection.tools.session import (
        _SESSION_KEY,
        set_active_session,
    )

    good = stub_server("s_here")
    # First /api/sessions call is discovery's health check (200); the second is
    # the tool's validation query and fails.
    flaky = stub_server("s_else", sessions_ok_calls=1)
    ctx = FakeContext()

    with _registry(tmp_path, good.url, flaky.url):
        result = await set_active_session(session_id="s_here", ctx=ctx)

    assert result["status"] == "OK", result
    assert result["active_server_url"] == good.url
    assert result["servers_discovered"] == 2
    assert result["servers_queried"] == [good.url]
    assert result["servers_failed"] == [
        {"server_url": flaky.url, "reason": "server_query_failed"}
    ]
    assert ctx._state[_SESSION_KEY] == "s_here"


async def test_lookup_is_read_only_and_classifies_both_paths(stub_server):
    """`lookup_session` is the read-only validator the tool builds on."""
    from marimo_inspection.tools.session import lookup_session

    stub = stub_server("s_real")

    found = await lookup_session("s_real", server_url=stub.url)
    assert found.server_url == stub.url
    assert found.reason == ""
    assert found.matches == (stub.url,)

    missing = await lookup_session("s_nope", server_url=stub.url)
    assert missing.server_url == ""
    assert missing.reason == "session_not_found"
    assert missing.queried == (stub.url,)
    assert missing.available == ((stub.url, "s_real"),)


def test_description_documents_validation_and_fail_closed_reasons():
    """The MCP description must teach the validation, not just the binding."""
    from marimo_inspection.tools.session import set_active_session

    raw = " ".join((set_active_session.__doc__ or "").lower().split())
    description = raw.replace("`", "")

    assert "session_not_found" in description
    assert "session_ambiguous" in description
    assert "server_unreachable" in description
    assert "before" in description and "bind" in description
    assert "invented" in description
    assert "no state" in description or "nothing is bound" in description
    assert "explicit server_url" in description


# ---------------------------------------------------------------------------
# What the binding promise says vs what a real client sees.
#
# Session state is keyed by the MCP session identity the client negotiates
# (`fastmcp/server/context.py`: session_id is cached on the SDK connection;
# `_make_state_key` prefixes every key with it). A client that starts a new MCP
# session per request therefore writes and reads the binding under different
# keys. These tests pin both sides of that split, and the wording that has to
# stay true for each — see resources/co-work-loop.md §1.
# ---------------------------------------------------------------------------

_SERVER_BIN = Path(sys.executable).parent / "marimo-inspect"


async def test_set_active_session_message_is_scoped_to_the_mcp_session(stub_server):
    """The confirmation must scope the promise instead of calling args optional."""
    from marimo_inspection.tools.session import set_active_session

    stub = stub_server("s_live")
    result = await set_active_session(
        session_id="s_live", server_url=stub.url, ctx=FakeContext()
    )

    message = result["message"]
    assert "now optional" not in message
    assert "process-global fallback" in message
    assert "binding_ambiguous" in message
    assert "single client" in result["binding_scope"]


async def test_missing_binding_error_names_the_condition():
    """The refusal tells the caller why an earlier binding may be invisible."""
    from marimo_inspection.tools.session import resolve_session_id

    with pytest.raises(ValueError) as excinfo:
        await resolve_session_id("", None)

    text = str(excinfo.value)
    assert "process-global fallback" in text
    assert "binding_ambiguous" in text


def _free_port() -> int:
    """Ask the OS for a free loopback port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    """Poll until something accepts connections on the port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        await asyncio.sleep(0.1)
    raise AssertionError(f"marimo-inspect did not start on port {port}")


async def _wait_for_mcp(url: str, timeout: float = 20.0) -> None:
    """Poll until an MCP handshake succeeds (the port may open first)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with (
                streamable_http_client(url) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
            return
        except Exception as exc:  # noqa: BLE001 - transport startup races
            last = exc
            await asyncio.sleep(0.2)
    raise AssertionError(f"MCP server never became ready at {url}: {last!r}")


def _start_http_server(port: int, log_path: Path) -> subprocess.Popen:
    """Launch the console script as a streamable-HTTP server.

    Synchronous on purpose: ``subprocess.Popen`` in an async test trips
    ASYNC220, and the child inherits the log fd after the handle closes here.
    """
    with log_path.open("wb") as handle:
        return subprocess.Popen(
            [str(_SERVER_BIN), "--transport", "http", "--port", str(port)],
            stdout=handle,
            stderr=handle,
        )


def _stop_process(proc: subprocess.Popen) -> None:
    """Terminate the server subprocess, killing it if it will not exit."""
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _spawn(params_args: list[str]) -> dict:
    return {
        "mcpServers": {
            "marimo-inspect": {
                "command": str(_SERVER_BIN),
                "args": params_args,
            }
        }
    }


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_binding_is_visible_to_a_session_stable_sdk_client(stub_server):
    """An `mcp`-SDK stdio client keeps one MCP session, so the binding holds.

    The bound URL is a real HTTP stub whose ``/api/sessions`` reports the bound
    id (so T17 validation passes) and whose every other endpoint answers with a
    marker body. Reaching that marker *is* the proof the binding was visible;
    pre-fix (no fallback/mis-keyed state) the argument-less call refused with
    "no active session bound" instead.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    stub = stub_server("s_probe")
    params = StdioServerParameters(
        command=str(_SERVER_BIN), args=["--transport", "stdio"]
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        bound = await session.call_tool(
            "set_active_session",
            {"session_id": "s_probe", "server_url": stub.url},
        )
        assert json.loads(bound.content[0].text)["status"] == "OK"

        after = await session.call_tool("get_cell_map", {})
        text = after.content[0].text if after.content else ""

    assert "no active session bound" not in text
    assert "BOUND-URL-REACHED" in text, text


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_fastmcp_client_starts_a_new_mcp_session_per_request(stub_server):
    """A session-per-request client is now covered by the process-global fallback.

    fastmcp's own ``Client`` starts a fresh MCP session per request on the
    pinned fastmcp 4.0.3 (measured — see the H11 entry in
    ``docs/agenda-bug-hunt-1.md``), so the binding written to the MCP-session
    state never reaches the following call. Over stdio one process serves
    exactly one client, so the process-global fallback carries it anyway: the
    argument-less call must reach the bound stub URL (marker body) rather than
    refuse.

    This test used to pin the opposite — ``no active session bound`` on every
    argument-less call — which would now be pinning the defect H11 fixed. It is
    the pre-fix-failing closure test for the stdio capability.
    """
    from fastmcp import Client

    stub = stub_server("s_probe")
    async with Client(_spawn(["--transport", "stdio"])) as client:
        bound = await client.call_tool(
            "set_active_session",
            {"session_id": "s_probe", "server_url": stub.url},
        )
        assert json.loads(bound.content[0].text)["status"] == "OK"

        after = await client.call_tool("get_cell_map", {}, raise_on_error=False)
        text = " | ".join(getattr(b, "text", "") for b in (after.content or []))

    assert "no active session bound" not in text
    assert "BOUND-URL-REACHED" in text, text


@pytest.mark.skipif(not _SERVER_BIN.exists(), reason="console script not installed")
async def test_two_http_clients_do_not_share_the_fallback_binding(
    tmp_path, stub_server
):
    """Client B must never inherit client A's binding over HTTP.

    Over ``--transport http`` one process serves many clients, so the
    process-global fallback is scoped: it stops being served as soon as a
    second distinct client session is observed. Client A (a session-stable
    ``mcp``-SDK HTTP client) binds and its own argument-less call is served;
    client B — a different connection that never bound — is then refused with
    the structured ``reason: binding_ambiguous`` instead of being handed A's
    session.

    Pre-fix there was no fallback at all, so B refused with the generic
    "no active session bound" text and the ``binding_ambiguous`` assertion
    failed; a naive *unscoped* fallback would instead let B reach the bound stub
    URL and fail the ``"BOUND-URL-REACHED" not in refused_text`` assertion.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    stub = stub_server("s_probe")
    port = _free_port()
    proc = _start_http_server(port, tmp_path / "http-server.log")
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        await _wait_for_port(port)
        await _wait_for_mcp(url)

        async with (
            streamable_http_client(url) as (read_a, write_a),
            ClientSession(read_a, write_a) as client_a,
        ):
            await client_a.initialize()
            bound = await client_a.call_tool(
                "set_active_session",
                {"session_id": "s_probe", "server_url": stub.url},
            )
            assert json.loads(bound.content[0].text)["status"] == "OK", (
                "client A could not bind"
            )

            # Positive control: while the process has seen one client session,
            # the fallback is served — the call proceeds to the bound stub URL
            # instead of being refused.
            served = await client_a.call_tool("get_cell_map", {})
            served_text = " ".join(
                getattr(block, "text", "") for block in (served.content or [])
            )
            assert "no active session bound" not in served_text
            assert "binding_ambiguous" not in served_text
            assert "BOUND-URL-REACHED" in served_text, served_text

            # Client B is a second, independent MCP session that never bound
            # anything.
            async with (
                streamable_http_client(url) as (read_b, write_b),
                ClientSession(read_b, write_b) as client_b,
            ):
                await client_b.initialize()
                refused = await client_b.call_tool("get_cell_map", {})
            refused_text = " ".join(
                getattr(block, "text", "") for block in (refused.content or [])
            )
    finally:
        _stop_process(proc)

    assert "binding_ambiguous" in refused_text, (
        "client B was not told why the binding was withheld: " + refused_text
    )
    assert "BOUND-URL-REACHED" not in refused_text


async def test_bind_active_session_stores_the_process_global_fallback():
    """The bind choke point feeds the fallback, not only the MCP-session state.

    Both the explicit ``set_active_session`` and ``list_active_notebooks``'
    auto-bind go through ``bind_active_session``, so this pins the wiring for
    both paths without needing a live marimo server to discover.
    """
    from marimo_inspection.tools.session import (
        _SERVER_URL_KEY,
        _SESSION_KEY,
        bind_active_session,
        fallback_decision,
    )

    ctx = ScopedContext("stdio", "rotated-any")
    await bind_active_session("s_new", ctx, server_url="http://127.0.0.1:9000")

    assert ctx._state[_SESSION_KEY] == "s_new"
    assert ctx._state[_SERVER_URL_KEY] == "http://127.0.0.1:9000"

    decision = fallback_decision("stdio", "rotated-other")
    assert decision.session_id == "s_new"
    assert decision.server_url == "http://127.0.0.1:9000"


async def test_fallback_scope_serves_stdio_and_withholds_on_a_second_client():
    """The production predicate: single-client serve, then fail closed.

    Hermetic: drives ``fallback_decision`` — the function the resolvers
    actually call — plus ``resolve_session_id`` through a context that reports
    the real scoping inputs, so this is the shipped predicate and not a fork.
    """
    from marimo_inspection.tools.session import (
        FallbackDecision,
        SessionBindingError,
        fallback_decision,
        resolve_session_id,
        store_fallback_binding,
    )

    store_fallback_binding("s_bound", "http://127.0.0.1:9000")
    served = FallbackDecision(session_id="s_bound", server_url="http://127.0.0.1:9000")

    # stdio is single-client by construction: always served, and the rotating
    # session identities of a session-per-request client are never counted.
    assert fallback_decision("stdio", "rotated-1") == served
    assert fallback_decision("stdio", "rotated-2") == served

    # HTTP: served while one client session has been seen ...
    assert fallback_decision("streamable-http", "session-a") == served

    # ... withheld as soon as a second distinct client session appears.
    withheld = fallback_decision("streamable-http", "session-b")
    assert withheld.session_id == ""
    assert withheld.reason == "binding_ambiguous"

    # The real resolver refuses with the structured reason, not a bare string.
    ctx_b = ScopedContext("streamable-http", "session-c")
    with pytest.raises(SessionBindingError) as excinfo:
        await resolve_session_id("", ctx_b)
    assert excinfo.value.reason == "binding_ambiguous"
    assert "reason: binding_ambiguous" in str(excinfo.value)

    # A single stdio client keeps resolving through the process-global
    # fallback even after many rotated MCP sessions.
    ctx_stdio = ScopedContext("stdio", "rotated-99")
    assert await resolve_session_id("", ctx_stdio) == "s_bound"
