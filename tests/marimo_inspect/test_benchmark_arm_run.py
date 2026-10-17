"""Kernel-free gates for one arm's agent run (``benchmark.arm_run``).

``benchmark/arm_run.py`` prepares ONE arm — an ephemeral ``HERMES_HOME``, a
prompt built from the shared mission plus the arm's interface note, the arm's
toolset list and budget — and launches its agent as a separate ``hermes chat``
process. These tests exercise the real entry point against ``tmp_path`` and an
**injected launch seam**, so nothing here invokes a model, boots a server or
launches a child process.

Covered: the wrapper arm's configuration declaring exactly its one MCP server and
the native arm's declaring none; a byte-exact prompt (mission, one blank line,
interface note); a missing budget argument refusing for both arms; a fingerprint
identical for two runs that differ only by arm; the native ``pair`` launcher
passing options through and quoting the manifest path safely; the dry run
executing nothing; the child argv and environment; the run metadata; and a scan
proving neither file carries a machine-specific absolute path.

Every verdict uses an always-on ``require`` (a bare ``assert`` would be stripped
under ``-O``, which is exactly the silent-pass this benchmark cannot afford).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
MODULE_PATH = BENCH / "arm_run.py"
PROMPTS = BENCH / "prompts"


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
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


arm_run = _load("benchmark_arm_run", MODULE_PATH)

MISSION_BYTES = (PROMPTS / "mission.md").read_bytes()
NATIVE_NOTE_BYTES = (PROMPTS / "native-interface.md").read_bytes()
WRAPPER_NOTE_BYTES = (PROMPTS / "wrapper-interface.md").read_bytes()

MISSION = PROMPTS / "mission.md"
NATIVE_NOTE = PROMPTS / "native-interface.md"
WRAPPER_NOTE = PROMPTS / "wrapper-interface.md"

# The five arguments that must never have a default.
BUDGET_ARGUMENTS = (
    "--model",
    "--provider",
    "--reasoning",
    "--max-turns",
    "--run-budget",
)

BUDGET = {
    "model": "a-model",
    "provider": "a-provider",
    "reasoning": "medium",
    "max_turns": 12,
    "run_budget": 240.0,
}

FIXED_TIME = "2026-01-01T00:00:00+00:00"


class Recorder:
    """The injected launch seam: records calls and launches nothing."""

    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        self.calls: list[tuple[list[str], dict, Path, float]] = []

    def __call__(self, argv, env, transcript, timeout):
        self.calls.append(
            ([str(item) for item in argv], dict(env), Path(transcript), timeout)
        )
        return self.exit_code


def _case(tmp_path, home_name="home"):
    """The shared arguments for one run, all under ``tmp_path``."""
    return {
        "home": tmp_path / home_name,
        "workdir": tmp_path / "work",
        "transcript": tmp_path / "out" / "arm.txt",
        "manifest": tmp_path / "manifest.json",
        "shim": BENCH / "pair_cli.py",
    }


def _run(tmp_path, arm, *, recorder=None, dry_run=False, home_name="home", **overrides):
    """``perform_run`` for one arm with the launch seam replaced."""
    case = _case(tmp_path, home_name)
    # A fixed command name keeps the printed argv machine-independent.
    case["command"] = "hermes"
    if arm == "wrapper":
        case["manifest"] = None
        case["shim"] = None
        case["mcp_command"] = str(tmp_path / "bin" / "marimo-inspect")
    else:
        case["mcp_command"] = None
    case.update(overrides)
    recorder = recorder if recorder is not None else Recorder()
    metadata = arm_run.perform_run(
        arm=arm,
        mission=MISSION,
        interface=NATIVE_NOTE if arm == "native" else WRAPPER_NOTE,
        dry_run=dry_run,
        launch=recorder,
        clock=lambda: FIXED_TIME,
        **{**BUDGET, **case},
    )
    return metadata, recorder, case


def _printed(capsys, prefix):
    """The single stdout line beginning with *prefix*, with the prefix removed."""
    lines = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith(prefix)
    ]
    require(len(lines) == 1, f"expected one {prefix!r} line, got {lines!r}")
    return lines[0][len(prefix) :]


# --------------------------------------------------------------------------- #
# 1. The configuration home: exactly one MCP server for the wrapper, none native
# --------------------------------------------------------------------------- #


def test_wrapper_configuration_declares_exactly_the_one_mcp_server(tmp_path):
    command = str(tmp_path / "bin" / "marimo-inspect")
    text = arm_run.render_config("wrapper", mcp_command=command)

    expected = (
        "# Ephemeral home for one arm's run (generated; do not edit).\n"
        "mcp_servers:\n"
        "  marimo-inspect:\n"
        f"    command: '{command}'\n"
        "    args:\n"
        "      - --transport\n"
        "      - stdio\n"
        "    timeout: 60\n"
    )
    require(text == expected, text)

    # Exactly one server, named as the interface note names it, and the server
    # runs the command the caller supplied.
    servers = _mcp_server_blocks(text)
    require(list(servers) == ["marimo-inspect"], servers)
    require(servers["marimo-inspect"]["command"] == f"'{command}'", servers)
    require("  --transport" not in text, text)


def test_native_configuration_declares_no_mcp_server(tmp_path):
    text = arm_run.render_config("native")

    require(
        text == "# Ephemeral home for one arm's run (generated; do not edit).\n"
        "mcp_servers: {}\n",
        text,
    )
    require(_mcp_server_blocks(text) == {}, text)
    require(arm_run.MCP_SERVER_NAME not in text, text)


def test_written_config_file_matches_the_rendered_text(tmp_path, capsys):
    recorder = Recorder()
    _run(tmp_path, "wrapper", recorder=recorder)

    written = (tmp_path / "home" / arm_run.CONFIG_FILENAME).read_bytes()
    expected = arm_run.render_config(
        "wrapper", mcp_command=str(tmp_path / "bin" / "marimo-inspect")
    ).encode("utf-8")
    require(written == expected, (written, expected))
    require(b"\r\n" not in written, "the config was written with translated newlines")


def test_wrapper_configuration_refuses_a_missing_command(tmp_path):
    with pytest.raises(arm_run.ArmRunRefusal, match="wrapper arm needs --mcp-command"):
        arm_run.render_config("wrapper")
    with pytest.raises(arm_run.ArmRunRefusal, match="wrapper arm needs --mcp-command"):
        arm_run.render_config("wrapper", mcp_command="   ")


def test_native_configuration_refuses_an_mcp_command(tmp_path):
    with pytest.raises(
        arm_run.ArmRunRefusal, match="only meaningful for the wrapper arm"
    ):
        arm_run.render_config("native", mcp_command="anything")


def _mcp_server_blocks(text: str) -> dict:
    """Parse the ``mcp_servers`` block: ``{}`` -> no servers, else name -> fields."""
    lines = text.splitlines()
    require(lines and lines[0].startswith("#"), "no generated-file header")
    require(lines[1].startswith("mcp_servers:"), lines[:2])
    if lines[1] == "mcp_servers: {}":
        return {}
    servers: dict[str, dict] = {}
    current: dict | None = None
    for line in lines[2:]:
        if line.startswith("    "):
            key, _, value = line.strip().partition(":")
            if value:
                current[key] = value.strip()
        elif line.startswith("  "):
            name = line.strip().rstrip(":")
            current = {}
            servers[name] = current
        elif line == "":
            break
        else:  # pragma: no cover - a shape this parser does not expect
            raise AssertionError(f"unexpected config line: {line!r}")
    return servers


# --------------------------------------------------------------------------- #
# 2. The prompt: mission verbatim, one blank line, interface note verbatim
# --------------------------------------------------------------------------- #


def test_prompt_is_mission_plus_blank_line_plus_interface_note():
    prompt = arm_run.assemble_prompt(MISSION_BYTES, NATIVE_NOTE_BYTES)

    # Byte-identical to the two committed files with exactly one blank line
    # between them.
    require(prompt == MISSION_BYTES + b"\n" + NATIVE_NOTE_BYTES, prompt[-64:])
    require(prompt.count(b"\n\n\n") == 0, "the join produced two blank lines")
    require(MISSION_BYTES.rstrip(b"\n") in prompt, "the mission text is not verbatim")
    require(NATIVE_NOTE_BYTES.strip() in prompt, "the interface note is not verbatim")
    require(
        prompt.index(MISSION_BYTES.rstrip(b"\n")) < prompt.index(b"# Native arm"),
        prompt,
    )


@pytest.mark.parametrize(
    ("mission", "interface"),
    [
        (b"mission\n", b"note\n"),
        (b"mission", b"note"),
        (b"mission\n\n\n", b"note"),
        (b"mission\r\n", b"note\r\n"),
    ],
    ids=str,
)
def test_prompt_boundary_is_exactly_one_blank_line(mission, interface):
    prompt = arm_run.assemble_prompt(mission, interface)

    require(
        prompt == mission.rstrip(b"\r\n") + b"\n\n" + interface.lstrip(b"\r\n"), prompt
    )
    require(prompt.count(b"\n\n") == 1, prompt)
    require(prompt.startswith(mission.rstrip(b"\r\n")), prompt)
    require(prompt.endswith(interface.lstrip(b"\r\n")), prompt)


def test_prompt_file_is_written_byte_exactly(tmp_path, capsys):
    _run(tmp_path, "native")

    written = (tmp_path / "home" / arm_run.PROMPT_FILENAME).read_bytes()
    require(
        written == arm_run.assemble_prompt(MISSION_BYTES, NATIVE_NOTE_BYTES), written
    )
    # The prompt carries no per-arm edit of the mission.
    require(
        written == MISSION_BYTES + b"\n" + NATIVE_NOTE_BYTES,
        "the committed mission was edited per arm",
    )


def test_both_arms_get_the_identical_mission_and_different_notes(tmp_path):
    native, _native_recorder, _case = _run(tmp_path, "native", home_name="native-home")
    wrapper, _wrapper_recorder, _case = _run(
        tmp_path, "wrapper", home_name="wrapper-home"
    )

    require(native["mission_sha256"] == wrapper["mission_sha256"], native)
    require(
        native["mission_sha256"] == hashlib.sha256(MISSION_BYTES).hexdigest(),
        native["mission_sha256"],
    )
    require(
        native["interface_sha256"] != wrapper["interface_sha256"],
        "the arms share a note",
    )
    require(
        native["interface_sha256"] == hashlib.sha256(NATIVE_NOTE_BYTES).hexdigest(),
        native,
    )


def test_prompt_assembly_refuses_an_empty_source():
    with pytest.raises(arm_run.ArmRunRefusal, match="mission file is empty"):
        arm_run.assemble_prompt(b"  \n", b"note\n")
    with pytest.raises(arm_run.ArmRunRefusal, match="interface note is empty"):
        arm_run.assemble_prompt(b"mission\n", b"\n")


# --------------------------------------------------------------------------- #
# 3. Every budget argument is required, for both arms
# --------------------------------------------------------------------------- #


def _argv(tmp_path, arm, *, drop=None, override=None):
    arguments = [
        "--arm",
        arm,
        "--home",
        str(tmp_path / "home"),
        "--workdir",
        str(tmp_path / "work"),
        "--mission",
        str(MISSION),
        "--interface",
        str(NATIVE_NOTE if arm == "native" else WRAPPER_NOTE),
        "--transcript",
        str(tmp_path / "out" / "arm.txt"),
        "--model",
        "a-model",
        "--provider",
        "a-provider",
        "--reasoning",
        "medium",
        "--max-turns",
        "12",
        "--run-budget",
        "240",
        "--dry-run",
    ]
    if arm == "native":
        arguments += [
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--shim",
            str(BENCH / "pair_cli.py"),
        ]
    else:
        arguments += ["--mcp-command", str(tmp_path / "bin" / "marimo-inspect")]
    if drop is not None:
        index = arguments.index(drop)
        del arguments[index : index + 2]
    if override is not None:
        index = arguments.index(override[0])
        arguments[index + 1] = override[1]
    return arguments


@pytest.mark.parametrize("arm", ["native", "wrapper"])
@pytest.mark.parametrize("argument", BUDGET_ARGUMENTS)
def test_missing_budget_argument_refuses_for_both_arms(tmp_path, capsys, arm, argument):
    with pytest.raises(SystemExit) as exit_info:
        arm_run.main(_argv(tmp_path, arm, drop=argument))

    require(exit_info.value.code not in (0, None), exit_info.value.code)
    captured = capsys.readouterr()
    require(argument in captured.err, captured.err)
    require(captured.out == "", "a refused run still printed a fingerprint")
    require(
        not (tmp_path / "home").exists(), "a refused run wrote its configuration home"
    )


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_a_supplied_budget_still_refuses_an_invalid_value(tmp_path, arm):
    for key, bogus in (
        ("reasoning", "loud"),
        ("max_turns", 0),
        ("run_budget", 0),
        ("model", "   "),
        ("provider", ""),
    ):
        arguments = _argv(
            tmp_path, arm, override=(f"--{key.replace('_', '-')}", str(bogus))
        )
        try:
            code = arm_run.main(arguments)
        except SystemExit as exit_info:  # argparse rejected the value outright
            require(exit_info.code not in (0, None), (key, exit_info.code))
            continue
        require(code != 0, (key, code))


def test_perform_run_requires_every_budget_keyword(tmp_path):
    with pytest.raises(TypeError):
        arm_run.perform_run(
            arm="native",
            home=tmp_path / "home",
            workdir=tmp_path / "work",
            mission=MISSION,
            interface=NATIVE_NOTE,
            transcript=tmp_path / "arm.txt",
            model="a-model",
            provider="a-provider",
            reasoning="medium",
            max_turns=12,
        )  # no run_budget


# --------------------------------------------------------------------------- #
# 4. The budget fingerprint: one value for both arms; toolsets recorded apart
# --------------------------------------------------------------------------- #


def test_two_runs_of_the_same_arm_print_the_same_fingerprint(tmp_path):
    first, _first_recorder, _case = _run(tmp_path, "native", home_name="first-home")
    second, _second_recorder, _case = _run(tmp_path, "native", home_name="second-home")

    require(first["fingerprint"] == second["fingerprint"], (first, second))
    require(len(first["fingerprint"]) == 64, first["fingerprint"])


def test_fingerprint_is_arm_independent_and_toolsets_are_recorded_apart(tmp_path):
    """The two arms must share one fingerprint; their surfaces are recorded apart.

    The toolset list is the interface under test, so it lives in the metadata and
    must never enter the fingerprint — otherwise the two arms could never be
    shown to have run under the same budget.
    """
    native, _native_recorder, _case = _run(tmp_path, "native", home_name="native-home")
    wrapper, _wrapper_recorder, _case = _run(
        tmp_path, "wrapper", home_name="wrapper-home"
    )

    require(
        arm_run.effective_toolsets("native") != arm_run.effective_toolsets("wrapper"),
        "the arms must not share a toolset list",
    )
    require(
        native["fingerprint"] == wrapper["fingerprint"],
        (native["fingerprint"], wrapper["fingerprint"]),
    )
    require(
        native["toolsets"] == list(arm_run.NATIVE_TOOLSETS),
        native["toolsets"],
    )
    require(
        wrapper["toolsets"] == list(arm_run.WRAPPER_TOOLSETS),
        wrapper["toolsets"],
    )
    # The fingerprint is insensitive to the toolset list by construction.
    require(
        arm_run.budget_fingerprint(**BUDGET) == arm_run.budget_fingerprint(**BUDGET),
        "the fingerprint is not stable",
    )


def test_the_arms_toolset_lists_are_narrow_and_disjoint():
    native = arm_run.effective_toolsets("native")
    wrapper = arm_run.effective_toolsets("wrapper")

    require(native == arm_run.NATIVE_TOOLSETS == ("terminal",), native)
    require(wrapper == arm_run.WRAPPER_TOOLSETS == (arm_run.MCP_TOOLSET,), wrapper)
    require("file" not in native and "file" not in wrapper, (native, wrapper))
    require(not set(native) & set(wrapper), (native, wrapper))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model", "another-model"),
        ("provider", "another-provider"),
        ("reasoning", "high"),
        ("max_turns", 99),
        ("run_budget", 999.0),
    ],
    ids=str,
)
def test_fingerprint_changes_with_any_arm_independent_setting(key, value):
    baseline = arm_run.budget_fingerprint(**BUDGET)
    changed = arm_run.budget_fingerprint(**{**BUDGET, key: value})

    require(baseline != changed, f"{key} did not change the fingerprint")
    require(
        baseline == arm_run.budget_fingerprint(**BUDGET),
        "the fingerprint is not stable",
    )


def test_fingerprint_covers_exactly_the_documented_settings():
    payload = json.dumps(
        {
            "model": BUDGET["model"],
            "provider": BUDGET["provider"],
            "reasoning": BUDGET["reasoning"],
            "max_turns": BUDGET["max_turns"],
            "run_budget": BUDGET["run_budget"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    require(
        arm_run.budget_fingerprint(**BUDGET) == hashlib.sha256(payload).hexdigest(),
        arm_run.budget_fingerprint(**BUDGET),
    )
    require(
        set(arm_run.FINGERPRINT_KEYS) == set(json.loads(payload)),
        arm_run.FINGERPRINT_KEYS,
    )


def test_fingerprint_line_is_printed_for_both_arms(tmp_path, capsys):
    native, _native_recorder, _case = _run(tmp_path, "native", home_name="native-home")
    native_line = _printed(capsys, "BUDGET_FINGERPRINT: ")
    wrapper, _wrapper_recorder, _case = _run(
        tmp_path, "wrapper", home_name="wrapper-home"
    )
    wrapper_line = _printed(capsys, "BUDGET_FINGERPRINT: ")

    require(native_line == native["fingerprint"], (native_line, native))
    require(wrapper_line == wrapper["fingerprint"], (wrapper_line, wrapper))
    require(native_line == wrapper_line, (native_line, wrapper_line))


# --------------------------------------------------------------------------- #
# 5. The native launcher: pass-through options, safely quoted manifest
# --------------------------------------------------------------------------- #


def test_native_launcher_passes_options_through_and_quotes_the_manifest(tmp_path):
    manifest = tmp_path / "a dir" / "the manifest.json"
    shim = tmp_path / "a dir" / "pair_cli.py"

    posix, windows = arm_run.write_launcher(
        tmp_path / "home", shim=shim, manifest=manifest, interpreter="python3"
    )

    require(
        posix == tmp_path / "home" / arm_run.BIN_DIRNAME / arm_run.PAIR_LAUNCHER, posix
    )
    require(
        windows == tmp_path / "home" / arm_run.BIN_DIRNAME / arm_run.PAIR_LAUNCHER_CMD,
        windows,
    )
    # Decode the bytes: universal-newline reading would hide the batch file's
    # CRLF endings, which is exactly what the launcher's shell needs.
    sh = posix.read_bytes().decode("utf-8")
    cmd = windows.read_bytes().decode("utf-8")

    require(sh == arm_run.posix_launcher("python3", str(shim), str(manifest)), sh)
    require(cmd == arm_run.cmd_launcher("python3", str(shim), str(manifest)), cmd)
    require(b"\r\n" in windows.read_bytes(), "the batch file has no CRLF endings")
    # The manifest path is quoted for the launcher's own shell, spaces included.
    require(f"--manifest '{manifest}'" in sh, sh)
    require(f'--manifest "{manifest}"' in cmd, cmd)
    require(f"'{shim}'" in sh, sh)
    # Options and subcommands are passed through, never interpreted here.
    require(sh.rstrip().endswith('"$@"'), sh)
    require(cmd.rstrip().endswith("%*"), cmd)
    # The launcher never names a target selector itself.
    for token in ("--file", "--session", "--url"):
        require(token not in sh and token not in cmd, token)


def test_posix_launcher_quotes_a_manifest_path_with_a_single_quote():
    text = arm_run.posix_launcher("/py", "/shim.py", "/a b/o'brien.json")

    require(text.count("'\\''") == 1, text)
    require("--manifest '/a b/o'\\''brien.json'" in text, text)


def test_cmd_launcher_refuses_a_manifest_it_cannot_quote():
    for bad in ("/a/percent%20.json", '/a/quote".json', "/a/two\nlines.json"):
        with pytest.raises(arm_run.ArmRunRefusal, match="safely for a cmd launcher"):
            arm_run.cmd_launcher("/py", "/shim.py", bad)


def test_launcher_refuses_a_relative_path(tmp_path):
    """The launcher runs from the agent's directory, so relative paths refuse."""
    with pytest.raises(arm_run.ArmRunRefusal, match="shim path must be absolute"):
        arm_run.write_launcher(
            tmp_path / "home",
            shim="benchmark/pair_cli.py",
            manifest=tmp_path / "manifest.json",
            interpreter="python3",
        )
    with pytest.raises(arm_run.ArmRunRefusal, match="manifest path must be absolute"):
        arm_run.write_launcher(
            tmp_path / "home",
            shim=tmp_path / "pair_cli.py",
            manifest="manifest.json",
            interpreter="python3",
        )


