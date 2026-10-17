"""Kernel-free gates for the native arm's only interface (``benchmark.pair_cli``).

``benchmark/pair_cli.py`` is the sole way the native arm is driven: it takes no
file, session or URL argument, reads those identifiers from an arm manifest, and
refuses when the live census no longer matches the manifest's anchor. These
tests exercise the real entry point (``pair_cli.main``) against ``tmp_path``
manifests, a fake census and a fake subprocess, so nothing here boots a server,
a kernel or a child process.

Covered: one happy path per subcommand; zero-, two- and rebound-session
censuses; unreachable census; manifest missing keys, malformed JSON, absent
file and invalid field types; an unsuccessful, non-JSON, non-zero-exit and
identity-missing/mismatched tool envelope; a native timeout; every
``--file``/``--session``/``--url`` attempt as an unknown option; and the
always-on census and identity gates surviving ``python -O``.

Every verdict uses an always-on ``require`` (a bare ``assert`` would be stripped
under ``-O``, which is exactly the silent-pass this benchmark cannot afford).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"

NOTEBOOK_BYTES = b"import marimo\n\napp = marimo.App()\n"


def require(condition, *diagnostics):
    """Always-on verdict check; a bare ``assert`` is stripped under ``python -O``."""
    if condition:
        return
    if not diagnostics:
        raise AssertionError("required condition failed")
    raise AssertionError(diagnostics[0] if len(diagnostics) == 1 else diagnostics)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"no spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before execution so ``@dataclass`` can resolve ``cls.__module__``
    # and so ``pair_cli``'s ``from session_guard import ...`` resolves without
    # putting ``benchmark/`` on ``sys.path``.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("session_guard", BENCH / "session_guard.py")
cli = _load("benchmark_pair_cli", BENCH / "pair_cli.py")


# --------------------------------------------------------------------------- #
# Fake arm: manifest + census + subprocess, all offline
# --------------------------------------------------------------------------- #


class Arm:
    """One manifest with its fake census reader and fake native runner."""

    def __init__(self, tmp_path, **overrides):
        self.notebook = tmp_path / "work" / "notebook.py"
        self.notebook.parent.mkdir(parents=True, exist_ok=True)
        self.notebook.write_bytes(NOTEBOOK_BYTES)
        self.session_id = "s-1"
        self.server_url = "http://127.0.0.1:2718/"
        self.process_id = 4242
        self.python = sys.executable
        document = {
            "server_url": self.server_url,
            "session_id": self.session_id,
            "notebook_path": str(self.notebook),
            "process_id": self.process_id,
            "python": self.python,
            "sha256": hashlib.sha256(NOTEBOOK_BYTES).hexdigest(),
        }
        document.update(overrides)
        self.document = document
        self.manifest_path = tmp_path / "manifest.json"
        self.manifest_path.write_text(json.dumps(document), encoding="utf-8")

        # Defaults: an anchored one-session census and a healthy tool envelope.
        self.census_result = {
            self.session_id: {"path": str(self.notebook)},
        }
        self.census_error = None
        self.stdout = self.tool_stdout()
        self.returncode = 0
        self.runner_error = None
        self.calls = []
        self.census_reads = []
        self.env = {"PATH": "fake"}
        self.ledger_path = tmp_path / "arm" / "ledger.jsonl"

    # -- seams ------------------------------------------------------------- #

    def census(self, url):
        self.census_reads.append(url)
        if self.census_error is not None:
            raise self.census_error
        return self.census_result

    def runner(self, argv, env, timeout):
        self.calls.append((list(argv), dict(env), timeout))
        if self.runner_error is not None:
            raise self.runner_error
        return subprocess.CompletedProcess(list(argv), self.returncode, self.stdout, "")

    # -- helpers ----------------------------------------------------------- #

    def tool_stdout(self, inner="PAIR_CLI_RAN 5"):
        """A healthy ``pair execute`` envelope echoing the anchored notebook."""
        identity = f"{cli.IDENTITY_PREFIX} {cli.normalize_path(str(self.notebook))}"
        return json.dumps({"success": True, "stdout": f"{identity}\n{inner}\n"})

    def run(self, *arguments):
        return cli.main(
            ["--manifest", str(self.manifest_path), *arguments],
            runner=self.runner,
            census_reader=self.census,
            env=self.env,
        )

    def run_with_ledger(self, *arguments):
        """Run through the real entry point with the arm's ledger recorded."""
        return cli.main(
            [
                "--manifest",
                str(self.manifest_path),
                "--ledger",
                str(self.ledger_path),
                *arguments,
            ],
            runner=self.runner,
            census_reader=self.census,
            env=self.env,
        )

    def ledger(self):
        return cli.read_ledger(self.ledger_path)

    def result(self, capsys):
        return json.loads(capsys.readouterr().out)


