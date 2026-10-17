"""Native-arm ``marimo pair`` CLI: the sole interface handed to a fresh agent.

The restricted comparison needs the native arm driven through exactly one narrow
interface. A fresh agent must never be able to name a notebook file or a session
itself: this CLI accepts **no** file, session or URL argument, reads those
identifiers from an arm manifest written by the setup step, and refuses when the
live census no longer matches that manifest.

Contract
--------
``python pair_cli.py --manifest <manifest.json> <subcommand> [options]``

``--manifest`` is mandatory and comes **before** exactly one subcommand:
``run_all``; ``create_cell --code TEXT [--hide-code]``; ``run_cell --cell-id
ID``; ``variables [--names A,B]``; ``errors``; ``set_ui --name NAME --value
VALUE``. No other option is accepted anywhere, so the native selector flags
``--file``/``--session``/``--url`` are rejected as unknown options. ``--value``
is parsed as JSON with a fallback to the literal string.

The manifest is a JSON object carrying ``server_url``, ``session_id``,
``notebook_path``, ``process_id``, ``python`` (the interpreter of the *native*
marimo environment, which is not this process's interpreter) and ``sha256``.
Unknown extra keys are ignored; missing or wrongly-typed keys refuse the run.

Every subcommand runs the same three steps in order:

1. load the manifest and build a :class:`session_guard.SessionAnchor`;
2. read the server's session census over loopback with ``urllib`` and call
   :func:`session_guard.validate_census` **before** the subcommand runs;
3. build ``<python> -m marimo pair execute --url <manifest url> --file
   <manifest notebook> -c <generated code>`` and run it with ``subprocess``.

``--url`` and ``--file`` come **only** from the manifest; command-line
arguments can never supply, override or extend them. The code argument is one
argv element, so no shell is involved (``shell=False`` is never relaxed).
Nothing here imports ``marimo`` or the package: this module is stdlib-only, so
it runs in the repository's own interpreter while the native CLI runs in the
arm's pinned environment. The inherited environment is passed through and no
token is ever read, written or echoed.

Scope honesty — detection, not atomic prevention
------------------------------------------------
Each step inspects an already-captured snapshot. The census is read and then
validated, so a rebind landing *after* the passing check is unaddressed: the
CLI proves the snapshot it was handed still matches the anchor, it does not pin
the live server to that anchor. The in-kernel identity echo below is likewise an
observation of one execution, not a lock — a wrong-but-consistent target that
echoes the anchored path is indistinguishable here.

The generated code prints an identity line from inside the kernel
(``ctx._kernel.app_metadata.filename``, the same private read the repository's
in-kernel guard probe uses) *before* it mutates anything, and the tool's
reported stdout must carry exactly one such line matching the manifest's
notebook path; a missing or mismatched line refuses the run. The kernel chooses
what to echo at execution time, so this detects a misrouted session after the
fact rather than preventing it. ``create_cell`` still queues its write in the
same context as the check, so the echo is not a transactional precondition.

``sha256`` is shape-checked (64 lowercase hex) and carried, not enforced against
the notebook's current bytes: legitimate ``create_cell`` mutations change those
bytes, so a per-invocation byte gate would fail the second subcommand of every
run. It belongs to the setup/verification step's initial-identity check.

Result and exit codes
---------------------
One compact JSON object is printed to stdout per run. ``status`` is ``ok`` or
``error``; ``stage`` names the subcommand on success and the failing step
(``manifest``, ``census``, ``native``, ``cli``) on a refusal; ``stdout`` is the
tool's reported kernel output; ``notebook_path`` is the resolved manifest path
whenever a manifest was loaded. Refusals add ``code`` (a stable string) and
``error`` (a human message).

Exit codes: 0 ok, 2 census mismatch, 3 manifest problem, 4 census unreachable,
5 native subprocess failure, 6 native timeout, 7 non-JSON tool stdout, 8 tool
reported an invalid or unsuccessful envelope, 9 identity missing or mismatched,
10 usage error, 11 internal invariant violated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from session_guard import SessionAnchor, validate_census

__all__ = [
    "ArmManifest",
    "PairCliError",
    "code_for",
    "load_manifest",
    "main",
    "native_argv",
    "normalize_path",
    "read_census",
    "run_native",
    "tool_stdout",
]

EXIT_OK = 0
EXIT_CENSUS_MISMATCH = 2
EXIT_MANIFEST = 3
EXIT_CENSUS_UNREACHABLE = 4
EXIT_NATIVE_FAILED = 5
EXIT_NATIVE_TIMEOUT = 6
EXIT_TOOL_NON_JSON = 7
EXIT_TOOL_ENVELOPE = 8
EXIT_IDENTITY = 9
EXIT_USAGE = 10
EXIT_INTERNAL = 11

#: Subcommands, in the order the interface documents them.
STAGES = ("run_all", "create_cell", "run_cell", "variables", "errors", "set_ui")

#: The only manifest keys the CLI reads. Extra keys are ignored on purpose: the
#: setup step may record bookkeeping that this interface has no opinion about.
MANIFEST_KEYS = (
    "server_url",
    "session_id",
    "notebook_path",
    "process_id",
    "python",
    "sha256",
)

#: Native argv prefix; the arm's interpreter is prepended at call time.
NATIVE_COMMAND = ("-m", "marimo", "pair", "execute")

#: Census endpoint suffix, appended to the manifest's server url.
SESSIONS_SUFFIX = "/api/sessions"

#: Sentinel the generated code prints from inside the kernel.
IDENTITY_PREFIX = "PAIR_CLI_IDENTITY"

#: Tool payload keys that, when present, must echo the anchored notebook.
IDENTITY_ECHO_KEYS = ("notebook_path", "file", "filename", "path")

#: Tool payload keys that, when present, must echo the anchored session.
SESSION_ECHO_KEYS = ("session_id", "pid")

NATIVE_TIMEOUT_S = 60.0
CENSUS_TIMEOUT_S = 5.0

_HEX_DIGITS = frozenset("0123456789abcdef")


def require(condition: bool, message: str, *, stage: str = "cli") -> None:
    """Always-on check; a bare ``assert`` is stripped under ``python -O``."""
    if not condition:
        raise PairCliError("internal_error", message, EXIT_INTERNAL, stage=stage)


class PairCliError(Exception):
    """A refusal carrying an explicit stage, stable code and exit status."""

    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int,
        *,
        stage: str,
        notebook_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.stage = stage
        self.notebook_path = notebook_path


class UsageError(PairCliError):
    """A malformed command line, including every unknown option."""

    def __init__(self, message: str) -> None:
        super().__init__("usage_error", message, EXIT_USAGE, stage="cli")


def _problem(code: str, message: str, exit_code: int, stage: str) -> PairCliError:
    return PairCliError(code, message, exit_code, stage=stage)


def normalize_path(path: str | os.PathLike[str]) -> str:
    """Case-folded absolute form used for every path comparison."""
    return os.path.normcase(os.path.abspath(str(path)))


def _require_option(condition: bool, stage: str, option: str) -> None:
    """Always-on check that a stage received its documented option."""
    if not condition:
        raise _problem(
            "cli_argument_missing",
            f"{stage} requires {option}",
            EXIT_USAGE,
            stage,
        )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


@dataclass(frozen=True)
class ArmManifest:
    """One arm's recorded live-session expectation, as written by setup."""

    server_url: str
    session_id: str
    notebook_path: str
    process_id: int
    python: str
    sha256: str

    @property
    def anchor(self) -> SessionAnchor:
        """The census anchor this manifest pins."""
        return SessionAnchor(
            session_id=self.session_id,
            notebook_path=self.notebook_path,
            process_id=self.process_id,
        )

    @property
    def resolved_notebook_path(self) -> str:
        """The absolute notebook path reported in every result."""
        return str(Path(self.notebook_path).resolve())


