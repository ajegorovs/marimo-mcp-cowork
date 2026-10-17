"""Kernel-free tests for the mission arm setup and grading modules.

Both programs are loaded directly by path (the pattern the other benchmark
tests use). Every test here is pure logic or file-level: no server is launched,
no kernel is booted — the server and kernel interactions sit behind the
``server_factory`` / ``prober`` seams and are replaced with fakes.
"""

import asyncio
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
FIXTURE_PATH = BENCHMARK / "notebooks" / "mission_clean.py"


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

# The REAL fixture, plus one appended cell in the shape marimo saves: the
# ``__main__`` guard stays last, so a real saved cell sits before it (a
# function after the guard is not a marimo cell — see
# ``grade.document_cell_sources``).
FIXTURE_TEXT = FIXTURE_PATH.read_text(encoding="utf-8")
_MAIN_GUARD = 'if __name__ == "__main__":'

AGENT_CELL = (
    "@app.cell\n"
    "def _(input_vector, processed):\n"
    "    normalized = float(abs(processed).mean() / abs(input_vector).max())\n"
    "    normalized  # noqa: B018 - marimo renders the last expression\n"
    "    return (normalized,)"
)
# A hardcoded diagnostic: assigns ``normalized`` and reads neither input.
CONSTANT_CELL = "@app.cell\ndef _():\n    normalized = 1.25\n    return (normalized,)"
# The word only in a comment: not in the AST at all.
COMMENT_ONLY = "# normalized is computed in a cell that does not exist"
# A cell that reads only one of the two inputs.
HALF_CELL = (
    "@app.cell\n"
    "def _(processed):\n"
    "    normalized = float(abs(processed).mean())\n"
    "    return (normalized,)"
)


def _fixture_with_cell(cell: str) -> bytes:
    """The real fixture plus one appended cell, before the ``__main__`` guard."""
    head, _, tail = FIXTURE_TEXT.rpartition(_MAIN_GUARD)
    return (
        head.rstrip("\n") + "\n\n" + cell.strip("\n") + "\n" + _MAIN_GUARD + tail
    ).encode()


# A structurally valid saved notebook (fixture + the mission's own expression),
# and the two structurally invalid shapes the old byte-substring test passed.
WITH_NORMALIZED = _fixture_with_cell(AGENT_CELL)
WITH_CONSTANT_NORMALIZED = _fixture_with_cell(CONSTANT_CELL)
WITH_COMMENT_NORMALIZED = _fixture_with_cell(COMMENT_ONLY)
WITH_HALF_NORMALIZED = _fixture_with_cell(HALF_CELL)

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


def _stage(tmp_path, payload=LF_BYTES, *, cls=_FakeServer, factory=None, arm="native"):
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
        arm,
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


@pytest.mark.parametrize("arm", list(setup.ARMS))
def test_manifest_records_the_arm_ledger_for_both_arms(tmp_path, arm):
    """``root/ledger.jsonl`` is recorded for both arms, created for neither."""
    _canonical, root, _state, manifest_path, manifest, _servers = _stage(
        tmp_path, arm=arm
    )
    expected = str(root / setup.LEDGER_FILENAME)

    assert setup.LEDGER_FILENAME == "ledger.jsonl"
    assert manifest["ledger_path"] == expected
    assert manifest["arm"] == arm
    # Recorded, not created: the shim writes it on its first call, so the file
    # a native run leaves behind either carries calls or does not exist.
    assert not Path(expected).exists()
    assert (
        json.loads(manifest_path.read_text(encoding="utf-8"))["ledger_path"] == expected
    )


def _manifest_kwargs(tmp_path, **overrides):
    """The keyword arguments ``build_manifest`` needs, valid but for *overrides*."""
    base = {
        "server_url": "http://127.0.0.1:9",
        "session_id": "fake-session",
        "notebook_path": str(tmp_path / "run" / "notebook.py"),
        "process_id": 5150,
        "python": "python3",
        "sha256": hashlib.sha256(LF_BYTES).hexdigest(),
        "arm": "native",
        "generated_at": FIXED_TIME,
        "canonical_path": str(tmp_path / "canonical.py"),
        "root": str(tmp_path / "run"),
        "state_dir": str(tmp_path / "run" / "state"),
        "ledger_path": str(tmp_path / "run" / "ledger.jsonl"),
    }
    base.update(overrides)
    return base