@pytest.fixture
def arm(tmp_path):
    return Arm(tmp_path)


def refusal(capsys):
    """The single JSON refusal object printed by the last ``main`` call."""
    out = capsys.readouterr().out.strip().splitlines()
    require(len(out) == 1, f"expected one result line, got {out!r}")
    payload = json.loads(out[0])
    require(payload.get("status") == "error", payload)
    return payload


# --------------------------------------------------------------------------- #
# Happy path, one case per subcommand
# --------------------------------------------------------------------------- #

STAGE_CASES = [
    ("run_all", []),
    ("create_cell", ["--code", "x = 1"]),
    ("create_cell", ["--code", "y = 2", "--hide-code"]),
    ("run_cell", ["--cell-id", "cell-7"]),
    ("variables", []),
    ("variables", ["--names", "gain, processed"]),
    ("errors", []),
    ("set_ui", ["--name", "gain", "--value", "4"]),
]


@pytest.mark.parametrize(("stage", "arguments"), STAGE_CASES, ids=str)
def test_happy_path_per_subcommand(arm, capsys, stage, arguments):
    code = arm.run(stage, *arguments)
    payload = arm.result(capsys)

    require(code == cli.EXIT_OK, payload)
    require(set(payload) == {"status", "stage", "notebook_path", "stdout"}, payload)
    require(payload["status"] == "ok", payload)
    require(payload["stage"] == stage, payload)
    require(payload["notebook_path"] == str(arm.notebook.resolve()), payload)
    require(
        payload["stdout"] == json.loads(arm.stdout)["stdout"],
        payload["stdout"],
    )


@pytest.mark.parametrize(("stage", "arguments"), STAGE_CASES, ids=str)
def test_native_argv_is_built_from_manifest_identifiers_only(
    arm, capsys, stage, arguments
):
    arm.run(stage, *arguments)
    capsys.readouterr()
    require(len(arm.calls) == 1, arm.calls)
    argv, env, timeout = arm.calls[0]

    require(argv[0] == arm.python, argv)
    require(argv[1:5] == ["-m", "marimo", "pair", "execute"], argv)
    require(
        argv[5:9] == ["--url", arm.server_url, "--file", str(arm.notebook.resolve())],
        argv,
    )
    require(argv[9] == "-c", argv)
    require(isinstance(argv[10], str) and argv[10].strip(), argv)
    # The census is read from the manifest url, never from an argument.
    require(arm.census_reads == [arm.server_url], arm.census_reads)
    require(timeout == cli.NATIVE_TIMEOUT_S, timeout)
    require(env == arm.env, env)


def test_runner_receives_the_inherited_environment(tmp_path, capsys):
    """With no ``env`` override the child inherits this process's environment."""
    subject = Arm(tmp_path)
    code = cli.main(
        ["--manifest", str(subject.manifest_path), "errors"],
        runner=subject.runner,
        census_reader=subject.census,
    )
    capsys.readouterr()
    require(code == cli.EXIT_OK, code)
    require(subject.calls[0][1] == dict(os.environ), subject.calls[0][1].keys())