def load_manifest(path: str | os.PathLike[str]) -> ArmManifest:
    """Read and strictly validate one arm manifest.

    Args:
        path: Manifest written by the setup step.

    Returns:
        The validated manifest.

    Raises:
        PairCliError: If the file is unreadable, is not a JSON object, is
            missing a required key, or carries a wrongly-typed value.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise _problem(
            "manifest_unreadable",
            f"cannot read manifest {path}: {error}",
            EXIT_MANIFEST,
            "manifest",
        ) from error
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise _problem(
            "manifest_json_invalid",
            f"manifest {path} is not valid JSON: {error}",
            EXIT_MANIFEST,
            "manifest",
        ) from error
    if not isinstance(data, dict):
        raise _problem(
            "manifest_not_object",
            "manifest must be a JSON object",
            EXIT_MANIFEST,
            "manifest",
        )

    missing = [key for key in MANIFEST_KEYS if key not in data]
    if missing:
        raise _problem(
            "manifest_missing_keys",
            f"manifest is missing required keys: {sorted(missing)}",
            EXIT_MANIFEST,
            "manifest",
        )

    for key in ("server_url", "session_id", "notebook_path", "python"):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise _problem(
                "manifest_field_invalid",
                f"manifest field {key!r} must be a non-empty string",
                EXIT_MANIFEST,
                "manifest",
            )

    process_id = data["process_id"]
    if not isinstance(process_id, int) or isinstance(process_id, bool):
        raise _problem(
            "manifest_field_invalid",
            "manifest field 'process_id' must be an integer",
            EXIT_MANIFEST,
            "manifest",
        )

    if not _is_sha256(data["sha256"]):
        raise _problem(
            "manifest_field_invalid",
            "manifest field 'sha256' must be 64 lowercase hex characters",
            EXIT_MANIFEST,
            "manifest",
        )

    return ArmManifest(
        server_url=data["server_url"],
        session_id=data["session_id"],
        notebook_path=data["notebook_path"],
        process_id=process_id,
        python=data["python"],
        sha256=data["sha256"],
    )


# --------------------------------------------------------------------------- #
# Census (injectable seam: ``census_reader``)
# --------------------------------------------------------------------------- #


def read_census(server_url: str) -> object:
    """Read the loopback session census: ``{session_id: entry}``.

    Raises:
        OSError: If the endpoint is unreachable.
        ValueError: If the response is not JSON.
    """
    endpoint = server_url.rstrip("/") + SESSIONS_SUFFIX
    with urllib.request.urlopen(endpoint, timeout=CENSUS_TIMEOUT_S) as response:
        return json.load(response)


# --------------------------------------------------------------------------- #
# Native call (injectable seam: ``runner``)
# --------------------------------------------------------------------------- #

#: A runner takes ``(argv, env, timeout)`` and returns a
#: :class:`subprocess.CompletedProcess`-like object with ``returncode``,
#: ``stdout`` and ``stderr``. Tests substitute it to stay wholly offline.
Runner = Callable[[Sequence[str], Mapping[str, str], float], object]


def run_native(
    argv: Sequence[str], env: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run the native CLI without a shell; the timeout is mandatory."""
    return subprocess.run(
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def native_argv(manifest: ArmManifest, code: str) -> list[str]:
    """Build the native argv from manifest identifiers and generated code only."""
    return [
        manifest.python,
        *NATIVE_COMMAND,
        "--url",
        manifest.server_url,
        "--file",
        manifest.resolved_notebook_path,
        "-c",
        code,
    ]


# --------------------------------------------------------------------------- #
# Generated code-mode snippets
# --------------------------------------------------------------------------- #

_PREAMBLE = "import os as _os\nimport marimo._code_mode as cm\n"

_IDENTITY_LINES = (
    "    _fn = ctx._kernel.app_metadata.filename\n"
    f"    print({IDENTITY_PREFIX!r},"
    " _os.path.normcase(_os.path.abspath(_fn)) if _fn else 'NONE')\n"
)


def code_for(
    stage: str,
    *,
    code: str | None = None,
    hide_code: bool = False,
    cell_id: str | None = None,
    names: list[str] | None = None,
    name: str | None = None,
    value: object = None,
) -> str:
    """Generate the code-mode program for *stage*.

    The identity echo is always the first statement inside the context, so a
    misrouted session is observed before the stage's own effects are queued.

    Raises:
        PairCliError: On an unknown stage (an internal invariant) or a missing
            stage option (a usage error). Both are always-on: a wrong program
            must never reach the kernel.
    """
    require(stage in STAGES, f"unknown stage {stage!r}")
    lines: list[str] = [_IDENTITY_LINES]

    if stage == "run_all":
        lines.append("    for _cell in ctx.cells.values():\n")
        lines.append("        ctx.run_cell(_cell.id)\n")
        lines.append("    print('PAIR_CLI_RAN', len(ctx.cells))\n")
    elif stage == "create_cell":
        _require_option(isinstance(code, str), stage, "--code")
        lines.append(
            f"    _cid = ctx.create_cell({code!r}, hide_code={bool(hide_code)!r})\n"
        )
        lines.append("    ctx.run_cell(_cid)\n")
        lines.append("    print('PAIR_CLI_CREATED', _cid)\n")
    elif stage == "run_cell":
        _require_option(isinstance(cell_id, str), stage, "--cell-id")
        lines.append(f"    ctx.run_cell({cell_id!r})\n")
        lines.append(f"    print('PAIR_CLI_RAN_CELL', {cell_id!r})\n")
    elif stage == "variables":
        lines.append("    import json as _json\n")
        lines.append(f"    _names = {names!r}\n")
        lines.append("    if _names is None:\n")
        lines.append(
            "        _names = sorted(k for k in ctx.globals.keys()"
            " if not k.startswith('_'))\n"
        )
        lines.append(
            "    print('PAIR_CLI_VARIABLES', _json.dumps("
            "{n: repr(ctx.globals.get(n)) for n in _names}))\n"
        )
    elif stage == "errors":
        lines.append("    import json as _json\n")
        lines.append("    _errs = {}\n")
        lines.append("    for _cid, _cell in ctx.cells.items():\n")
        lines.append(
            "        _errs[_cid] = [str(_e) for _e in"
            " (getattr(_cell, 'errors', None) or [])]\n"
        )
        lines.append("    print('PAIR_CLI_ERRORS', _json.dumps(_errs))\n")
    elif stage == "set_ui":
        _require_option(isinstance(name, str), stage, "--name")
        lines.append(f"    ctx.set_ui_value(ctx.globals[{name!r}], {value!r})\n")
        lines.append(f"    print('PAIR_CLI_SET_UI', {name!r})\n")

    return _PREAMBLE + "async with cm.get_context() as ctx:\n" + "".join(lines)


# --------------------------------------------------------------------------- #
# Tool-payload validation
# --------------------------------------------------------------------------- #


def _decoded_object(stdout: object) -> dict | None:
    """Decode stdout into a JSON object, or return ``None``."""
    if not isinstance(stdout, str):
        return None
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _identity_echo(payload: str, manifest: ArmManifest) -> None:
    expected = normalize_path(manifest.notebook_path)
    lines = [line for line in payload.splitlines() if line.startswith(IDENTITY_PREFIX)]
    if len(lines) != 1:
        raise _problem(
            "identity_missing",
            f"expected exactly one {IDENTITY_PREFIX} line in tool stdout,"
            f" saw {len(lines)}",
            EXIT_IDENTITY,
            "native",
        )
    reported = lines[0][len(IDENTITY_PREFIX) :].strip()
    if normalize_path(reported) != expected:
        raise _problem(
            "identity_mismatch",
            f"kernel reported notebook {reported!r}, expected"
            f" {manifest.notebook_path!r}",
            EXIT_IDENTITY,
            "native",
        )


def _field_echo(payload: dict, manifest: ArmManifest) -> None:
    """Refuse a tool payload whose optional identity fields contradict the anchor."""
    for key in IDENTITY_ECHO_KEYS:
        value = payload.get(key)
        if (
            isinstance(value, str)
            and value.strip()
            and normalize_path(value) != normalize_path(manifest.notebook_path)
        ):
            raise _problem(
                "identity_echo_mismatch",
                f"tool payload {key!r} is {value!r}, expected the anchored notebook",
                EXIT_IDENTITY,
                "native",
            )
    for key in SESSION_ECHO_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        expected = manifest.session_id if key == "session_id" else manifest.process_id
        if str(value) != str(expected):
            raise _problem(
                "identity_echo_mismatch",
                f"tool payload {key!r} is {value!r}, expected {expected!r}",
                EXIT_IDENTITY,
                "native",
            )


def tool_stdout(completed: object, manifest: ArmManifest) -> str:
    """Validate the tool's envelope and return its reported kernel stdout.

    Precedence: a parsed envelope that is unsuccessful refuses as
    ``tool_unsuccessful``; a parsed envelope claiming success while the process
    exited non-zero refuses as a native failure; an unparsed envelope refuses as
    a native failure when the exit status was non-zero and as non-JSON stdout
    otherwise. Identity is checked last, only for a healthy envelope.

    Raises:
        PairCliError: On any unusable, unsuccessful, or misidentified envelope.
    """
    returncode = getattr(completed, "returncode", None)
    stderr = getattr(completed, "stderr", "") or ""
    payload = _decoded_object(getattr(completed, "stdout", None))

    if payload is not None:
        if not payload.get("success"):
            detail = payload.get("error") or payload.get("stderr") or payload
            raise _problem(
                "tool_unsuccessful",
                f"tool reported failure: {str(detail)[:400]}",
                EXIT_TOOL_ENVELOPE,
                "native",
            )
        if returncode != 0:
            raise _problem(
                "tool_exit_conflict",
                f"tool claimed success but exited {returncode}",
                EXIT_NATIVE_FAILED,
                "native",
            )
        reported = payload.get("stdout")
        if not isinstance(reported, str):
            raise _problem(
                "tool_stdout_missing",
                "tool payload carries no string 'stdout'",
                EXIT_TOOL_ENVELOPE,
                "native",
            )
        _identity_echo(reported, manifest)
        _field_echo(payload, manifest)
        return reported

    raw = getattr(completed, "stdout", None)
    excerpt = str(raw)[:400] if raw is not None else ""
    if returncode != 0:
        raise _problem(
            "native_failed",
            f"native CLI exited {returncode}: stdout={excerpt!r}"
            f" stderr={str(stderr)[:400]!r}",
            EXIT_NATIVE_FAILED,
            "native",
        )
    raise _problem(
        "tool_non_json",
        f"native CLI stdout is not a JSON object: {excerpt!r}",
        EXIT_TOOL_NON_JSON,
        "native",
    )


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


class _ArgumentParser(argparse.ArgumentParser):
    """An argument parser that refuses by raising instead of exiting."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(f"{self.prog}: {message}")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="pair_cli.py",
        description=(
            "Native-arm pair CLI. Takes no file, session or url argument: the"
            " manifest supplies them."
        ),
    )
    parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="arm manifest JSON written by the setup step",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("run_all", help="run every cell in the anchored session")

    create = subcommands.add_parser("create_cell", help="create and run one cell")
    create.add_argument("--code", required=True, help="cell source")
    create.add_argument(
        "--hide-code", action="store_true", help="hide the created cell's code"
    )

    run = subcommands.add_parser("run_cell", help="run one existing cell")
    run.add_argument("--cell-id", required=True, help="cell id to run")

    variables = subcommands.add_parser("variables", help="read kernel variables")
    variables.add_argument(
        "--names", default=None, help="comma-separated names (default: all public)"
    )

    subcommands.add_parser("errors", help="read per-cell kernel errors")

    set_ui = subcommands.add_parser("set_ui", help="set one UI element's value")
    set_ui.add_argument("--name", required=True, help="UI element variable name")
    set_ui.add_argument("--value", required=True, help="JSON value, else the literal")
    return parser


def _parse_names(text: str | None) -> list[str] | None:
    if text is None:
        return None
    names = [name.strip() for name in text.split(",") if name.strip()]
    if not names:
        raise UsageError("--names carries no variable name")
    return names


def _parse_value(text: str) -> object:
    """Parse ``--value`` as JSON, falling back to the literal string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))


