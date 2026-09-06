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


async def test_create_cell_requires_source():
    from marimo_inspection.tools import mutation

    result = await mutation.create_cell("  ", server_url="u", session_id="s")
    assert result["status"] == "error"


async def test_edit_cell_ok():
    from marimo_inspection.tools import mutation

    mock_cls = _mock_client(['{"status": "ok", "cell_id": "C1", "code_hash": "abc"}'])
    try:
        result = await mutation.edit_cell(
            "C1", "y = 2", server_url="u", session_id="s", check_fresh=False
        )
        assert result["status"] == "ok"
        assert result["cell_id"] == "C1"
    finally:
        mock_cls.stop()


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
    tracker.commit("test-session-1", {"C1": CellFingerprint(code_hash="oldhash")})

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
    tracker.commit("s1", {"C1": CellFingerprint(code_hash="samehash")})

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