def test_variables_default_lists_all_public_globals(arm, capsys):
    arm.run("variables")
    capsys.readouterr()
    code = arm.calls[0][0][10]
    require("_names = None" in code, code)
    require("ctx.globals.keys()" in code, code)


def test_value_is_parsed_as_json_with_string_fallback(arm, capsys):
    arm.run("set_ui", "--name", "gain", "--value", "4")
    arm.run("set_ui", "--name", "gain", "--value", "snap")
    capsys.readouterr()
    require("ctx.set_ui_value(ctx.globals['gain'], 4)" in arm.calls[0][0][10])
    require("ctx.set_ui_value(ctx.globals['gain'], 'snap')" in arm.calls[1][0][10])


def test_hide_code_flag_reaches_the_created_cell(arm, capsys):
    arm.run("create_cell", "--code", "y = 2", "--hide-code")
    capsys.readouterr()
    require("hide_code=True" in arm.calls[0][0][10], arm.calls[0][0][10])


# --------------------------------------------------------------------------- #
# Census gate
# --------------------------------------------------------------------------- #

CENSUS_FAILURES = {
    "zero-sessions": {},
    "two-sessions": {
        "s-1": {"path": "{notebook}"},
        "s-2": {"path": "{other}"},
    },
    "rebound-id": {"s-1": {"path": "{other}"}},
    "not-a-mapping": ["s-1"],
    "empty-entry-path": {"s-1": {"path": ""}},
}


def _census_case(case, notebook, other):
    """Build one failing census, substituting real paths (never via JSON)."""
    raw = CENSUS_FAILURES[case]
    if not isinstance(raw, dict):
        return raw
    return {
        session: {
            key: (
                value.replace("{notebook}", str(notebook)).replace(
                    "{other}", str(other)
                )
                if isinstance(value, str)
                else value
            )
            for key, value in entry.items()
        }
        for session, entry in raw.items()
    }


@pytest.mark.parametrize("case", sorted(CENSUS_FAILURES), ids=str)
def test_census_failure_refuses_with_exit_2_before_the_native_call(
    arm, capsys, tmp_path, case
):
    other = tmp_path / "someone_elses.py"
    other.write_bytes(NOTEBOOK_BYTES)
    arm.census_result = _census_case(case, arm.notebook, other)

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_CENSUS_MISMATCH, payload)
    require(payload["code"] == "census_mismatch", payload)
    require(payload["stage"] == "census", payload)
    require(payload["notebook_path"] == str(arm.notebook.resolve()), payload)
    require(arm.calls == [], "the native CLI ran despite a failed census gate")


@pytest.mark.parametrize(("stage", "arguments"), STAGE_CASES, ids=str)
def test_census_gate_precedes_every_subcommand(arm, capsys, stage, arguments):
    arm.census_result = {}

    code = arm.run(stage, *arguments)
    payload = refusal(capsys)

    require(code == cli.EXIT_CENSUS_MISMATCH, payload)
    require(arm.calls == [], f"{stage} reached the native CLI")


def test_unreachable_census_refuses_with_exit_4(arm, capsys):
    arm.census_error = OSError("connection refused")

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_CENSUS_UNREACHABLE, payload)
    require(payload["code"] == "census_unreachable", payload)
    require(arm.calls == [], "the native CLI ran despite an unreachable census")


def test_non_json_census_refuses_with_exit_4(arm, capsys):
    arm.census_error = ValueError("not json")

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_CENSUS_UNREACHABLE, payload)
    require(arm.calls == [], "the native CLI ran despite an unreadable census")


