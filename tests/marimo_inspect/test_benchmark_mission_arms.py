"""Kernel-free tests for the mission arm setup and grading modules.

Both programs are loaded directly by path (the pattern the other benchmark
tests use). Every test here is pure logic or file-level: no server is launched,
no kernel is booted — the server and kernel interactions sit behind the
``server_factory`` / ``prober`` seams and are replaced with fakes.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[2] / "benchmark"
SETUP_PATH = BENCHMARK / "mission_setup.py"
GRADE_PATH = BENCHMARK / "mission_grade.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before execution so any ``@dataclass`` can resolve ``__module__``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


setup = _load("benchmark_mission_setup", SETUP_PATH)
grade = _load("benchmark_mission_grade", GRADE_PATH)

LF_BYTES = b"import marimo\n\napp = marimo.App()\n"
CRLF_BYTES = b"import marimo\r\n\r\napp = marimo.App()\r\n"
WITH_NORMALIZED = LF_BYTES + b"\nnormalized = 1.0\n"

FIXED_TIME = "2026-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fakes for the injectable seams
# --------------------------------------------------------------------------- #


class _FakeServer:
    """Stands in for ``ServerSession`` without launching anything."""

    def __init__(
        self, notebook, python, state_dir, *, session_id="fake-session", pid=5150
    ):
        self.notebook = Path(notebook)
        self.python = python
        self.state_dir = Path(state_dir)
        self.server_url = "http://127.0.0.1:9"
        self._session_id = session_id
        self._pid = pid
        self.started = False
        self.stopped = False

    @property
    def process_id(self) -> int:
        return self._pid

    def start(self):
        self.started = True

    def create_session(self) -> str:
        return self._session_id

    def census(self) -> dict:
        if not self.started:
            raise AssertionError("census() called before start()")
        return {self._session_id: {"path": str(self.notebook)}}

    def stop(self):
        self.stopped = True


class _BadCensusServer(_FakeServer):
    """A census that carries two sessions — the guard must refuse it."""

    def census(self) -> dict:
        return {
            self._session_id: {"path": str(self.notebook)},
            "intruder": {"path": str(self.notebook.parent / "elsewhere.py")},
        }


def _recording_factory(cls=_FakeServer, **kwargs):
    servers: list[_FakeServer] = []

    def factory(notebook, python, state_dir):
        server = cls(notebook, python, state_dir, **kwargs)
        servers.append(server)
        return server

    return factory, servers


def _stage(tmp_path, payload=LF_BYTES, *, cls=_FakeServer, factory=None):
    canonical = tmp_path / "canonical" / "mission_clean.py"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(payload)
    root = tmp_path / "run"
    state = root / "state"
    manifest_path = root / "manifest.json"
    servers: list[_FakeServer] = []
    if factory is None:
        factory, servers = _recording_factory(cls)
    manifest = setup.perform_setup(
        canonical,
        root,
        "python3",
        "native",
        state,
        manifest_path,
        server_factory=factory,
        clock=lambda: FIXED_TIME,
    )
    return canonical, root, state, manifest_path, manifest, servers


# --------------------------------------------------------------------------- #
# Byte-exact copying and the disposable guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", [LF_BYTES, CRLF_BYTES])
def test_copy_bytes_is_byte_exact(tmp_path, payload):
    source = tmp_path / "source.py"
    source.write_bytes(payload)
    dest = tmp_path / "nested" / "dest.py"

    result = setup.copy_bytes(source, dest)

    assert result == dest
    assert dest.read_bytes() == payload


def test_stage_disposable_is_byte_exact_and_returns_canonical_digest(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(CRLF_BYTES)
    root = tmp_path / "run"

    copy, digest = setup.stage_disposable(canonical, root)

    assert copy.read_bytes() == CRLF_BYTES
    assert digest == hashlib.sha256(CRLF_BYTES).hexdigest()
    assert copy.parent == root


def test_disposable_digest_refuses_differing_copy(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    root = tmp_path / "run"
    root.mkdir()
    copy = root / "copy.py"
    copy.write_bytes(LF_BYTES + b"# drift\n")

    with pytest.raises(ValueError, match="differs from the canonical"):
        setup.disposable_digest(canonical, [copy], root)


def test_disposable_digest_refuses_copy_escaping_root(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    root = tmp_path / "run"
    root.mkdir()
    escaped = tmp_path / "escaped.py"
    escaped.write_bytes(LF_BYTES)

    with pytest.raises(ValueError, match="escapes the disposable root"):
        setup.disposable_digest(canonical, [escaped], root)


# --------------------------------------------------------------------------- #
# perform_setup / manifest
# --------------------------------------------------------------------------- #


def test_manifest_round_trip_and_required_keys(tmp_path):
    canonical, root, _state, manifest_path, manifest, _servers = _stage(tmp_path)

    assert set(setup.MANIFEST_KEYS) <= set(manifest)
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert manifest["arm"] == "native"
    assert manifest["sha256"] == hashlib.sha256(LF_BYTES).hexdigest()
    assert manifest["session_id"] == "fake-session"
    assert manifest["process_id"] == 5150
    assert manifest["server_url"] == "http://127.0.0.1:9"
    assert manifest["python"] == "python3"
    assert manifest["notebook_path"] == str(root / "notebook.py")
    assert manifest["canonical_path"] == str(canonical)
    assert manifest["generated_at"] == FIXED_TIME


def test_setup_stages_copy_and_leaves_server_running(tmp_path):
    canonical, root, _state, _manifest_path, _manifest, servers = _stage(tmp_path)

    staged = root / "notebook.py"
    assert staged.read_bytes() == canonical.read_bytes()
    assert len(servers) == 1
    assert servers[0].started is True
    # The mission runs against the live session: setup must not stop it.
    assert servers[0].stopped is False


def test_setup_refuses_unknown_arm(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    factory, _servers = _recording_factory()
    with pytest.raises(setup.SetupRefusal, match="arm must be one of"):
        setup.perform_setup(
            canonical,
            tmp_path / "run",
            "python3",
            "other",
            tmp_path / "state",
            tmp_path / "manifest.json",
            server_factory=factory,
        )


def test_setup_refuses_non_empty_root(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    root = tmp_path / "run"
    root.mkdir()
    (root / "leftover.txt").write_text("churn", encoding="utf-8")
    factory, _servers = _recording_factory()
    with pytest.raises(setup.SetupRefusal, match="not empty"):
        setup.perform_setup(
            canonical,
            root,
            "python3",
            "native",
            tmp_path / "state",
            tmp_path / "manifest.json",
            server_factory=factory,
        )


def test_setup_stops_server_and_refuses_when_census_is_invalid(tmp_path):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    factory, servers = _recording_factory(cls=_BadCensusServer)

    with pytest.raises(setup.SetupRefusal, match="exactly one session"):
        setup.perform_setup(
            canonical,
            tmp_path / "run",
            "python3",
            "native",
            tmp_path / "state",
            tmp_path / "manifest.json",
            server_factory=factory,
        )

    assert len(servers) == 1
    assert servers[0].started is True
    # A refused stage must stop the server it started and write no manifest.
    assert servers[0].stopped is True
    assert not (tmp_path / "manifest.json").exists()


def test_setup_refuses_flag_rejects_target_selectors():
    with pytest.raises(SystemExit):
        setup.main(
            [
                "--canonical",
                "c.py",
                "--root",
                "r",
                "--python",
                "python3",
                "--arm",
                "native",
                "--state-dir",
                "s",
                "--manifest",
                "m.json",
                "--session",
                "smuggled",
            ]
        )


# --------------------------------------------------------------------------- #
# grade(): verdict composition
# --------------------------------------------------------------------------- #


class _FakeProber:
    """Stands in for ``ArmProber`` — returns a canned result or raises."""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def probe(self, notebook_path):
        if self._error is not None:
            raise self._error
        return self._result


def _grade_case(tmp_path, *, notebook_bytes=WITH_NORMALIZED, arm="native"):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(notebook_bytes)
    manifest = setup.build_manifest(
        server_url="http://127.0.0.1:9",
        session_id="old-session",
        notebook_path=str(notebook),
        process_id=111,
        python="python3",
        sha256=hashlib.sha256(LF_BYTES).hexdigest(),
        arm=arm,
        generated_at=FIXED_TIME,
        canonical_path=str(canonical),
        root=str(tmp_path / "run"),
        state_dir=str(tmp_path / "state"),
    )
    return canonical, notebook, manifest


def _ok_result(notebook):
    return grade.ProbeResult(
        session_id="new-session",
        process_id=222,
        notebook_path=str(notebook),
        gain=2.0,
        normalized=1.25,
        error_count=0,
        run_status="ok",
    )


def _check(verdict, name):
    matches = [c for c in verdict["checks"] if c["name"] == name]
    assert len(matches) == 1, f"expected one {name!r} check"
    return matches[0]


def test_grade_passes_a_healthy_arm(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is True
    assert [c["name"] for c in verdict["checks"]] == list(grade.CHECK_NAMES)
    assert all(c["pass"] for c in verdict["checks"])
    assert verdict["observed"]["normalized"] == 1.25
    assert verdict["observed"]["expected_metric"] == 1.25
    assert verdict["observed"]["manifest_sha256"] == manifest["sha256"]


def test_grade_fails_on_fixture_drift(tmp_path):
    canonical, notebook, manifest = _grade_case(tmp_path)
    canonical.write_bytes(LF_BYTES + b"# drift\n")

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    assert _check(verdict, "canonical_fixture_unchanged")["pass"] is False


def test_grade_fails_when_normalized_cell_missing(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path, notebook_bytes=LF_BYTES)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    assert _check(verdict, "normalized_cell_saved")["pass"] is False


def test_grade_fails_on_wrong_metric(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = grade.ProbeResult("new-session", 222, str(notebook), 2.0, 99.0, 0, "ok")

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_normalized_matches_oracle")["pass"] is False
    assert _check(verdict, "reopen_rotated_identity")["pass"] is True


def test_grade_fails_when_cell_errors_present(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = grade.ProbeResult("new-session", 222, str(notebook), 2.0, 1.25, 1, "ok")

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_zero_cell_errors")["pass"] is False


def test_grade_fails_when_process_id_unchanged(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    # Same process id as the setup manifest: not a reopen.
    result = grade.ProbeResult(
        "new-session", manifest["process_id"], str(notebook), 2.0, 1.25, 0, "ok"
    )

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "reopen_rotated_identity")["pass"] is False


def test_grade_fails_when_session_id_unchanged(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = grade.ProbeResult(
        manifest["session_id"], 222, str(notebook), 2.0, 1.25, 0, "ok"
    )

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "reopen_rotated_identity")["pass"] is False


def test_grade_fails_closed_when_probe_errors(tmp_path):
    _canonical, _notebook, manifest = _grade_case(tmp_path)

    verdict = grade.grade(
        manifest, _FakeProber(error=RuntimeError("cannot boot a fresh server"))
    )

    assert verdict["pass"] is False
    for name in (
        "reopen_rotated_identity",
        "kernel_normalized_matches_oracle",
        "kernel_gain_is_default",
        "kernel_zero_cell_errors",
    ):
        assert _check(verdict, name)["pass"] is False
    assert "normalized" not in verdict["observed"]


def test_grade_never_passes_without_the_full_check_set(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))
    assert len(verdict["checks"]) == len(grade.CHECK_NAMES)
    assert verdict["pass"] is all(c["pass"] for c in verdict["checks"])


# --------------------------------------------------------------------------- #
# grade.main exit codes
# --------------------------------------------------------------------------- #


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_main_exits_zero_on_a_passing_arm(tmp_path, monkeypatch):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    manifest_path = _write_manifest(tmp_path, manifest)
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )

    code = grade.main(
        [
            "--manifest",
            str(manifest_path),
            "--python",
            "python3",
            "--state-dir",
            str(tmp_path / "s"),
            "--allow-unverified-anchor",
        ]
    )

    assert code == 0


def test_main_exits_nonzero_on_a_failed_check(tmp_path, monkeypatch):
    canonical, _notebook, manifest = _grade_case(tmp_path)
    manifest_path = _write_manifest(tmp_path, manifest)
    canonical.write_bytes(LF_BYTES + b"# drift\n")  # force the drift check to fail
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(error=RuntimeError("no boot")),
    )

    code = grade.main(
        [
            "--manifest",
            str(manifest_path),
            "--python",
            "python3",
            "--state-dir",
            str(tmp_path / "s"),
            "--allow-unverified-anchor",
        ]
    )

    assert code == 1


def test_main_exits_nonzero_on_unreadable_manifest(tmp_path):
    code = grade.main(
        ["--manifest", str(tmp_path / "absent.json"), "--python", "python3"]
    )
    assert code == 1


# --------------------------------------------------------------------------- #
# Reader selection: the fresh-kernel read follows the arm
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_default_reader_follows_manifest_arm(arm):
    assert grade.resolve_reader(None, {"arm": arm}) == arm


@pytest.mark.parametrize("arm", ["native", "wrapper"])
@pytest.mark.parametrize("reader", ["native", "wrapper"])
def test_explicit_reader_overrides_manifest_arm(reader, arm):
    manifest = {"arm": arm}
    assert grade.resolve_reader(reader, manifest) == reader


def test_unknown_reader_is_refused():
    with pytest.raises(grade.GradeRefusal, match="reader must be one of"):
        grade.resolve_reader("serial", {"arm": "native"})
    # An unknown manifest arm must not silently fall back either.
    with pytest.raises(grade.GradeRefusal, match="arm must be one of"):
        grade.resolve_reader(None, {"arm": "serial"})


def test_missing_reader_value_is_refused():
    with pytest.raises(grade.GradeRefusal, match="arm must be one of"):
        grade.resolve_reader(None, {})
    # The CLI also refuses a bare ``--reader`` with no value.
    with pytest.raises(SystemExit):
        grade._parse_args(["--manifest", "m.json", "--python", "python3", "--reader"])


def test_main_refuses_an_unknown_reader_choice(tmp_path):
    _canonical, _notebook, manifest = _grade_case(tmp_path)
    manifest_path = _write_manifest(tmp_path, manifest)

    with pytest.raises(SystemExit):
        grade.main(
            [
                "--manifest",
                str(manifest_path),
                "--python",
                "python3",
                "--reader",
                "bogus",
            ]
        )


def test_main_default_reader_sends_a_wrapper_arm_to_the_wrapper_prober(
    tmp_path, monkeypatch
):
    _canonical, notebook, manifest = _grade_case(tmp_path, arm="wrapper")
    manifest_path = _write_manifest(tmp_path, manifest)
    monkeypatch.setattr(
        grade,
        "WrapperProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("native prober used for a wrapper arm"),
    )

    code = grade.main(
        [
            "--manifest",
            str(manifest_path),
            "--python",
            "python3",
            "--state-dir",
            str(tmp_path / "s"),
            "--allow-unverified-anchor",
        ]
    )

    assert code == 0


def test_main_explicit_reader_overrides_a_native_arm(tmp_path, monkeypatch):
    _canonical, notebook, manifest = _grade_case(tmp_path, arm="native")
    manifest_path = _write_manifest(tmp_path, manifest)
    monkeypatch.setattr(
        grade,
        "WrapperProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("manifest arm was not overridden"),
    )

    code = grade.main(
        [
            "--manifest",
            str(manifest_path),
            "--python",
            "python3",
            "--state-dir",
            str(tmp_path / "s"),
            "--reader",
            "wrapper",
            "--allow-unverified-anchor",
        ]
    )

    assert code == 0


# --------------------------------------------------------------------------- #
# Payload mapping: both readers resolve to the same shape and verdict
# --------------------------------------------------------------------------- #

# A native reader payload: the JSON envelope's ``stdout`` carries the
# ``label <value>`` result lines.
NATIVE_HEALTHY = {
    "success": True,
    "stdout": "gain 2\nnormalized 1.25\nerrors 0\n",
}

# A wrapper reader payload: the package handlers' dicts. ``gain`` is a UI
# element, so its ``.value`` is serialized one level deeper than ``normalized``.
WRAPPER_HEALTHY_VARIABLES = {
    "session_id": "new-session",
    "variables": {
        "gain": {"value": {"value": "2", "datatype": "int"}, "datatype": "Slider"},
        "normalized": {"value": "1.25", "datatype": "float"},
    },
    "tables": {},
}
WRAPPER_HEALTHY_ERRORS = {"session_id": "new-session", "total_errors": 0, "cells": []}

# A wrapper reader run payload: ``run_cell`` reports ``ok`` only when every
# requested cell is idle. ``partial`` means at least one failed or never ran.
WRAPPER_HEALTHY_RUN = {
    "session_id": "new-session",
    "status": "ok",
    "counts": {"requested": 3, "succeeded": 3, "failed": 0},
}
WRAPPER_PARTIAL_RUN = {
    "session_id": "new-session",
    "status": "partial",
    "counts": {"requested": 3, "succeeded": 2, "failed": 1},
}

# A native payload that does not claim success: the mapping must never call it a
# completed run.
NATIVE_PARTIAL = {
    "success": False,
    "stdout": "gain 2\nnormalized 1.25\nerrors 0\n",
}


def test_both_readers_map_identical_inputs_to_the_same_values():
    native = grade.native_kernel_values(NATIVE_HEALTHY)
    wrapper = grade.wrapper_kernel_values(
        WRAPPER_HEALTHY_VARIABLES, WRAPPER_HEALTHY_ERRORS
    )

    assert native == wrapper == (2.0, 1.25, 0)
    # Healthy inputs from either reader also report a completed run.
    assert grade.native_run_status(NATIVE_HEALTHY) in grade.RUN_OK_STATUSES
    assert grade.wrapper_run_status(WRAPPER_HEALTHY_RUN) in grade.RUN_OK_STATUSES


def test_both_readers_payload_mapping_produces_the_same_verdict(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    native_values = grade.native_kernel_values(NATIVE_HEALTHY)
    wrapper_values = grade.wrapper_kernel_values(
        WRAPPER_HEALTHY_VARIABLES, WRAPPER_HEALTHY_ERRORS
    )
    native_result = grade.probe_result(
        session_id="new-session",
        process_id=222,
        notebook_path=str(notebook),
        values=native_values,
        run_status=grade.native_run_status(NATIVE_HEALTHY),
    )
    wrapper_result = grade.probe_result(
        session_id="new-session",
        process_id=222,
        notebook_path=str(notebook),
        values=wrapper_values,
        run_status=grade.wrapper_run_status(WRAPPER_HEALTHY_RUN),
    )

    native_verdict = grade.grade(manifest, _FakeProber(result=native_result))
    wrapper_verdict = grade.grade(manifest, _FakeProber(result=wrapper_result))

    assert native_verdict == wrapper_verdict
    assert native_verdict["pass"] is True
    assert [c["name"] for c in native_verdict["checks"]] == list(grade.CHECK_NAMES)
    # Both producers name the same completed status, and it lands in the detail.
    assert (
        "run status: ok" in _check(native_verdict, "kernel_zero_cell_errors")["detail"]
    )
    assert (
        "run status: ok" in _check(wrapper_verdict, "kernel_zero_cell_errors")["detail"]
    )


def test_both_readers_map_cell_errors_identically(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    native_payload = {
        "success": True,
        "stdout": "gain 2\nnormalized 1.25\nerrors 2\n",
    }
    wrapper_errors = {"session_id": "new-session", "total_errors": 2, "cells": []}

    native_values = grade.native_kernel_values(native_payload)
    wrapper_values = grade.wrapper_kernel_values(
        WRAPPER_HEALTHY_VARIABLES, wrapper_errors
    )
    assert native_values == wrapper_values == (2.0, 1.25, 2)

    native_verdict = grade.grade(
        manifest,
        _FakeProber(
            result=grade.probe_result(
                session_id="new-session",
                process_id=222,
                notebook_path=str(notebook),
                values=native_values,
                run_status=grade.native_run_status(native_payload),
            )
        ),
    )
    wrapper_verdict = grade.grade(
        manifest,
        _FakeProber(
            result=grade.probe_result(
                session_id="new-session",
                process_id=222,
                notebook_path=str(notebook),
                values=wrapper_values,
                run_status=grade.wrapper_run_status(WRAPPER_HEALTHY_RUN),
            )
        ),
    )

    assert native_verdict == wrapper_verdict
    assert native_verdict["pass"] is False
    assert _check(native_verdict, "kernel_zero_cell_errors")["pass"] is False
    assert (
        "2 cell error(s) present"
        in _check(native_verdict, "kernel_zero_cell_errors")["detail"]
    )


def test_wrapper_mapping_refuses_a_missing_value():
    variables = {
        "variables": {"gain": {"value": {"value": "2"}, "datatype": "Slider"}},
    }
    with pytest.raises(grade.GradeRefusal, match="no 'normalized' variable"):
        grade.wrapper_kernel_values(variables, WRAPPER_HEALTHY_ERRORS)
    with pytest.raises(grade.GradeRefusal, match="get_variables failed"):
        grade.wrapper_kernel_values(
            {"error": "Failed to parse variables result"}, WRAPPER_HEALTHY_ERRORS
        )


# --------------------------------------------------------------------------- #
# The control copy: an anchor the arm is not pointed at
# --------------------------------------------------------------------------- #


def _staged_arm(tmp_path, *, arm="native", control=True):
    """Stage a real arm through ``perform_setup``: manifest file + control copy.

    The canonical fixture carries the ``normalized`` marker, so the byte-exact
    copy the fake server opens satisfies the saved-cell check too. No server is
    launched and no kernel is booted.
    """
    canonical = tmp_path / "canonical" / "mission_clean.py"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(WITH_NORMALIZED)
    root = tmp_path / "run"
    manifest_path = root / "manifest.json"
    control_dir = tmp_path / "control" if control else None
    factory, _servers = _recording_factory()
    manifest = setup.perform_setup(
        canonical,
        root,
        "python3",
        arm,
        root / "state",
        manifest_path,
        server_factory=factory,
        clock=lambda: FIXED_TIME,
        control_dir=control_dir,
    )
    return canonical, root, manifest_path, manifest, root / "notebook.py", control_dir


def _grade_argv(tmp_path, manifest_path, *extra):
    return [
        "--manifest",
        str(manifest_path),
        "--python",
        "python3",
        "--state-dir",
        str(tmp_path / "grade-state"),
        *extra,
    ]


def test_setup_writes_the_control_copy_off_the_arm_path(tmp_path):
    _canonical, root, manifest_path, manifest, notebook, control_dir = _staged_arm(
        tmp_path
    )

    anchor_path = control_dir / setup.CONTROL_FILENAME
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))

    assert set(setup.CONTROL_KEYS) == set(anchor)
    assert (
        anchor["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    for key in setup.CONTROL_IDENTITY_KEYS:
        if key == "notebook_path":
            # Recorded resolved: the control copy pins the file, not the spelling.
            assert anchor[key] == str(notebook.resolve())
        else:
            assert anchor[key] == manifest[key]
    # The arm-visible manifest is unchanged: still exactly its documented keys,
    # and no copy of the anchor inside the disposable root the arm can reach.
    assert set(manifest) == set(setup.MANIFEST_KEYS) | {
        "canonical_path",
        "root",
        "state_dir",
    }
    assert set(json.loads(manifest_path.read_text(encoding="utf-8"))) == set(manifest)
    assert not (root / setup.CONTROL_FILENAME).exists()


def test_grade_accepts_a_control_copy_that_still_pins_the_manifest(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, notebook, control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )

    code = grade.main(
        _grade_argv(tmp_path, manifest_path, "--control-dir", str(control_dir))
    )

    assert code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["pass"] is True
    assert verdict["observed"]["anchor"] == "control_verified"


def test_build_control_anchor_refuses_a_manifest_it_cannot_pin(tmp_path):
    _canonical, _root, _manifest_path, manifest, _notebook, _control_dir = _staged_arm(
        tmp_path
    )
    digest = hashlib.sha256(b"manifest bytes").hexdigest()

    with pytest.raises(setup.SetupRefusal, match="carries no \\['arm'\\]"):
        setup.build_control_anchor(
            {key: value for key, value in manifest.items() if key != "arm"}, digest
        )
    with pytest.raises(setup.SetupRefusal, match="process_id is not an int"):
        setup.build_control_anchor({**manifest, "process_id": "not-a-pid"}, digest)
    with pytest.raises(setup.SetupRefusal, match="non-empty manifest sha256"):
        setup.build_control_anchor(manifest, "")


def test_grade_refuses_when_the_control_copy_is_missing(tmp_path, capsys, monkeypatch):
    _canonical, _root, manifest_path, _manifest, _notebook, _control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("probe must not run before the anchor"),
    )

    code = grade.main(
        _grade_argv(
            tmp_path, manifest_path, "--control-dir", str(tmp_path / "no-control")
        )
    )

    assert code == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["error"] == "anchor_refused"
    assert grade.CONTROL_FILENAME in error["detail"]
    assert captured.out == ""


def test_grade_refuses_when_the_manifest_bytes_moved_under_the_control_copy(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, manifest, _notebook, control_dir = _staged_arm(
        tmp_path
    )
    # Same identity, different bytes: the control copy records the digest of the
    # bytes the setup wrote, so a rewritten manifest is a moved anchor.
    manifest_path.write_text(json.dumps(manifest, indent=4), encoding="utf-8")
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("probe must not run before the anchor"),
    )

    code = grade.main(
        _grade_argv(tmp_path, manifest_path, "--control-dir", str(control_dir))
    )

    assert code == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["error"] == "anchor_refused"
    assert "do not match the control anchor" in error["detail"]
    assert captured.out == ""


def test_grade_refuses_when_a_control_identity_field_disagrees(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, _notebook, control_dir = _staged_arm(
        tmp_path
    )
    anchor_path = control_dir / setup.CONTROL_FILENAME
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["session_id"] = "moved-session"
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("probe must not run before the anchor"),
    )

    code = grade.main(
        _grade_argv(tmp_path, manifest_path, "--control-dir", str(control_dir))
    )

    assert code == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["error"] == "anchor_refused"
    assert "session_id mismatch" in error["detail"]
    assert captured.out == ""


def test_verify_control_anchor_compares_paths_by_identity_not_spelling(tmp_path):
    _canonical, root, manifest_path, manifest, notebook, control_dir = _staged_arm(
        tmp_path
    )
    # A manifest that spells the same notebook relatively still agrees: the
    # comparison is on resolved paths, which is what makes it identity.
    respelled = dict(manifest)
    respelled["notebook_path"] = os.path.relpath(notebook, Path(os.getcwd()))

    anchor = grade.verify_control_anchor(control_dir, manifest_path, respelled)

    assert anchor["notebook_path"] == str(notebook.resolve())
    assert root in notebook.parents


def test_grade_refuses_an_unverified_anchor_without_the_flag(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, _notebook, _control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: pytest.fail("probe must not run unverified"),
    )

    code = grade.main(_grade_argv(tmp_path, manifest_path))

    assert code == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["error"] == "anchor_unverified"
    assert "--allow-unverified-anchor" in error["detail"]
    assert captured.out == ""


def test_allow_unverified_anchor_grades_and_records_the_unverified_anchor(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, notebook, _control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )

    code = grade.main(_grade_argv(tmp_path, manifest_path, "--allow-unverified-anchor"))

    assert code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["pass"] is True
    # The verdict says out loud that the manifest was the only anchor.
    assert verdict["observed"]["anchor"] == "unverified"


def test_a_control_copy_is_preferred_over_the_unverified_flag(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, notebook, control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )

    code = grade.main(
        _grade_argv(
            tmp_path,
            manifest_path,
            "--control-dir",
            str(control_dir),
            "--allow-unverified-anchor",
        )
    )

    assert code == 0
    assert (
        json.loads(capsys.readouterr().out)["observed"]["anchor"] == "control_verified"
    )


# --------------------------------------------------------------------------- #
# A partial run-all is a failed check, never a pass
# --------------------------------------------------------------------------- #


def test_partial_run_status_fails_the_check_in_both_readers(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    native_status = grade.native_run_status(NATIVE_PARTIAL)
    wrapper_status = grade.wrapper_run_status(WRAPPER_PARTIAL_RUN)
    assert native_status == wrapper_status == "partial"

    for reader, status in (("native", native_status), ("wrapper", wrapper_status)):
        result = grade.probe_result(
            session_id="new-session",
            process_id=222,
            notebook_path=str(notebook),
            values=(2.0, 1.25, 0),  # zero structured errors: the partial marker
            run_status=status,  # is the only failure signal here
        )

        verdict = grade.grade(manifest, _FakeProber(result=result))

        assert verdict["pass"] is False, reader
        failed = _check(verdict, "kernel_zero_cell_errors")
        assert failed["pass"] is False, reader
        assert status in failed["detail"], reader
        # Everything else about this read is healthy.
        assert _check(verdict, "kernel_normalized_matches_oracle")["pass"] is True


def test_a_completed_run_status_passes_and_is_recorded_in_the_detail(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    for status in grade.RUN_OK_STATUSES:
        result = grade.probe_result(
            session_id="new-session",
            process_id=222,
            notebook_path=str(notebook),
            values=(2.0, 1.25, 0),
            run_status=status,
        )

        verdict = grade.grade(manifest, _FakeProber(result=result))

        assert verdict["pass"] is True, status
        detail = _check(verdict, "kernel_zero_cell_errors")["detail"]
        assert f"run status: {status}" in detail


def test_native_reader_takes_the_status_from_the_run_all_envelope(tmp_path):
    """The live native reader reports the run-all's status, not the read's.

    ``_read_kernel`` is driven with an injected ``_execute``, so the real reader
    plumbing runs with no server and no kernel.
    """
    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(WITH_NORMALIZED)
    prober = grade.ArmProber("python3", tmp_path / "state")
    queued = [
        {"success": True, "status": "partial", "stdout": ""},  # the run-all
        {"success": True, "stdout": "gain 2\nnormalized 1.25\nerrors 0\n"},  # the read
    ]
    seen = []

    def fake_execute(env, url, notebook_arg, code):
        seen.append(code)
        return queued.pop(0)

    prober._execute = fake_execute
    values, run_status = prober._read_kernel(
        {}, "http://127.0.0.1:9", notebook, "fresh-session"
    )

    assert seen == [grade._RUN_ALL_CELLS, grade._READ_VALUES]
    assert values == (2.0, 1.25, 0)
    assert run_status == "partial"


def test_wrapper_run_status_refuses_a_payload_without_a_status():
    with pytest.raises(grade.GradeRefusal, match="reported no status"):
        grade.wrapper_run_status({"counts": {"requested": 1}})
    with pytest.raises(grade.GradeRefusal, match="no payload"):
        grade.wrapper_run_status("partial")


# --------------------------------------------------------------------------- #
# The oracle is inside the guard: a JSON verdict always comes out
# --------------------------------------------------------------------------- #


class _BrokenOracle:
    """Stands in for a missing or failing ``benchmark/oracle.py``."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def expected_metric(self, gain):
        raise self._exc


