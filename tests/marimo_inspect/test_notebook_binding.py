"""Issue #7 items 1-2: notebook-path binding, anchored resolution, reporting.

A session's *id* is not a stable handle: marimo re-keys a session when a client
reconnects, and a stale id can be handed to a *different* notebook's kernel
(the wrapper churn probe in `docs/native-pair-benchmark-runbook.md`). The
binding therefore records the notebook **path** alongside the id, and every
later argument-less call is anchored to that path:

* an id-only **rotation** (the bound id is gone, the same path is now served
  under a new id) is repaired to the current id;
* an id the server **reused for a different notebook** is a typed refusal
  (`reason: notebook_mismatch`) — never a silent retarget;
* the anchor covers a call **on the bound id** — implicit, or restated by the
  caller — so a recorded id cannot be replayed against another notebook;
* naming a **different** id, or a **different** server, is explicit targeting:
  the caller named another target, so the bound path is not asserted against it;
* every mutation result reports the notebook path that was actually resolved.

These cases drive the real `resolve_target` choke point through an injected
`client_factory` (its documented seam) and the real handler functions via the
per-module `MarimoClient` patch, so no HTTP server is needed below the tool
boundary. The end-to-end live readback lives in the hermetic `-m live` suite.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

#: The notebook the binding was anchored to, and an unrelated one.
BOUND = "/nb/bound.py"
OTHER = "/nb/other.py"


class FakeContext:
    """Minimal stand-in for fastmcp.Context's state store (as elsewhere)."""

    def __init__(self, transport: str | None = None, session_id: str | None = None):
        self._state: dict[str, object] = {}
        self.infos: list[str] = []
        self.transport = transport
        self.session_id = session_id

    async def set_state(
        self, key: str, value: object, serializable: bool = True
    ) -> None:
        self._state[key] = value

    async def get_state(self, key: str) -> object | None:
        return self._state.get(key)

    async def info(self, message: str) -> None:
        self.infos.append(message)


@pytest.fixture(autouse=True)
def _reset_fallback():
    """Keep the process-global fallback from leaking between tests."""
    from marimo_inspection.tools.session import reset_fallback_state

    reset_fallback_state()
    yield
    reset_fallback_state()


def _session(session_id: str, path: str | None) -> SimpleNamespace:
    """A SessionInfo-shaped row (only the fields the resolver reads)."""
    return SimpleNamespace(
        session_id=session_id,
        file=path,
        basename=(path.rsplit("/", 1)[-1] if path else None),
    )


def _client(
    *,
    census=(),
    resolve_result: object = None,
    resolve_error: Exception | None = None,
) -> MagicMock:
    """A MarimoClient double exposing the two seams `resolve_target` reads.

    ``resolve_session`` models the real client: it returns the row for the id
    when present and raises ``ValueError`` when absent (or on a mismatched
    explicit pair). ``list_sessions`` is the census the anchoring reads.
    """
    client = MagicMock()
    client.list_sessions = AsyncMock(return_value=list(census))
    if resolve_error is not None:
        client.resolve_session = AsyncMock(side_effect=resolve_error)
    else:
        client.resolve_session = AsyncMock(return_value=resolve_result)
    client.execute = AsyncMock(
        return_value=MagicMock(status="ok", stdout=["{}"], stderr=[])
    )
    client.close = AsyncMock()
    return client


async def _bind(ctx, **kwargs):
    """Bind a session (id/url/path) through the shared bind choke point."""
    from marimo_inspection.tools.session import bind_active_session

    await bind_active_session(
        kwargs.pop("session_id"),
        ctx,
        server_url=kwargs.pop("server_url", "http://127.0.0.1:9000"),
        notebook_path=kwargs.pop("notebook_path", BOUND),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Both bind sites record the notebook path (state + process-global fallback).
# ---------------------------------------------------------------------------


async def test_bind_active_session_records_the_path_in_state_and_fallback():
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        _SERVER_URL_KEY,
        _SESSION_KEY,
        fallback_decision,
    )

    ctx = FakeContext("stdio", "rotated-any")
    await _bind(ctx, session_id="s_new", server_url="http://127.0.0.1:9000")

    assert ctx._state[_SESSION_KEY] == "s_new"
    assert ctx._state[_SERVER_URL_KEY] == "http://127.0.0.1:9000"
    assert ctx._state[_NOTEBOOK_KEY] == BOUND

    decision = fallback_decision("stdio", "rotated-other")
    assert decision.session_id == "s_new"
    assert decision.notebook_path == BOUND