# --------------------------------------------------------------------------- #
# Manifest validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", cli.MANIFEST_KEYS, ids=str)
def test_manifest_missing_key_refuses_before_any_read(arm, capsys, key):
    document = dict(arm.document)
    document.pop(key)
    arm.manifest_path.write_text(json.dumps(document), encoding="utf-8")

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_MANIFEST, payload)
    require(payload["code"] == "manifest_missing_keys", payload)
    require(key in payload["error"], payload)
    require(arm.census_reads == [] and arm.calls == [], "work started anyway")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("notebook_path", ""),
        ("notebook_path", "   "),
        ("notebook_path", 17),
        ("server_url", ""),
        ("session_id", None),
        ("python", ""),
        ("process_id", "4242"),
        ("process_id", True),
        ("sha256", "deadbeef"),
        ("sha256", "D" * 64),
    ],
    ids=str,
)
def test_manifest_invalid_field_refuses_with_exit_3(arm, capsys, key, value):
    document = dict(arm.document)
    document[key] = value
    arm.manifest_path.write_text(json.dumps(document), encoding="utf-8")

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_MANIFEST, payload)
    require(payload["code"] == "manifest_field_invalid", payload)
    require(arm.census_reads == [] and arm.calls == [], "work started anyway")


def test_manifest_malformed_json_refuses_with_exit_3(arm, capsys):
    arm.manifest_path.write_text("{not json", encoding="utf-8")

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_MANIFEST, payload)
    require(payload["code"] == "manifest_json_invalid", payload)


def test_manifest_absent_refuses_with_exit_3(arm, capsys):
    arm.manifest_path.unlink()

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_MANIFEST, payload)
    require(payload["code"] == "manifest_unreadable", payload)


def test_manifest_non_object_refuses_with_exit_3(arm, capsys):
    arm.manifest_path.write_text("[1, 2]", encoding="utf-8")

    code = arm.run("errors")
    payload = refusal(capsys)

    require(code == cli.EXIT_MANIFEST, payload)
    require(payload["code"] == "manifest_not_object", payload)


def test_load_manifest_accepts_extra_keys(arm):
    """Bookkeeping keys the setup step adds must not break the interface."""
    document = dict(arm.document, created_at="2026-10-17", arm="native")
    arm.manifest_path.write_text(json.dumps(document), encoding="utf-8")

    manifest = cli.load_manifest(arm.manifest_path)

    require(manifest.session_id == "s-1", manifest)
    require(manifest.anchor.process_id == 4242, manifest.anchor)


# --------------------------------------------------------------------------- #
# Tool envelope and identity
# --------------------------------------------------------------------------- #


def test_tool_success_false_refuses_with_exit_8(arm, capsys):
    arm.returncode = 1
    arm.stdout = json.dumps({"success": False, "error": "no running session"})

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_TOOL_ENVELOPE, payload)
    require(payload["code"] == "tool_unsuccessful", payload)
    require("no running session" in payload["error"], payload)


def test_tool_success_false_with_exit_0_still_refuses(arm, capsys):
    arm.stdout = json.dumps({"success": False, "stdout": ""})

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_TOOL_ENVELOPE, payload)
    require(payload["code"] == "tool_unsuccessful", payload)


def test_tool_non_json_stdout_refuses_with_exit_7(arm, capsys):
    arm.stdout = "Traceback (most recent call last): boom"

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_TOOL_NON_JSON, payload)
    require(payload["code"] == "tool_non_json", payload)


def test_tool_non_json_stdout_with_nonzero_exit_refuses_with_exit_5(arm, capsys):
    arm.returncode = 3
    arm.stdout = "usage error"

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_NATIVE_FAILED, payload)
    require(payload["code"] == "native_failed", payload)


def test_tool_success_claim_with_nonzero_exit_refuses(arm, capsys):
    arm.returncode = 1

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_NATIVE_FAILED, payload)
    require(payload["code"] == "tool_exit_conflict", payload)


def test_tool_payload_without_stdout_refuses_with_exit_8(arm, capsys):
    arm.stdout = json.dumps({"success": True})

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_TOOL_ENVELOPE, payload)
    require(payload["code"] == "tool_stdout_missing", payload)


def test_tool_without_identity_echo_refuses_with_exit_9(arm, capsys):
    arm.stdout = json.dumps({"success": True, "stdout": "PAIR_CLI_RAN 5\n"})

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_IDENTITY, payload)
    require(payload["code"] == "identity_missing", payload)


