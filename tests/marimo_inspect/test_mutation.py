"""Tests for the notebook mutation tools (create/edit/run/delete)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch


class _Result:
    def __init__(self, stdout, status="ok", stderr=None):
        self.stdout = stdout
        self.status = status
        self.stderr = stderr or []
        self.output = None
        self.execution_count = None


def _json(result_dict):
    return [str(result_dict).replace("'", '"')]


def _mock_client(stdout=None):
    """Return a context manager exposing 'stop' and the started mock instance.

    Usage:
        mock = _mock_client([...])
        with mock:
            ...
    after which mock.stop() cleans up; mock.instance gives the MarimoClient mock.
    """
    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(
                session_id="test-session-1",
                file="/repo/notebooks/test.py",
                basename="test.py",
            )
        ),
        execute=AsyncMock(
            return_value=_Result(stdout or ['{"status": "ok", "cell_id": "X1"}'])
        ),
    )
    patcher = patch("marimo_inspection.tools.mutation.MarimoClient")
    patcher.start().return_value = instance

    class _R:
        def __init__(self, inst):
            self.instance = inst
            self._patcher = patcher

        def stop(self):
            self._patcher.stop()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stop()

    return _R(instance)


# ── templates ────────────────────────────────────────────────────────────


def test_create_cell_template_builds():
    from marimo_inspection.templates.mutation import build_create_cell_template

    code = build_create_cell_template("x = 1", name="mycell", hide_code=False)
    assert "ctx.create_cell" in code
    assert '"x = 1"' in code
    assert "hide_code=False" in code
    assert 'name="mycell"' in code


def test_create_cell_template_default_is_visible():
    """T6: without hide_code the rendered call shows hide_code=False."""
    from marimo_inspection.templates.mutation import build_create_cell_template

    code = build_create_cell_template("x = 1")
    assert "hide_code=False" in code
    assert "hide_code=True" not in code


def test_create_cell_template_explicit_hide_code_true_renders():
    """T6: hide_code=True stays an explicit opt-in."""
    from marimo_inspection.templates.mutation import build_create_cell_template

    code = build_create_cell_template("x = 1", hide_code=True)
    assert "hide_code=True" in code


def test_edit_cell_template_builds():
    from marimo_inspection.templates.mutation import build_edit_cell_template

    code = build_edit_cell_template("C1", "y = 2")
    assert "ctx.edit_cell" in code
    assert '"C1"' in code
    assert '"y = 2"' in code


def test_run_delete_templates_build():
    from marimo_inspection.templates.mutation import (
        build_delete_cell_template,
        build_run_cells_template,
    )

    assert "ctx.run_cell" in build_run_cells_template(["C1"])
    assert "ctx.delete_cell" in build_delete_cell_template("C1")


def test_templates_are_valid_python():
    import ast

    from marimo_inspection.templates.mutation import (
        build_cell_hashes_template,
        build_cell_status_template,
        build_create_cell_template,
        build_delete_cell_template,
        build_edit_cell_template,
        build_run_cells_template,
        build_run_plan_template,
    )

    for code in [
        build_cell_hashes_template(),
        build_create_cell_template("x=1"),
        build_edit_cell_template("C1", "y=2"),
        build_run_cells_template(["C1"]),
        build_run_plan_template("C1", "cell"),
        build_cell_status_template(["C1"]),
        build_delete_cell_template("C1"),
    ]:
        ast.parse(code)  # must not raise


# ── tools ────────────────────────────────────────────────────────────────


async def test_create_cell_ok():
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "X1"}'])
    try:
        result = await mutation.create_cell(
            "x = 1",
            server_url="http://127.0.0.1:8123",
            session_id="s1",
        )
        assert result["status"] == "ok"
        assert result["cell_id"] == "X1"
    finally:
        mock_cls.stop()


async def test_create_cell_default_renders_hide_code_false():
    """T6: the tool's default hide_code=False flows into the executed template."""
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "X1"}'])
    try:
        result = await mutation.create_cell("x = 1", server_url="u", session_id="s1")
        assert result["status"] == "ok"
        # First execute call is the create itself (second is the hash refresh).
        create_call = mock_cls.instance.execute.call_args_list[0][0][1]
        assert "hide_code=False" in create_call
        assert "hide_code=True" not in create_call
    finally:
        mock_cls.stop()