async def test_bind_without_a_path_records_an_explicit_empty_anchor():
    """A path-less bind (an unsaved notebook) records an explicit empty anchor.

    Explicit empty is safer than leaving the key unset: an unset key would fall
    through to the process-global fallback and could borrow a *previous*
    notebook's path. The empty value says "bound, but nothing to anchor".
    """
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        bind_active_session,
        fallback_decision,
    )

    ctx = FakeContext("stdio", "x")
    await bind_active_session("s", ctx, server_url="http://127.0.0.1:9000")
    assert ctx._state[_NOTEBOOK_KEY] == ""
    assert fallback_decision("stdio", "probe").notebook_path == ""


async def test_rebinding_an_unsaved_notebook_clears_the_previous_anchor():
    """Saved A -> unsaved B must not retain A's path as a stale anchor."""
    from marimo_inspection.tools.session import _NOTEBOOK_KEY, fallback_decision

    ctx = FakeContext("stdio", "x")
    await _bind(ctx, session_id="s_a", notebook_path=BOUND)
    assert ctx._state[_NOTEBOOK_KEY] == BOUND
    assert fallback_decision("stdio", "probe").notebook_path == BOUND

    await _bind(ctx, session_id="s_b", notebook_path="")
    assert ctx._state[_NOTEBOOK_KEY] == "", "stale A anchor retained in state"
    assert fallback_decision("stdio", "probe").notebook_path == "", (
        "stale A anchor retained in fallback"
    )


async def test_list_active_notebooks_auto_bind_records_the_notebook_path():
    """The auto-bind site records the session's own file as the anchor."""
    from marimo_inspection.tools.notebooks import list_active_notebooks
    from marimo_inspection.tools.session import _NOTEBOOK_KEY, fallback_decision

    ctx = FakeContext()
    with patch("marimo_inspection.tools.notebooks.MarimoClient") as cls:
        instance = MagicMock()
        instance.list_sessions = AsyncMock(return_value=[_session("s_live", BOUND)])
        cls.return_value = instance
        result = await list_active_notebooks(
            server_url="http://127.0.0.1:9000", ctx=ctx
        )

    assert result["summary"]["session_count"] == 1
    assert ctx._state[_NOTEBOOK_KEY] == BOUND
    assert fallback_decision("stdio", "probe").notebook_path == BOUND


async def test_set_active_session_records_the_notebook_path():
    """`set_active_session` binds the path its validation read reported."""
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        SessionLookup,
        set_active_session,
    )

    ctx = FakeContext()
    lookup = SessionLookup(
        server_url="http://127.0.0.1:9000", source="explicit", notebook_path=BOUND
    )
    with patch(
        "marimo_inspection.tools.session.lookup_session",
        AsyncMock(return_value=lookup),
    ):
        result = await set_active_session(
            session_id="s_live", server_url="http://127.0.0.1:9000", ctx=ctx
        )

    assert result["status"] == "OK"
    assert ctx._state[_NOTEBOOK_KEY] == BOUND


# ---------------------------------------------------------------------------
# 2. resolve_notebook_path: state first, then the scoped fallback; isolation.
# ---------------------------------------------------------------------------


async def test_resolve_notebook_path_reads_state_then_fallback():
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        resolve_notebook_path,
        store_fallback_binding,
    )

    ctx = FakeContext("stdio", "s1")
    await ctx.set_state(_NOTEBOOK_KEY, "/from/state.py")
    store_fallback_binding("s", "u", notebook_path="/from/fallback.py")
    assert await resolve_notebook_path(ctx) == "/from/state.py"

    fresh = FakeContext("stdio", "s2")
    assert await resolve_notebook_path(fresh) == "/from/fallback.py"