def test_tool_with_mismatched_identity_refuses_with_exit_9(arm, capsys):
    arm.stdout = json.dumps(
        {
            "success": True,
            "stdout": f"{cli.IDENTITY_PREFIX} /elsewhere/another.py\n",
        }
    )

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_IDENTITY, payload)
    require(payload["code"] == "identity_mismatch", payload)
    require("another.py" in payload["error"], payload)


def test_tool_with_two_identity_lines_refuses_with_exit_9(arm, capsys):
    line = f"{cli.IDENTITY_PREFIX} {cli.normalize_path(str(arm.notebook))}\n"
    arm.stdout = json.dumps({"success": True, "stdout": line + line})

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_IDENTITY, payload)
    require(payload["code"] == "identity_missing", payload)


def test_tool_payload_path_echo_mismatch_refuses(arm, capsys):
    arm.stdout = json.dumps(
        {
            "success": True,
            "file": "/elsewhere/another.py",
            "stdout": f"{cli.IDENTITY_PREFIX} "
            f"{cli.normalize_path(str(arm.notebook))}\n",
        }
    )

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_IDENTITY, payload)
    require(payload["code"] == "identity_echo_mismatch", payload)


def test_tool_payload_session_echo_mismatch_refuses(arm, capsys):
    arm.stdout = json.dumps(
        {
            "success": True,
            "session_id": "someone-elses",
            "stdout": f"{cli.IDENTITY_PREFIX} "
            f"{cli.normalize_path(str(arm.notebook))}\n",
        }
    )

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_IDENTITY, payload)
    require(payload["code"] == "identity_echo_mismatch", payload)


def test_tool_payload_echoing_the_anchor_is_accepted(arm, capsys):
    arm.stdout = json.dumps(
        {
            "success": True,
            "file": str(arm.notebook),
            "session_id": arm.session_id,
            "pid": arm.process_id,
            "stdout": f"{cli.IDENTITY_PREFIX} "
            f"{cli.normalize_path(str(arm.notebook))}\nPAIR_CLI_RAN 5\n",
        }
    )

    code = arm.run("run_all")
    payload = arm.result(capsys)

    require(code == cli.EXIT_OK, payload)
    require(payload["status"] == "ok", payload)


def test_native_timeout_refuses_with_exit_6(arm, capsys):
    arm.runner_error = subprocess.TimeoutExpired(cmd=["python"], timeout=60)

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_NATIVE_TIMEOUT, payload)
    require(payload["code"] == "native_timeout", payload)


def test_unlaunchable_native_interpreter_refuses_with_exit_5(arm, capsys):
    arm.runner_error = OSError("no such interpreter")

    code = arm.run("run_all")
    payload = refusal(capsys)

    require(code == cli.EXIT_NATIVE_FAILED, payload)
    require(payload["code"] == "native_unlaunchable", payload)


# --------------------------------------------------------------------------- #
# The CLI never takes a file, session or url selector
# --------------------------------------------------------------------------- #

SELECTOR_ATTEMPTS = [
    ["errors", "--file", "evil.py"],
    ["errors", "--session", "someone-elses"],
    ["errors", "--url", "http://127.0.0.1:9999"],
    ["run_all", "--file", "evil.py"],
    ["create_cell", "--code", "x = 1", "--file", "evil.py"],
    ["variables", "--names", "a", "--session", "x"],
    ["set_ui", "--name", "gain", "--value", "4", "--file", "evil.py"],
    ["run_cell", "--cell-id", "c", "--session", "x"],
    ["--file", "evil.py", "errors"],
    ["--url", "http://127.0.0.1:9999", "run_all"],
]