def test_native_dry_run_writes_the_launcher_and_prepends_its_bin_dir(tmp_path, capsys):
    _run(tmp_path, "native")

    bin_dir = tmp_path / "home" / arm_run.BIN_DIRNAME
    require(
        (bin_dir / arm_run.PAIR_LAUNCHER).is_file(),
        sorted(p.name for p in bin_dir.iterdir()),
    )
    require(
        (bin_dir / arm_run.PAIR_LAUNCHER_CMD).is_file(),
        sorted(p.name for p in bin_dir.iterdir()),
    )

    env = arm_run.build_child_env(
        tmp_path / "home", "native", base_env={"PATH": "rest"}
    )
    require(env["PATH"].startswith(str(bin_dir)), env["PATH"])
    require(env["PATH"].endswith("rest"), env["PATH"])
    require(env["PATH"] == str(bin_dir) + os.pathsep + "rest", env["PATH"])


def test_wrapper_run_writes_no_launcher(tmp_path, capsys):
    _run(tmp_path, "wrapper")

    bin_dir = tmp_path / "home" / arm_run.BIN_DIRNAME
    require(not bin_dir.exists(), "the wrapper arm got a pair launcher")


# --------------------------------------------------------------------------- #
# 6. The dry run prepares everything and launches nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_dry_run_launches_nothing_and_writes_everything(tmp_path, capsys, arm):
    recorder = Recorder(exit_code=99)

    metadata, _recorder, case = _run(tmp_path, arm, recorder=recorder, dry_run=True)

    require(recorder.calls == [], "the dry run launched a child process")
    home = tmp_path / "home"
    require(
        (home / arm_run.CONFIG_FILENAME).is_file(),
        sorted(p.name for p in home.iterdir()),
    )
    require(
        (home / arm_run.PROMPT_FILENAME).is_file(),
        sorted(p.name for p in home.iterdir()),
    )
    meta_path = arm_run.meta_path_for(case["transcript"])
    require(meta_path.is_file(), meta_path)
    require(json.loads(meta_path.read_text(encoding="utf-8")) == metadata, metadata)
    require(metadata["exit_code"] is None, metadata)
    require(metadata["dry_run"] is True, metadata)
    require(metadata["started_at"] == FIXED_TIME, metadata)
    require(metadata["ended_at"] == FIXED_TIME, metadata)
    if arm == "native":
        require((home / arm_run.BIN_DIRNAME / arm_run.PAIR_LAUNCHER).is_file(), home)
    require(not case["transcript"].exists(), "the dry run wrote a transcript")


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_dry_run_prints_the_exact_command_line(tmp_path, capsys, arm):
    _run(tmp_path, arm, dry_run=True)

    line = _printed(capsys, "COMMAND: ")
    expected = arm_run.build_child_argv(
        command="hermes",
        prompt=tmp_path / "home" / arm_run.PROMPT_FILENAME,
        workdir=tmp_path / "work",
        model=BUDGET["model"],
        provider=BUDGET["provider"],
        reasoning=BUDGET["reasoning"],
        max_turns=BUDGET["max_turns"],
        run_budget=BUDGET["run_budget"],
        toolsets=arm_run.effective_toolsets(arm),
        source=f"arm-{arm}",
    )
    require(line == shlex.join([str(item) for item in expected]), line)