async def test_notebook_path_is_not_leaked_to_a_second_http_client():
    """Once a second client session is seen, its path is withheld too."""
    from marimo_inspection.tools.session import (
        record_client_session,
        resolve_notebook_path,
        store_fallback_binding,
    )

    store_fallback_binding("s", "u", notebook_path="/nb/private.py")
    record_client_session("streamable-http", "session-a")
    ctx_b = FakeContext("streamable-http", "session-b")
    assert await resolve_notebook_path(ctx_b) == ""


async def test_empty_local_anchor_is_honored_over_the_global_fallback():
    """An explicit empty local anchor must not borrow the global fallback path.

    Once the local key is set (even to ""), it is the binding for this MCP
    session: the fallback is consulted only when *nothing* is bound locally.
    """
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        resolve_notebook_path,
        store_fallback_binding,
    )

    ctx = FakeContext("stdio", "s1")
    await ctx.set_state(_NOTEBOOK_KEY, "")
    store_fallback_binding("s", "u", notebook_path="/from/fallback.py")
    assert await resolve_notebook_path(ctx) == ""


# ---------------------------------------------------------------------------
# 2b. resolve_bound_notebook_path: the shared anchor seam.
# ---------------------------------------------------------------------------


async def test_bound_notebook_path_anchors_an_argument_less_call():
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")
    assert await resolve_bound_notebook_path("", "", ctx) == BOUND


async def test_bound_notebook_path_anchors_when_the_bound_url_is_supplied():
    """An implicit sid with the *same* bound server URL is still anchored."""
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")
    assert await resolve_bound_notebook_path("", "http://127.0.0.1:9000", ctx) == BOUND


async def test_bound_notebook_path_is_empty_for_a_different_server_url():
    """An explicit *different* server URL removes the anchor (explicit wins)."""
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")
    assert await resolve_bound_notebook_path("", "http://127.0.0.1:9999", ctx) == ""


async def test_bound_notebook_path_anchors_when_the_caller_restates_the_bound_id():
    """Restating the *bound* id keeps the anchor: it is still the bound notebook.

    A caller that replays the id it recorded is not naming a new target, so the
    anchor applies and a reused id refuses. Only a *different* id is explicit
    targeting.
    """
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")
    assert (
        await resolve_bound_notebook_path("s_old", "http://127.0.0.1:9000", ctx)
        == BOUND
    )
    # ... and with no URL restated, the bound one is used.
    assert await resolve_bound_notebook_path("s_old", "", ctx) == BOUND


async def test_bound_notebook_path_is_empty_for_a_different_session_id():
    """Naming another id is explicit targeting: no anchor is transferred."""
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")
    assert await resolve_bound_notebook_path("s_other", "", ctx) == ""
    assert (
        await resolve_bound_notebook_path("s_other", "http://127.0.0.1:9000", ctx) == ""
    )


async def test_bound_notebook_path_is_empty_for_a_different_server_for_the_bound_id():
    """The bound id on another server is another target: no anchor."""
    from marimo_inspection.tools.session import resolve_bound_notebook_path

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")
    assert (
        await resolve_bound_notebook_path("s_old", "http://127.0.0.1:9999", ctx) == ""
    )


async def test_bound_notebook_path_ignores_a_stale_fallback_for_a_local_empty_anchor():
    """A local empty anchor never borrows the global fallback's path."""
    from marimo_inspection.tools.session import (
        _NOTEBOOK_KEY,
        resolve_bound_notebook_path,
        store_fallback_binding,
    )

    ctx = FakeContext("stdio", "s1")
    await ctx.set_state(_NOTEBOOK_KEY, "")
    store_fallback_binding("s", "u", notebook_path="/from/fallback.py")
    assert await resolve_bound_notebook_path("", "", ctx) == ""