@pytest.mark.parametrize("arguments", SELECTOR_ATTEMPTS, ids=str)
def test_selector_attempts_are_rejected_as_unknown_options(arm, capsys, arguments):
    code = arm.run(*arguments)
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require(payload["code"] == "usage_error", payload)
    require(payload["stage"] == "cli", payload)
    require(
        arm.census_reads == [] and arm.calls == [],
        "a selector attempt reached the census or the native CLI",
    )


def test_selector_flag_is_reported_as_an_unrecognized_argument(arm, capsys):
    code = arm.run("errors", "--file", "evil.py")
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require("unrecognized arguments" in payload["error"], payload)
    require("--file" in payload["error"], payload)


def test_a_code_value_that_names_a_flag_is_still_rejected(arm, capsys):
    """``--code --file`` cannot smuggle a selector: ``--code`` wants a value."""
    code = arm.run("create_cell", "--code", "--file", "evil.py")
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require(arm.calls == [], "the native CLI ran for a smuggled flag")


def test_manifest_option_is_mandatory(arm, capsys):
    code = cli.main(
        ["errors"], runner=arm.runner, census_reader=arm.census, env=arm.env
    )
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require("--manifest" in payload["error"], payload)
    require(arm.calls == [], "the native CLI ran without a manifest")


def test_subcommand_is_mandatory(arm, capsys):
    code = cli.main(
        ["--manifest", str(arm.manifest_path)],
        runner=arm.runner,
        census_reader=arm.census,
        env=arm.env,
    )
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require(arm.calls == [], "the native CLI ran without a subcommand")


def test_unknown_subcommand_is_rejected(arm, capsys):
    code = arm.run("delete_cell")
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require(arm.calls == [], "the native CLI ran for an unknown subcommand")


def test_blank_names_is_rejected(arm, capsys):
    code = arm.run("variables", "--names", " , ")
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require("--names" in payload["error"], payload)
    require(arm.calls == [], "the native CLI ran for a blank name list")


def test_missing_stage_option_is_rejected(arm, capsys):
    code = arm.run("run_cell")
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require("--cell-id" in payload["error"], payload)
    require(arm.calls == [], "the native CLI ran without a cell id")


# --------------------------------------------------------------------------- #
# No shell execution, generated-code shape
# --------------------------------------------------------------------------- #


def test_default_runner_never_enables_a_shell(monkeypatch):
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli.run_native(["/native/python", "-m", "marimo"], {"PATH": "x"}, 7.0)

    require(recorded["argv"] == ["/native/python", "-m", "marimo"], recorded["argv"])
    require(recorded["shell"] is False, recorded["shell"])
    require(recorded["check"] is False, recorded["check"])
    require(recorded["capture_output"] is True, recorded["capture_output"])
    require(recorded["text"] is True, recorded["text"])
    require(recorded["timeout"] == 7.0, recorded["timeout"])
    require(recorded["env"] == {"PATH": "x"}, recorded["env"])


@pytest.mark.parametrize("stage", cli.STAGES, ids=str)
def test_identity_echo_precedes_the_stage_effects(stage):
    options = {
        "run_all": {},
        "create_cell": {"code": "x = 1"},
        "run_cell": {"cell_id": "c"},
        "variables": {},
        "errors": {},
        "set_ui": {"name": "gain", "value": 4},
    }[stage]
    # The first call the stage makes, which the echo must precede.
    marker = {
        "run_all": "ctx.cells.values()",
        "create_cell": "ctx.create_cell(",
        "run_cell": "ctx.run_cell(",
        "variables": "ctx.globals.keys()",
        "errors": "ctx.cells.items()",
        "set_ui": "ctx.set_ui_value(",
    }[stage]
    code = cli.code_for(stage, **options)

    require(
        code.startswith("import os as _os\nimport marimo._code_mode as cm\n"),
        code,
    )
    require("async with cm.get_context() as ctx:" in code, code)
    require("app_metadata.filename" in code, code)
    require(
        code.index(cli.IDENTITY_PREFIX) < code.index(marker),
        f"{stage}: the identity echo must precede the stage's own calls",
    )