def test_the_launched_argv_is_the_printed_argv(tmp_path, capsys):
    recorder = Recorder(exit_code=3)

    metadata, _recorder, _case = _run(tmp_path, "native", recorder=recorder)

    printed = _printed(capsys, "COMMAND: ")
    require(len(recorder.calls) == 1, recorder.calls)
    argv, env, transcript, timeout = recorder.calls[0]
    require(shlex.join(argv) == printed, (argv, printed))
    require(transcript == _case["transcript"], transcript)
    require(timeout == BUDGET["run_budget"] + arm_run.RUN_BUDGET_GRACE_S, timeout)
    require(env["HERMES_HOME"] == str(tmp_path / "home"), env.get("HERMES_HOME"))
    require(metadata["exit_code"] == 3, metadata)
    require(metadata["dry_run"] is False, metadata)


# --------------------------------------------------------------------------- #
# The child argv and environment themselves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_child_argv_is_one_shot_isolated_and_names_the_arm(tmp_path, arm):
    argv = arm_run.build_child_argv(
        command="hermes",
        prompt=tmp_path / "prompt.md",
        workdir=tmp_path / "work",
        model="a-model",
        provider="a-provider",
        reasoning="medium",
        max_turns=12,
        run_budget=240.0,
        toolsets=arm_run.effective_toolsets(arm),
        source=f"arm-{arm}",
    )

    require(argv[0] == "hermes" and argv[1] == "chat", argv)
    require("--oneshot" in argv, argv)
    require(argv[argv.index("--query-file") + 1] == str(tmp_path / "prompt.md"), argv)
    require(argv[argv.index("--in") + 1] == str(tmp_path / "work"), argv)
    require(argv[argv.index("-m") + 1] == "a-model", argv)
    require(argv[argv.index("--provider") + 1] == "a-provider", argv)
    require(argv[argv.index("--reasoning") + 1] == "medium", argv)
    require(argv[argv.index("--max-turns") + 1] == "12", argv)
    require(argv[argv.index("--run-budget") + 1] == "240.0", argv)
    require(
        argv[argv.index("-t") + 1] == ",".join(arm_run.effective_toolsets(arm)), argv
    )
    require(argv[argv.index("--source") + 1] == f"arm-{arm}", argv)
    # Rules, AGENTS.md and memory injection are off; no session is resumed.
    require("--ignore-rules" in argv, argv)
    for forbidden in ("--resume", "-r", "--continue", "-c", "--safe-mode"):
        require(forbidden not in argv, (forbidden, argv))