def test_build_manifest_requires_the_ledger_path(tmp_path):
    """No default: a manifest cannot be written without naming its ledger."""
    kwargs = _manifest_kwargs(tmp_path)
    del kwargs["ledger_path"]

    with pytest.raises(TypeError):
        setup.build_manifest(**kwargs)


def test_build_manifest_refuses_a_ledger_path_that_is_not_absolute(tmp_path):
    """The arm reaches it through a launcher run from its own directory."""
    with pytest.raises(
        setup.SetupRefusal, match="ledger_path must be an absolute path"
    ):
        setup.build_manifest(**_manifest_kwargs(tmp_path, ledger_path="ledger.jsonl"))
    with pytest.raises(setup.SetupRefusal, match="ledger_path must be a non-empty"):
        setup.build_manifest(**_manifest_kwargs(tmp_path, ledger_path="   "))


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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ledger_entry(seq, command, before, after, *, created_cell_ids=(), **extra):
    """One shim ledger entry, in the shape ``pair_cli`` appends."""
    entry = {
        "seq": seq,
        "ts": FIXED_TIME,
        "command": command,
        "status": "ok",
        "notebook_path": "/run/notebook.py",
        "sha256_before": before,
        "sha256_after": after,
        "created_cell_ids": list(created_cell_ids),
    }
    entry.update(extra)
    return entry


def _healthy_ledger(notebook_bytes):
    """A ledger that chains from the fixture hash to the unchanged notebook.

    The mission's own sequence: ``create_cell`` writes the diagnostic cell and a
    read-only call follows it, with every hash equal because the saved bytes no
    longer change after the write. The entry carries the source it submitted,
    because the grader attributes the cell on disk to a mediated call: a ledger
    whose recorded code matches nothing on disk proves nothing.
    """
    text = (
        notebook_bytes.decode("utf-8")
        if isinstance(notebook_bytes, bytes)
        else notebook_bytes
    )
    digest = _sha256(notebook_bytes)
    return [
        _ledger_entry(
            1,
            "create_cell",
            digest,
            digest,
            created_cell_ids=["cell-1"],
            code=grade.normalized_cell_source(text) or "normalized = 1.25",
        ),
        _ledger_entry(2, "variables", digest, digest),
    ]


def _write_ledger(path, entries):
    """Write *entries* as the JSONL ledger the shim appends to."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )
    return path


def _grade_case(
    tmp_path,
    *,
    notebook_bytes=WITH_NORMALIZED,
    arm="native",
    ledger=("healthy",),
    ledger_path=None,
    drop_ledger_key=False,
):
    """A manifest plus its artifacts, as ``mission_setup`` + the shim leave them.

    ``ledger`` selects the audit trail: ``("healthy",)`` writes a ledger that
    chains correctly to *notebook_bytes*, a list of entry mappings is written
    verbatim, and ``None`` writes no file at all (a native arm that was never
    audited). ``ledger_path`` overrides the manifest's recorded path and
    ``drop_ledger_key`` removes the key entirely.
    """
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(notebook_bytes)
    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(notebook_bytes)
    root = tmp_path / "run"
    recorded = ledger_path if ledger_path is not None else root / "ledger.jsonl"
    manifest = setup.build_manifest(
        server_url="http://127.0.0.1:9",
        session_id="old-session",
        notebook_path=str(notebook),
        process_id=111,
        python="python3",
        # The staged copy IS the canonical fixture's bytes, so the manifest's
        # ``sha256`` is the disposable copy's initial hash — the value the
        # ledger's first entry must carry.
        sha256=hashlib.sha256(notebook_bytes).hexdigest(),
        arm=arm,
        generated_at=FIXED_TIME,
        canonical_path=str(canonical),
        root=str(root),
        state_dir=str(tmp_path / "state"),
        ledger_path=str(recorded),
    )
    if drop_ledger_key:
        manifest.pop("ledger_path")
    if ledger == ("healthy",):
        _write_ledger(recorded, _healthy_ledger(notebook_bytes))
    elif ledger is not None:
        _write_ledger(recorded, ledger)
    return canonical, notebook, manifest


def _result(notebook, **overrides):
    """A healthy probe result, including the grader-driven reactivity sequence.

    Each override names ONE field, so a negative regression changes exactly the
    read it is about — ``normalized_at_4=1.25`` is a hardcoded diagnostic that
    never followed the slider.
    """
    fields = {
        "session_id": "new-session",
        "process_id": 222,
        "notebook_path": str(notebook),
        "gain": 2.0,
        "normalized": 1.25,
        "error_count": 0,
        "run_status": "ok",
        "gain_at_4": 4.0,
        "normalized_at_4": 2.5,
        "gain_restored": 2.0,
        "normalized_restored": 1.25,
    }
    fields.update(overrides)
    return grade.ProbeResult(**fields)


def _ok_result(notebook):
    """A healthy arm: the saved notebook reflects the fixture's reactivity."""
    return _result(notebook)


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
    # The grader-driven sequence is recorded: the mission's own two numbers.
    assert verdict["observed"]["normalized_at_4"] == 2.5
    assert verdict["observed"]["expected_metric_at_gain_4"] == 2.5
    assert verdict["observed"]["gain_restored"] == 2
    assert verdict["observed"]["normalized_cell_structural"] is True


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
    result = _result(notebook, normalized=99.0)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_normalized_matches_oracle")["pass"] is False
    assert _check(verdict, "reopen_rotated_identity")["pass"] is True