def test_code_for_refuses_a_missing_stage_option():
    with pytest.raises(cli.PairCliError) as error:
        cli.code_for("create_cell")
    require(error.value.exit_code == cli.EXIT_USAGE, error.value.exit_code)
    require(error.value.code == "cli_argument_missing", error.value.code)

    with pytest.raises(cli.PairCliError) as error:
        cli.code_for("run_cell")
    require(error.value.exit_code == cli.EXIT_USAGE, error.value.exit_code)
    require(error.value.code == "cli_argument_missing", error.value.code)


def test_code_for_refuses_an_unknown_stage():
    with pytest.raises(cli.PairCliError) as error:
        cli.code_for("restart_kernel")
    require(error.value.exit_code == cli.EXIT_INTERNAL, error.value.exit_code)


def test_run_all_code_runs_every_cell():
    code = cli.code_for("run_all")

    require("for _cell in ctx.cells.values():" in code, code)
    require("ctx.run_cell(_cell.id)" in code, code)


def test_create_cell_code_embeds_the_literal_source():
    code = cli.code_for("create_cell", code="print('--file not a flag')")

    require(
        "ctx.create_cell(\"print('--file not a flag')\", hide_code=False)" in code,
        code,
    )


# --------------------------------------------------------------------------- #
# The always-on gates must survive ``python -O``
# --------------------------------------------------------------------------- #

_OPTIMIZED_DRIVER = r"""
import json
import subprocess
import sys

MODE, BENCH, MANIFEST = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, BENCH)
import pair_cli

DOCUMENT = json.loads(open(MANIFEST, encoding="utf-8").read())
NOTEBOOK = DOCUMENT["notebook_path"]
TWO = {"a": {"path": "one.py"}, "b": {"path": "two.py"}}
ONE = {DOCUMENT["session_id"]: {"path": NOTEBOOK}}


def never_runner(*args, **kwargs):
    raise AssertionError("runner called")


def runner(argv, env, timeout):
    if MODE == "bad-identity":
        line = pair_cli.IDENTITY_PREFIX + " /elsewhere/other.py"
    else:
        line = pair_cli.IDENTITY_PREFIX + " " + pair_cli.normalize_path(NOTEBOOK)
    envelope = json.dumps({"success": True, "stdout": line})
    return subprocess.CompletedProcess(argv, 0, envelope, "")


CENSUS = TWO if MODE == "two-sessions" else ONE
CODE = pair_cli.main(
    ["--manifest", MANIFEST, "errors"],
    census_reader=lambda url: CENSUS,
    runner=never_runner if MODE == "two-sessions" else runner,
)
EXPECTED = {"two-sessions": 2, "bad-identity": 9, "ok": 0}[MODE]
if CODE != EXPECTED:
    raise SystemExit("WRONG_CODE " + repr(CODE))
print("OPTIMIZED_HELD")
"""


