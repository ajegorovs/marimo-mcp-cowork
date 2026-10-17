"""Run ONE arm's agent for the restricted native-vs-wrapper mission comparison.

This is the piece that actually *runs* an arm. The other benchmark programs
stage the notebook, the server and the manifest (``mission_setup``), provide the
native arm's restricted interface (``pair_cli``) and grade the result
(``mission_grade``); this one prepares and launches the agent that does the work.

Why a separate OS process
-------------------------
Each arm runs as its own ``hermes chat`` process. A delegated child inside one
agent cannot differ in toolset or budget from its parent, so two arms cannot be
represented as two delegations — they must be two processes. Each arm therefore
gets an **ephemeral configuration home** (``--home``) exported as
``HERMES_HOME`` for the child, so the active profile's config, memory, skills
and sessions are never touched or read.

What the arms differ in
-----------------------
===========  =========================================  ==========================
             wrapper arm                                native arm
===========  =========================================  ==========================
marimo       ``0.24`` (this repo's pinned range)         ``0.25.1``
interface    this repo's MCP server, ``marimo-inspect``  a ``pair`` launcher
guidance     the server's packaged MCP resources         the CLI's own ``--help``
toolsets     ``mcp-marimo-inspect`` only                  ``terminal`` only
===========  =========================================  ==========================

Everything else is identical by construction: the same mission file, the same
model, provider, reasoning level, turn limit and wall-clock budget, and one
fresh session with no resumed context.

Each arm is granted exactly the tools its own interface needs and nothing more:
the native arm gets a shell (to invoke the ``pair`` shim), the wrapper arm gets
the MCP server's tools, and **neither** gets the generic file toolset — the
mission forbids editing the notebook with file tooling, so granting it would add
a violation path that neither interface requires. The two lists are disjoint.

The budget fingerprint covers the **arm-independent** settings only: model,
provider, reasoning level, turn limit and wall-clock budget. Both arms must
print the *same* value, and a difference between them is a harness fault rather
than a finding — that equality is the check that the two runs were comparable.
The arm's own toolset list is deliberately **not** in the fingerprint: it is part
of the interface under test, so it is recorded separately in the run metadata
(``toolsets``) and printed on the command line. Hashing it into the fingerprint
would make the two arms' fingerprints differ by construction and destroy the
comparability check.

The configuration home
----------------------
``--home`` receives a ``config.yaml`` that declares the wrapper arm's single MCP
server (``marimo-inspect``) and *nothing else*; for the native arm it declares
no MCP servers at all, because the native arm reaches the notebook only through
the ``pair`` launcher. The launcher is two files — a POSIX ``sh`` script and a
``.cmd`` batch file — inside ``<home>/bin``, and that directory is prepended to
the *native* child's ``PATH`` so the bare command ``pair`` resolves. Both
launchers pass their arguments through to the shim untouched, with the manifest
path quoted for their own shell; the shim keeps the manifest the only place the
notebook and session identifiers come from.

Prompt assembly
---------------
The prompt is the mission file verbatim, one blank line, then the interface note
verbatim (:func:`assemble_prompt` canonicalizes only that boundary, so a
trailing newline on either file cannot produce two blank lines or none). The
SHA-256 of each source file is recorded in the run metadata, so accidental
per-arm drift in the shared mission or in one note is detectable after the fact.

Scope honesty
-------------
This module *prepares and launches*; it does not enforce the budget it passes.
``--max-turns`` and ``--run-budget`` go to ``hermes chat`` as its own ceilings,
and the same settings hash into the fingerprint so two arms can be compared, but
a run that ignores them is a fault this module can only record, not prevent. A
non-empty ``--home`` is **not** refused: ``--dry-run`` materialises the same home
a real run uses, so a refusal would forbid the dry run itself. Files this module
writes are overwritten deterministically; state a previous ``hermes`` child left
in the home is the caller's to discard. Use one fresh home per run.

Development contract: ``--dry-run`` is the tested path. It writes the
configuration, prompt, launcher and metadata, prints the exact command line it
would execute, and launches nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ARMS",
    "BIN_DIRNAME",
    "CONFIG_FILENAME",
    "FINGERPRINT_KEYS",
    "MCP_SERVER_NAME",
    "MCP_TOOLSET",
    "NATIVE_TOOLSETS",
    "PAIR_LAUNCHER",
    "PAIR_LAUNCHER_CMD",
    "PROMPT_FILENAME",
    "REASONING_LEVELS",
    "RUN_BUDGET_GRACE_S",
    "TRANSPORT_ARGS",
    "WRAPPER_TOOLSETS",
    "ArmRunRefusal",
    "assemble_prompt",
    "budget_fingerprint",
    "build_child_argv",
    "build_child_env",
    "cmd_launcher",
    "effective_toolsets",
    "main",
    "meta_path_for",
    "perform_run",
    "posix_launcher",
    "render_config",
    "resolve_hermes_command",
    "run_child",
    "sha256_bytes",
    "utc_now",
    "write_launcher",
]

ARMS = ("native", "wrapper")

#: The wrapper arm's one MCP server, and the toolset name its tools land in
#: (Hermes registers MCP tools under ``mcp-<server>``).
MCP_SERVER_NAME = "marimo-inspect"
MCP_TOOLSET = f"mcp-{MCP_SERVER_NAME}"

#: Each arm's toolset list, as narrow as its own interface allows. The native
#: arm's only interface is the ``pair`` command, so it needs a shell and nothing
#: else. The wrapper arm's only interface is the MCP server, so it needs that
#: server's tools and nothing else. Neither arm gets the generic file toolset:
#: the mission forbids editing the notebook with file tools, so granting it
#: would add a violation path that neither interface requires.
NATIVE_TOOLSETS = ("terminal",)
WRAPPER_TOOLSETS = (MCP_TOOLSET,)

#: Transport the generated MCP entry runs the server with, and its call timeout.
TRANSPORT_ARGS = ("--transport", "stdio")
MCP_TIMEOUT_S = 60

#: Names the run writes inside the ephemeral home.
CONFIG_FILENAME = "config.yaml"
PROMPT_FILENAME = "prompt.md"
BIN_DIRNAME = "bin"
PAIR_LAUNCHER = "pair"
PAIR_LAUNCHER_CMD = "pair.cmd"

#: The reasoning levels ``hermes chat --reasoning`` accepts.
REASONING_LEVELS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

#: The settings the budget fingerprint covers — exactly the arm-independent ones.
#: The arm's toolset list is *not* here: it is part of the interface under test
#: and is recorded in the run metadata instead, so the two arms can still print
#: one identical fingerprint (see :func:`budget_fingerprint`).
FINGERPRINT_KEYS = (
    "model",
    "provider",
    "reasoning",
    "max_turns",
    "run_budget",
)

#: Grace on top of ``--run-budget`` before the launched process is killed. The
#: child enforces its own budget; this only stops a wedged process from hanging
#: the harness forever.
RUN_BUDGET_GRACE_S = 60.0

#: Default command name for the agent under test; ``$HERMES_BIN`` overrides it.
HERMES_COMMAND = "hermes"

#: Session source tag prefix, so a run is filterable by the arm it belongs to.
SOURCE_PREFIX = "arm-"

#: A launcher runs ``(argv, env, transcript, timeout)`` and returns the exit code.
ChildRun = Callable[[Sequence[str], Mapping[str, str], Path, float], int]


class ArmRunRefusal(RuntimeError):
    """The arm's run could not be prepared fail-closed; the caller must not use it."""