def _toolsets_in_argv(argv):
    require("-t" in argv, argv)
    return argv[argv.index("-t") + 1].split(",")


def _toolsets_in_printed_line(line):
    """The ``-t`` value of a printed command line, as a token list."""
    marker = " -t "
    require(marker in line, line)
    return line.split(marker, 1)[1].split(" ", 1)[0].split(",")


@pytest.mark.parametrize("arm", ["native", "wrapper"])
def test_built_argv_grants_only_the_arms_own_toolsets(tmp_path, arm):
    argv = arm_run.build_child_argv(
        command="hermes",
        prompt=tmp_path / "prompt.md",
        workdir=tmp_path / "work",
        model="a-model",
        provider="a-provider",
        reasoning="medium",
        max_turns=12,
        run_budget=240.0,
        toolsets=arm_run.effective_toolsets(arm),
        source=f"arm-{arm}",
    )
    toolsets = _toolsets_in_argv(argv)

    if arm == "native":
        require(toolsets == ["terminal"], toolsets)
    else:
        require(toolsets == [arm_run.MCP_TOOLSET], toolsets)
    # The generic file toolset is granted to neither arm: the mission forbids
    # file-tool edits, so it would be a violation path neither interface needs.
    require("file" not in toolsets, toolsets)


def test_native_command_line_has_the_terminal_toolset_and_no_file_toolset(
    tmp_path, capsys
):
    _run(tmp_path, "native", dry_run=True)

    line = _printed(capsys, "COMMAND: ")
    toolsets = _toolsets_in_printed_line(line)

    require("terminal" in toolsets, (toolsets, line))
    require("file" not in toolsets, (toolsets, line))
    # ``--query-file`` names the prompt path; it is not the file toolset.
    require("--query-file" in line, line)
    require(arm_run.MCP_TOOLSET not in toolsets, (toolsets, line))