def test_grade_fails_when_cell_errors_present(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = _result(notebook, error_count=1)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_zero_cell_errors")["pass"] is False


def test_grade_fails_when_process_id_unchanged(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    # Same process id as the setup manifest: not a reopen.
    result = _result(notebook, process_id=manifest["process_id"])

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "reopen_rotated_identity")["pass"] is False


def test_grade_fails_when_session_id_unchanged(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = _result(notebook, session_id=manifest["session_id"])

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
        "kernel_reactivity_at_gain_4",
        "kernel_restored_to_default",
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
# The saved cell is checked STRUCTURALLY, not by a byte substring
# --------------------------------------------------------------------------- #


def _block_marimo(monkeypatch):
    """Make ``marimo._ast.parse`` genuinely unimportable here.

    A ``None`` entry in ``sys.modules`` makes the real ``from marimo._ast.parse
    import parse_notebook`` raise ``ImportError``, so the fallback is exercised
    through the real import path rather than a stubbed helper.
    """
    monkeypatch.setitem(sys.modules, "marimo._ast.parse", None)


def test_structural_check_passes_the_real_fixture_agent_cell_shape():
    passed, detail = grade.normalized_cell_shape(
        WITH_NORMALIZED.decode("utf-8"), filepath="mission_clean.py"
    )

    assert passed is True
    assert "assigns 'normalized'" in detail
    assert "input_vector" in detail


def test_structural_check_rejects_a_bare_constant():
    """``normalized = 1.25`` is not the mission's diagnostic."""
    passed, detail = grade.normalized_cell_shape(
        WITH_CONSTANT_NORMALIZED.decode("utf-8"), filepath="mission_clean.py"
    )

    assert passed is False
    assert "never reads" in detail
    assert "input_vector" in detail


def test_structural_check_rejects_a_comment_mention():
    passed, detail = grade.normalized_cell_shape(
        WITH_COMMENT_NORMALIZED.decode("utf-8"), filepath="mission_clean.py"
    )

    assert passed is False
    assert detail == "no cell assigns 'normalized'"


def test_structural_check_rejects_a_module_level_assignment():
    """The old byte-substring shape: the word outside every cell."""
    source = LF_BYTES.decode("utf-8") + "\nnormalized = 1.0\n"

    passed, detail = grade.normalized_cell_shape(source, filepath="notebook.py")

    assert passed is False
    assert detail == "saved notebook defines no marimo cells"


def test_structural_check_names_the_input_that_is_missing():
    passed, detail = grade.normalized_cell_shape(
        WITH_HALF_NORMALIZED.decode("utf-8"), filepath="mission_clean.py"
    )

    assert passed is False
    assert "'input_vector'" in detail
    assert "'processed'" not in detail


def test_structural_check_rejects_an_unparseable_notebook():
    passed, detail = grade.normalized_cell_shape("def (", filepath="notebook.py")

    assert passed is False
    assert "not parseable" in detail


def test_structural_check_falls_back_to_stdlib_without_marimo(monkeypatch):
    """No marimo in the grading interpreter must not fail a good notebook."""
    source = WITH_NORMALIZED.decode("utf-8")
    marimo_verdict = grade.normalized_cell_shape(source, filepath="mission_clean.py")
    assert marimo_verdict[0] is True

    _block_marimo(monkeypatch)
    fallback_verdict = grade.normalized_cell_shape(source, filepath="mission_clean.py")
    constant_verdict = grade.normalized_cell_shape(
        WITH_CONSTANT_NORMALIZED.decode("utf-8"), filepath="mission_clean.py"
    )

    # Same cell rule either way, so the fallback agrees with marimo's parser.
    assert fallback_verdict == marimo_verdict
    assert constant_verdict[0] is False


def test_a_cell_after_the_main_guard_is_not_a_marimo_cell(monkeypatch):
    """marimo saves the ``__main__`` guard last; the check follows that rule."""
    source = FIXTURE_TEXT.rstrip("\n") + "\n\n" + AGENT_CELL + "\n"
    assert source.index(_MAIN_GUARD) < source.index(AGENT_CELL)

    passed, detail = grade.normalized_cell_shape(source, filepath="mission_clean.py")

    assert passed is False
    assert detail == "no cell assigns 'normalized'"
    # The stdlib fallback applies the same rule rather than being more lenient.
    _block_marimo(monkeypatch)
    assert grade.normalized_cell_shape(source, filepath="mission_clean.py")[0] is False


def test_grade_pairs_the_constant_notebook_with_the_real_one(tmp_path):
    """The constant fails the saved-cell check; the real shape passes it."""
    constant_dir = tmp_path / "constant"
    real_dir = tmp_path / "real"
    constant_dir.mkdir()
    real_dir.mkdir()
    _c1, constant_notebook, constant_manifest = _grade_case(
        constant_dir, notebook_bytes=WITH_CONSTANT_NORMALIZED
    )
    _c2, real_notebook, real_manifest = _grade_case(real_dir)

    failed = grade.grade(
        constant_manifest, _FakeProber(result=_result(constant_notebook))
    )
    assert failed["pass"] is False
    assert _check(failed, "normalized_cell_saved")["pass"] is False
    assert "never reads" in _check(failed, "normalized_cell_saved")["detail"]
    # The kernel reads are healthy: the failure is the saved STRUCTURE.
    assert _check(failed, "kernel_reactivity_at_gain_4")["pass"] is True

    passed = grade.grade(real_manifest, _FakeProber(result=_ok_result(real_notebook)))
    assert _check(passed, "normalized_cell_saved")["pass"] is True
    assert passed["pass"] is True


# --------------------------------------------------------------------------- #
# The grader DRIVES the slider: reactivity and restoration
# --------------------------------------------------------------------------- #


def test_grade_passes_the_grader_driven_sequence(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    verdict = grade.grade(manifest, _FakeProber(result=_result(notebook)))

    assert verdict["pass"] is True
    reactive = _check(verdict, "kernel_reactivity_at_gain_4")
    assert reactive["pass"] is True
    assert "gain 4.0 -> normalized 2.5 == oracle 2.5" in reactive["detail"]
    restored = _check(verdict, "kernel_restored_to_default")
    assert restored["pass"] is True
    assert "gain back at 2.0 -> normalized 1.25 == oracle 1.25" in restored["detail"]


def test_reactivity_check_fails_on_a_hardcoded_diagnostic(tmp_path):
    """The constant that passes the default read must fail the changed one."""
    _canonical, notebook, manifest = _grade_case(tmp_path)
    # The slider was driven to 4, but the diagnostic stayed at the default.
    result = _result(notebook, normalized_at_4=1.25)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    failed = _check(verdict, "kernel_reactivity_at_gain_4")
    assert failed["pass"] is False
    assert "!= oracle 2.5" in failed["detail"]
    # It never left the default, so only the reactivity check can catch it.
    assert _check(verdict, "kernel_restored_to_default")["pass"] is True
    # And the default read itself is still healthy — the point of the check.
    assert _check(verdict, "kernel_normalized_matches_oracle")["pass"] is True


def test_reactivity_check_fails_when_the_value_never_left_the_default(tmp_path):
    """A diagnostic that reads 1.25 at BOTH gains is the mission's step 4 unmet."""
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = _result(notebook, gain_at_4=4.0, normalized_at_4=1.25)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_reactivity_at_gain_4")["pass"] is False
    assert _check(verdict, "kernel_gain_is_default")["pass"] is True


def test_reactivity_check_fails_when_the_slider_never_moved(tmp_path):
    """The changed value without the changed gain is not the mission either."""
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = _result(notebook, gain_at_4=2.0, normalized_at_4=2.5)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    failed = _check(verdict, "kernel_reactivity_at_gain_4")
    assert failed["pass"] is False
    assert "gain did not move to 4" in failed["detail"]


def test_restoration_check_fails_when_the_gain_was_not_restored(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    # Changed correctly, left at 4: the mission's step 5 is unmet.
    result = _result(notebook, gain_restored=4.0, normalized_restored=2.5)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_reactivity_at_gain_4")["pass"] is True
    failed = _check(verdict, "kernel_restored_to_default")
    assert failed["pass"] is False
    assert "not restored to 2" in failed["detail"]


def test_restoration_check_fails_when_the_value_came_back_wrong(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = _result(notebook, gain_restored=2.0, normalized_restored=2.5)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    failed = _check(verdict, "kernel_restored_to_default")
    assert failed["pass"] is False
    assert "!= oracle 1.25" in failed["detail"]


def test_both_reactivity_checks_fail_closed_without_a_sequence(tmp_path):
    """A reader that cannot report the sequence must not be read as a pass."""
    _canonical, notebook, manifest = _grade_case(tmp_path)
    result = grade.probe_result(
        session_id="new-session",
        process_id=222,
        notebook_path=str(notebook),
        values=(2.0, 1.25, 0),
        run_status="ok",
    )

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    for name in ("kernel_reactivity_at_gain_4", "kernel_restored_to_default"):
        failed = _check(verdict, name)
        assert failed["pass"] is False
        assert "no gain/normalized value" in failed["detail"]
    assert verdict["observed"]["normalized_at_4"] is None


def test_both_reactivity_checks_fail_when_the_oracle_cannot_load(tmp_path, monkeypatch):
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
    for name in ("kernel_reactivity_at_gain_4", "kernel_restored_to_default"):
        failed = _check(verdict, name)
        assert failed["pass"] is False
        assert "oracle unavailable" in failed["detail"]


def test_grade_docstring_states_the_artifact_limit():
    """The limit is documented where a reader of the verdict will meet it."""
    doc = grade.grade.__doc__
    assert "does NOT establish the agent's history" in doc
    assert "artifact-level reactivity" in doc
    assert "captured tool actions" in doc


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

# The reactivity reads a healthy arm produces when the GRADER drives the
# slider: (gain_at_4, normalized_at_4, gain_restored, normalized_restored).
HEALTHY_REACTIVITY = (4.0, 2.5, 2.0, 1.25)

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
        reactivity=HEALTHY_REACTIVITY,
    )
    wrapper_result = grade.probe_result(
        session_id="new-session",
        process_id=222,
        notebook_path=str(notebook),
        values=wrapper_values,
        run_status=grade.wrapper_run_status(WRAPPER_HEALTHY_RUN),
        reactivity=HEALTHY_REACTIVITY,
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
                reactivity=HEALTHY_REACTIVITY,
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
                reactivity=HEALTHY_REACTIVITY,
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

    The canonical fixture carries the structurally valid ``normalized`` cell, so
    the byte-exact copy the fake server opens satisfies the saved-cell check
    too. No server is launched and no kernel is booted. The shim's ledger is
    written as well — setup only records its path — because a native arm's
    verdict needs the audit trail.
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
    if arm == "native":
        _write_ledger(manifest["ledger_path"], _healthy_ledger(WITH_NORMALIZED))
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
            reactivity=HEALTHY_REACTIVITY,
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
            reactivity=HEALTHY_REACTIVITY,
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
        {"success": True, "stdout": ""},  # the grader sets gain to 4
        {"success": True, "stdout": "gain 4\nnormalized 2.5\nerrors 0\n"},
        {"success": True, "stdout": ""},  # the grader restores gain to 2
        {"success": True, "stdout": "gain 2\nnormalized 1.25\nerrors 0\n"},
    ]
    seen = []

    def fake_execute(env, url, notebook_arg, code):
        seen.append(code)
        return queued.pop(0)

    prober._execute = fake_execute
    values, run_status, reactivity = prober._read_kernel(
        {}, "http://127.0.0.1:9", notebook, "fresh-session"
    )

    # The grader drives the whole sequence through the native arm's mechanism:
    # set the slider, read, restore, read again.
    assert seen == [
        grade._RUN_ALL_CELLS,
        grade._READ_VALUES,
        grade._set_gain_code(4),
        grade._READ_VALUES,
        grade._set_gain_code(2),
        grade._READ_VALUES,
    ]
    assert values == (2.0, 1.25, 0)
    assert run_status == "partial"
    assert reactivity == (4.0, 2.5, 2.0, 1.25)


def test_native_driver_program_is_the_shim_set_ui_call():
    """The grader's slider program calls what the arm's own ``set_ui`` calls."""
    for value in (4, 2):
        code = grade._set_gain_code(value)
        assert f"ctx.set_ui_value(ctx.globals['gain'], {value})" in code
        assert "async with cm.get_context() as ctx:" in code


def _wrapper_variables(*, gain, normalized):
    """A ``get_variables`` payload: a slider's value is nested one deeper."""
    return {
        "session_id": "fresh-session",
        "variables": {
            "gain": {"value": {"value": gain, "datatype": "int"}, "datatype": "Slider"},
            "normalized": {"value": normalized, "datatype": "float"},
        },
        "tables": {},
    }


def test_wrapper_driver_moves_the_slider_through_the_package(tmp_path, monkeypatch):
    """The wrapper arm's own mechanism: the package's ``set_ui_value`` handler.

    The package's handlers are replaced, so this exercises the real reader
    plumbing and the real call sequence with no server and no kernel.
    """
    from types import SimpleNamespace

    import marimo_inspection.client as client_module
    import marimo_inspection.tools.errors as errors_module
    import marimo_inspection.tools.mutation as mutation_module
    import marimo_inspection.tools.ui as ui_module
    import marimo_inspection.tools.variables as variables_module

    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(WITH_NORMALIZED)
    calls = []
    reads = iter(
        [
            _wrapper_variables(gain="2", normalized="1.25"),
            _wrapper_variables(gain="4", normalized="2.5"),
            _wrapper_variables(gain="2", normalized="1.25"),
        ]
    )

    async def fake_get_variables(*, session_id, variable_names, server_url):
        assert variable_names == ["gain", "normalized"]
        return next(reads)

    async def fake_set_ui_value(variable_name, value, *, session_id, server_url):
        calls.append((variable_name, value))
        return {"status": "ok", "applied": True, "verified": True}

    async def fake_run_cell(*, mode, session_id, server_url):
        return {"status": "ok", "counts": {"requested": 6, "succeeded": 6, "failed": 0}}

    async def fake_get_errors(*, session_id, server_url):
        return {"session_id": session_id, "total_errors": 0, "cells": []}

    class _FakeClient:
        def __init__(self, url):
            self.url = url

        async def list_sessions(self):
            return [SimpleNamespace(session_id="fresh-session")]

    monkeypatch.setattr(client_module, "MarimoClient", _FakeClient)
    monkeypatch.setattr(variables_module, "get_variables", fake_get_variables)
    monkeypatch.setattr(ui_module, "set_ui_value", fake_set_ui_value)
    monkeypatch.setattr(mutation_module, "run_cell", fake_run_cell)
    monkeypatch.setattr(errors_module, "get_errors", fake_get_errors)

    prober = grade.WrapperProber("python3", tmp_path / "state")
    values, run_status, reactivity = prober._read_kernel(
        {}, "http://127.0.0.1:9", notebook, "fresh-session"
    )

    # The package's own widget handler, driven value by value.
    assert calls == [("gain", 4), ("gain", 2)]
    assert values == (2.0, 1.25, 0)
    assert run_status == "ok"
    assert reactivity == (4.0, 2.5, 2.0, 1.25)


def test_wrapper_driver_refuses_a_slider_move_the_handler_rejects():
    """A rejected move fails the probe closed rather than reading stale state."""

    async def failing_set_ui_value(variable_name, value, *, session_id, server_url):
        return {"status": "error", "reason": "invalid_value"}

    with pytest.raises(grade.GradeRefusal, match="set_ui_value"):
        asyncio.run(
            grade._wrapper_set_gain(
                failing_set_ui_value, 4, "http://127.0.0.1:9", "fresh-session"
            )
        )


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
# The native arm's audit trail: the shim's ledger chains to the saved notebook
# --------------------------------------------------------------------------- #


def _ledger_check(verdict):
    """The one ``interface_ledger_intact`` check in *verdict*."""
    return _check(verdict, "interface_ledger_intact")


def test_ledger_check_is_the_last_documented_check():
    """The ledger covers what the artifact-level checks cannot: the history."""
    assert list(grade.CHECK_NAMES)[-1] == "interface_ledger_intact"
    assert len(grade.CHECK_NAMES) == 9


def test_grade_passes_a_native_arm_whose_ledger_chains_to_the_saved_notebook(
    tmp_path,
):
    _canonical, notebook, manifest = _grade_case(tmp_path)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is True
    check = _ledger_check(verdict)
    assert check["pass"] is True
    assert "create_cell" in check["detail"]
    assert verdict["observed"]["ledger_applicable"] is True
    assert verdict["observed"]["ledger_entry_count"] == 2


def test_grade_fails_when_the_notebook_was_written_outside_the_adapter(tmp_path):
    """The bypass: a direct write leaves the ledger's last hash behind."""
    _canonical, notebook, manifest = _grade_case(tmp_path)
    notebook.write_bytes(WITH_NORMALIZED + b"\n# written outside the adapter\n")

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "outside the adapter" in check["detail"]
    assert verdict["observed"]["ledger_applicable"] is True
    # The artifact checks are still healthy: only the audit trail caught it.
    assert _check(verdict, "normalized_cell_saved")["pass"] is True


def test_coordinated_notebook_and_ledger_edits_are_not_detected(tmp_path):
    """Boundary, not a success claim: both files are writable by the agent.

    Keep this paired with the unrecorded-write negative test. If a protected
    command record is introduced later, update both this test and the limitation.
    """
    _canonical, notebook, manifest = _grade_case(tmp_path)
    notebook.write_bytes(WITH_NORMALIZED + b"\n# unmediated write\n")
    ledger_path = Path(manifest["ledger_path"])
    entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    entries[-1]["sha256_after"] = _sha256(notebook.read_bytes())
    _write_ledger(ledger_path, entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert _ledger_check(verdict)["pass"] is True, (
        "If coordinated tampering is now detected, update the documented boundary"
    )
    contract = (
        BENCHMARK.parent / "docs" / "restricted-interface-comparison.md"
    ).read_text(encoding="utf-8")
    assert "not a tamper-resistant audit log" in contract


def test_grade_fails_when_no_ledger_was_captured(tmp_path):
    """An absent ledger is a failure, never "nothing happened"."""
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=None)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "no ledger" in check["detail"]
    assert verdict["observed"]["ledger_applicable"] is True
    assert verdict["observed"]["ledger_entry_count"] == 0


def test_grade_fails_when_the_manifest_names_no_ledger(tmp_path):
    _canonical, notebook, manifest = _grade_case(tmp_path, drop_ledger_key=True)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "no ledger_path" in check["detail"]


def test_grade_fails_when_the_ledger_has_a_seq_gap(tmp_path):
    digest = _sha256(WITH_NORMALIZED)
    entries = [
        _ledger_entry(1, "create_cell", digest, digest),
        _ledger_entry(3, "variables", digest, digest),
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "seq 3" in check["detail"]
    assert "expected 2" in check["detail"]


def test_grade_fails_when_the_ledger_starts_from_another_notebook(tmp_path):
    """The first entry's before-hash must be the disposable copy's."""
    digest = _sha256(WITH_NORMALIZED)
    entries = [
        _ledger_entry(1, "create_cell", _sha256(b"another notebook entirely"), digest)
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "sha256_before" in check["detail"]
    assert manifest["sha256"] in check["detail"]


def test_grade_fails_when_the_ledger_chain_is_broken(tmp_path):
    digest = _sha256(WITH_NORMALIZED)
    changed = _sha256(WITH_NORMALIZED + b"# a write the ledger missed\n")
    entries = [
        _ledger_entry(1, "create_cell", digest, changed),
        # Entry 2 starts from the FIXTURE hash again: the chain skips a write.
        _ledger_entry(2, "variables", digest, changed),
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "does not chain" in check["detail"]


def test_grade_fails_when_no_ledger_entry_created_a_cell(tmp_path):
    digest = _sha256(WITH_NORMALIZED)
    entries = [
        _ledger_entry(1, "variables", digest, digest),
        _ledger_entry(2, "errors", digest, digest),
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "create_cell" in check["detail"]
    # The chain itself is intact: the failure is the mission's own call.
    assert "does not chain" not in check["detail"]


def test_grade_fails_when_the_ledger_is_corrupt(tmp_path):
    """The shim's own reader refuses it, and the verdict says so."""
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=None)
    corrupted = Path(manifest["ledger_path"])
    corrupted.parent.mkdir(parents=True, exist_ok=True)
    corrupted.write_text("{not json}\n", encoding="utf-8")

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "not JSON" in check["detail"]


def test_grade_records_the_wrapper_arms_ledger_as_not_applicable(tmp_path):
    """No shim, no ledger: the wrapper arm is never credited as audited."""
    _canonical, notebook, manifest = _grade_case(tmp_path, arm="wrapper", ledger=None)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is True
    check = _ledger_check(verdict)
    assert check["pass"] is True
    assert "not applicable" in check["detail"]
    assert verdict["observed"]["ledger_applicable"] is False
    # No entry count is claimed for an arm that has no audit trail at all.
    assert "ledger_entry_count" not in verdict["observed"]


def test_a_wrapper_arm_is_not_credited_by_a_native_ledger(tmp_path):
    """A ledger left in a wrapper arm's root cannot make it look audited."""
    _canonical, notebook, manifest = _grade_case(tmp_path, arm="wrapper")

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["observed"]["ledger_applicable"] is False
    assert _ledger_check(verdict)["pass"] is True


def test_the_wrapper_arm_still_fails_the_checks_that_do_apply(tmp_path):
    """Not applicable is not a bypass: the other checks still bite."""
    _canonical, notebook, manifest = _grade_case(tmp_path, arm="wrapper", ledger=None)
    result = _result(notebook, error_count=3)

    verdict = grade.grade(manifest, _FakeProber(result=result))

    assert verdict["pass"] is False
    assert _check(verdict, "kernel_zero_cell_errors")["pass"] is False
    assert _ledger_check(verdict)["pass"] is True


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


def test_grade_fails_when_the_cell_on_disk_answers_to_no_mediated_call(tmp_path):
    """Attribution: a cell nobody submitted through the adapter is not credited."""
    digest = _sha256(WITH_NORMALIZED)
    entries = [
        _ledger_entry(1, "create_cell", digest, digest, code="normalized = 1.25"),
        _ledger_entry(2, "variables", digest, digest),
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is False
    check = _ledger_check(verdict)
    assert check["pass"] is False
    assert "answers to no mediated" in check["detail"]


def test_a_failed_create_cell_still_attributes_the_cell_it_left_on_disk(tmp_path):
    """A failed call is not an ineffective one: ``create_cell`` creates, then runs.

    Observed live: ``pair create_cell`` reported ``tool_unsuccessful`` because the
    cell it had just created failed to run (``processed`` was not in the kernel
    namespace yet), yet the cell was already saved. The entry records the call
    with its error status, and the cell on disk still answers to it.
    """
    text = WITH_NORMALIZED.decode("utf-8")
    source = grade.normalized_cell_source(text)
    assert source is not None
    digest = _sha256(WITH_NORMALIZED)
    entries = [
        _ledger_entry(
            1,
            "create_cell",
            digest,
            digest,
            created_cell_ids=[],
            status="error:tool_unsuccessful",
            code=source,
        ),
        _ledger_entry(2, "errors", digest, digest),
    ]
    _canonical, notebook, manifest = _grade_case(tmp_path, ledger=entries)

    verdict = grade.grade(manifest, _FakeProber(result=_ok_result(notebook)))

    assert verdict["pass"] is True
    check = _ledger_check(verdict)
    assert check["pass"] is True