# ---------------------------------------------------------------------------
# 3. Anchored resolution: repair an id-only rotation, refuse a reused id.
# ---------------------------------------------------------------------------


async def test_bound_target_repairs_an_id_only_rotation():
    """The bound id is gone; the same path is served under a new id."""
    from marimo_inspection.tools.session import resolve_target

    rotated = _client(
        census=[_session("s_new", BOUND)],
        resolve_error=ValueError("Session s_old not found"),
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")

    resolution = await resolve_target(
        "", "", ctx=ctx, client_factory=lambda _u: rotated
    )

    assert resolution.refusal is None, resolution.refusal
    client, session = resolution.unwrap()
    assert client is rotated
    assert session.session_id == "s_new"
    assert session.file == BOUND
    assert resolution.notebook_path == BOUND
    assert resolution.repaired is True
    # The repair hands the live client to the caller, so it must NOT be closed.
    assert rotated.close.await_count == 0


async def test_bound_target_refuses_when_the_id_now_serves_another_notebook():
    """The bound id exists but serves a different notebook: never retarget."""
    from marimo_inspection.tools.session import resolve_target

    reused = _client(
        census=[_session("s_old", OTHER)],
        resolve_result=_session("s_old", OTHER),
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")

    resolution = await resolve_target("", "", ctx=ctx, client_factory=lambda _u: reused)

    refusal = resolution.refusal
    assert refusal is not None
    assert refusal["status"] == "error"
    assert refusal["reason"] == "notebook_mismatch"
    assert refusal["target_resolved"] is False
    assert refusal["operation_ran"] is False
    assert refusal["state_changed"] is False
    assert refusal["session_id"] == "s_old"
    assert refusal["bound_notebook"] == BOUND
    assert refusal["resolved_notebook"] == OTHER
    assert refusal["next_steps"]
    # The refusal closes the client it refused to hand off.
    assert reused.close.await_count == 1


async def test_notebook_mismatch_reaches_a_read_and_a_mutation_handler():
    """The busy choke point refuses identically for a read and a mutation."""
    from marimo_inspection.tools import cells, mutation

    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")

    for module, call in (
        (cells, lambda c: c.get_cell_map(ctx=ctx)),
        (mutation, lambda c: c.create_cell("x = 1", ctx=ctx)),
    ):
        reused = _client(
            census=[_session("s_old", OTHER)],
            resolve_result=_session("s_old", OTHER),
        )
        with patch.object(module, "MarimoClient", return_value=reused):
            result = await call(module)
        assert result["status"] == "error", result
        assert result["reason"] == "notebook_mismatch", result
        assert result["operation_ran"] is False, result
        assert reused.execute.call_count == 0, result
        assert reused.close.await_count == 1, result


async def test_explicit_different_id_is_not_path_anchored():
    """Naming another id is explicit targeting: the bound path is not asserted.

    A *different* session id is the caller targeting that id, so the bound
    notebook's path has nothing to do with it.
    """
    from marimo_inspection.tools.session import resolve_target

    client = _client(
        census=[_session("s_explicit", OTHER)],
        resolve_result=_session("s_explicit", OTHER),
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")

    resolution = await resolve_target(
        "s_explicit",
        "http://127.0.0.1:9000",
        ctx=ctx,
        client_factory=lambda _u: client,
    )

    assert resolution.refusal is None, resolution.refusal
    _c, session = resolution.unwrap()
    assert session.session_id == "s_explicit"
    assert resolution.notebook_path == OTHER


async def test_explicit_bound_id_is_still_anchored():
    """Restating the *bound* id does not buy freedom from the anchor."""
    from marimo_inspection.tools.session import resolve_target

    reused = _client(
        census=[_session("s_old", OTHER)], resolve_result=_session("s_old", OTHER)
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old")

    resolution = await resolve_target(
        "s_old", "", ctx=ctx, client_factory=lambda _u: reused
    )

    refusal = resolution.refusal
    assert refusal is not None, "restating the bound id bypassed the anchor"
    assert refusal["reason"] == "notebook_mismatch", refusal
    assert refusal["bound_notebook"] == BOUND, refusal
    assert reused.execute.call_count == 0, refusal


async def test_explicit_bound_id_with_a_different_server_is_not_anchored():
    """The same id on another server is a different target: no anchor."""
    from marimo_inspection.tools.session import resolve_target

    client = _client(
        census=[_session("s_old", OTHER)], resolve_result=_session("s_old", OTHER)
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")

    resolution = await resolve_target(
        "s_old", "http://127.0.0.1:9999", ctx=ctx, client_factory=lambda _u: client
    )

    assert resolution.refusal is None, resolution.refusal
    _c, session = resolution.unwrap()
    assert session.session_id == "s_old"


async def test_bound_target_is_anchored_when_the_bound_url_is_restated():
    """Implicit sid + the *same* bound server URL is still path-anchored.

    The guard used to require both arguments empty, so restating the bound
    server URL while letting the sid come from the binding bypassed the
    notebook-mismatch check.
    """
    from marimo_inspection.tools.session import resolve_target

    reused = _client(
        census=[_session("s_old", OTHER)], resolve_result=_session("s_old", OTHER)
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")

    resolution = await resolve_target(
        "", "http://127.0.0.1:9000", ctx=ctx, client_factory=lambda _u: reused
    )

    refusal = resolution.refusal
    assert refusal is not None, "bound URL restated bypassed the anchor"
    assert refusal["reason"] == "notebook_mismatch", refusal
    assert refusal["bound_notebook"] == BOUND, refusal
    assert refusal["resolved_notebook"] == OTHER, refusal
    assert reused.execute.call_count == 0, refusal


async def test_bound_target_is_anchored_when_the_bound_url_is_restated_with_a_slash():
    """A trailing slash names the same server, so the anchor still applies.

    ``MarimoClient`` and discovery both normalise with ``rstrip("/")``, so
    comparing the restated URL raw would let ``"...:9000/"`` skip the anchor —
    the same bypass through a spelling difference.
    """
    from marimo_inspection.tools.session import resolve_target

    reused = _client(
        census=[_session("s_old", OTHER)], resolve_result=_session("s_old", OTHER)
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")

    resolution = await resolve_target(
        "", "http://127.0.0.1:9000/", ctx=ctx, client_factory=lambda _u: reused
    )

    refusal = resolution.refusal
    assert refusal is not None, "a trailing slash bypassed the anchor"
    assert refusal["reason"] == "notebook_mismatch", refusal
    assert reused.execute.call_count == 0, refusal


async def test_explicit_different_server_url_is_not_path_anchored():
    """Naming a *different* server is explicit targeting: no anchor transfer."""
    from marimo_inspection.tools.session import resolve_target

    client = _client(
        census=[_session("s_old", OTHER)], resolve_result=_session("s_old", OTHER)
    )
    ctx = FakeContext("stdio", "s-anchor")
    await _bind(ctx, session_id="s_old", server_url="http://127.0.0.1:9000")

    resolution = await resolve_target(
        "", "http://127.0.0.1:9999", ctx=ctx, client_factory=lambda _u: client
    )

    assert resolution.refusal is None, resolution.refusal
    _c, session = resolution.unwrap()
    assert session.session_id == "s_old"
    assert resolution.notebook_path == OTHER


async def test_absent_id_without_an_anchor_is_still_session_not_found():
    """No bound path: an absent id keeps its documented not-found shape."""
    from marimo_inspection.tools.session import resolve_target

    client = _client(
        census=[_session("s_other", OTHER)],
        resolve_error=ValueError("Session s_missing not found"),
    )
    resolution = await resolve_target(
        "s_missing", "http://127.0.0.1:9000", client_factory=lambda _u: client
    )
    refusal = resolution.refusal
    assert refusal is not None
    assert refusal["reason"] == "session_not_found"
    assert refusal["available_sessions_readable"] is True


# ---------------------------------------------------------------------------
# 4. Every mutation result reports the resolved notebook path.
# ---------------------------------------------------------------------------


def _mutation_client(path: str, *results):
    """A MarimoClient double whose resolved session serves ``path``."""
    client = MagicMock()
    client.resolve_session = AsyncMock(return_value=_session("s1", path))
    client.execute = AsyncMock(side_effect=list(results))
    client.close = AsyncMock()
    return client


class _Result:
    """A MarimoClient.execute result double."""

    def __init__(self, stdout, status="ok", stderr=None):
        self.stdout = stdout
        self.status = status
        self.stderr = stderr or []
        self.output = None
        self.execution_count = None


async def test_create_cell_reports_the_resolved_notebook_path():
    from marimo_inspection.tools import mutation

    client = _mutation_client(
        BOUND,
        _Result(['{"status": "ok", "cell_id": "X1"}']),
        _Result(["{}"]),
    )
    with patch.object(mutation, "MarimoClient", return_value=client):
        result = await mutation.create_cell("x = 1", server_url="u", session_id="s1")
    assert result["status"] == "ok", result
    assert result["notebook_path"] == BOUND


async def test_edit_cell_reports_the_resolved_notebook_path():
    from marimo_inspection.tools import mutation

    client = _mutation_client(
        BOUND,
        _Result(['{"C1": "h"}']),
        _Result(['{"status": "ok", "cell_id": "C1"}']),
        _Result(['{"C1": "h2"}']),
    )
    with patch.object(mutation, "MarimoClient", return_value=client):
        result = await mutation.edit_cell(
            "C1", "y = 2", check_fresh=False, server_url="u", session_id="s1"
        )
    assert result["status"] == "ok", result
    assert result["notebook_path"] == BOUND


async def test_run_cell_reports_the_resolved_notebook_path():
    from marimo_inspection.tools import mutation

    client = _mutation_client(
        BOUND,
        _Result(
            [
                json.dumps(
                    {
                        "status": "ok",
                        "mode": "cell",
                        "cell_id": "X1",
                        "resolved_cell_id": "X1",
                        "requested_cell_ids": ["X1"],
                    }
                )
            ]
        ),
        _Result(['{"status": "ok", "queued": ["X1"]}']),
        _Result(
            [
                json.dumps(
                    {
                        "rows": [
                            {
                                "cell_id": "X1",
                                "runtime_state": "idle",
                                "known": True,
                                "output_stale": False,
                                "errors": [],
                            }
                        ]
                    }
                )
            ]
        ),
    )
    with patch.object(mutation, "MarimoClient", return_value=client):
        result = await mutation.run_cell("X1", server_url="u", session_id="s1")
    assert result["status"] == "ok", result
    assert result["notebook_path"] == BOUND


async def test_delete_cell_reports_the_resolved_notebook_path():
    from marimo_inspection.tools import mutation

    client = _mutation_client(
        BOUND,
        _Result([json.dumps({"targets": [{"cell_id": "C1", "name": None}]})]),
        _Result(['{"status": "ok", "cell_id": "C1"}']),
        _Result(["{}"]),
    )
    with patch.object(mutation, "MarimoClient", return_value=client):
        result = await mutation.delete_cell("C1", server_url="u", session_id="s1")
    assert result["status"] == "ok", result
    assert result["notebook_path"] == BOUND


async def test_set_ui_value_reports_the_resolved_notebook_path():
    from marimo_inspection.tools import ui

    payload = {
        "status": "ok",
        "variable_name": "slider",
        "element_type": "slider",
        "accepted_shape": "int | float",
        "verified": True,
        "applied": True,
        "value_before": 3,
        "value_after": 7,
    }
    client = _mutation_client(BOUND, _Result([json.dumps(payload)]))
    client.execute = AsyncMock(return_value=_Result([json.dumps(payload)]))
    with patch.object(ui, "MarimoClient", return_value=client):
        result = await ui.set_ui_value(
            "slider", value=7, server_url="u", session_id="s1"
        )
    assert result["status"] == "ok", result
    assert result["notebook_path"] == BOUND