def test_wrapper_command_line_has_only_the_mcp_toolset(tmp_path, capsys):
    _run(tmp_path, "wrapper", dry_run=True)

    line = _printed(capsys, "COMMAND: ")
    toolsets = _toolsets_in_printed_line(line)

    require(toolsets == [arm_run.MCP_TOOLSET], (toolsets, line))
    require(arm_run.MCP_SERVER_NAME in toolsets[0], (toolsets, line))
    require("file" not in toolsets, (toolsets, line))
    require("terminal" not in toolsets, (toolsets, line))


def test_every_budget_flag_is_identical_across_the_two_arms(tmp_path):
    def budget_of(arm):
        argv = arm_run.build_child_argv(
            command="hermes",
            prompt=tmp_path / "prompt.md",
            workdir=tmp_path / "work",
            model="a-model",
            provider="a-provider",
            reasoning="medium",
            max_turns=7,
            run_budget=125.0,
            toolsets=arm_run.effective_toolsets(arm),
            source=f"arm-{arm}",
        )
        keys = (
            "-m",
            "--provider",
            "--reasoning",
            "--max-turns",
            "--run-budget",
        )
        return {key: argv[argv.index(key) + 1] for key in keys}

    require(budget_of("native") == budget_of("wrapper"), budget_of("native"))


