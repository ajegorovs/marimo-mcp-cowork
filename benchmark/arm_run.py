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
``-t``       ``marimo-inspect`` (the server name)        ``terminal``
built-ins    ``[]`` (the server is the whole interface)  ``[terminal]``
===========  =========================================  ==========================

Everything else is identical by construction: the same mission file, the same
model, provider, reasoning level, turn limit and wall-clock budget, and one
fresh session with no resumed context.

What ``-t/--toolsets`` actually means (measured, not assumed)
------------------------------------------------------------
``-t`` is **not** a toolset-name selector for the built-in surface. It is the
**process-wide MCP spawn allowlist**: ``hermes_cli/mcp_startup.py``
``set_mcp_server_filter()`` splits the value on commas and stores it, and every
discovery path keeps only the servers whose ``mcp_servers`` **key** is in that
list (``[n for n in pending if n in _mcp_server_filter]``). Built-in toolset
names in the list are inert there — they simply never match a server key.

So the wrapper arm's value is the **bare configured server name**
(``marimo-inspect``), never ``mcp-<server>``: ``hermes_cli/tools_config.py``
``CONFIGURABLE_TOOLSETS`` has no ``mcp`` entry and no ``mcp-<server>`` form, so
``mcp-marimo-inspect`` matches no server and narrows nothing. An unknown
``-t`` list does **not** narrow the built-in tools either — the native arm's
``terminal`` survives a rewrite of the wrapper arm's list. Narrowing the
built-in surface therefore has to be written into the arm's own config as well
(below).

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

Unattended mode
---------------
Both arms run ``hermes chat --yolo``. Terminal commands in a non-interactive
child cannot answer an approval prompt, so without the CLI's unattended switch
the native arm (which *must* go through a shell to run ``pair``) can be stopped
dead by a prompt no one can answer. ``--yolo`` is applied to **both** arms by
construction — it is one entry in :func:`build_child_argv` for either arm — so it
cannot favour one interface over the other; it removes a way for the run to
stall, not a restriction on either arm.

The configuration home
----------------------
``--home`` receives a ``config.yaml`` that declares the wrapper arm's single MCP
server (``marimo-inspect``) and *nothing else*; for the native arm it declares
no MCP servers at all, because the native arm reaches the notebook only through
the ``pair`` launcher. The same file pins the **built-in** toolset list under
``platform_toolsets.<platform>``, because ``-t`` is only the MCP spawn allowlist
(above): the native arm gets exactly ``[terminal]``, and the wrapper arm gets an
**empty** list, so its sole interface is the declared MCP server. The platform
key is ``cli`` — the child is ``hermes chat``, which resolves its toolsets for
``platform="cli"`` (``hermes_cli/oneshot.py``). An explicitly saved list is
authoritative in ``hermes_cli/tools_config.py`` ``_get_platform_tools`` (via
``parse_platform_toolsets_value``), and an explicit *empty* list is a fail-closed
zero-tool selection rather than "no restriction"
(``_platform_toolsets_explicitly_saved``).

The launcher is two files — a POSIX ``sh`` script and a ``.cmd`` batch file —
inside ``<home>/bin``, and that directory is prepended to the *native* child's
``PATH`` so the bare command ``pair`` resolves. On Windows/git-bash that prepend
is not sufficient on its own: Hermes runs terminal commands through a **login**
bash, and git-bash's ``/etc/profile`` rebuilds ``PATH`` from ``$ORIGINAL_PATH``
when it is set, discarding anything added only to ``PATH``. The entry is
therefore added to both ``PATH`` and ``ORIGINAL_PATH`` (see
:func:`build_child_env`). Both launchers pass their arguments through to the
shim untouched, with the manifest path quoted for their own shell; the shim
keeps the manifest the only place the notebook and session identifiers come
from. They also carry ``--ledger <manifest ledger_path>`` **before** the
pass-through arguments, so every call the agent makes through the shim lands
in the arm's audit trail. That path is read from the manifest and must be
absolute: a manifest without one refuses the run (see
:func:`ledger_path_from_manifest`) instead of launching an unaudited native
arm.