def test_grade_reports_a_failed_check_when_the_oracle_cannot_load(
    tmp_path, monkeypatch
):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    monkeypatch.setattr(
        grade,
        "_oracle",
        lambda: _BrokenOracle(
            grade.GradeRefusal("cannot load sibling module oracle.py")
        ),
    )

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    assert [c["name"] for c in verdict["checks"]] == list(grade.CHECK_NAMES)
    failed = _check(verdict, "kernel_normalized_matches_oracle")
    assert failed["pass"] is False
    assert "oracle unavailable" in failed["detail"]
    assert "expected_metric" not in verdict["observed"]


def test_oracle_failure_still_prints_a_verdict_and_exits_nonzero(
    tmp_path, capsys, monkeypatch
):
    _canonical, _root, manifest_path, _manifest, notebook, _control_dir = _staged_arm(
        tmp_path
    )
    monkeypatch.setattr(
        grade,
        "ArmProber",
        lambda python, state_dir: _FakeProber(result=_ok_result(notebook)),
    )
    # Any oracle failure — not just the missing-file refusal — is a failed check.
    monkeypatch.setattr(
        grade, "_oracle", lambda: _BrokenOracle(RuntimeError("oracle.py exploded"))
    )

    code = grade.main(_grade_argv(tmp_path, manifest_path, "--allow-unverified-anchor"))

    assert code == 1
    captured = capsys.readouterr()
    verdict = json.loads(captured.out)  # a JSON verdict, not a traceback
    assert verdict["pass"] is False
    failed = _check(verdict, "kernel_normalized_matches_oracle")
    assert failed["pass"] is False
    assert "oracle.py exploded" in failed["detail"]