@pytest.mark.parametrize("mode", ["two-sessions", "bad-identity", "ok"])
def test_gates_survive_optimized_mode(tmp_path, mode):
    """A census or identity gate stripped under ``-O`` would pass silently."""
    subject = Arm(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            _OPTIMIZED_DRIVER,
            mode,
            str(BENCH),
            str(subject.manifest_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    require(
        completed.returncode == 0,
        (completed.returncode, completed.stdout, completed.stderr),
    )
    require("OPTIMIZED_HELD" in completed.stdout, completed.stdout)
    require("runner called" not in completed.stderr, completed.stderr)


# --------------------------------------------------------------------------- #
# Ledger: the audit trail a bypass cannot fake
# --------------------------------------------------------------------------- #


def test_ledger_records_a_created_cell_with_its_source_and_ids(arm, capsys):
    arm.stdout = arm.tool_stdout("PAIR_CLI_CREATED cell-9")
    code = arm.run_with_ledger("create_cell", "--code", "normalized = 1.25")
    require(code == cli.EXIT_OK, arm.result(capsys))

    entries = arm.ledger()
    require(len(entries) == 1, entries)
    entry = entries[0]
    require(entry["seq"] == 1, entry)
    require(entry["command"] == "create_cell", entry)
    require(entry["status"] == "ok", entry)
    require(entry["created_cell_ids"] == ["cell-9"], entry)
    require(entry["code"] == "normalized = 1.25", entry)
    require(entry["sha256_before"] == hashlib.sha256(NOTEBOOK_BYTES).hexdigest(), entry)
    require(isinstance(entry["sha256_after"], str), entry)


def test_ledger_chains_before_to_the_previous_after(arm, capsys):
    require(arm.run_with_ledger("variables") == cli.EXIT_OK, arm.result(capsys))
    require(arm.run_with_ledger("errors") == cli.EXIT_OK, arm.result(capsys))

    entries = arm.ledger()
    require([entry["seq"] for entry in entries] == [1, 2], entries)
    require(entries[1]["sha256_before"] == entries[0]["sha256_after"], entries)


def test_a_write_the_shim_did_not_make_breaks_the_chain(arm, capsys):
    """The detector's input: an external write leaves a discontinuity."""
    require(arm.run_with_ledger("variables") == cli.EXIT_OK, arm.result(capsys))
    arm.notebook.write_bytes(NOTEBOOK_BYTES + b"# written outside the shim\n")
    require(arm.run_with_ledger("variables") == cli.EXIT_OK, arm.result(capsys))

    entries = arm.ledger()
    require(entries[1]["sha256_before"] != entries[0]["sha256_after"], entries)


def test_a_read_only_call_records_equal_hashes_and_no_cells(arm, capsys):
    require(arm.run_with_ledger("variables", "--names", "gain") == cli.EXIT_OK)
    entry = arm.ledger()[0]
    require(entry["sha256_before"] == entry["sha256_after"], entry)
    require(entry["created_cell_ids"] == [], entry)
    require("code" not in entry, entry)


def test_set_ui_records_the_name_and_parsed_value(arm, capsys):
    require(
        arm.run_with_ledger("set_ui", "--name", "gain", "--value", "4") == cli.EXIT_OK
    )
    entry = arm.ledger()[0]
    require(entry["ui"] == {"name": "gain", "value": 4}, entry)


def test_a_refused_call_writes_no_ledger_entry(arm, capsys):
    arm.census_result = {"other-session": {"path": str(arm.notebook)}}
    code = arm.run_with_ledger("variables")
    payload = refusal(capsys)

    require(code == cli.EXIT_CENSUS_MISMATCH, payload)
    require(arm.ledger() == [], "a refused call must not write a ledger entry")


def test_ledger_path_must_be_absolute(arm, capsys):
    code = cli.main(
        [
            "--manifest",
            str(arm.manifest_path),
            "--ledger",
            "relative/ledger.jsonl",
            "variables",
        ],
        runner=arm.runner,
        census_reader=arm.census,
        env=arm.env,
    )
    payload = refusal(capsys)

    require(code == cli.EXIT_USAGE, payload)
    require("absolute" in payload["error"], payload)
    require(arm.calls == [], "the native CLI must not run when the ledger is refused")


def test_a_corrupt_ledger_refuses_before_the_native_call(arm, capsys):
    arm.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    arm.ledger_path.write_text("{not json}\n", encoding="utf-8")
    code = arm.run_with_ledger("variables")
    payload = refusal(capsys)

    require(code == cli.EXIT_LEDGER, payload)
    require(payload["code"] == "ledger_corrupt", payload)
    require(arm.calls == [], "the native CLI must not run against a corrupt ledger")


def test_a_ledger_line_that_is_not_an_object_refuses(arm, capsys):
    arm.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    arm.ledger_path.write_text("[]\n", encoding="utf-8")
    code = arm.run_with_ledger("variables")
    payload = refusal(capsys)

    require(code == cli.EXIT_LEDGER, payload)
    require(payload["code"] == "ledger_corrupt", payload)