def require(condition: bool, message: str) -> None:
    """Always-on check; a bare ``assert`` is stripped under ``python -O``."""
    if not condition:
        raise ArmRunRefusal(message)


def utc_now() -> str:
    """An ISO-8601 UTC timestamp for the run metadata."""
    return datetime.now(UTC).isoformat()


def sha256_bytes(data: bytes) -> str:
    """The SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def effective_toolsets(arm: str) -> tuple[str, ...]:
    """The explicit toolset list for *arm*.

    The native arm gets exactly :data:`NATIVE_TOOLSETS` (a shell, to invoke the
    ``pair`` shim); the wrapper arm gets exactly :data:`WRAPPER_TOOLSETS` (the
    MCP server's tools, its one interface). Neither list contains the generic
    file toolset, and the two lists are disjoint — the arm's interface is the
    variable under test, so each arm is granted only its own.
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    if arm == "wrapper":
        return WRAPPER_TOOLSETS
    return NATIVE_TOOLSETS


def budget_fingerprint(
    *,
    model: str,
    provider: str,
    reasoning: str,
    max_turns: int,
    run_budget: float,
) -> str:
    """A stable hash of the run's **arm-independent** budget settings.

    The hash covers exactly :data:`FINGERPRINT_KEYS`: model, provider, reasoning
    level, turn limit and wall-clock budget. All five are arm-independent, so two
    comparable runs print the **same** value regardless of arm — comparing the
    two arms' fingerprints is the check that neither was given a different
    budget. Two runs of one arm print the same value, which makes accidental
    drift detectable.

    The arm's toolset list is intentionally excluded: it is part of the interface
    under test, so it is recorded in the run metadata instead. Including it would
    make the two arms differ by construction and destroy the check.
    """
    payload = {
        "model": model,
        "provider": provider,
        "reasoning": reasoning,
        "max_turns": int(max_turns),
        "run_budget": float(run_budget),
    }
    require(
        set(payload) == set(FINGERPRINT_KEYS),
        "the fingerprint must cover exactly the documented settings",
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


# --------------------------------------------------------------------------- #
# The ephemeral configuration home
# --------------------------------------------------------------------------- #


def _yaml_single_quoted(value: str) -> str:
    """*value* as a single-quoted YAML scalar (backslashes stay literal)."""
    require(
        isinstance(value, str) and value.strip() != "",
        "a config value must be a non-empty string",
    )
    require(not any(ch in value for ch in "\r\n"), "a config value cannot span lines")
    return "'" + value.replace("'", "''") + "'"


def render_config(arm: str, *, mcp_command: str | None = None) -> str:
    """The ``config.yaml`` text for *arm*'s configuration home.

    The wrapper arm declares exactly one MCP server — ``marimo-inspect``, run
    with *mcp_command* over stdio — and no others. The native arm declares no MCP
    servers at all. Nothing else is written into the file, so the two arms'
    configurations differ in exactly the way the comparison intends.

    Raises:
        ArmRunRefusal: On an unknown arm, a missing or blank ``mcp_command`` for
            the wrapper arm, or an ``mcp_command`` supplied for the native arm
            (a stray MCP server there is a contradiction, not an option).
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    header = "# Ephemeral home for one arm's run (generated; do not edit)."
    if arm == "wrapper":
        require(
            isinstance(mcp_command, str) and mcp_command.strip() != "",
            "the wrapper arm needs --mcp-command (the marimo-inspect executable)",
        )
        lines = [
            header,
            "mcp_servers:",
            f"  {MCP_SERVER_NAME}:",
            f"    command: {_yaml_single_quoted(mcp_command)}",
            "    args:",
            *(f"      - {argument}" for argument in TRANSPORT_ARGS),
            f"    timeout: {MCP_TIMEOUT_S}",
        ]
    else:
        require(
            mcp_command is None,
            "--mcp-command is only meaningful for the wrapper arm",
        )
        lines = [header, "mcp_servers: {}"]
    return "\n".join([*lines, ""])


def write_config(
    home: str | os.PathLike[str], arm: str, *, mcp_command: str | None = None
) -> Path:
    """Write ``config.yaml`` for *arm* into *home* and return its path."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = home / CONFIG_FILENAME
    path.write_text(
        render_config(arm, mcp_command=mcp_command),
        encoding="utf-8",
        newline="\n",
    )
    return path


# --------------------------------------------------------------------------- #
# The native arm's `pair` launcher
# --------------------------------------------------------------------------- #


def sh_quote(value: str) -> str:
    """*value* as a POSIX ``sh`` single-quoted word."""
    return "'" + value.replace("'", "'\\''") + "'"


def cmd_quote(value: str) -> str:
    """*value* as a ``cmd.exe`` double-quoted word.

    Raises:
        ArmRunRefusal: If *value* cannot be quoted safely for ``cmd`` — an
            embedded double quote, a percent sign (expanded even inside quotes)
            or a line break.
    """
    require(
        not any(character in value for character in '"%\r\n'),
        f"cannot quote {value!r} safely for a cmd launcher",
    )
    return f'"{value}"'


def posix_launcher(interpreter: str, shim: str, manifest: str) -> str:
    """The POSIX ``sh`` launcher text for the bare command ``pair``."""
    return (
        "#!/bin/sh\n"
        "# Generated per-run launcher for the native arm's restricted interface.\n"
        "# The manifest is the only place the target identifiers come from; every\n"
        '# argument after it is passed through untouched ("$@").\n'
        f"exec {sh_quote(interpreter)} {sh_quote(shim)}"
        f' --manifest {sh_quote(manifest)} "$@"\n'
    )


def cmd_launcher(interpreter: str, shim: str, manifest: str) -> str:
    """The ``cmd.exe`` launcher text for the bare command ``pair``."""
    return (
        "@echo off\r\n"
        "rem Generated per-run launcher for the native arm's restricted interface.\r\n"
        "rem The manifest is the only place the target identifiers come from; every\r\n"
        "rem argument after it is passed through untouched (%*).\r\n"
        f"{cmd_quote(interpreter)} {cmd_quote(shim)}"
        f" --manifest {cmd_quote(manifest)} %*\r\n"
    )


def _make_executable(path: Path) -> None:
    """Best-effort exec bit (a no-op on Windows, where the suffix carries it)."""
    try:
        path.chmod(path.stat().st_mode | 0o111)
    except OSError:  # pragma: no cover - filesystem without POSIX modes
        pass


def write_launcher(
    home: str | os.PathLike[str],
    *,
    shim: str | os.PathLike[str],
    manifest: str | os.PathLike[str],
    interpreter: str | None = None,
) -> tuple[Path, Path]:
    """Write the ``pair`` launchers under ``<home>/bin`` and return both paths.

    Two files are written — a POSIX ``sh`` script and a ``cmd.exe`` batch file —
    so the bare command resolves in either shell. Both invoke the shim with
    ``--manifest`` and pass their own arguments through, which is why the
    launcher can never add a target selector.

    Raises:
        ArmRunRefusal: If no interpreter is available, if the shim or manifest
            path is relative (the launcher runs from the agent's own working
            directory, so a relative path would silently point elsewhere), or
            if (for the ``cmd`` launcher) the manifest path cannot be quoted
            safely.
    """
    bin_dir = Path(home) / BIN_DIRNAME
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = interpreter if interpreter is not None else sys.executable
    require(
        isinstance(python, str) and python.strip() != "",
        "no interpreter to run the pair shim with",
    )
    shim_path, manifest_path = Path(shim), Path(manifest)
    # The launcher is invoked by the agent, whose working directory is not the
    # harness's: both paths are embedded verbatim and must already be absolute.
    require(shim_path.is_absolute(), f"the pair shim path must be absolute: {shim}")
    require(
        manifest_path.is_absolute(), f"the manifest path must be absolute: {manifest}"
    )
    shim_text, manifest_text = str(shim_path), str(manifest_path)
    posix = bin_dir / PAIR_LAUNCHER
    posix.write_text(
        posix_launcher(python, shim_text, manifest_text),
        encoding="utf-8",
        newline="\n",
    )
    _make_executable(posix)
    windows = bin_dir / PAIR_LAUNCHER_CMD
    windows.write_text(
        cmd_launcher(python, shim_text, manifest_text),
        encoding="utf-8",
        newline="",
    )
    _make_executable(windows)
    return posix, windows


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def assemble_prompt(mission: bytes, interface: bytes) -> bytes:
    """The mission verbatim, one blank line, the interface note verbatim.

    Only the boundary between the two files is canonicalized: trailing line
    breaks on the mission and leading ones on the note are removed and the two
    are joined by exactly one blank line. Each file's own bytes are otherwise
    untouched, so the arm cannot see an accidental double blank line or a
    run-together join.
    """
    require(bool(mission.strip()), "the mission file is empty")
    require(bool(interface.strip()), "the interface note is empty")
    return mission.rstrip(b"\r\n") + b"\n\n" + interface.lstrip(b"\r\n")


# --------------------------------------------------------------------------- #
# The child command and environment
# --------------------------------------------------------------------------- #


def resolve_hermes_command() -> str:
    """The ``hermes`` executable: ``$HERMES_BIN``, else ``PATH``, else the name."""
    override = os.environ.get("HERMES_BIN")
    if isinstance(override, str) and override.strip() != "":
        return override
    return shutil.which(HERMES_COMMAND) or HERMES_COMMAND


def build_child_argv(
    *,
    command: str,
    prompt: str | os.PathLike[str],
    workdir: str | os.PathLike[str],
    model: str,
    provider: str,
    reasoning: str,
    max_turns: int,
    run_budget: float,
    toolsets: Sequence[str],
    source: str,
) -> list[str]:
    """The exact ``hermes chat`` argv for one arm.

    One-shot mode with ``--query-file`` (nothing on the command line is
    shell-interpreted), ``--in`` for the starting directory, the same model,
    provider, reasoning level, turn limit and run budget for both arms, the
    explicit narrow toolset list, ``--ignore-rules`` to skip AGENTS.md/SOUL.md/
    memory/preloaded-skill injection, and ``--source`` naming the arm. No
    ``--resume``/``--continue`` is passed, so the child starts one fresh session
    with no resumed context.
    """
    return [
        command,
        "chat",
        "--oneshot",
        "--query-file",
        str(prompt),
        "--in",
        str(workdir),
        "-m",
        model,
        "--provider",
        provider,
        "--reasoning",
        reasoning,
        "--max-turns",
        str(int(max_turns)),
        "--run-budget",
        str(run_budget),
        "-t",
        ",".join(toolsets),
        "--ignore-rules",
        "--source",
        source,
        "--quiet",
        "--format",
        "text",
    ]


def build_child_env(
    home: str | os.PathLike[str],
    arm: str,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The child's environment: an ephemeral ``HERMES_HOME``, plus ``bin`` for native.

    ``HERMES_HOME`` points the child at the run's own configuration home, so the
    active profile is neither read nor modified. The native arm also gets
    ``<home>/bin`` prepended to ``PATH``, which is what makes the bare command
    ``pair`` resolve to its launcher.
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    env = dict(os.environ if base_env is None else base_env)
    home = Path(home)
    env["HERMES_HOME"] = str(home)
    if arm == "native":
        bin_dir = str(home / BIN_DIRNAME)
        existing = env.get("PATH", "")
        env["PATH"] = f"{bin_dir}{os.pathsep}{existing}" if existing else bin_dir
    return env


def run_child(
    argv: Sequence[str],
    env: Mapping[str, str],
    transcript: str | os.PathLike[str],
    timeout: float,
) -> int:
    """Run the child with its stdout and stderr captured to *transcript*.

    No shell is involved anywhere: the argv is passed through as a list. The
    timeout is the run budget plus :data:`RUN_BUDGET_GRACE_S`.
    """
    transcript = Path(transcript)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with open(transcript, "wb") as handle:
        completed = subprocess.run(
            [str(argument) for argument in argv],
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            shell=False,
        )
    return int(completed.returncode)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def meta_path_for(transcript: str | os.PathLike[str]) -> Path:
    """The run metadata path written beside *transcript*."""
    transcript = Path(transcript)
    return transcript.parent / f"{transcript.stem}.meta.json"


def _read_source(path: str | os.PathLike[str], label: str) -> bytes:
    """Read one prompt source file, refusing rather than raising ``OSError``."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ArmRunRefusal(f"cannot read the {label} file {path}: {exc}") from exc


def _validate(
    *,
    arm: str,
    model: str,
    provider: str,
    reasoning: str,
    max_turns: object,
    run_budget: object,
    manifest: str | os.PathLike[str] | None,
    shim: str | os.PathLike[str] | None,
    mcp_command: str | None,
) -> None:
    """Refuse an incomplete or self-contradictory run before any file is written."""
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    require(isinstance(model, str) and model.strip(), "model must be supplied")
    require(isinstance(provider, str) and provider.strip(), "provider must be supplied")
    require(
        reasoning in REASONING_LEVELS,
        f"reasoning must be one of {REASONING_LEVELS}, got {reasoning!r}",
    )
    require(
        isinstance(max_turns, int)
        and not isinstance(max_turns, bool)
        and int(max_turns) > 0,
        f"max_turns must be a positive integer, got {max_turns!r}",
    )
    require(
        isinstance(run_budget, (int, float))
        and not isinstance(run_budget, bool)
        and float(run_budget) > 0,
        f"run_budget must be a positive number of seconds, got {run_budget!r}",
    )
    if arm == "native":
        require(
            manifest is not None and str(manifest).strip() != "",
            "the native arm needs --manifest (written by mission_setup)",
        )
        require(
            shim is not None and str(shim).strip() != "",
            "the native arm needs --shim (benchmark/pair_cli.py)",
        )
        require(
            mcp_command is None,
            "--mcp-command is only meaningful for the wrapper arm",
        )
        # Both native paths are embedded in the launcher, which the *agent*
        # invokes from its own working directory, so a relative path would
        # resolve somewhere else. Checked here -- before anything is printed or
        # written -- so a refused run never reaches the fingerprint line.
        require(
            Path(str(shim)).is_absolute(),
            f"the pair shim path must be absolute: {shim}",
        )
        require(
            Path(str(manifest)).is_absolute(),
            f"the manifest path must be absolute: {manifest}",
        )
    else:
        require(
            isinstance(mcp_command, str) and mcp_command.strip() != "",
            "the wrapper arm needs --mcp-command (the marimo-inspect executable)",
        )
        require(
            shim is None,
            "--shim is only meaningful for the native arm",
        )


def _write_metadata(path: Path, metadata: dict) -> None:
    """Write *metadata* as UTF-8 JSON with LF newlines, deterministically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def perform_run(
    *,
    arm: str,
    home: str | os.PathLike[str],
    workdir: str | os.PathLike[str],
    mission: str | os.PathLike[str],
    interface: str | os.PathLike[str],
    transcript: str | os.PathLike[str],
    model: str,
    provider: str,
    reasoning: str,
    max_turns: int,
    run_budget: float,
    manifest: str | os.PathLike[str] | None = None,
    shim: str | os.PathLike[str] | None = None,
    mcp_command: str | None = None,
    dry_run: bool = False,
    command: str | None = None,
    launch: ChildRun = run_child,
    clock: Callable[[], str] | None = None,
) -> dict:
    """Prepare one arm's run and — unless *dry_run* — launch its agent.

    Steps: validate that every budget argument was supplied; compute and print
    the budget fingerprint; materialize the configuration home (``config.yaml``,
    and for the native arm the ``pair`` launcher); assemble the prompt from the
    mission plus the interface note and record both digests; build the child
    argv and environment; then either print the command line and write the
    metadata without launching (``dry_run=True``), or run the child, capture its
    transcript and write the metadata beside it.

    Args:
        arm: One of :data:`ARMS`.
        home: The ephemeral configuration home for this run (exported as the
            child's ``HERMES_HOME``).
        workdir: The directory the agent starts in.
        mission: The shared mission file, identical for both arms.
        interface: The arm's interface note.
        transcript: Where the child's combined stdout/stderr is captured; the
            metadata is written beside it.
        model: Model identifier, identical for both arms.
        provider: Inference provider, identical for both arms.
        reasoning: Reasoning level, one of :data:`REASONING_LEVELS`.
        max_turns: Tool-calling iteration limit, identical for both arms.
        run_budget: Wall-clock budget in seconds, identical for both arms.
        manifest: The arm manifest written by ``mission_setup``; required for
            the native arm (the launcher hands it to the shim).
        shim: Path to ``benchmark/pair_cli.py``; required for the native arm.
        mcp_command: The ``marimo-inspect`` executable; required for the wrapper
            arm.
        dry_run: Prepare everything, print the command line, launch nothing.
        command: Override the ``hermes`` executable (default:
            :func:`resolve_hermes_command`).
        launch: The launch seam. Defaults to :func:`run_child`; tests inject a
            recorder so nothing is executed.
        clock: Optional ``() -> str`` timestamp source for deterministic tests.

    Returns:
        The run metadata that was written.

    Raises:
        ArmRunRefusal: If a budget argument, the arm, or an arm's own required
            argument is missing or contradictory, or a prompt source is
            unreadable.
    """
    _validate(
        arm=arm,
        model=model,
        provider=provider,
        reasoning=reasoning,
        max_turns=max_turns,
        run_budget=run_budget,
        manifest=manifest,
        shim=shim,
        mcp_command=mcp_command,
    )
    now = clock or utc_now
    toolsets = effective_toolsets(arm)
    fingerprint = budget_fingerprint(
        model=model,
        provider=provider,
        reasoning=reasoning,
        max_turns=max_turns,
        run_budget=run_budget,
    )
    print(f"BUDGET_FINGERPRINT: {fingerprint}")

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    write_config(home, arm, mcp_command=mcp_command)
    if arm == "native":
        write_launcher(home, shim=str(shim), manifest=str(manifest))

    mission_bytes = _read_source(mission, "mission")
    interface_bytes = _read_source(interface, "interface note")
    prompt_path = home / PROMPT_FILENAME
    prompt_path.write_bytes(assemble_prompt(mission_bytes, interface_bytes))

    argv = build_child_argv(
        command=command or resolve_hermes_command(),
        prompt=prompt_path,
        workdir=workdir,
        model=model,
        provider=provider,
        reasoning=reasoning,
        max_turns=max_turns,
        run_budget=run_budget,
        toolsets=toolsets,
        source=f"{SOURCE_PREFIX}{arm}",
    )
    env = build_child_env(home, arm)
    transcript = Path(transcript)

    metadata = {
        "arm": arm,
        "fingerprint": fingerprint,
        # Recorded here rather than hashed into the fingerprint: the toolset list
        # is part of the interface under test, so a per-arm difference in it is
        # expected and must not make the two arms look incomparable.
        "toolsets": list(toolsets),
        "mission_sha256": sha256_bytes(mission_bytes),
        "interface_sha256": sha256_bytes(interface_bytes),
        "started_at": now(),
        "ended_at": None,
        "exit_code": None,
        "dry_run": bool(dry_run),
    }
    print("COMMAND: " + shlex.join([str(argument) for argument in argv]))

    if dry_run:
        metadata["ended_at"] = now()
        _write_metadata(meta_path_for(transcript), metadata)
        return metadata

    exit_code = launch(argv, env, transcript, float(run_budget) + RUN_BUDGET_GRACE_S)
    metadata["exit_code"] = int(exit_code)
    metadata["ended_at"] = now()
    _write_metadata(meta_path_for(transcript), metadata)
    return metadata


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and run ONE arm's agent. Every budget argument is required"
            " with no default, so two arms cannot silently diverge."
        )
    )
    parser.add_argument("--arm", required=True, choices=list(ARMS))
    parser.add_argument(
        "--home",
        required=True,
        help="ephemeral configuration home for this run (the child's HERMES_HOME)",
    )
    parser.add_argument(
        "--workdir", required=True, help="the agent's starting directory"
    )
    parser.add_argument("--mission", required=True, help="shared mission file")
    parser.add_argument("--interface", required=True, help="the arm's interface note")
    parser.add_argument("--transcript", required=True, help="captured stdout/stderr")
    parser.add_argument(
        "--manifest",
        default=None,
        help="arm manifest written by mission_setup (required for the native arm)",
    )
    parser.add_argument(
        "--shim",
        default=None,
        help="path to benchmark/pair_cli.py (required for the native arm)",
    )
    parser.add_argument(
        "--mcp-command",
        default=None,
        dest="mcp_command",
        help="the marimo-inspect executable (required for the wrapper arm)",
    )
    parser.add_argument("--model", required=True, help="model identifier")
    parser.add_argument("--provider", required=True, help="inference provider")
    parser.add_argument(
        "--reasoning",
        required=True,
        choices=list(REASONING_LEVELS),
        help="reasoning level",
    )
    parser.add_argument(
        "--max-turns", required=True, type=int, dest="max_turns", help="turn limit"
    )
    parser.add_argument(
        "--run-budget",
        required=True,
        type=float,
        dest="run_budget",
        help="wall-clock budget in seconds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="write everything, print the command line, launch nothing",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: prepare and run one arm, or refuse with JSON on stderr."""
    args = _parse_args(argv)
    try:
        metadata = perform_run(
            arm=args.arm,
            home=args.home,
            workdir=args.workdir,
            mission=args.mission,
            interface=args.interface,
            transcript=args.transcript,
            model=args.model,
            provider=args.provider,
            reasoning=args.reasoning,
            max_turns=args.max_turns,
            run_budget=args.run_budget,
            manifest=args.manifest,
            shim=args.shim,
            mcp_command=args.mcp_command,
            dry_run=args.dry_run,
        )
    except ArmRunRefusal as exc:
        print(
            json.dumps({"error": "arm_run_refused", "detail": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