async def test_create_cell_explicit_hide_code_true_renders():
    """T6: explicit hide_code=True still renders in the executed template."""
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "X1"}'])
    try:
        result = await mutation.create_cell(
            "x = 1", hide_code=True, server_url="u", session_id="s1"
        )
        assert result["status"] == "ok"
        create_call = mock_cls.instance.execute.call_args_list[0][0][1]
        assert "hide_code=True" in create_call
    finally:
        mock_cls.stop()


async def test_create_cell_requires_source():
    from marimo_inspection.tools import mutation

    result = await mutation.create_cell("  ", server_url="u", session_id="s")
    assert result["status"] == "error"


async def test_edit_cell_ok_returns_post_exit_hash():
    """The returned code_hash is the refreshed post-exit hash, not the
    template-provided pre-exit one."""
    import hashlib

    from marimo_inspection.tools import mutation

    edited_src = "y = 2"
    edited_hash = hashlib.sha256(edited_src.encode("utf-8")).hexdigest()[:12]

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return _Result(['{"C1": "livehash"}'])
        if n == 2:
            # The edit itself reports a (stale, pre-context-exit) hash.
            return _Result(
                ['{"status": "ok", "cell_id": "C1", "code_hash": "stale-pre-exit"}']
            )
        # Post-edit snapshot refresh reports the true post-exit hash.
        return _Result([f'{{"C1": "{edited_hash}"}}'])

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="s1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        result = await mutation.edit_cell(
            "C1", edited_src, server_url="u", session_id="s1", check_fresh=False
        )
        assert result["status"] == "ok"
        assert result["cell_id"] == "C1"
        assert instance.execute.call_count == 3
        assert result["code_hash"] == edited_hash
        assert result["code_hash"] != "stale-pre-exit"
    finally:
        mock_cls.stop()


