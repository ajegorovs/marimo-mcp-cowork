"""Unit tests for the restricted-session guards (``benchmark.session_guard``).

The guards are pure and stdlib-only, so these tests need no kernel, no server
and no package import — they exercise the module directly through the same
importlib loader the other benchmark tests use.
"""

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "benchmark_session_guard", ROOT / "benchmark" / "session_guard.py"
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
# Register before execution so ``@dataclass`` can resolve ``cls.__module__``.
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)

SessionAnchor = guard.SessionAnchor

LF_BYTES = b"import marimo\n\napp = marimo.App()\n"
CRLF_BYTES = b"import marimo\r\n\r\napp = marimo.App()\r\n"


# --------------------------------------------------------------------------- #
# validate_census
# --------------------------------------------------------------------------- #


def test_census_accepts_single_anchored_session():
    anchor = SessionAnchor("abc", "/work/nb.py", 100)
    guard.validate_census({"abc": {"path": "/work/nb.py"}}, anchor)
    # The native census may spell the notebook as ``filename`` instead.
    guard.validate_census({"abc": {"filename": "/work/nb.py"}}, anchor)


def test_census_matches_path_by_resolution():
    anchor = SessionAnchor("abc", "/work/a/b/../nb.py", 100)
    guard.validate_census({"abc": {"path": "/work/a/nb.py"}}, anchor)


def test_census_rejects_multiple_sessions():
    anchor = SessionAnchor("abc", "/work/nb.py", 100)
    census = {"abc": {"path": "/work/nb.py"}, "def": {"path": "/work/other.py"}}
    with pytest.raises(ValueError, match="exactly one session"):
        guard.validate_census(census, anchor)


def test_census_rejects_empty_census():
    anchor = SessionAnchor("abc", "/work/nb.py", 100)
    with pytest.raises(ValueError, match="exactly one session"):
        guard.validate_census({}, anchor)


def test_census_rejects_reused_id_now_denoting_wrong_path():
    anchor = SessionAnchor("abc", "/work/mine.py", 100)
    census = {"abc": {"path": "/work/someone_elses.py"}}
    with pytest.raises(ValueError, match="does not resolve to the anchored"):
        guard.validate_census(census, anchor)


def test_census_rejects_missing_anchor_id():
    anchor = SessionAnchor("abc", "/work/nb.py", 100)
    census = {"def": {"path": "/work/nb.py"}}
    with pytest.raises(ValueError, match="session id"):
        guard.validate_census(census, anchor)


@pytest.mark.parametrize("entry", [{}, {"path": ""}, {"filename": "   "}])
def test_census_rejects_empty_entry_path(entry):
    anchor = SessionAnchor("abc", "/work/nb.py", 100)
    with pytest.raises(ValueError, match="non-empty path or filename"):
        guard.validate_census({"abc": entry}, anchor)


# --------------------------------------------------------------------------- #
# validate_reopen
# --------------------------------------------------------------------------- #


def test_reopen_accepts_rotated_identity():
    previous = SessionAnchor("old", "/work/nb.py", 100)
    current = SessionAnchor("new", "/work/nb.py", 200)
    guard.validate_reopen(previous, current)


def test_reopen_matches_path_by_resolution():
    previous = SessionAnchor("old", "/work/a/nb.py", 100)
    current = SessionAnchor("new", "/work/a/b/../nb.py", 200)
    guard.validate_reopen(previous, current)


def test_reopen_rejects_same_process():
    previous = SessionAnchor("old", "/work/nb.py", 100)
    current = SessionAnchor("new", "/work/nb.py", 100)
    with pytest.raises(ValueError, match="different process"):
        guard.validate_reopen(previous, current)


def test_reopen_rejects_same_session_id():
    previous = SessionAnchor("same", "/work/nb.py", 100)
    current = SessionAnchor("same", "/work/nb.py", 200)
    with pytest.raises(ValueError, match="different session id"):
        guard.validate_reopen(previous, current)


def test_reopen_rejects_different_notebook():
    previous = SessionAnchor("old", "/work/nb.py", 100)
    current = SessionAnchor("new", "/work/moved.py", 200)
    with pytest.raises(ValueError, match="same resolved notebook path"):
        guard.validate_reopen(previous, current)


# --------------------------------------------------------------------------- #
# validate_disposable
# --------------------------------------------------------------------------- #


def _workspace(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "canonical"
    outside.mkdir()
    return root, outside


def test_disposable_returns_canonical_digest_for_lf_copies(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    a = root / "a.py"
    b = root / "b.py"
    a.write_bytes(LF_BYTES)
    b.write_bytes(LF_BYTES)

    digest = guard.validate_disposable(canonical, [a, b], root)

    assert digest == hashlib.sha256(LF_BYTES).hexdigest()


def test_disposable_accepts_crlf_payload_byte_for_byte(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(CRLF_BYTES)
    copy = root / "copy.py"
    copy.write_bytes(CRLF_BYTES)

    assert guard.validate_disposable(canonical, [copy], root) == (
        hashlib.sha256(CRLF_BYTES).hexdigest()
    )


def test_disposable_rejects_byte_mismatch(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    copy = root / "copy.py"
    copy.write_bytes(LF_BYTES + b"# drift\n")

    with pytest.raises(ValueError, match="differs from the canonical"):
        guard.validate_disposable(canonical, [copy], root)


def test_disposable_rejects_missing_copy(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    with pytest.raises(ValueError, match="does not exist"):
        guard.validate_disposable(canonical, [root / "absent.py"], root)


def test_disposable_rejects_canonical_inside_root(tmp_path):
    root, _ = _workspace(tmp_path)
    canonical = root / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    copy = root / "copy.py"
    copy.write_bytes(LF_BYTES)
    with pytest.raises(ValueError, match="outside the disposable root"):
        guard.validate_disposable(canonical, [copy], root)


def test_disposable_rejects_copy_outside_root(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    escaped = outside / "escaped.py"
    escaped.write_bytes(LF_BYTES)
    with pytest.raises(ValueError, match="escapes the disposable root"):
        guard.validate_disposable(canonical, [escaped], root)


def test_disposable_rejects_duplicate_copy_paths(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    copy = root / "copy.py"
    copy.write_bytes(LF_BYTES)
    with pytest.raises(ValueError, match="distinct paths"):
        guard.validate_disposable(canonical, [copy, copy], root)


def test_disposable_rejects_hardlinked_copies(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    first = root / "first.py"
    first.write_bytes(LF_BYTES)
    second = root / "second.py"
    try:
        os.link(first, second)
    except OSError as error:  # pragma: no cover - filesystem without hardlinks
        pytest.skip(f"hard links unavailable: {error}")

    with pytest.raises(ValueError, match="alias the same file"):
        guard.validate_disposable(canonical, [first, second], root)


def test_disposable_rejects_copy_hardlinked_to_canonical(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    copy = root / "copy.py"
    try:
        os.link(canonical, copy)
    except OSError as error:  # pragma: no cover - filesystem without hardlinks
        pytest.skip(f"hard links unavailable: {error}")

    with pytest.raises(ValueError, match="alias the same file"):
        guard.validate_disposable(canonical, [copy], root)


def test_disposable_rejects_empty_copy_list(tmp_path):
    root, outside = _workspace(tmp_path)
    canonical = outside / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    with pytest.raises(ValueError, match="at least one disposable copy"):
        guard.validate_disposable(canonical, [], root)
