"""Kernel-free tests for the mission arm teardown (``mission_teardown``).

The module is loaded directly by path (the pattern the other benchmark tests
use). Every OS-level action sits behind the injectable ``ops`` seam, so these
tests run offline: no server is launched, no census is read over the network,
and no real process is ever signalled. Each destructive call is recorded by the
fake and asserted on.

The topology modelled here is the **measured** one, not the assumed one. The
recorded manifest pid is the venv-shim interpreter; its direct child is another
python process; the marimo *kernel* is a grandchild running under a **different**
interpreter whose command line does **not** name the notebook. Eligibility is
the live parent chain reaching the recorded pid, so the interpreter difference
and the missing notebook argument must never cause a skip — the rule that did
cause every real child to be skipped.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[2] / "benchmark"
SETUP_PATH = BENCHMARK / "mission_setup.py"
TEARDOWN_PATH = BENCHMARK / "mission_teardown.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before execution so any ``@dataclass`` can resolve ``__module__``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


setup = _load("benchmark_mission_setup", SETUP_PATH)
teardown = _load("benchmark_mission_teardown", TEARDOWN_PATH)

LF_BYTES = b"import marimo\n\napp = marimo.App()\n"
FIXED_TIME = "2026-01-01T00:00:00+00:00"
PID = 5150  # the recorded manifest pid: the venv shim that ran ``marimo edit``
CHILD = 5151  # the server's direct child: another python process
GRANDCHILD = 5152  # the marimo kernel: a grandchild
# Snapshot order, shallowest first — what ``children(recursive=True)`` yields.
CHILDREN = (CHILD, GRANDCHILD)
# Deepest first: the order the tree must be signalled in.
DEEPEST_FIRST = [GRANDCHILD, CHILD, PID]
# The recorded parent is the venv shim; the kernel runs under the base
# interpreter. Different paths, same run — never an eligibility gate.
SHIM_EXE = "/opt/venvs/issue7/Scripts/python.exe"
BASE_EXE = "/opt/python/pythoncore-3.12-64/python.exe"


# --------------------------------------------------------------------------- #
# Fakes and fixtures
# --------------------------------------------------------------------------- #


class FakeOps:
    """Records every OS-level call; simulates the process/server lifecycle.

    ``census_follows_process`` models a server whose census stops answering once
    its recorded process is gone (the normal case). ``terminate_effective`` /
    ``kill_effective`` model a process that ignores one or both signals;
    ``terminate_ignores`` / ``kill_ignores`` name individual pids that survive
    one signal each. ``parents`` maps a pid to its parent pid and ``exes`` maps a
    pid to its executable path — both drive the child re-verification seam.
    """

    def __init__(
        self,
        root_pid: int,
        *,
        census=None,
        processes=None,
        children=(),
        parents=None,
        exes=None,
        census_follows_process=True,
        terminate_effective=True,
        kill_effective=True,
        terminate_ignores=(),
        kill_ignores=(),
    ):
        self.root_pid = root_pid
        self.census = census
        self.processes = dict(processes or {})
        self.children = list(children)
        self.parents = dict(parents or {})
        self.exes = dict(exes or {})
        self.census_follows_process = census_follows_process
        self.terminate_effective = terminate_effective
        self.kill_effective = kill_effective
        self.terminate_ignores = set(terminate_ignores)
        self.kill_ignores = set(kill_ignores)
        self.terminate_calls = []
        self.kill_calls = []
        self.terminate_saw = {}
        self.census_calls = 0

    def probe_census(self, server_url):
        self.census_calls += 1
        if self.census is None:
            return None
        if self.census_follows_process and self.root_pid not in self.processes:
            return None
        return dict(self.census)

    def process_info(self, pid):
        if pid in self.processes:
            return teardown.ProcessInfo(
                pid=pid,
                cmdline=list(self.processes[pid]),
                ppid=self.parents.get(pid),
                exe=self.exes.get(pid),
            )
        return None

    def child_pids(self, pid):
        # The live descendant snapshot: the point-in-time list, minus any pid
        # that has since exited.
        if pid != self.root_pid or pid not in self.processes:
            return []
        return [child for child in self.children if child in self.processes]

    def terminate(self, pid):
        # Remember who was still alive at the instant of the call, so a test can
        # prove deepest-first ordering kept the intermediate alive.
        self.terminate_saw[pid] = sorted(self.processes)
        self.terminate_calls.append(pid)
        if self.terminate_effective and pid not in self.terminate_ignores:
            self.processes.pop(pid, None)

    def kill(self, pid):
        self.kill_calls.append(pid)
        if self.kill_effective and pid not in self.kill_ignores:
            self.processes.pop(pid, None)

    def wait_gone(self, pid, timeout):
        return pid not in self.processes


def _stage(
    tmp_path,
    *,
    notebook_bytes=LF_BYTES,
    session_id="session-1",
    pid=PID,
    outside_root=False,
    sha256=None,
):
    """Build a manifest the way ``mission_setup`` writes one."""
    canonical = tmp_path / "canonical.py"
    canonical.write_bytes(notebook_bytes)
    root = tmp_path / "run"
    root.mkdir(parents=True, exist_ok=True)
    notebook = tmp_path / "elsewhere.py" if outside_root else root / "notebook.py"
    notebook.write_bytes(notebook_bytes)
    manifest = setup.build_manifest(
        server_url="http://127.0.0.1:9",
        session_id=session_id,
        notebook_path=str(notebook),
        process_id=pid,
        python="python3",
        sha256=sha256 or hashlib.sha256(notebook_bytes).hexdigest(),
        arm="native",
        generated_at=FIXED_TIME,
        canonical_path=str(canonical),
        root=str(root),
        state_dir=str(root / "state"),
        ledger_path=str(root / "ledger.jsonl"),
    )
    return manifest, notebook, root


def _census(notebook, session_id):
    return {session_id: {"path": str(notebook)}}


def _healthy_ops(notebook, session_id, *, pid=PID, child=CHILD, grandchild=GRANDCHILD):
    """Ops modelling the measured marimo tree, wholly alive.

    ``server (venv shim) -> python child -> kernel python (base interpreter)``.
    The kernel's command line deliberately omits the notebook path and its
    executable is a different interpreter: neither is an eligibility signal.
    """
    processes = {
        pid: ["python", "-m", "marimo", "edit", str(notebook), "--headless"],
        child: ["python", "-m", "marimo", "_server", "--port", "0"],
        grandchild: ["python", "-m", "marimo", "_runtime", "--kernel"],
    }
    parents = {pid: 1, child: pid, grandchild: child}
    exes = {pid: SHIM_EXE, child: SHIM_EXE, grandchild: BASE_EXE}
    return FakeOps(
        pid,
        census=_census(notebook, session_id),
        processes=processes,
        children=[child, grandchild],
        parents=parents,
        exes=exes,
    )


def _refusal(manifest, ops, **kwargs):
    """Run teardown expecting a refusal; return the report."""
    with pytest.raises(teardown.TeardownRefusal) as excinfo:
        teardown.teardown(manifest, ops, **kwargs)
    return excinfo.value.report


# --------------------------------------------------------------------------- #
# Happy path and idempotence
# --------------------------------------------------------------------------- #


def test_teardown_stops_the_recorded_process_and_its_children(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.code == teardown.CODE_OK == 0
    assert report.reachable is False
    assert report.killed_pid == manifest["process_id"]
    assert report.children == list(CHILDREN)
    assert report.escalated is False
    # Deepest first, then the recorded parent.
    assert ops.terminate_calls == DEEPEST_FIRST
    assert ops.kill_calls == []
    assert ops.process_info(manifest["process_id"]) is None


def test_full_measured_topology_is_disposed_of_completely(tmp_path):
    """The measured tree — every process alive — is reaped with no skips.

    This is the live shape that broke the previous rule: the kernel is a
    grandchild under a different interpreter and its command line omits the
    notebook, yet the whole tree must still be disposed of.
    """
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # Preconditions, asserted on the fake itself: the kernel really is a
    # grandchild, really runs a different interpreter, and really does not name
    # the notebook.
    assert ops.parents[GRANDCHILD] == CHILD
    assert ops.exes[GRANDCHILD] != ops.exes[PID]
    assert not teardown.cmdline_references(ops.processes[GRANDCHILD], notebook)

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.code == teardown.CODE_OK
    assert report.skipped == []
    assert report.children == list(CHILDREN)
    # Deepest first: the intermediate is still alive when the kernel is disposed
    # of, and the recorded parent is signalled last.
    assert ops.terminate_calls == DEEPEST_FIRST
    assert CHILD in ops.terminate_saw[GRANDCHILD]
    assert PID in ops.terminate_saw[GRANDCHILD]
    # Every member of the tree is gone.
    for process in (PID, CHILD, GRANDCHILD):
        assert ops.process_info(process) is None


def test_a_different_interpreter_path_is_never_a_skip_reason(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # Even a third interpreter, unrelated to both the shim and the kernel's own,
    # must not disqualify a live descendant.
    ops.exes[CHILD] = "/opt/python/another-interpreter.exe"

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.skipped == []
    assert ops.terminate_calls == DEEPEST_FIRST


def test_teardown_is_idempotent_when_the_arm_is_already_gone(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    first = teardown.teardown(manifest, ops)
    second = teardown.teardown(manifest, ops)

    assert first.status == "ok" and first.killed_pid == manifest["process_id"]
    assert second.status == "ok"
    assert second.code == teardown.CODE_OK
    assert second.killed_pid is None
    assert second.children == []
    assert second.reachable is False
    # The second run had nothing to do: it must not signal anything.
    assert ops.terminate_calls == DEEPEST_FIRST
    assert ops.kill_calls == []


def test_teardown_escalates_to_a_hard_kill_only_when_needed(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.terminate_effective = False  # the process ignores the polite signal

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.escalated is True
    assert report.reachable is False
    # Every target gets a terminate first, then a kill — both deepest first.
    assert ops.terminate_calls == DEEPEST_FIRST
    assert ops.kill_calls == DEEPEST_FIRST


def test_escalation_kills_an_orphaned_kernel_whose_intermediate_we_stopped(
    tmp_path,
):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # The graceful signal stops the intermediate and the recorded parent but not
    # the kernel. The kernel is then an orphan of a pid this run already
    # signalled, which must still be killable — without re-proving a chain that
    # our own signal invalidated.
    ops.terminate_ignores = {GRANDCHILD}

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.skipped == []
    assert report.escalated is True
    assert ops.terminate_calls == DEEPEST_FIRST
    assert ops.kill_calls == [GRANDCHILD, PID]
    assert ops.process_info(GRANDCHILD) is None


def test_teardown_does_not_escalate_when_the_graceful_stop_works(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = teardown.teardown(manifest, ops)

    assert report.escalated is False
    assert ops.kill_calls == []


# --------------------------------------------------------------------------- #
# Survivors are reported honestly, and a confirmed survivor fails the run
# --------------------------------------------------------------------------- #


def test_a_confirmed_kernel_that_survives_both_signals_fails_the_run(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.terminate_ignores = {GRANDCHILD}
    ops.kill_ignores = {GRANDCHILD}  # the kernel ignores terminate and kill

    report = _refusal(manifest, ops)

    assert report.status == "refused"
    assert report.code == teardown.CODE_TEARDOWN_FAILED
    assert report.stage == "verified"
    assert report.reachable is False
    assert GRANDCHILD in report.children
    assert ops.process_info(GRANDCHILD) is not None


# --------------------------------------------------------------------------- #
# Guard refusals — the process tree is never signalled
# --------------------------------------------------------------------------- #


def test_teardown_refuses_a_census_mismatch_and_terminates_nothing(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.census = {"someone-elses-session": {"path": str(notebook)}}

    report = _refusal(manifest, ops)

    assert report.status == "refused"
    assert report.code == teardown.CODE_CENSUS_MISMATCH
    assert report.stage == "identity"
    assert report.reachable is True
    assert report.killed_pid is None
    assert ops.terminate_calls == []
    assert ops.kill_calls == []
    # The server was inspected exactly once and left running.
    assert ops.census_calls == 1


def test_teardown_refuses_a_reused_session_pointing_elsewhere(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.census = {manifest["session_id"]: {"path": str(tmp_path / "other.py")}}

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_CENSUS_MISMATCH
    assert ops.terminate_calls == []
    assert ops.kill_calls == []


def test_teardown_refuses_a_multi_session_census(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.census = _census(notebook, manifest["session_id"])
    ops.census["intruder"] = {"path": str(tmp_path / "intruder.py")}

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_CENSUS_MISMATCH
    assert ops.terminate_calls == []


def test_teardown_refuses_a_dead_process_with_a_live_census(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.processes.clear()  # the recorded pid is gone ...
    ops.census_follows_process = False  # ... yet the URL still answers

    report = _refusal(manifest, ops)

    assert report.status == "refused"
    assert report.code == teardown.CODE_PROCESS_GONE_SERVER_LIVE
    assert report.stage == "identity"
    assert report.reachable is True
    assert report.killed_pid is None
    # The live server is not named by the manifest: it must not be killed.
    assert ops.terminate_calls == []
    assert ops.kill_calls == []


def test_teardown_refuses_when_the_pid_is_alive_but_not_the_named_notebook(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.processes[manifest["process_id"]] = ["python", "-m", "something_else"]

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_TARGET_UNVERIFIED
    assert report.reachable is True
    assert ops.terminate_calls == []
    assert ops.kill_calls == []


def test_teardown_refuses_census_unreachable_with_a_live_process(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.census = None  # server cannot be interrogated

    report = _refusal(manifest, ops)

    assert report.status == "refused"
    assert report.code == teardown.CODE_CENSUS_UNREACHABLE_PROCESS_ALIVE
    assert report.stage == "census"
    assert report.reachable is False
    assert ops.terminate_calls == []
    assert ops.kill_calls == []


def test_teardown_reports_failure_and_never_claims_success_when_unstoppable(
    tmp_path,
):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.terminate_effective = False
    ops.kill_effective = False  # the arm survives both signals

    report = _refusal(manifest, ops)

    assert report.status == "refused"
    assert report.code == teardown.CODE_TEARDOWN_FAILED
    assert report.stage == "verified"
    assert report.reachable is True
    assert report.killed_pid == manifest["process_id"]
    assert report.children == list(CHILDREN)
    assert ops.kill_calls == DEEPEST_FIRST


# --------------------------------------------------------------------------- #
# Manifest and path refusals
# --------------------------------------------------------------------------- #


def test_teardown_refuses_a_notebook_outside_the_manifest_root(tmp_path):
    manifest, _notebook, _root = _stage(tmp_path, outside_root=True)
    ops = _healthy_ops(Path(manifest["notebook_path"]), manifest["session_id"])

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_NOTEBOOK_UNRESOLVED
    assert report.stage == "notebook"
    assert ops.census_calls == 0
    assert ops.terminate_calls == []


def test_teardown_refuses_a_missing_notebook(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    notebook.unlink()
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_NOTEBOOK_UNRESOLVED
    assert ops.census_calls == 0


def test_teardown_refuses_a_sha_mismatch(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = _refusal(manifest, ops, expect_sha256="0" * 64)

    assert report.code == teardown.CODE_SHA_MISMATCH
    assert report.stage == "sha256"
    # Nothing was inspected or signalled.
    assert ops.census_calls == 0
    assert ops.terminate_calls == []
    assert ops.kill_calls == []


def test_teardown_accepts_a_matching_expected_sha256(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = teardown.teardown(manifest, ops, expect_sha256=manifest["sha256"])

    assert report.status == "ok"
    assert report.killed_pid == manifest["process_id"]


@pytest.mark.parametrize(
    "missing_key",
    ["server_url", "session_id", "notebook_path", "process_id", "sha256", "root"],
)
def test_teardown_refuses_a_manifest_missing_a_required_key(tmp_path, missing_key):
    manifest, notebook, _root = _stage(tmp_path)
    manifest.pop(missing_key)
    ops = _healthy_ops(notebook, "session-1")

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_MANIFEST_INVALID
    assert report.stage == "manifest"
    assert ops.census_calls == 0
    assert ops.terminate_calls == []


@pytest.mark.parametrize("bad_pid", [True, "5150", 5.5])
def test_teardown_refuses_a_non_integer_process_id(tmp_path, bad_pid):
    manifest, notebook, _root = _stage(tmp_path)
    manifest["process_id"] = bad_pid
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = _refusal(manifest, ops)

    assert report.code == teardown.CODE_MANIFEST_INVALID
    assert ops.terminate_calls == []


def test_teardown_refuses_a_non_mapping_manifest():
    ops = FakeOps(PID, census=None)
    with pytest.raises(teardown.TeardownRefusal) as excinfo:
        teardown.teardown(["not", "a", "manifest"], ops)
    assert excinfo.value.report.code == teardown.CODE_MANIFEST_INVALID
    assert ops.census_calls == 0


# --------------------------------------------------------------------------- #
# The manifest key contract and the CLI
# --------------------------------------------------------------------------- #


def test_required_keys_are_written_by_the_setup_manifest(tmp_path):
    # Drift guard: teardown must not ask for a key setup never writes.
    manifest, _notebook, _root = _stage(tmp_path)
    assert set(teardown.REQUIRED_KEYS) <= set(manifest)


def test_cli_prints_one_json_object_and_exits_zero_on_success(tmp_path, capsys):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code = teardown.main(["--manifest", str(manifest_path)], ops_factory=lambda: ops)

    payload = json.loads(capsys.readouterr().out)
    assert code == teardown.CODE_OK == 0
    assert set(payload) >= {
        "status",
        "stage",
        "killed_pid",
        "children",
        "skipped",
        "reachable",
        "code",
        "detail",
    }
    assert payload["status"] == "ok"
    assert payload["killed_pid"] == manifest["process_id"]
    assert payload["children"] == list(CHILDREN)
    assert payload["skipped"] == []
    assert payload["reachable"] is False


def test_cli_exits_with_the_refusal_code_and_terminates_nothing(tmp_path, capsys):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.census = {"someone-elses-session": {"path": str(notebook)}}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code = teardown.main(["--manifest", str(manifest_path)], ops_factory=lambda: ops)

    payload = json.loads(capsys.readouterr().out)
    assert code == teardown.CODE_CENSUS_MISMATCH
    assert payload["status"] == "refused"
    assert payload["code"] == teardown.CODE_CENSUS_MISMATCH
    assert payload["killed_pid"] is None
    assert payload["reachable"] is True
    assert ops.terminate_calls == []


def test_cli_exits_nonzero_on_an_unreadable_manifest(tmp_path, capsys):
    code = teardown.main(["--manifest", str(tmp_path / "absent.json")])

    payload = json.loads(capsys.readouterr().out)
    assert code == teardown.CODE_MANIFEST_INVALID
    assert payload["status"] == "refused"
    assert payload["stage"] == "manifest"


def test_cli_exits_with_the_sha_mismatch_code(tmp_path, capsys):
    manifest, notebook, _root = _stage(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    created = []

    def factory():
        ops = _healthy_ops(notebook, manifest["session_id"])
        created.append(ops)
        return ops

    code = teardown.main(
        ["--manifest", str(manifest_path), "--expect-sha256", "0" * 64],
        ops_factory=factory,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == teardown.CODE_SHA_MISMATCH
    assert payload["code"] == teardown.CODE_SHA_MISMATCH
    # The refusal precedes any OS action: nothing was read or signalled.
    assert created[0].census_calls == 0
    assert created[0].terminate_calls == []
    assert created[0].kill_calls == []


def test_refusal_codes_are_distinct_and_nonzero():
    codes = list(teardown.REFUSAL_CODES.values())
    assert len(codes) == len(set(codes))
    assert all(code != teardown.CODE_OK for code in codes)


# --------------------------------------------------------------------------- #
# Privacy: no machine-specific absolute paths in the module
# --------------------------------------------------------------------------- #

_MACHINE_PATTERNS = (
    re.compile(r"(?<![\w/])[A-Za-z]:[\\/][^\s\"']"),
    re.compile(r"/home/[A-Za-z]"),
    re.compile(r"/Users/[A-Za-z]"),
    re.compile(r"/root/"),
    re.compile(r"\btailscale\b"),
    re.compile(r"\b(?:100|10|172|192)\.\d"),
    re.compile(r"\bmhd01\b"),
)


def test_module_carries_no_machine_specific_paths():
    text = TEARDOWN_PATH.read_text(encoding="utf-8")
    for pattern in _MACHINE_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"mission_teardown.py matches {pattern.pattern}: {match.group(0)!r}"
        )


# --------------------------------------------------------------------------- #
# Child re-verification — eligibility is the live chain, never the executable
# --------------------------------------------------------------------------- #


def test_happy_path_reports_no_skipped_children(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.skipped == []


def test_a_grandchild_reparented_off_the_tree_is_skipped_and_never_signalled(
    tmp_path,
):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # The kernel pid no longer sits under the recorded process: its parent is an
    # unrelated pid that is not even alive. Signalling it is unprovable.
    ops.parents[GRANDCHILD] = 999999

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert {"pid": GRANDCHILD, "reason": teardown.SKIP_UNCONFIRMED} in report.skipped
    assert GRANDCHILD not in ops.terminate_calls
    assert GRANDCHILD not in ops.kill_calls
    # Only the confirmed member and the recorded parent were signalled.
    assert ops.terminate_calls == [CHILD, PID]
    # The unconfirmed pid is left alone, not silently claimed.
    assert ops.process_info(GRANDCHILD) is not None


def test_a_reused_child_pid_under_another_parent_is_skipped(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # The pid is now the child of a different, live process — a reused pid that
    # no longer descends from the recorded parent.
    ops.parents[GRANDCHILD] = 800001
    ops.processes[800001] = ["python", "-m", "unrelated"]
    ops.parents[800001] = 800002  # ... whose own parent is gone.

    report = teardown.teardown(manifest, ops)

    assert {"pid": GRANDCHILD, "reason": teardown.SKIP_UNCONFIRMED} in report.skipped
    assert GRANDCHILD not in ops.terminate_calls
    assert GRANDCHILD not in ops.kill_calls
    assert ops.terminate_calls == [CHILD, PID]


def test_a_dead_intermediate_orphans_the_kernel_and_it_is_reported(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    # The intermediate exited on its own before teardown: the kernel's chain can
    # no longer be walked to the recorded process, so it must not be signalled.
    ops.processes.pop(CHILD)

    report = teardown.teardown(manifest, ops)

    assert report.status == "ok"
    assert report.children == [GRANDCHILD]
    assert {"pid": GRANDCHILD, "reason": teardown.SKIP_UNCONFIRMED} in report.skipped
    assert ops.terminate_calls == [PID]
    assert ops.kill_calls == []
    assert ops.process_info(GRANDCHILD) is not None


def test_revalidate_child_confirms_a_live_direct_child():
    ops = _healthy_ops(Path("/tmp/run/notebook.py"), "session-1")

    assert teardown.revalidate_child(ops, CHILD, PID) == (True, "")


def test_revalidate_child_confirms_a_kernel_grandchild_under_another_interpreter():
    ops = _healthy_ops(Path("/tmp/run/notebook.py"), "session-1")

    assert ops.exes[GRANDCHILD] != ops.exes[PID]
    assert teardown.revalidate_child(ops, GRANDCHILD, PID) == (True, "")


def test_revalidate_child_reports_unconfirmed_when_the_parent_is_already_gone():
    # The child is alive and its ppid still *numerically* names the recorded
    # pid, but that parent is no longer a live process: the chain cannot be
    # confirmed, so the child must be rejected rather than acted on.
    ops = FakeOps(PID, processes={CHILD: ["python"]}, parents={CHILD: PID})

    confirmed, reason = teardown.revalidate_child(ops, CHILD, PID)

    assert confirmed is False
    assert reason == teardown.SKIP_UNCONFIRMED


def test_revalidate_child_reports_unconfirmed_when_an_ancestor_is_gone():
    # The kernel is alive and its ppid names the intermediate, but the
    # intermediate is no longer live: the chain cannot be confirmed.
    ops = FakeOps(
        PID,
        processes={PID: ["python", "-m", "marimo"], GRANDCHILD: ["python"]},
        parents={PID: 1, GRANDCHILD: CHILD},
    )

    confirmed, reason = teardown.revalidate_child(ops, GRANDCHILD, PID)

    assert confirmed is False
    assert reason == teardown.SKIP_UNCONFIRMED


def test_revalidate_child_reports_gone_for_a_missing_pid():
    ops = FakeOps(PID, processes={}, parents={})

    confirmed, reason = teardown.revalidate_child(ops, CHILD, PID)

    assert confirmed is False
    assert reason == teardown.SKIP_GONE


# --------------------------------------------------------------------------- #
# Command-line identity — a whole path component, never a substring
# --------------------------------------------------------------------------- #


def _notebook_file(tmp_path):
    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(LF_BYTES)
    return notebook


@pytest.mark.parametrize(
    "template",
    ["{p}", '"{p}"', '{p}"', '"{p}', '--notebook="{p}"'],
)
def test_cmdline_references_accepts_a_whole_path_component(tmp_path, template):
    notebook = _notebook_file(tmp_path)
    assert teardown.cmdline_references([template.format(p=notebook)], notebook) is True


def test_cmdline_references_accepts_the_path_among_other_arguments(tmp_path):
    notebook = _notebook_file(tmp_path)
    cmdline = ["python", "-m", "marimo", "edit", str(notebook), "--headless"]
    assert teardown.cmdline_references(cmdline, notebook) is True


def test_cmdline_references_accepts_a_case_insensitive_spelling(tmp_path):
    notebook = _notebook_file(tmp_path)
    # os.path.normcase folds case the way the platform does (lowercase on
    # Windows); this asserts the accepted platform rule, not a new one.
    assert teardown.cmdline_references([str(notebook).upper()], notebook) is True


@pytest.mark.parametrize(
    "template",
    ["{p}x", "{p}.bak", "{p};x", "x{p}", "C:{p}"],
)
def test_cmdline_references_rejects_a_merely_similar_path(tmp_path, template):
    notebook = _notebook_file(tmp_path)
    assert teardown.cmdline_references([template.format(p=notebook)], notebook) is False


def test_cmdline_references_rejects_a_bare_name(tmp_path):
    notebook = _notebook_file(tmp_path)
    cmdline = ["python", "-m", "notebook.py"]
    assert teardown.cmdline_references(cmdline, notebook) is False


# --------------------------------------------------------------------------- #
# Refusals never signal; skipped pids are surfaced in the JSON
# --------------------------------------------------------------------------- #


def test_every_refusal_class_leaves_the_process_tree_untouched(tmp_path):
    manifest, notebook, _root = _stage(tmp_path)

    def census_mismatch(ops, _manifest):
        ops.census = {"someone-elses-session": {"path": str(notebook)}}

    def target_unverified(ops, manifest_):
        ops.processes[manifest_["process_id"]] = ["python", "-m", "something_else"]

    def process_gone_server_live(ops, _manifest):
        ops.processes.clear()
        ops.census_follows_process = False

    def census_unreachable(ops, _manifest):
        ops.census = None

    for mutate in (
        census_mismatch,
        target_unverified,
        process_gone_server_live,
        census_unreachable,
    ):
        ops = _healthy_ops(notebook, manifest["session_id"])
        mutate(ops, manifest)
        report = _refusal(manifest, ops)
        assert report.status == "refused"
        assert report.skipped == []
        assert ops.terminate_calls == []
        assert ops.kill_calls == []


def test_cli_surfaces_skipped_child_pids_in_the_json(tmp_path, capsys):
    manifest, notebook, _root = _stage(tmp_path)
    ops = _healthy_ops(notebook, manifest["session_id"])
    ops.parents[GRANDCHILD] = 999999
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code = teardown.main(["--manifest", str(manifest_path)], ops_factory=lambda: ops)

    payload = json.loads(capsys.readouterr().out)
    assert code == teardown.CODE_OK
    assert payload["skipped"] == [
        {"pid": GRANDCHILD, "reason": teardown.SKIP_UNCONFIRMED}
    ]