async def test_edit_cell_needs_read_without_any_snapshot():
    """A first edit of a never-read cell demands a read even when the session
    has no snapshot at all (old behavior silently bypassed the guard)."""
    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import get_tracker

    tracker = get_tracker()
    tracker.clear_session("test-session-1")
    mock_cls = _mock_client(['{"C1": "livehash"}'])
    try:
        result = await mutation.edit_cell(
            "C1", "y = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert result["status"] == "needs_read"
        # Only the live-hash read happened — the edit must not have run.
        assert mock_cls.instance.execute.call_count == 1
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_edit_cell_missing_cell_returns_error():
    """A cell_id absent from the live hashes is an error, not 'fresh'."""
    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    tracker = get_tracker()
    tracker.record_cells("test-session-1", {"C1": CellFingerprint(code_hash="h")})
    # Live hashes do not contain C1 at all.
    mock_cls = _mock_client(['{"OTHER": "h"}'])
    try:
        result = await mutation.edit_cell(
            "C1", "y = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert result["status"] == "error"
        assert "not found" in result["message"]
        assert mock_cls.instance.execute.call_count == 1  # no mutation
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_edit_cell_reread_then_retry_proceeds():
    """Re-reading a conflicting cell (get_cell_data/record_cells) refreshes
    the baseline so the retried edit proceeds."""
    import hashlib

    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    h1 = hashlib.sha256(b"x = 1").hexdigest()[:12]
    h2 = hashlib.sha256(b"x = 2").hexdigest()[:12]
    h3 = hashlib.sha256(b"x = 3").hexdigest()[:12]

    tracker = get_tracker()
    tracker.record_cells("test-session-1", {"C1": CellFingerprint(code_hash=h1)})

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return _Result([f'{{"C1": "{h2}"}}'])  # external change -> conflict
        if n == 2:
            return _Result([f'{{"C1": "{h2}"}}'])  # re-read -> matches baseline
        if n == 3:
            return _Result(['{"status": "ok", "cell_id": "C1"}'])
        return _Result([f'{{"C1": "{h3}"}}'])  # post-edit refresh

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="test-session-1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        first = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert first["status"] == "conflict"

        # The get_cell_data path: merge the freshly-read fingerprint.
        tracker.record_cells(
            "test-session-1", {"C1": CellFingerprint(code_hash=h2, state="idle")}
        )

        second = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert second["status"] == "ok"
        assert instance.execute.call_count == 4
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_edit_cell_conflict_persists_after_reread_and_new_change():
    """After a re-read, ANOTHER external change still trips the guard."""
    import hashlib

    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    h1 = hashlib.sha256(b"x = 1").hexdigest()[:12]
    h2 = hashlib.sha256(b"x = 2").hexdigest()[:12]
    h3 = hashlib.sha256(b"x = 3").hexdigest()[:12]

    tracker = get_tracker()
    tracker.record_cells("test-session-1", {"C1": CellFingerprint(code_hash=h1)})

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return _Result([f'{{"C1": "{h2}"}}'])  # external change -> conflict
        return _Result([f'{{"C1": "{h3}"}}'])  # yet another change

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="test-session-1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        first = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert first["status"] == "conflict"

        tracker.record_cells(
            "test-session-1", {"C1": CellFingerprint(code_hash=h2, state="idle")}
        )

        second = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert second["status"] == "conflict"
        assert instance.execute.call_count == 2  # no mutation ever ran
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_edit_cell_refresh_error_preserves_baseline_and_warns():
    """A failed snapshot refresh must not wipe the baseline and must be
    surfaced as a warning — never as a stale code_hash."""
    import hashlib

    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    h1 = hashlib.sha256(b"x = 1").hexdigest()[:12]
    h2 = hashlib.sha256(b"x = 2").hexdigest()[:12]

    tracker = get_tracker()
    tracker.record_cells("test-session-1", {"C1": CellFingerprint(code_hash=h1)})

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return _Result([f'{{"C1": "{h1}"}}'])  # matches baseline
        if n == 2:
            return _Result(['{"status": "ok", "cell_id": "C1"}'])
        if n == 3:
            return _Result(
                ['{"error": "Execution failed", "stderr": "boom"}']
            )  # refresh fails
        return _Result([f'{{"C1": "{h2}"}}'])  # follow-up live read

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="test-session-1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        result = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert result["status"] == "ok"
        assert "warning" in result
        assert "code_hash" not in result  # never a stale hash

        # Baseline preserved: a follow-up edit still sees the OLD baseline.
        follow_up = await mutation.edit_cell(
            "C1", "x = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert follow_up["status"] == "conflict"
        assert instance.execute.call_count == 4
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_create_cell_warns_on_refresh_error():
    """create_cell keeps its success response but warns when the snapshot
    refresh fails — no empty-commit, no crash."""
    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import get_tracker

    tracker = get_tracker()
    tracker.clear_session("test-session-1")

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return _Result(['{"status": "ok", "cell_id": "X1"}'])
        return _Result(['{"error": "Execution failed", "stderr": "boom"}'])

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="test-session-1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        result = await mutation.create_cell("x = 1", server_url="u", session_id="s1")
        assert result["status"] == "ok"
        assert result["cell_id"] == "X1"
        assert "warning" in result
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


# ── run_cell execution modes (T15) ───────────────────────────────────────


def _run_client(*results):
    """Mock client whose ``execute`` returns the given results in order.

    `run_cell` now makes three calls: the plan/read, the run itself, and the
    separate post-run report.
    """
    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="test-session-1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=list(results)),
    )
    patcher = patch("marimo_inspection.tools.mutation.MarimoClient")
    patcher.start().return_value = instance

    class _R:
        def __init__(self, inst):
            self.instance = inst
            self._patcher = patcher

        def stop(self):
            self._patcher.stop()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stop()

    return _R(instance)


def _plan_result(
    requested=(), *, mode="cell", reason=None, unknown=(), resolved=None, target=None
):
    """A plan payload as the plan template emits it.

    ``target`` is the input the caller passed (id or name); ``resolved`` is the
    cell id the plan resolved it to — the two differ when a NAME was passed.
    """
    return _Result(
        [
            json.dumps(
                {
                    "status": "error" if reason else "ok",
                    "mode": mode,
                    "cell_id": target,
                    "resolved_cell_id": resolved,
                    "reason": reason,
                    "requested_cell_ids": list(requested),
                    "document_cell_ids": list(requested),
                    "graph_cell_ids": [],
                    "unknown_cell_ids": list(unknown),
                }
            )
        ]
    )


def _status_row(cell_id, state, errors=None, *, unreadable=False):
    """A report row; ``unreadable=True`` emits the null-errors channel."""
    return {
        "cell_id": cell_id,
        "runtime_state": state,
        "known": True,
        "output_stale": state == "stale",
        "errors": None if unreadable else list(errors or []),
    }


def _report_result(rows):
    return _Result([json.dumps({"rows": rows})])


def _executed(client):
    """The snippets passed to ``execute``, in call order."""
    return [call[0][1] for call in client.instance.execute.call_args_list]


async def test_run_cell_default_mode_plans_runs_and_reports():
    """mode='cell' (default): plan → run → separate report, one call each."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["X1"], mode="cell"),
        _Result(['{"status": "ok", "queued": ["X1"]}']),
        _report_result([_status_row("X1", "idle")]),
    )
    try:
        result = await mutation.run_cell("X1", server_url="u", session_id="s1")
        assert result["status"] == "ok", result
        assert result["mode"] == "cell", result
        # Backward-compatible single-cell field.
        assert result["cell_id"] == "X1", result
        assert result["requested_cell_ids"] == ["X1"], result
        assert result["succeeded_cell_ids"] == ["X1"], result
        assert result["failed_cell_ids"] == [], result
        assert result["not_run_cell_ids"] == [], result
        assert result["counts"] == {
            "requested": 1,
            "succeeded": 1,
            "failed": 0,
            "not_run": 0,
        }, result
        assert result["cells"] == [
            {
                "cell_id": "X1",
                "runtime_state": "idle",
                "output_stale": False,
                "known": True,
                "errors": [],
                "errors_readable": True,
            }
        ], result
        assert "ancestors" in result["note"], result
        assert "unspecified" in result["note"], result

        snippets = _executed(mock)
        assert len(snippets) == 3, snippets
        assert 'mode = "cell"' in snippets[0], snippets[0]
        assert "ctx.run_cell" in snippets[1], snippets[1]
        assert "runtime_state" in snippets[2], snippets[2]
    finally:
        mock.stop()


async def test_run_cell_all_rejects_a_nonempty_cell_id_without_calling_anything():
    """mode='all' + cell_id is a caller error, refused (never ignored)."""
    from marimo_inspection.tools import mutation

    mock = _run_client()
    try:
        result = await mutation.run_cell(
            "X1", mode="all", server_url="u", session_id="s1"
        )
        assert result["status"] == "error", result
        assert result["reason"] == "cell_id_not_allowed", result
        assert "must be empty" in result["message"], result
        # Refused BEFORE any session/client work, let alone execution.
        assert mock.instance.execute.call_count == 0
        assert mock.instance.resolve_session.call_count == 0
    finally:
        mock.stop()


async def test_run_cell_requires_cell_id_for_cell_and_descendants_modes():
    from marimo_inspection.tools import mutation

    for mode in ("cell", "descendants"):
        result = await mutation.run_cell("", mode=mode, server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "cell_id_required", result
        assert result["mode"] == mode, result


async def test_run_cell_rejects_an_unknown_mode():
    from marimo_inspection.tools import mutation

    result = await mutation.run_cell(
        "X1", mode="bogus", server_url="u", session_id="s1"
    )
    assert result["status"] == "error", result
    assert result["reason"] == "invalid_mode", result


async def test_run_cell_unknown_id_aborts_before_any_run():
    """One unknown id must abort the plan — marimo would discard the batch."""
    from marimo_inspection.tools import mutation

    mock = _run_client(_plan_result(reason="unknown_cell_ids", unknown=["ghost"]))
    try:
        result = await mutation.run_cell("ghost", server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "unknown_cell_ids", result
        assert result["unknown_cell_ids"] == ["ghost"], result
        # Only the plan ran; no `ctx.run_cell` was ever queued.
        assert mock.instance.execute.call_count == 1, _executed(mock)
    finally:
        mock.stop()


async def test_run_cell_descendants_refuses_when_the_graph_is_unpopulated():
    """No silent degradation: graph_unpopulated points at mode='all'."""
    from marimo_inspection.tools import mutation

    mock = _run_client(_plan_result(reason="graph_unpopulated"))
    try:
        result = await mutation.run_cell(
            "X1", mode="descendants", server_url="u", session_id="s1"
        )
        assert result["status"] == "error", result
        assert result["reason"] == "graph_unpopulated", result
        assert result["cell_id"] == "X1", result
        assert result["requested_cell_ids"] == ["X1"], result
        assert "mode='all'" in result["message"], result
        assert any("mode='all'" in step for step in result["next_steps"]), result
        assert mock.instance.execute.call_count == 1, _executed(mock)
    finally:
        mock.stop()


async def test_run_cell_all_plans_every_document_cell_and_queues_it_once():
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A", "B", "C"], mode="all"),
        _Result(['{"status": "ok", "queued": ["A", "B", "C"]}']),
        _report_result([_status_row(c, "idle") for c in ("A", "B", "C")]),
    )
    try:
        result = await mutation.run_cell(mode="all", server_url="u", session_id="s1")
        assert result["status"] == "ok", result
        assert result["mode"] == "all", result
        assert set(result["requested_cell_ids"]) == {"A", "B", "C"}, result
        assert set(result["succeeded_cell_ids"]) == {"A", "B", "C"}, result
        # No single-cell compat field in a batch mode.
        assert "cell_id" not in result, result
        run_snippet = _executed(mock)[1]
        body = run_snippet.split("async def _run():", 1)[1]
        assert '"A", "B", "C"' in run_snippet, run_snippet
        assert body.count("ctx.run_cell(") == 1, run_snippet
        # Every target is queued inside ONE code-mode context.
        assert body.count("cm.get_context()") == 1, run_snippet
    finally:
        mock.stop()


async def test_run_cell_partial_reports_per_cell_failures_and_non_runs():
    """A mixed batch: exception + cancelled fail, stale is not_run, idle succeeds.

    The run call itself fails (marimo discards the payload when a target
    raises), so the post-run report is the only truthful source — and the
    response must still name every requested target individually.
    """
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A", "B", "C", "D"], mode="all"),
        _Result(
            ["Traceback (most recent call last):", "NameError: boom"],
            status="error",
            stderr=["Traceback (most recent call last):\n", "NameError: boom\n"],
        ),
        _report_result(
            [
                _status_row("A", "idle"),
                _status_row(
                    "B",
                    "exception",
                    [{"kind": "runtime", "message": "NameError: boom"}],
                ),
                _status_row("C", "cancelled"),
                _status_row("D", "stale"),
            ]
        ),
    )
    try:
        result = await mutation.run_cell(mode="all", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["execution_error"] == "Execution failed", result
        assert "NameError: boom" in result["stderr"], result
        assert result["succeeded_cell_ids"] == ["A"], result
        assert result["failed_cell_ids"] == ["B", "C"], result
        assert result["not_run_cell_ids"] == ["D"], result
        assert result["counts"] == {
            "requested": 4,
            "succeeded": 1,
            "failed": 2,
            "not_run": 1,
        }, result
        rows = {cell["cell_id"]: cell for cell in result["cells"]}
        assert rows["B"]["runtime_state"] == "exception", rows["B"]
        assert rows["B"]["errors"] == [
            {"kind": "runtime", "message": "NameError: boom"}
        ], rows["B"]
        assert rows["C"]["runtime_state"] == "cancelled", rows["C"]
        assert rows["D"]["runtime_state"] == "stale", rows["D"]
        assert rows["D"]["output_stale"] is True, rows["D"]
    finally:
        mock.stop()


async def test_run_cell_marimo_error_and_interrupted_are_failures():
    """The full failed-state set is honoured; anything else is not_run."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A", "B", "C", "D"], mode="all"),
        _Result(['{"status": "ok", "queued": ["A", "B", "C", "D"]}']),
        _report_result(
            [
                _status_row(
                    "A", "marimo-error", [{"kind": "marimo", "message": "cyc"}]
                ),
                _status_row("B", "interrupted"),
                _status_row("C", "disabled"),
                _status_row("D", "running"),
            ]
        ),
    )
    try:
        result = await mutation.run_cell(mode="all", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["failed_cell_ids"] == ["A", "B"], result
        assert result["not_run_cell_ids"] == ["C", "D"], result
        assert result["succeeded_cell_ids"] == [], result
    finally:
        mock.stop()


async def test_run_cell_execution_error_with_all_idle_is_still_partial():
    """A failing run whose requested targets are all idle is not 'ok'.

    The kernel can run cells outside the requested set (stale ancestors,
    autorun descendants); if the batch reported a failure, the verdict must not
    be an unqualified success.
    """
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A"], mode="cell"),
        _Result(["boom"], status="error", stderr=["RuntimeError: ancestor\n"]),
        _report_result([_status_row("A", "idle")]),
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["execution_error"] == "Execution failed", result
        assert result["succeeded_cell_ids"] == ["A"], result
    finally:
        mock.stop()


async def test_run_cell_planning_failure_is_an_error_and_runs_nothing():
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _Result(["down"], status="error", stderr=["Connection refused\n"])
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "planning_failed", result
        assert "Connection refused" in result["stderr"], result
        assert mock.instance.execute.call_count == 1, _executed(mock)
    finally:
        mock.stop()


async def test_run_cell_reporting_failure_is_an_error_not_a_false_ok():
    """If the post-run report cannot be read, the run is unverifiable."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A"], mode="cell"),
        _Result(['{"status": "ok", "queued": ["A"]}']),
        _Result(["down"], status="error", stderr=["Kernel died\n"]),
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "reporting_failed", result
        assert result["requested_cell_ids"] == ["A"], result
        assert "Kernel died" in result["stderr"], result
    finally:
        mock.stop()


async def test_run_cell_all_on_an_empty_notebook_is_a_truthful_ok():
    from marimo_inspection.tools import mutation

    mock = _run_client(_plan_result([], mode="all"))
    try:
        result = await mutation.run_cell(mode="all", server_url="u", session_id="s1")
        assert result["status"] == "ok", result
        assert result["requested_cell_ids"] == [], result
        assert result["counts"] == {
            "requested": 0,
            "succeeded": 0,
            "failed": 0,
            "not_run": 0,
        }, result
        # Nothing to queue: no run call was made.
        assert mock.instance.execute.call_count == 1, _executed(mock)
    finally:
        mock.stop()


async def test_run_cell_resolves_a_cell_name_to_the_live_id():
    """A cell NAME works like it did before the modes: requested holds the ID.

    The pre-modes `run_cell` forwarded the target straight to `ctx.run_cell`,
    which resolves an id or a name — so a caller passing a name must keep
    working. The payload echoes the name it was given and reports the resolved
    id, and the RUN queues the resolved id, never the name.
    """
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["X1"], mode="cell", target="named_cell", resolved="X1"),
        _Result(['{"status": "ok", "queued": ["X1"]}']),
        _report_result([_status_row("X1", "idle")]),
    )
    try:
        result = await mutation.run_cell("named_cell", server_url="u", session_id="s1")
        assert result["status"] == "ok", result
        assert result["cell_id"] == "named_cell", result
        assert result["resolved_cell_id"] == "X1", result
        assert result["requested_cell_ids"] == ["X1"], result
        assert result["succeeded_cell_ids"] == ["X1"], result

        run_snippet = _executed(mock)[1]
        assert '"X1"' in run_snippet, run_snippet
        assert "named_cell" not in run_snippet, run_snippet
    finally:
        mock.stop()


async def test_run_cell_unknown_name_refuses_before_any_run():
    """An unknown NAME is refused like an unknown id — nothing is queued."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(
            reason="unknown_cell_ids", unknown=["no_such_name"], target="no_such_name"
        )
    )
    try:
        result = await mutation.run_cell(
            "no_such_name", server_url="u", session_id="s1"
        )
        assert result["status"] == "error", result
        assert result["reason"] == "unknown_cell_ids", result
        assert result["unknown_cell_ids"] == ["no_such_name"], result
        assert result["error"], result
        assert mock.instance.execute.call_count == 1, _executed(mock)
    finally:
        mock.stop()