def _refuse(
    code: str,
    message: str,
    exit_code: int,
    *,
    stage: str,
    notebook_path: str | None = None,
) -> int:
    payload: dict = {
        "status": "error",
        "stage": stage,
        "code": code,
        "error": message,
    }
    if notebook_path:
        payload["notebook_path"] = notebook_path
    _emit(payload)
    return exit_code


def _main(
    argv: Sequence[str],
    *,
    runner: Runner,
    census_reader: Callable[[str], object],
    env: Mapping[str, str] | None,
) -> int:
    args = _build_parser().parse_args(list(argv))
    manifest = load_manifest(args.manifest)
    try:
        try:
            census = census_reader(manifest.server_url)
        except (OSError, ValueError) as error:
            raise _problem(
                "census_unreachable",
                f"cannot read the session census from {manifest.server_url}: {error}",
                EXIT_CENSUS_UNREACHABLE,
                "census",
            ) from error
        try:
            validate_census(census, manifest.anchor)
        except ValueError as error:
            raise _problem(
                "census_mismatch",
                f"live census no longer matches the manifest anchor: {error}",
                EXIT_CENSUS_MISMATCH,
                "census",
            ) from error

        # Only the selected stage's options are read: every other subcommand's
        # attributes are absent from the namespace by design.
        if args.command == "create_cell":
            options: dict[str, object] = {
                "code": args.code,
                "hide_code": args.hide_code,
            }
        elif args.command == "run_cell":
            options = {"cell_id": args.cell_id}
        elif args.command == "variables":
            options = {"names": _parse_names(args.names)}
        elif args.command == "set_ui":
            options = {"name": args.name, "value": _parse_value(args.value)}
        else:
            options = {}
        code = code_for(args.command, **options)
        require(
            bool(code.strip()),
            "generated code must not be empty",
            stage=args.command,
        )

        native = native_argv(manifest, code)
        native_env = dict(os.environ) if env is None else dict(env)
        try:
            completed = runner(native, native_env, NATIVE_TIMEOUT_S)
        except subprocess.TimeoutExpired as error:
            raise _problem(
                "native_timeout",
                f"native CLI exceeded {NATIVE_TIMEOUT_S}s",
                EXIT_NATIVE_TIMEOUT,
                "native",
            ) from error
        except OSError as error:
            raise _problem(
                "native_unlaunchable",
                f"cannot launch the native CLI: {error}",
                EXIT_NATIVE_FAILED,
                "native",
            ) from error

        reported = tool_stdout(completed, manifest)
        _emit(
            {
                "status": "ok",
                "stage": args.command,
                "notebook_path": manifest.resolved_notebook_path,
                "stdout": reported,
            }
        )
        return EXIT_OK
    except PairCliError as error:
        if error.notebook_path is None:
            error.notebook_path = manifest.resolved_notebook_path
        raise


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = run_native,
    census_reader: Callable[[str], object] = read_census,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run one subcommand and return its explicit exit code.

    The ``runner`` and ``census_reader`` seams default to the real
    ``subprocess`` execution and the real ``urllib`` census read; tests replace
    both so the whole interface can be exercised without a server, a kernel or a
    child process.
    """
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        return _main(arguments, runner=runner, census_reader=census_reader, env=env)
    except PairCliError as error:
        return _refuse(
            error.code,
            str(error),
            error.exit_code,
            stage=error.stage,
            notebook_path=error.notebook_path,
        )


if __name__ == "__main__":
    raise SystemExit(main())
