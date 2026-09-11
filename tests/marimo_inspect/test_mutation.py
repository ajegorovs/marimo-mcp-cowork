"""Tests for the notebook mutation tools (create/edit/run/delete)."""

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
        build_run_cell_template,
    )

    assert "ctx.run_cell" in build_run_cell_template("C1")
    assert "ctx.delete_cell" in build_delete_cell_template("C1")


def test_templates_are_valid_python():
    import ast

    from marimo_inspection.templates.mutation import (
        build_cell_hashes_template,
        build_create_cell_template,
        build_delete_cell_template,
        build_edit_cell_template,
        build_run_cell_template,
    )

    for code in [
        build_cell_hashes_template(),
        build_create_cell_template("x=1"),
        build_edit_cell_template("C1", "y=2"),
        build_run_cell_template("C1"),
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


async def test_run_cell_ok():
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "C1"}'])
    try:
        result = await mutation.run_cell("C1", server_url="u", session_id="s")
        assert result["status"] == "ok"
    finally:
        mock_cls.stop()


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