async def test_run_cell_descendants_by_name_names_the_resolved_id():
    """descendants-by-name refuses on an empty graph but reports the real id."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(reason="graph_unpopulated", target="named_cell", resolved="X1")
    )
    try:
        result = await mutation.run_cell(
            "named_cell", mode="descendants", server_url="u", session_id="s1"
        )
        assert result["status"] == "error", result
        assert result["reason"] == "graph_unpopulated", result
        assert result["cell_id"] == "named_cell", result
        assert result["resolved_cell_id"] == "X1", result
        assert result["requested_cell_ids"] == ["X1"], result
        assert result["error"], result
    finally:
        mock.stop()


async def test_run_cell_validation_failures_keep_the_legacy_error_key():
    """Validation refusals keep top-level `error` beside `status`/`reason`.

    The pre-modes `run_cell` reported failures as `{"error": ...}`; a caller
    written against that must still see the failure after the modes landed.
    """
    from marimo_inspection.tools import mutation

    missing = await mutation.run_cell("", mode="cell", server_url="u", session_id="s1")
    assert missing["status"] == "error", missing
    assert missing["error"] == "cell_id is required", missing

    invalid = await mutation.run_cell(
        "X1",
        mode="bogus",
        server_url="u",
        session_id="s1",  # type: ignore[arg-type]
    )
    assert invalid["status"] == "error", invalid
    assert invalid["reason"] == "invalid_mode", invalid
    assert invalid["error"], invalid

    both = await mutation.run_cell("X1", mode="all", server_url="u", session_id="s1")
    assert both["status"] == "error", both
    assert both["reason"] == "cell_id_not_allowed", both
    assert both["error"], both


async def test_run_cell_planning_reporting_and_run_failures_keep_the_error_key():
    from marimo_inspection.tools import mutation

    # Planning failure — the plan call itself failed, nothing was queued.
    mock = _run_client(
        _Result(["down"], status="error", stderr=["Connection refused\n"])
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "planning_failed", result
        assert result["error"], result
    finally:
        mock.stop()

    # Reporting failure — queued, but the post-run state is unreadable.
    mock = _run_client(
        _plan_result(["A"], mode="cell"),
        _Result(['{"status": "ok", "queued": ["A"]}']),
        _Result(["down"], status="error", stderr=["Kernel died\n"]),
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "error", result
        assert result["reason"] == "reporting_failed", result
        assert result["error"], result
    finally:
        mock.stop()

    # Partial run — the batch call failed, so `error` mirrors `execution_error`.
    mock = _run_client(
        _plan_result(["A"], mode="cell"),
        _Result(["boom"], status="error", stderr=["RuntimeError: ancestor\n"]),
        _report_result([_status_row("A", "idle")]),
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["execution_error"] == "Execution failed", result
        assert result["error"] == result["execution_error"], result
    finally:
        mock.stop()


async def test_run_cell_unreadable_errors_channel_is_never_success():
    """A null errors channel is UNREADABLE: that target is not `succeeded`.

    `succeeded` requires `idle` AND a readable, empty `errors`; an unreadable
    channel makes the outcome unverified, so the target lands in
    `not_run_cell_ids` / `unverified_cell_ids` with a truthful next step.
    """
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A", "B"], mode="all"),
        _Result(['{"status": "ok", "queued": ["A", "B"]}']),
        _report_result(
            [
                _status_row("A", "idle", unreadable=True),
                _status_row("B", "idle"),
            ]
        ),
    )
    try:
        result = await mutation.run_cell(mode="all", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["succeeded_cell_ids"] == ["B"], result
        assert result["failed_cell_ids"] == [], result
        assert result["not_run_cell_ids"] == ["A"], result
        assert result["unverified_cell_ids"] == ["A"], result
        assert result["counts"]["not_run"] == 1, result
        row = next(c for c in result["cells"] if c["cell_id"] == "A")
        assert row["runtime_state"] == "idle", row
        assert row["errors"] is None, row
        assert row["errors_readable"] is False, row
        assert any("UNVERIFIED" in step for step in result["next_steps"]), result
    finally:
        mock.stop()


async def test_run_cell_report_row_missing_is_unverified_not_succeeded():
    """A target the report omits has no readable channel: not succeeded."""
    from marimo_inspection.tools import mutation

    mock = _run_client(
        _plan_result(["A"], mode="cell"),
        _Result(['{"status": "ok", "queued": ["A"]}']),
        _report_result([]),  # the report produced no row for the target
    )
    try:
        result = await mutation.run_cell("A", server_url="u", session_id="s1")
        assert result["status"] == "partial", result
        assert result["succeeded_cell_ids"] == [], result
        assert result["not_run_cell_ids"] == ["A"], result
        # The state is unknown, not idle, so it is not the idle-unreadable case.
        assert result["unverified_cell_ids"] == [], result
        row = result["cells"][0]
        assert row["errors"] is None and row["errors_readable"] is False, row
        assert row["known"] is False, row
    finally:
        mock.stop()


async def test_delete_cell_ok():
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "C1"}'])
    try:
        result = await mutation.delete_cell("C1", server_url="u", session_id="s")
        assert result["status"] == "ok"
    finally:
        mock_cls.stop()


async def test_edit_cell_conflict_guard():
    """Editing a cell that changed since last read is refused."""
    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    tracker = get_tracker()
    # Simulate: agent previously read cell C1 with hash "oldhash".
    # NOTE: the mocked resolve_session returns session_id "test-session-1".
    tracker.record_cells("test-session-1", {"C1": CellFingerprint(code_hash="oldhash")})

    # Live hashes: C1 now has a different hash -> the cell changed.
    mock_cls = _mock_client(['{"C1": "newhash"}'])
    try:
        result = await mutation.edit_cell(
            "C1", "y = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert result["status"] == "conflict"
        assert "modified since" in result["message"]
        # No edit call should have been made.
        assert mock_cls.instance.execute.call_count == 1  # only the hash read
    finally:
        mock_cls.stop()
        tracker.clear_session("test-session-1")


async def test_edit_cell_ok_when_unchanged():
    """Editing a cell whose hash matches last read proceeds."""
    from marimo_inspection.tools import mutation
    from marimo_inspection.tools.change_tracking import (
        CellFingerprint,
        get_tracker,
    )

    tracker = get_tracker()
    tracker.record_cells("s1", {"C1": CellFingerprint(code_hash="samehash")})

    calls = {"n": 0}

    def _execute(session_id, code):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            # First call: live-hash read (guard) -> matches baseline.
            return _Result(['{"C1": "samehash"}'])
        if n == 2:
            # Second call: the actual edit -> edit result.
            return _Result(
                ['{"status": "ok", "cell_id": "C1", "code_hash": "newhash"}']
            )
        # Third call: post-edit snapshot refresh.
        return _Result(['{"C1": "newhash"}'])

    instance = MagicMock(
        resolve_session=AsyncMock(
            return_value=MagicMock(session_id="s1", basename="test.py")
        ),
        execute=AsyncMock(side_effect=_execute),
    )
    mock_cls = patch("marimo_inspection.tools.mutation.MarimoClient")
    mock_cls.start().return_value = instance
    try:
        result = await mutation.edit_cell(
            "C1", "y = 2", server_url="u", session_id="s1", check_fresh=True
        )
        assert result["status"] == "ok"
        assert result["cell_id"] == "C1"
        assert instance.execute.call_count == 3
    finally:
        mock_cls.stop()
        tracker.clear_session("s1")