The child's marimo state
------------------------
marimo's discovery is **registry-based**: a server records itself in a ``servers``
registry under the state directory *its own process* resolves (``XDG_STATE_HOME``
on POSIX, ``%USERPROFILE%`` on Windows), and ``discover_servers()`` only ever
reads the registry of that same directory. ``mission_setup`` launches the arm's
staged server with an isolated state dir — it exports ``USERPROFILE`` and
``XDG_STATE_HOME`` for that server — so the staged server's registry entry lives
there and nowhere else.

A wrapper-arm child that simply inherited the operator's environment therefore
read the *operator's* registry, found no entry for the staged server, and —
measured, not assumed — ``discover_servers()`` did not finish within 90 s
(``{"seconds": 90.0, "timeout": true}``); run against the arm's own state dir the
same discovery returned in **0.2 s** with exactly the one staged server. In the
two real runs this surfaced as ``list_active_notebooks`` — the wrapper arm's
documented discovery entry point — hanging until the MCP client cut it off at
60 s: no session was ever bound, every other tool then refused with
``session_required``, and the wrapper arm has no shell with which to work around
it. Pointing the child at the arm's own state dir is what makes discovery
succeed.

``--state-dir`` is therefore **required and has no default**, so a run cannot
silently fall back to the operator's state, and it is validated (absolute, and
an existing directory) before anything is printed or written. The directory is
exported to the child as ``USERPROFILE``, ``XDG_STATE_HOME`` **and** ``HOME``
(:data:`STATE_DIR_ENV_VARS` — all three, because which one a given platform's
marimo consults differs), and the mapping is applied to **both arms
identically**, so it is not an advantage for either: the native arm reaches the
same staged server through the ``pair`` launcher, which resolves that same
registry. The dir is recorded in the run metadata.

Prompt assembly
---------------
The prompt is the mission file verbatim, one blank line, then the interface note
verbatim (:func:`assemble_prompt` canonicalizes only that boundary, so a
trailing newline on either file cannot produce two blank lines or none). The
SHA-256 of each source file is recorded in the run metadata, so accidental
per-arm drift in the shared mission or in one note is detectable after the fact.

Outcome: terminal state, elapsed time and exit status
-----------------------------------------------------
The metadata records what actually happened to the child. ``terminal_state`` is
``"completed"`` when the child terminated on its own and ``"timeout"`` when the
harness killed it for exceeding its budget; ``elapsed_s`` is the seconds spent
in the launch, measured monotonically. A completed run also records the child's
own ``exit_code``; a killed child has no status of its own, so its ``exit_code``
is ``null`` rather than a fabricated ``0``. ``main`` (:func:`cli_exit_status`)
propagates the outcome: the child's nonzero status verbatim, or
:data:`TIMEOUT_EXIT_STATUS` for a killed child, so a caller that checks the
command's return code cannot treat a failed launch as a success and advance to
grading. A dry run launches nothing and therefore keeps its original metadata
shape; a refusal writes no metadata at all.

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
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ARMS",
    "BIN_DIRNAME",
    "CHILD_PLATFORM",
    "CONFIG_FILENAME",
    "FINGERPRINT_KEYS",
    "MCP_SERVER_NAME",
    "NATIVE_TOOLSETS",
    "PAIR_LAUNCHER",
    "PAIR_LAUNCHER_CMD",
    "PROMPT_FILENAME",
    "REASONING_LEVELS",
    "RUN_BUDGET_GRACE_S",
    "STATE_DIR_ENV_VARS",
    "TERMINAL_COMPLETED",
    "TERMINAL_TIMEOUT",
    "TIMEOUT_EXIT_STATUS",
    "TRANSPORT_ARGS",
    "WRAPPER_BUILTIN_TOOLSETS",
    "WRAPPER_TOOLSETS",
    "ArmRunRefusal",
    "assemble_prompt",
    "budget_fingerprint",
    "build_child_argv",
    "build_child_env",
    "cli_exit_status",
    "cmd_launcher",
    "config_toolsets",
    "effective_toolsets",
    "ledger_path_from_manifest",
    "main",
    "meta_path_for",
    "perform_run",
    "posix_launcher",
    "render_config",
    "resolve_hermes_command",
    "run_child",
    "sha256_bytes",
    "utc_now",
    "validate_state_dir",
    "write_launcher",
]