def test_build_child_env_points_at_the_ephemeral_home(tmp_path):
    env = arm_run.build_child_env(
        tmp_path / "home", "wrapper", base_env={"PATH": "rest", "KEEP": "1"}
    )

    require(env["HERMES_HOME"] == str(tmp_path / "home"), env)
    require(env["KEEP"] == "1", env)
    require(env["PATH"] == "rest", env)


def test_build_child_env_refuses_an_unknown_arm(tmp_path):
    with pytest.raises(arm_run.ArmRunRefusal, match="arm must be one of"):
        arm_run.build_child_env(tmp_path / "home", "serial")


# --------------------------------------------------------------------------- #
# The run metadata
# --------------------------------------------------------------------------- #


def test_metadata_carries_the_documented_keys(tmp_path, capsys):
    metadata, _recorder, _case = _run(tmp_path, "wrapper")

    require(
        set(metadata)
        == {
            "arm",
            "fingerprint",
            "toolsets",
            "mission_sha256",
            "interface_sha256",
            "started_at",
            "ended_at",
            "exit_code",
            "dry_run",
        },
        sorted(metadata),
    )
    require(metadata["arm"] == "wrapper", metadata)
    require(metadata["ended_at"] == FIXED_TIME, metadata)
    require(metadata["exit_code"] == 0, metadata)