# --------------------------------------------------------------------------- #
# Setup refuses a file where a directory belongs — as JSON, not a traceback
# --------------------------------------------------------------------------- #


def test_setup_refuses_a_root_path_that_is_not_a_directory(tmp_path, capsys):
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(LF_BYTES)
    root = tmp_path / "run"
    root.write_text("a file, not a directory", encoding="utf-8")
    factory, servers = _recording_factory()

    with pytest.raises(setup.SetupRefusal, match="not a directory"):
        setup.perform_setup(
            canonical,
            root,
            "python3",
            "native",
            tmp_path / "state",
            tmp_path / "manifest.json",
            server_factory=factory,
        )
    # Refused before the factory ran: no server to leak.
    assert servers == []

    code = setup.main(
        [
            "--canonical",
            str(canonical),
            "--root",
            str(root),
            "--python",
            "python3",
            "--arm",
            "native",
            "--state-dir",
            str(tmp_path / "state"),
            "--manifest",
            str(tmp_path / "manifest.json"),
        ]
    )

    assert code == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "setup_refused"
    assert "not a directory" in error["detail"]


# --------------------------------------------------------------------------- #
# Privacy: no machine-specific absolute paths in either module
# --------------------------------------------------------------------------- #

_MACHINE_PATTERNS = (
    # A Windows drive-letter path (a letter, colon, separator) — the negative
    # look-behind keeps a URL scheme such as ``http://`` from matching.
    re.compile(r"(?<![\w/])[A-Za-z]:[\\/][^\s\"']"),
    re.compile(r"/home/[A-Za-z]"),  # Unix home
    re.compile(r"/Users/[A-Za-z]"),  # macOS home
    re.compile(r"/root/"),
    re.compile(r"\btailscale\b"),
    re.compile(r"\b(?:100|10|172|192)\.\d"),  # non-loopback private IPv4 literals
    re.compile(r"\bmhd01\b"),  # any concrete machine user name
)


def test_modules_carry_no_machine_specific_paths():
    for path in (SETUP_PATH, GRADE_PATH):
        text = path.read_text(encoding="utf-8")
        for pattern in _MACHINE_PATTERNS:
            match = pattern.search(text)
            assert match is None, (
                f"{path.name} matches {pattern.pattern}: {match.group(0)!r}"
            )