ARMS = ("native", "wrapper")

#: The wrapper arm's one MCP server. It is also the value of that arm's
#: ``-t/--toolsets``: ``-t`` is the **MCP spawn allowlist**, matched against the
#: ``mcp_servers`` keys (``hermes_cli/mcp_startup.py`` ``set_mcp_server_filter``),
#: so the bare server name is what selects it. ``mcp-<server>`` is *not* a
#: toolset name (``hermes_cli/tools_config.py`` ``CONFIGURABLE_TOOLSETS`` has no
#: ``mcp`` entry and no ``mcp-<server>`` form) and matches no server key, so it
#: narrows nothing — see the module docstring.
MCP_SERVER_NAME = "marimo-inspect"

#: The platform ``hermes chat`` resolves its toolsets for, so the key the arm's
#: own config must use under ``platform_toolsets`` (``hermes_cli/oneshot.py``).
CHILD_PLATFORM = "cli"

#: Each arm's ``-t/--toolsets`` value. The native arm's only interface is the
#: ``pair`` command, so it needs a shell and nothing else (and a built-in name in
#: the allowlist is inert). The wrapper arm's only interface is the MCP server, so
#: its allowlist names exactly that server — the bare name, not ``mcp-<server>``.
NATIVE_TOOLSETS = ("terminal",)
WRAPPER_TOOLSETS = (MCP_SERVER_NAME,)

#: The **built-in** toolset list each arm's own config pins under
#: ``platform_toolsets.<platform>``. ``-t`` only gates MCP server spawning, so it
#: cannot narrow the built-in surface; the saved config list can, and an explicit
#: empty list is a fail-closed zero-tool selection rather than "no restriction".
#: The wrapper arm therefore gets no built-in toolset at all — its declared MCP
#: server is its whole interface. Neither arm gets the generic file toolset: the
#: mission forbids editing the notebook with file tools, so granting it would add
#: a violation path that neither interface requires.
WRAPPER_BUILTIN_TOOLSETS: tuple[str, ...] = ()

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

#: The environment variables through which a child's marimo resolves the state
#: directory (and therefore the ``servers`` registry) it reads. All three are set
#: to the arm's own ``--state-dir`` so the child's registry discovery sees the
#: staged server instead of the operator's registry — see the module docstring.
STATE_DIR_ENV_VARS = ("USERPROFILE", "XDG_STATE_HOME", "HOME")

#: Session source tag prefix, so a run is filterable by the arm it belongs to.
SOURCE_PREFIX = "arm-"

#: The terminal states a launched run's metadata records. ``COMPLETED``: the child
#: terminated on its own (whatever its exit status). ``TIMEOUT``: the harness
#: killed it for exceeding its budget, so it has no exit status of its own.
TERMINAL_COMPLETED = "completed"
TERMINAL_TIMEOUT = "timeout"