def test_metadata_is_written_beside_the_transcript(tmp_path, capsys):
    _metadata, _recorder, case = _run(tmp_path, "native")

    meta_path = arm_run.meta_path_for(case["transcript"])
    require(meta_path.parent == case["transcript"].parent, meta_path)
    require(meta_path.name == "arm.meta.json", meta_path.name)
    require(meta_path.read_bytes().endswith(b"\n"), "metadata has no trailing newline")


# --------------------------------------------------------------------------- #
# The default launch seam captures the transcript without a shell
# --------------------------------------------------------------------------- #


def test_default_launcher_captures_output_and_never_enables_a_shell(
    tmp_path, monkeypatch
):
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded.update(kwargs)
        kwargs["stdout"].write(b"transcript bytes")
        return subprocess.CompletedProcess(argv, 5, "", "")

    monkeypatch.setattr(arm_run.subprocess, "run", fake_run)
    transcript = tmp_path / "out" / "arm.txt"

    code = arm_run.run_child(["hermes", "chat"], {"PATH": "x"}, transcript, 7.0)

    require(code == 5, code)
    require(recorded["argv"] == ["hermes", "chat"], recorded["argv"])
    require(recorded["shell"] is False, recorded["shell"])
    require(recorded["check"] is False, recorded["check"])
    require(recorded["stderr"] == subprocess.STDOUT, recorded["stderr"])
    require(recorded["env"] == {"PATH": "x"}, recorded["env"])
    require(recorded["timeout"] == 7.0, recorded["timeout"])
    require(transcript.read_bytes() == b"transcript bytes", transcript.read_bytes())