#: The exit status the CLI reports for a killed child. There is no child status
#: to propagate, but a caller must not read the launch as a success — 124 mirrors
#: coreutils ``timeout``.
TIMEOUT_EXIT_STATUS = 124

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
    """The ``-t/--toolsets`` value for *arm*.

    ``-t`` is the **MCP spawn allowlist**, matched against ``mcp_servers`` keys
    (``hermes_cli/mcp_startup.py`` ``set_mcp_server_filter``): built-in names in
    the list never match a server key, and a server name does not narrow the
    built-in surface. So the native arm gets exactly :data:`NATIVE_TOOLSETS` (a
    shell, to invoke the ``pair`` shim) and the wrapper arm gets exactly
    :data:`WRAPPER_TOOLSETS` — the **bare server name**, which is what actually
    selects the server; ``mcp-<server>`` selects nothing. Neither list contains
    the generic file toolset, and the two lists are disjoint.

    The built-in surface is pinned separately, in the arm's own config — see
    :func:`config_toolsets`.
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    if arm == "wrapper":
        return WRAPPER_TOOLSETS
    return NATIVE_TOOLSETS


def config_toolsets(arm: str) -> tuple[str, ...]:
    """The built-in toolset list *arm*'s config pins under ``platform_toolsets``.

    ``-t`` is only the MCP spawn allowlist, so the built-in surface has to be
    narrowed in the arm's own ``config.yaml`` (as ``platform_toolsets.<platform>``
    for :data:`CHILD_PLATFORM`). The native arm gets exactly
    :data:`NATIVE_TOOLSETS`; the wrapper arm gets :data:`WRAPPER_BUILTIN_TOOLSETS`,
    an explicit **empty** list, so its declared MCP server is its only interface
    and no built-in tool leaks in.

    Raises:
        ArmRunRefusal: On an unknown arm.
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    if arm == "wrapper":
        return WRAPPER_BUILTIN_TOOLSETS
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
    servers at all.

    Both arms additionally pin the **built-in** toolset list under
    ``platform_toolsets.<platform>`` (:data:`CHILD_PLATFORM`), because
    ``-t/--toolsets`` is only the MCP spawn allowlist and cannot narrow the
    built-in surface (see the module docstring). The native arm gets exactly
    :data:`NATIVE_TOOLSETS`; the wrapper arm gets an explicit **empty** list, so
    its declared MCP server is its whole interface and no built-in tool leaks in.
    Nothing else is written into the file, so the two arms' configurations differ
    in exactly the way the comparison intends.

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
    lines.append("platform_toolsets:")
    toolsets = config_toolsets(arm)
    if toolsets:
        lines.append(f"  {CHILD_PLATFORM}:")
        lines.extend(f"    - {name}" for name in toolsets)
    else:
        # Explicit empty list: fail-closed zero built-in tools, not "no
        # restriction" (hermes_cli.tools_config._platform_toolsets_explicitly_saved).
        lines.append(f"  {CHILD_PLATFORM}: []")
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


def posix_launcher(interpreter: str, shim: str, manifest: str, ledger: str) -> str:
    """The POSIX ``sh`` launcher text for the bare command ``pair``."""
    return (
        "#!/bin/sh\n"
        "# Generated per-run launcher for the native arm's restricted interface.\n"
        "# The manifest is the only place the target identifiers come from; every\n"
        '# argument after it is passed through untouched ("$@").\n'
        "# --ledger records every call in the shim's audit trail, so a write that\n"
        "# bypasses the adapter is detectable by the independent grader.\n"
        f"exec {sh_quote(interpreter)} {sh_quote(shim)}"
        f' --manifest {sh_quote(manifest)} --ledger {sh_quote(ledger)} "$@"\n'
    )


def cmd_launcher(interpreter: str, shim: str, manifest: str, ledger: str) -> str:
    """The ``cmd.exe`` launcher text for the bare command ``pair``."""
    return (
        "@echo off\r\n"
        "rem Generated per-run launcher for the native arm's restricted interface.\r\n"
        "rem The manifest is the only place the target identifiers come from; every\r\n"
        "rem argument after it is passed through untouched (%*).\r\n"
        "rem --ledger records every call in the shim's audit trail, so a write that\r\n"
        "rem bypasses the adapter is detectable by the independent grader.\r\n"
        f"{cmd_quote(interpreter)} {cmd_quote(shim)}"
        f" --manifest {cmd_quote(manifest)} --ledger {cmd_quote(ledger)} %*\r\n"
    )


def _make_executable(path: Path) -> None:
    """Best-effort exec bit (a no-op on Windows, where the suffix carries it)."""
    try:
        path.chmod(path.stat().st_mode | 0o111)
    except OSError:  # pragma: no cover - filesystem without POSIX modes
        pass


def ledger_path_from_manifest(manifest: str | os.PathLike[str]) -> str:
    """The absolute ledger path the native arm's manifest records, or refuse.

    The native arm reaches the notebook only through the ``pair`` launcher, and
    every call that goes through it is appended to the shim's ledger — the audit
    trail the independent grader checks against the saved notebook. A manifest
    that names no absolute ledger path would still launch the arm, but every
    call would be unaudited and a bypass would leave no trace at all, so the run
    is refused instead. Reading the manifest here — before anything is printed
    or written — makes that a refusal rather than a silently unaudited run.

    The wrapper arm has no shim and no ledger; this is only consulted for the
    native arm (see :func:`perform_run`).

    Raises:
        ArmRunRefusal: If the manifest cannot be read, is not a JSON object, or
            carries no non-empty absolute ``ledger_path`` (written by
            ``mission_setup``).
    """
    try:
        payload = json.loads(Path(manifest).read_bytes().decode("utf-8"))
    except OSError as exc:
        raise ArmRunRefusal(f"cannot read the arm manifest {manifest}: {exc}") from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArmRunRefusal(
            f"the arm manifest is not readable JSON: {manifest} ({exc})"
        ) from exc
    if not isinstance(payload, dict):
        raise ArmRunRefusal(f"the arm manifest must be a JSON object: {manifest}")
    ledger = payload.get("ledger_path")
    if not isinstance(ledger, str) or ledger.strip() == "":
        raise ArmRunRefusal(
            "the manifest carries no ledger_path: the native arm would run"
            " unaudited (re-run mission_setup to record one)"
        )
    if not Path(ledger).is_absolute():
        raise ArmRunRefusal(f"the manifest ledger_path must be absolute: {ledger!r}")
    return ledger


def write_launcher(
    home: str | os.PathLike[str],
    *,
    shim: str | os.PathLike[str],
    manifest: str | os.PathLike[str],
    ledger: str | os.PathLike[str],
    interpreter: str | None = None,
) -> tuple[Path, Path]:
    """Write the ``pair`` launchers under ``<home>/bin`` and return both paths.

    Two files are written — a POSIX ``sh`` script and a ``cmd.exe`` batch file —
    so the bare command resolves in either shell. Both invoke the shim with
    ``--manifest`` and ``--ledger`` and pass their own arguments through, which
    is why the launcher can never add a target selector.

    ``--ledger`` is the manifest's own ``ledger_path`` (read by
    :func:`ledger_path_from_manifest`) and is written **before** the pass-through
    arguments, so the agent's own options can neither supply, replace nor
    reorder it.

    Raises:
        ArmRunRefusal: If no interpreter is available, if the shim, manifest or
            ledger path is relative (the launcher runs from the agent's own
            working directory, so a relative path would silently point
            elsewhere), or if (for the ``cmd`` launcher) the manifest or ledger
            path cannot be quoted safely.
    """
    bin_dir = Path(home) / BIN_DIRNAME
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = interpreter if interpreter is not None else sys.executable
    require(
        isinstance(python, str) and python.strip() != "",
        "no interpreter to run the pair shim with",
    )
    shim_path, manifest_path = Path(shim), Path(manifest)
    ledger_path = Path(ledger)
    # The launcher is invoked by the agent, whose working directory is not the
    # harness's: every path is embedded verbatim and must already be absolute.
    require(shim_path.is_absolute(), f"the pair shim path must be absolute: {shim}")
    require(
        manifest_path.is_absolute(), f"the manifest path must be absolute: {manifest}"
    )
    require(ledger_path.is_absolute(), f"the ledger path must be absolute: {ledger}")
    shim_text, manifest_text, ledger_text = (
        str(shim_path),
        str(manifest_path),
        str(ledger_path),
    )
    posix = bin_dir / PAIR_LAUNCHER
    posix.write_text(
        posix_launcher(python, shim_text, manifest_text, ledger_text),
        encoding="utf-8",
        newline="\n",
    )
    _make_executable(posix)
    windows = bin_dir / PAIR_LAUNCHER_CMD
    windows.write_text(
        cmd_launcher(python, shim_text, manifest_text, ledger_text),
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
    explicit narrow ``-t`` value, ``--ignore-rules`` to skip
    AGENTS.md/SOUL.md/memory/preloaded-skill injection, ``--yolo`` (the
    unattended switch, applied identically to both arms — a non-interactive
    child cannot answer a dangerous-command approval prompt, so without it the
    native arm can stall on one), and ``--source`` naming the arm. No
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
        "--yolo",
        "--source",
        source,
        "--quiet",
        "--format",
        "text",
    ]