def test_refusals_are_json_not_tracebacks(tmp_path, capsys):
    code = arm_run.main(_argv(tmp_path, "native", drop="--manifest"))

    require(code == 1, code)
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    require(error["error"] == "arm_run_refused", error)
    require("--manifest" in error["detail"], error)
    require(captured.out == "", captured.out)


def _without(arguments, option):
    """*arguments* with ``option`` and its value removed."""
    index = arguments.index(option)
    return arguments[:index] + arguments[index + 2 :]


def _refusal(tmp_path, capsys, arguments):
    """Run the CLI expecting a JSON refusal, and return its detail."""
    code = arm_run.main(arguments)

    require(code == 1, code)
    captured = capsys.readouterr()
    require(captured.out == "", captured.out)
    error = json.loads(captured.err)
    require(error["error"] == "arm_run_refused", error)
    return error["detail"]


def test_native_without_a_shim_is_refused(tmp_path, capsys):
    detail = _refusal(tmp_path, capsys, _without(_argv(tmp_path, "native"), "--shim"))

    require("--shim" in detail, detail)


def test_wrapper_with_a_shim_is_refused(tmp_path, capsys):
    arguments = [*_argv(tmp_path, "wrapper"), "--shim", "pair_cli.py"]

    detail = _refusal(tmp_path, capsys, arguments)

    require("only meaningful for the native arm" in detail, detail)


def test_native_with_an_mcp_command_is_refused(tmp_path, capsys):
    arguments = [*_argv(tmp_path, "native"), "--mcp-command", "somewhere"]

    detail = _refusal(tmp_path, capsys, arguments)

    require("only meaningful for the wrapper arm" in detail, detail)


def test_wrapper_without_an_mcp_command_is_refused(tmp_path, capsys):
    detail = _refusal(
        tmp_path, capsys, _without(_argv(tmp_path, "wrapper"), "--mcp-command")
    )

    require("--mcp-command" in detail, detail)


# --------------------------------------------------------------------------- #
# Privacy: no machine-specific absolute path in either file
# --------------------------------------------------------------------------- #

_MACHINE_PATTERNS = (
    # A drive-letter path (a letter, colon, separator) — the look-behind keeps a
    # URL scheme such as ``http://`` from matching.
    re.compile(r"(?<![\w/])[A-Za-z]:[\\/]\S"),
    re.compile("/" + "home/[A-Za-z]"),  # Unix home
    re.compile("/" + "Users/[A-Za-z]"),  # macOS home
    re.compile("/" + "root/"),
    re.compile(r"\btail" + r"scale\b"),
    re.compile(r"\b(?:100|10|172|192)\.\d"),  # non-loopback private IPv4 literals
    re.compile(r"\b" + "mhd" + "01" + r"\b"),  # a concrete machine user name
)


@pytest.mark.parametrize(
    "path", [MODULE_PATH, Path(__file__).resolve()], ids=lambda p: p.name
)
def test_no_machine_specific_path_in_either_file(path):
    text = path.read_text(encoding="utf-8")

    for pattern in _MACHINE_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise AssertionError(
                f"{path.name} matches {pattern.pattern}: {match.group(0)!r}"
            )


def test_the_privacy_patterns_would_catch_a_real_machine_path():
    """The scan is only meaningful if it would catch a real machine path.

    Each probe is assembled from fragments so this file never carries the
    contiguous literal the scan forbids.
    """
    probes = (
        "user = " + repr("C" + ":" + "/" + "Users/someone/work"),
        "root = " + repr("/" + "home/someone/Repos/x"),
        "host = " + repr("/" + "root/data"),
        "endpoint = " + repr("http://" + "192" + ".168.1.9:8000"),
        "mesh = " + repr("tail" + "scale" + "-only tile"),
        "owner = " + repr("mhd" + "01"),
    )

    for probe in probes:
        if not any(pattern.search(probe) for pattern in _MACHINE_PATTERNS):
            raise AssertionError(f"no pattern caught {probe!r}")
    # A loopback URL and a bare relative file name must stay clean.
    for clean in ("url = 'http://127.0.0.1:2718'", "cfg = 'config.yaml'"):
        caught = [p.pattern for p in _MACHINE_PATTERNS if p.search(clean)]
        if caught:
            raise AssertionError(f"{clean!r} was flagged by {caught}")