def _prepend_path_entry(existing: str, entry: str) -> str:
    """*existing* (``os.pathsep``-separated) with *entry* first, de-duplicated.

    An empty *existing* yields just *entry*; an *entry* already present is moved
    to the front rather than duplicated.
    """
    entries = [item for item in existing.split(os.pathsep) if item and item != entry]
    return os.pathsep.join([entry, *entries])


def validate_state_dir(state_dir: str | os.PathLike[str] | None) -> Path:
    """The arm's isolated marimo state dir, refused unless absolute and existing.

    The child's registry discovery reads the state directory *its own* process
    resolves, so this must be the directory ``mission_setup`` launched this arm's
    staged server with: a blank, relative or missing one would silently fall back
    to the operator's registry and hang the wrapper arm's discovery (see the
    module docstring).

    Returns:
        The state dir as a :class:`Path`.

    Raises:
        ArmRunRefusal: If *state_dir* is absent, blank, relative, or not an
            existing directory.
    """
    require(
        state_dir is not None and str(state_dir).strip() != "",
        "the state dir must be supplied (--state-dir)",
    )
    path = Path(str(state_dir))
    require(path.is_absolute(), f"the state dir must be absolute: {state_dir}")
    require(
        path.is_dir(),
        f"the state dir must be an existing directory: {state_dir}",
    )
    return path


def build_child_env(
    home: str | os.PathLike[str],
    arm: str,
    *,
    state_dir: str | os.PathLike[str],
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The child's environment: an ephemeral home, the marimo state, ``bin`` for native.

    ``HERMES_HOME`` points the child at the run's own configuration home, so the
    active profile is neither read nor modified.

    ``USERPROFILE``, ``XDG_STATE_HOME`` and ``HOME`` are all set to
    *state_dir* (:data:`STATE_DIR_ENV_VARS`) — the isolated state directory
    ``mission_setup`` launched this arm's staged marimo server with. marimo's
    discovery is registry-based and reads the registry under the state directory
    its own process resolves, so without this the wrapper arm's
    ``list_active_notebooks`` reads the operator's registry, finds no staged
    server and hangs until the MCP client times out (see the module docstring).
    The mapping is applied to **both** arms identically.

    The native arm also gets
    ``<home>/bin`` prepended to ``PATH``, which is what makes the bare command
    ``pair`` resolve.

    On Windows the entry is prepended to ``ORIGINAL_PATH`` as well, because
    ``PATH`` alone is not enough there: Hermes runs the agent's terminal commands
    through a **login** bash, and git-bash's ``/etc/profile`` rebuilds ``PATH``
    from ``$ORIGINAL_PATH`` when that variable is set
    (``ORIGINAL_PATH="${ORIGINAL_PATH:-${PATH}}"``) — so an entry added only to
    ``PATH`` is discarded and the agent sees ``command not found``. The harness
    therefore declares the directory in both lists when ``ORIGINAL_PATH`` is
    present. On POSIX no such variable exists and the ``PATH`` entry is the whole
    story. Names are added as-is (``os.pathsep``-joined): on Windows the MSYS
    runtime converts Windows-form entries when bash starts, and ``ORIGINAL_PATH``
    shares that form.

    Raises:
        ArmRunRefusal: On an unknown arm, or a state dir that is not an existing
            absolute directory.
    """
    require(arm in ARMS, f"arm must be one of {ARMS}, got {arm!r}")
    state = validate_state_dir(state_dir)
    env = dict(os.environ if base_env is None else base_env)
    home = Path(home)
    env["HERMES_HOME"] = str(home)
    for name in STATE_DIR_ENV_VARS:
        env[name] = str(state)
    if arm == "native":
        bin_dir = str(home / BIN_DIRNAME)
        env["PATH"] = _prepend_path_entry(env.get("PATH", ""), bin_dir)
        if "ORIGINAL_PATH" in env:
            env["ORIGINAL_PATH"] = _prepend_path_entry(env["ORIGINAL_PATH"], bin_dir)
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

    Raises:
        subprocess.TimeoutExpired: If the child outlives *timeout*.
            ``subprocess.run`` kills the process before raising, so the child is
            already dead when this escapes; :func:`perform_run` turns it into an
            explicit ``timeout`` terminal state rather than a fabricated status.
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
    state_dir: str | os.PathLike[str] | None,
    workdir: str | os.PathLike[str] | None,
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
    # The arm's marimo state dir, for both arms and checked here -- before
    # anything is printed or written -- so a refused run never reaches the
    # fingerprint line: a blank, relative or missing dir would let the child's
    # registry discovery fall back to the operator's state.
    validate_state_dir(state_dir)
    # The child is launched with ``--in <workdir>``; a missing directory makes the
    # child exit before it does anything, which then looks like a failed arm
    # rather than a failed harness. Observed live: one attempt died in 1.8 s with
    # "Error: --in directory not found" and graded as a failed run.
    require(
        workdir is not None and str(workdir).strip() != "",
        "workdir must be supplied: it is the agent's starting directory",
    )
    workdir_path = Path(str(workdir))
    require(
        workdir_path.is_absolute(),
        f"the workdir must be absolute: {workdir}",
    )
    require(
        workdir_path.is_dir(),
        f"the workdir must be an existing directory: {workdir}",
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
    state_dir: str | os.PathLike[str],
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
    timer: Callable[[], float] | None = None,
) -> dict:
    """Prepare one arm's run and — unless *dry_run* — launch its agent.

    Steps: validate that every budget argument was supplied; for the native arm
    read the manifest and take its ``ledger_path`` (refusing a manifest that
    names none, so the run cannot be launched unaudited); compute and print the
    budget fingerprint; materialize the configuration home (``config.yaml``,
    and for the native arm the ``pair`` launcher); assemble the prompt from the
    mission plus the interface note and record both digests; build the child
    argv and environment; then either print the command line and write the
    metadata without launching (``dry_run=True``), or run the child, capture its
    transcript and write the metadata beside it.

    A launched run's metadata records a ``terminal_state`` (``"completed"`` if
    the child terminated on its own, ``"timeout"`` if it was killed for
    exceeding its budget), the ``elapsed_s`` seconds the launch took and the
    child's own ``exit_code`` — ``null`` after a timeout, never a fabricated
    ``0``. A timed-out child is still recorded in full rather than left
    unwritten. A dry run launches nothing and keeps the metadata shape it had.

    Args:
        arm: One of :data:`ARMS`.
        home: The ephemeral configuration home for this run (exported as the
            child's ``HERMES_HOME``).
        state_dir: The arm's isolated marimo state dir — the directory
            ``mission_setup`` launched the staged server with. Required, with no
            default, so a run cannot silently fall back to the operator's state;
            exported to the child as ``USERPROFILE``, ``XDG_STATE_HOME`` and
            ``HOME`` (:data:`STATE_DIR_ENV_VARS`), for both arms identically.
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
            recorder so nothing is executed. It may raise
            :class:`subprocess.TimeoutExpired` to signal a killed child.
        clock: Optional ``() -> str`` timestamp source for deterministic tests.
        timer: Optional ``() -> float`` monotonic seconds source for the elapsed
            measurement; defaults to :func:`time.monotonic` (tests inject it).
            Consulted only on the launch path, so a dry run is unaffected.

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
        state_dir=state_dir,
        workdir=workdir,
        manifest=manifest,
        shim=shim,
        mcp_command=mcp_command,
    )
    # The native arm's audit trail: the manifest's ledger_path, read BEFORE
    # anything is printed or written. A manifest that names none refuses the run
    # here, so a native arm cannot be launched without the shim recording every
    # call it makes through the adapter.
    ledger = ledger_path_from_manifest(manifest) if arm == "native" else None
    state = validate_state_dir(state_dir)
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
        write_launcher(home, shim=str(shim), manifest=str(manifest), ledger=str(ledger))

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
    env = build_child_env(home, arm, state_dir=state)
    transcript = Path(transcript)

    metadata = {
        "arm": arm,
        "fingerprint": fingerprint,
        # The arm's isolated marimo state dir, exported to the child as
        # STATE_DIR_ENV_VARS. Recorded here, not hashed into the fingerprint:
        # each arm stages its own server under its own dir, so a per-arm
        # difference is expected and must not break the comparability check.
        "state_dir": str(state),
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

    tick = timer or time.monotonic
    launch_started = tick()
    try:
        exit_code: int | None = launch(
            argv, env, transcript, float(run_budget) + RUN_BUDGET_GRACE_S
        )
    except subprocess.TimeoutExpired:
        # The child was killed, not finished: it has no status of its own, so the
        # run is recorded as an explicit timeout with a null exit code rather
        # than a fabricated success.
        exit_code = None
        metadata["terminal_state"] = TERMINAL_TIMEOUT
    else:
        metadata["terminal_state"] = TERMINAL_COMPLETED
    metadata["elapsed_s"] = float(tick() - launch_started)
    metadata["exit_code"] = None if exit_code is None else int(exit_code)
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
        "--state-dir",
        required=True,
        dest="state_dir",
        help=(
            "the arm's isolated marimo state dir (the directory mission_setup"
            " launched the staged server with; exported to the child as "
            + ", ".join(STATE_DIR_ENV_VARS)
            + ")"
        ),
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


def cli_exit_status(metadata: Mapping[str, object]) -> int:
    """The process exit status *metadata* implies for a finished run.

    Zero only when nothing was launched (a dry run) or the child terminated on
    its own with status 0. A nonzero child status is propagated verbatim, so a
    caller that checks the command's return code cannot advance to grading after
    a failed launch. A killed child has no status to propagate, and a launched
    run whose status cannot be read is not a success either: both fail closed
    with :data:`TIMEOUT_EXIT_STATUS`.
    """
    if metadata.get("dry_run"):
        return 0
    code = metadata.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    return TIMEOUT_EXIT_STATUS


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: prepare and run one arm, or refuse with JSON on stderr.

    The metadata is always printed for a run that was prepared — including a
    failed one — and the process exit status follows it: the child's own nonzero
    status, or :data:`TIMEOUT_EXIT_STATUS` for a killed child. A refusal prints a
    JSON error on stderr and exits 1 without writing metadata.
    """
    args = _parse_args(argv)
    try:
        metadata = perform_run(
            arm=args.arm,
            home=args.home,
            state_dir=args.state_dir,
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
    return cli_exit_status(metadata)


if __name__ == "__main__":
    raise SystemExit(main())
