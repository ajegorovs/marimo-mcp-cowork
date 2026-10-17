"""Independent grading for a finished restricted-comparison arm.

Grades ONE staged arm from its manifest and its on-disk artifacts alone. It
never reads the agent's self-report and never trusts the setup process's memory:

1. re-hash the canonical fixture against the manifest ``sha256``;
2. inspect the *saved* notebook bytes for the mission's ``normalized`` cell;
3. boot a FRESH loopback server on the same notebook (touching nothing the
   setup owns), create a fresh session, prove the identity rotated with
   ``session_guard.validate_reopen``, run every cell, and read ``gain`` and
   ``normalized`` straight from the kernel;
4. compare the kernel's ``normalized`` with ``oracle.expected_metric`` and
   require a COMPLETED run-all with zero cell errors.

The verdict is a JSON object with a ``checks`` list (``name`` / ``pass`` /
``detail``), an overall ``pass`` flag and the ``observed`` values. A failed
check always makes the process exit non-zero, and a partial read is never
reported as a pass: a kernel read whose producer reports anything other than a
completed run — the package's ``run_cell`` reports ``partial`` when a cell
failed, never ran, or raised — fails the check that carries the read's status. A
missing or failing oracle is likewise a failed check, not an uncaught exception
— a JSON verdict is always printed.

Anchor verification
-------------------
The manifest is an anchor the arm itself can reach, so it is not trustworthy on
its own. ``--control-dir`` names the directory holding the setup's control copy
(``anchor.json``): the same identity fields plus the SHA-256 of the manifest
file's bytes, written where the arm was never pointed. Supplying it makes
grading refuse, before any probe, unless that copy exists, its recorded manifest
digest still matches the manifest file's current bytes, and every identity field
it carries equals the manifest's (``notebook_path`` compared on resolved paths,
``process_id`` as an int). Without ``--control-dir`` the caller must pass
``--allow-unverified-anchor`` to grade at all, and the verdict then records
``observed["anchor"] == "unverified"``.

Design seam
-----------
:func:`grade` takes an injectable ``prober`` exposing ``probe(path) ->
ProbeResult``. :class:`ArmProber` is the live implementation; a test substitutes
a fake and the whole verdict logic runs with no server.

Reader follows the arm
----------------------
Grading must read the kernel under the SAME marimo version the arm ran under.
The ``native`` arm drives the agent through the ``marimo pair execute`` CLI, so
its environment is a marimo release that has that subcommand; the ``wrapper``
arm runs this repository's package against a marimo release that may have no
``pair execute`` at all. A grader that always shelled out to ``pair execute``
would therefore fail a wrapper arm whose mission actually succeeded — the check
would be measuring the grader's tool, not the arm's work.

So ``--reader`` defaults to the manifest's ``arm`` value and the selected reader
uses that arm's marimo. The two readers differ only in HOW the kernel is read:
:class:`ArmProber` shells out to ``pair execute`` (unchanged behaviour), while
:class:`WrapperProber` boots the same fresh process and then drives
``MarimoClient`` and the package's ``run_cell`` / ``get_variables`` /
``get_errors`` handlers in-process against the session id and server url the
grader itself created. Independence is unchanged and comes from elsewhere — the
grader boots its own fresh process and session, re-hashes the canonical fixture,
and compares against :mod:`oracle`'s expectation instead of the agent's
self-report — never from using a different tool than the arm.

Both readers resolve to the same shape — the kernel's ``gain``, its
``normalized`` value, its structured cell-error count and the status of the
run-all it performed — so the verdict composition and every check are identical
whichever reader ran. The wrapper reader's package imports live inside the
coroutine that uses them, so this module and its tests still load when
``marimo_inspection`` is not installed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CHECK_NAMES",
    "CONTROL_FILENAME",
    "CONTROL_IDENTITY_KEYS",
    "READERS",
    "RUN_OK_STATUSES",
    "ArmProber",
    "GradeRefusal",
    "ProbeResult",
    "WrapperProber",
    "build_server_command",
    "grade",
    "main",
    "make_prober",
    "native_kernel_values",
    "native_run_status",
    "pick_free_port",
    "probe_result",
    "resolve_reader",
    "sha256_file",
    "verify_control_anchor",
    "wrapper_kernel_values",
    "wrapper_run_status",
]

# The kernel readers. ``native`` shells out to ``marimo pair execute``;
# ``wrapper`` drives the repository package in-process. The reader follows the
# arm's marimo version (see the module docstring).
READERS = ("native", "wrapper")

# The verdict always carries exactly these checks, in this order.
CHECK_NAMES = (
    "canonical_fixture_unchanged",
    "normalized_cell_saved",
    "reopen_rotated_identity",
    "kernel_normalized_matches_oracle",
    "kernel_gain_is_default",
    "kernel_zero_cell_errors",
)

# A read only counts as a completed run when its producer says the run finished
# normally: the package's ``run_cell`` reports ``ok`` when every requested cell
# is idle (``partial`` when any failed, never ran, or reported an execution
# error, and ``error`` refuses the probe outright), and the native ``pair
# execute`` envelope claims ``success`` — mapped to ``ok`` below. Any other
# status fails the check that carries it: a partial run is never a pass.
RUN_OK_STATUSES = ("ok", "success")

# The control copy written by ``mission_setup --control-dir``: the file name and
# the identity fields this grader compares against the manifest.
CONTROL_FILENAME = "anchor.json"
CONTROL_IDENTITY_KEYS = (
    "server_url",
    "session_id",
    "notebook_path",
    "process_id",
    "sha256",
    "arm",
)

DEFAULT_EXPECTED_GAIN = 2

# The saved notebook must carry a ``normalized`` definition; the pristine
# fixture deliberately does not (see ``oracle.verify_fixture``). The check is a
# byte substring, never a decoded-text one.
NORMALIZED_MARKER = b"normalized"

_RUN_ALL_CELLS = (
    "import marimo._code_mode as cm\n"
    "async with cm.get_context() as ctx:\n"
    "    for cell in ctx.cells.values():\n"
    "        ctx.run_cell(cell.id)"
)
_READ_VALUES = (
    "import marimo._code_mode as cm\n"
    "async with cm.get_context() as ctx:\n"
    '    print("gain", ctx.globals["gain"].value)\n'
    '    print("normalized", ctx.globals.get("normalized"))\n'
    '    print("errors", sum(len(c.errors) for c in ctx.cells.values()))'
)


class GradeRefusal(RuntimeError):
    """A grading precondition failed; no verdict can be produced from it."""


def _load_sibling(name: str, filename: str):
    """Load a sibling module by path, cached in ``sys.modules`` under *name*."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / filename
    )
    if spec is None or spec.loader is None:
        raise GradeRefusal(f"cannot load sibling module {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _guard():
    """The pure session guards (``benchmark/session_guard.py``)."""
    return _load_sibling("_mission_session_guard", "session_guard.py")


def _oracle():
    """The independent numeric expectation (``benchmark/oracle.py``)."""
    return _load_sibling("_mission_oracle", "oracle.py")


def pick_free_port() -> int:
    """Return a free loopback port for the grader's own fresh server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_server_command(python: str, notebook, port: int) -> list[str]:
    """The headless loopback ``marimo edit`` argv for the grader's fresh boot.

    Deliberately duplicated from :mod:`mission_setup` rather than imported, so
    grading never depends on the setup program it is validating.
    """
    return [
        str(python),
        "-m",
        "marimo",
        "edit",
        str(notebook),
        "--headless",
        "--no-token",
        "--no-skew-protection",
        "--skip-update-check",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def sha256_file(path) -> str:
    """The SHA-256 hex digest of *path*'s bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _measured_value(payload: dict, label: str) -> float:
    """Require exactly one ``label <value>`` result line, not a traceback hit."""
    lines = str(payload.get("stdout", "")).splitlines()
    matches = [line for line in lines if line.startswith(label + " ")]
    if len(matches) != 1:
        raise GradeRefusal(
            f"expected exactly one {label!r} result line, got {matches!r}"
        )
    return float(matches[0].removeprefix(label + " "))


def native_kernel_values(payload: dict) -> tuple[float, float, int]:
    """Map a ``pair execute`` JSON payload to ``(gain, normalized, errors)``.

    The shared shape both readers produce, so verdict composition never depends
    on which reader ran.
    """
    return (
        _measured_value(payload, "gain"),
        _measured_value(payload, "normalized"),
        int(_measured_value(payload, "errors")),
    )


def native_run_status(payload: dict) -> str:
    """The run status a ``pair execute`` payload implies.

    The native envelope reports a per-run ``status`` when the CLI supplies one
    (honoured verbatim); otherwise it claims ``success``, which maps to ``ok``.
    A payload that does not claim success is mapped to ``"partial"`` — never to
    a completed status — so the check that carries it fails closed. (In the live
    reader a non-success envelope is refused outright by ``_execute`` before it
    reaches this mapping, so a native probe that returns values always reports a
    completed status; the kernel's own error count remains the native reader's
    independent failure signal.)
    """
    status = payload.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    return "ok" if payload.get("success") else "partial"


def _serialized_number(node: object, label: str) -> float:
    """Coerce a package-serialized value to ``float``, or refuse.

    ``get_variables`` reports each value as ``{"value": ..., "datatype": ...}``
    and serializes a UI element's ``.value`` one level deeper, so the walk
    descends every ``value`` mapping before parsing the leaf.
    """
    walked = node
    while isinstance(walked, dict) and "value" in walked:
        walked = walked["value"]
    try:
        return float(walked)
    except (TypeError, ValueError) as exc:
        raise GradeRefusal(f"kernel reported no numeric {label!r}: {node!r}") from exc


def wrapper_kernel_values(variables: dict, errors: dict) -> tuple[float, float, int]:
    """Map the package's ``get_variables`` / ``get_errors`` dicts to the shape.

    Raises:
        GradeRefusal: If either tool payload is an error or carries no readable
            value, so the caller reports a failed probe rather than a partial
            pass.
    """
    if variables.get("error"):
        raise GradeRefusal(f"get_variables failed: {variables.get('error')}")
    if errors.get("error"):
        raise GradeRefusal(f"get_errors failed: {errors.get('error')}")
    found = variables.get("variables") or {}
    for name in ("gain", "normalized"):
        if name not in found:
            raise GradeRefusal(f"kernel reported no {name!r} variable")
    gain = _serialized_number(found["gain"], "gain")
    normalized = _serialized_number(found["normalized"], "normalized")
    total = errors.get("total_errors")
    if total is None:
        raise GradeRefusal("get_errors reported no total_errors")
    return (gain, normalized, int(total))


def wrapper_run_status(run: object) -> str:
    """The status a package ``run_cell`` payload reports, or refuse.

    ``run_cell`` reports ``ok`` only when every requested cell is idle — no
    failures, nothing left unrun, no execution error; ``partial`` means at least
    one failed or did not finish. The status is returned verbatim so the verdict
    can record it, and a payload that reports no status at all is refused rather
    than assumed complete.

    Raises:
        GradeRefusal: If *run* is not a mapping or carries no status string.
    """
    if not isinstance(run, dict):
        raise GradeRefusal(f"run_cell returned no payload: {run!r}")
    status = run.get("status")
    if not isinstance(status, str) or not status.strip():
        raise GradeRefusal(f"run_cell reported no status: {str(run)[:500]}")
    return status.strip()


def _stop_process(process: subprocess.Popen) -> None:
    """Terminate and reap only the process the grader started itself."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@dataclass
class ProbeResult:
    """One independent read of a finished arm's kernel.

    Attributes:
        session_id: The fresh session id created for the read.
        process_id: The fresh server's process id.
        notebook_path: The notebook the fresh server opened.
        gain: The kernel's ``gain`` value, or None if it was not read.
        normalized: The kernel's ``normalized`` value, or None.
        error_count: Total cell errors reported by the kernel, or None.
        run_status: The status the read's producer reported for the run-all
            (``ok`` / ``success`` = a completed run; ``partial`` = a target
            failed, never ran, or reported an execution error). Required: a
            reader that cannot say whether the run completed must not report a
            pass, so the check that carries this fails for any status outside
            :data:`RUN_OK_STATUSES`.
    """

    session_id: str
    process_id: int
    notebook_path: str
    gain: float | None
    normalized: float | None
    error_count: int | None
    run_status: str


def probe_result(
    *,
    session_id: str,
    process_id: int,
    notebook_path: str,
    values: tuple[float, float, int],
    run_status: str,
) -> ProbeResult:
    """Build the one :class:`ProbeResult` shape both readers produce."""
    gain, normalized, error_count = values
    return ProbeResult(
        session_id=session_id,
        process_id=process_id,
        notebook_path=notebook_path,
        gain=gain,
        normalized=normalized,
        error_count=error_count,
        run_status=run_status,
    )


class ArmProber:
    """Boots a FRESH loopback server on the arm notebook and reads the kernel.

    The native reader: it shells out to ``marimo pair execute`` in the arm's
    interpreter. It never touches the setup's server and stops only the process
    it starts itself.
    """

    READY_TIMEOUT_S = 30.0
    HANDLE_TIMEOUT_S = 15.0
    EXEC_TIMEOUT_S = 60.0

    def __init__(self, python: str, state_dir):
        self.python = str(python)
        self.state_dir = Path(state_dir)

    def probe(self, notebook_path: str) -> ProbeResult:
        """Boot a fresh server, prove it up, run cells and read the values."""
        notebook = Path(notebook_path)
        if not notebook.is_file():
            raise GradeRefusal(f"notebook does not exist: {notebook}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["USERPROFILE"] = str(self.state_dir)
        env["XDG_STATE_HOME"] = str(self.state_dir)
        port = pick_free_port()
        process = subprocess.Popen(
            build_server_command(self.python, notebook, port),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = int(process.pid)
        try:
            url = self._await_ready(process, port)
            session_id = self._create_session(url, notebook)
            values, run_status = self._read_kernel(env, url, notebook, session_id)
        finally:
            _stop_process(process)
        return probe_result(
            session_id=session_id,
            process_id=pid,
            notebook_path=str(notebook),
            values=values,
            run_status=run_status,
        )

    def _read_kernel(
        self, env: dict, url: str, notebook: Path, session_id: str
    ) -> tuple[tuple[float, float, int], str]:
        """Native read: run every cell, then read values via ``pair execute``.

        Returns ``(values, run_status)``: the status is taken from the RUN-ALL
        envelope (what the run itself reported), the values from the read
        envelope. A run-all envelope that does not claim a completed run fails
        the check that carries the status even when the values look healthy.
        """
        run_payload = self._execute(env, url, notebook, _RUN_ALL_CELLS)
        payload = self._execute(env, url, notebook, _READ_VALUES)
        return native_kernel_values(payload), native_run_status(run_payload)

    def _await_ready(self, process: subprocess.Popen, port: int) -> str:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise GradeRefusal(
                    f"fresh server exited early with {process.returncode}"
                )
            try:
                with urllib.request.urlopen(
                    url + "/api/version", timeout=1
                ) as response:
                    if response.status == 200:
                        return url
            except OSError:
                time.sleep(0.1)
        raise GradeRefusal("fresh server readiness timeout")

    def _create_session(self, url: str, notebook: Path) -> str:
        session_id = str(uuid.uuid4())
        params = urllib.parse.urlencode(
            {"session_id": session_id, "file": str(notebook)}
        )
        with urllib.request.urlopen(
            f"{url}/sse?{params}", timeout=self.HANDLE_TIMEOUT_S
        ) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    break
            else:
                raise GradeRefusal("fresh session handshake produced no kernel-ready")
        return session_id

    def _execute(self, env: dict, url: str, notebook: Path, code: str) -> dict:
        command = [
            self.python,
            "-m",
            "marimo",
            "pair",
            "execute",
            "--url",
            url,
            "--file",
            str(notebook),
            "-c",
            code,
        ]
        process = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.EXEC_TIMEOUT_S,
            check=False,
        )
        try:
            data = json.loads(process.stdout)
        except ValueError as exc:
            raise GradeRefusal(
                f"pair execute produced no JSON (stderr: {process.stderr[-500:]})"
            ) from exc
        if process.returncode or not data.get("success"):
            raise GradeRefusal(f"pair execute failed: {str(data)[:500]}")
        return data


async def _wrapper_read(url: str, session_id: str) -> tuple[dict, dict, str]:
    """Read the live kernel through the repository package (async).

    The package imports live inside this coroutine body, so this module and its
    tests load even when ``marimo_inspection`` is not installed: the wrapper
    path is only importable while it is actually being used. ``run_cell`` with
    ``mode="all"`` runs every document cell (the package's equivalent of the
    native reader's run-all snippet); the values, the run status and the error
    count then come from the package's own handlers, against the fresh session
    id and server url the grader created. The status is returned, never
    swallowed: ``partial`` reaches the verdict as a failed check.
    """
    from marimo_inspection.client import MarimoClient
    from marimo_inspection.tools.errors import get_errors
    from marimo_inspection.tools.mutation import run_cell
    from marimo_inspection.tools.variables import get_variables

    # Confirm over the wire that the fresh session this grader created is the
    # one being read — never the arm's setup session.
    listed = {session.session_id for session in await MarimoClient(url).list_sessions()}
    if session_id not in listed:
        raise GradeRefusal(
            f"fresh session {session_id!r} is not served by {url}:"
            f" listed {sorted(listed)}"
        )

    run = await run_cell(mode="all", session_id=session_id, server_url=url)
    if isinstance(run, dict) and run.get("status") == "error":
        raise GradeRefusal(f"run_cell failed: {run.get('reason') or run.get('error')}")
    run_status = wrapper_run_status(run)
    variables = await get_variables(
        session_id=session_id,
        variable_names=["gain", "normalized"],
        server_url=url,
    )
    errors = await get_errors(session_id=session_id, server_url=url)
    return variables, errors, run_status


class WrapperProber(ArmProber):
    """Boots the same fresh server, then reads the kernel IN-PROCESS.

    The wrapper arm ran this repository's package against a marimo release that
    may have no ``pair execute`` subcommand, so this reader drives
    ``MarimoClient`` and the package's ``run_cell`` / ``get_variables`` /
    ``get_errors`` handlers over the fresh session the grader itself created —
    never the arm's session. Boot mechanics are inherited unchanged, so only the
    read step differs from the native reader.
    """

    def _read_kernel(
        self, env: dict, url: str, notebook: Path, session_id: str
    ) -> tuple[tuple[float, float, int], str]:
        """Wrapper read: run every cell, then read values via the package.

        The package's ``run_cell`` status travels back with the values, so a
        ``partial`` run-all — a cell that failed or never finished — reaches the
        verdict as a failed check instead of a pass.
        """
        variables, errors, run_status = asyncio.run(_wrapper_read(url, session_id))
        return wrapper_kernel_values(variables, errors), run_status


def make_prober(reader: str, python: str, state_dir) -> object:
    """Construct the live prober for *reader*.

    :class:`ArmProber` is resolved at call time so a test can replace it on the
    module and intercept the native default without launching anything.
    """
    if reader == "wrapper":
        return WrapperProber(python, state_dir)
    if reader == "native":
        return ArmProber(python, state_dir)
    raise GradeRefusal(f"reader must be one of {READERS}, got {reader!r}")


def resolve_reader(requested: str | None, manifest: dict) -> str:
    """Pick the kernel reader: an explicit request, else the manifest's arm.

    Both readers boot their OWN fresh server; the choice only selects HOW the
    kernel is read, which must follow the marimo version the arm ran under (see
    the module docstring).

    Raises:
        GradeRefusal: If the requested reader or the manifest arm is missing or
            unknown — an unknown reader must never silently fall back.
    """
    if requested is not None:
        if requested not in READERS:
            raise GradeRefusal(f"reader must be one of {READERS}, got {requested!r}")
        return requested
    arm = manifest.get("arm")
    if arm not in READERS:
        raise GradeRefusal(f"manifest arm must be one of {READERS}, got {arm!r}")
    return arm


def _resolved_path(value: object) -> str:
    """The resolved spelling of a manifest path value, or refuse.

    Raises:
        GradeRefusal: If *value* is not a non-empty string.
    """
    if not isinstance(value, str) or not value.strip():
        raise GradeRefusal(f"anchor path must be a non-empty string, got {value!r}")
    return str(Path(value).resolve())


def verify_control_anchor(
    control_dir: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    manifest: dict,
) -> dict:
    """Require the control copy to pin the manifest, or refuse to grade.

    The manifest is an anchor the arm itself can rewrite, so a control copy the
    arm was never pointed at is the only thing that makes it trustworthy. Three
    comparisons, all before any probe runs:

    1. the control copy exists and is a JSON object;
    2. its ``manifest_sha256`` equals the SHA-256 of the manifest FILE's current
       bytes — a rewritten manifest fails even if its identity fields are intact;
    3. every identity field it carries equals the manifest's: ``notebook_path``
       on resolved paths (identity is the file, not the spelling),
       ``process_id`` as an int, the rest verbatim.

    This is detection, not prevention: it holds only if the caller placed
    *control_dir* where the arm cannot write.

    Args:
        control_dir: The directory holding the setup's ``anchor.json``.
        manifest_path: The manifest file the caller read *manifest* from.
        manifest: The manifest mapping being graded.

    Returns:
        The parsed control copy.

    Raises:
        GradeRefusal: On any of the three failures above.
    """
    path = Path(control_dir) / CONTROL_FILENAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GradeRefusal(f"control anchor is missing: {path} ({exc})") from exc
    try:
        anchor = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GradeRefusal(f"control anchor is not readable JSON: {path}") from exc
    if not isinstance(anchor, dict):
        raise GradeRefusal(f"control anchor must be a JSON object: {path}")

    recorded_sha = anchor.get("manifest_sha256")
    if not isinstance(recorded_sha, str) or not recorded_sha.strip():
        raise GradeRefusal(f"control anchor carries no manifest_sha256: {path}")
    try:
        actual_sha = sha256_file(manifest_path)
    except OSError as exc:
        raise GradeRefusal(f"cannot re-read the manifest: {exc}") from exc
    if actual_sha != recorded_sha:
        raise GradeRefusal(
            "manifest bytes do not match the control anchor:"
            f" {actual_sha} != {recorded_sha}"
        )

    for key in CONTROL_IDENTITY_KEYS:
        if key not in anchor:
            raise GradeRefusal(f"control anchor carries no {key!r}: {path}")
        if key not in manifest:
            raise GradeRefusal(f"manifest carries no {key!r} to compare against")
        if key == "notebook_path":
            matches = _resolved_path(anchor[key]) == _resolved_path(manifest[key])
        elif key == "process_id":
            try:
                matches = int(anchor[key]) == int(manifest[key])
            except (TypeError, ValueError) as exc:
                raise GradeRefusal(
                    f"control anchor process_id is not an int: {anchor[key]!r}"
                ) from exc
        else:
            matches = anchor[key] == manifest[key]
        if not matches:
            raise GradeRefusal(
                f"control anchor {key} mismatch: {anchor[key]!r} != manifest"
                f" {manifest[key]!r}"
            )
    return anchor


def grade(
    manifest: dict,
    prober: object,
    *,
    expected_gain: int = DEFAULT_EXPECTED_GAIN,
    anchor_verified: bool = False,
) -> dict:
    """Compose the independent verdict for one finished arm.

    Args:
        manifest: The setup manifest mapping (see :mod:`mission_setup`).
        prober: An object with ``probe(notebook_path) -> ProbeResult``.
        expected_gain: The fixture's default slider value.
        anchor_verified: Whether the caller verified a control copy the arm
            cannot reach (see :func:`verify_control_anchor`). Recorded in the
            verdict, so an unverified run can never be read as a verified one.

    Returns:
        ``{"pass": bool, "checks": [{"name", "pass", "detail"}, ...],
        "observed": {...}}``. ``pass`` is true only when every check passed and
        the full check set is present — a partial read never passes.
    """
    observed: dict = {}
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    # 1. Canonical fixture unchanged: re-hash against the manifest digest.
    canonical_path = manifest.get("canonical_path")
    expected_sha = manifest.get("sha256")
    if not canonical_path:
        add("canonical_fixture_unchanged", False, "manifest carries no canonical_path")
    else:
        try:
            actual_sha = sha256_file(canonical_path)
        except OSError as exc:
            add("canonical_fixture_unchanged", False, f"cannot read canonical: {exc}")
        else:
            observed["canonical_sha256"] = actual_sha
            observed["manifest_sha256"] = expected_sha
            if actual_sha == expected_sha:
                add(
                    "canonical_fixture_unchanged",
                    True,
                    f"canonical matches {actual_sha}",
                )
            else:
                add(
                    "canonical_fixture_unchanged",
                    False,
                    f"canonical drifted: {actual_sha} != {expected_sha}",
                )

    # 2. The SAVED notebook carries a normalized cell (byte substring).
    notebook_path = manifest.get("notebook_path")
    if not notebook_path:
        add("normalized_cell_saved", False, "manifest carries no notebook_path")
    else:
        try:
            raw = Path(notebook_path).read_bytes()
        except OSError as exc:
            add("normalized_cell_saved", False, f"cannot read notebook: {exc}")
        else:
            present = NORMALIZED_MARKER in raw
            observed["normalized_marker_present"] = present
            add(
                "normalized_cell_saved",
                present,
                "saved notebook bytes carry a normalized cell"
                if present
                else "saved notebook bytes carry no normalized cell",
            )

    # 3. Fresh boot + rotated identity + kernel reads.
    previous = None
    if (
        notebook_path
        and manifest.get("session_id") is not None
        and manifest.get("process_id") is not None
    ):
        previous = _guard().SessionAnchor(
            str(manifest["session_id"]),
            str(notebook_path),
            int(manifest["process_id"]),
        )

    probe_error = "manifest carries no session anchor" if previous is None else None
    result: ProbeResult | None = None
    if previous is not None:
        try:
            result = prober.probe(str(notebook_path))
        except Exception as exc:  # noqa: BLE001 - any probe failure is a failed check
            probe_error = f"fresh boot failed: {exc}"

    if result is not None:
        observed["fresh_process_id"] = result.process_id
        observed["fresh_session_id"] = result.session_id
        observed["gain"] = result.gain
        observed["normalized"] = result.normalized
        observed["cell_errors"] = result.error_count

    # 3a. Reopen rotated both ids while keeping the notebook.
    if result is None:
        add("reopen_rotated_identity", False, probe_error or "no fresh anchor")
    else:
        try:
            current = _guard().SessionAnchor(
                str(result.session_id),
                str(result.notebook_path),
                int(result.process_id),
            )
            _guard().validate_reopen(previous, current)
        except ValueError as exc:
            add("reopen_rotated_identity", False, str(exc))
        else:
            add(
                "reopen_rotated_identity",
                True,
                "fresh process and session id, same notebook",
            )

    # 3b. Kernel normalized matches the independent expectation. The oracle is
    # looked up INSIDE the guard: a missing or failing oracle must still produce
    # a JSON verdict with a failed check, never an uncaught exception.
    target = None
    oracle_error: str | None = None
    try:
        target = _oracle().expected_metric(expected_gain)
    except Exception as exc:  # noqa: BLE001 - any oracle failure is a failed check
        oracle_error = f"oracle unavailable: {exc}"
    if target is not None:
        observed["expected_metric"] = target
    if oracle_error is not None:
        add("kernel_normalized_matches_oracle", False, oracle_error)
    elif result is None or result.normalized is None:
        add(
            "kernel_normalized_matches_oracle",
            False,
            probe_error or "kernel reported no normalized value",
        )
    elif result.normalized == target:
        add(
            "kernel_normalized_matches_oracle",
            True,
            f"kernel normalized {result.normalized} == oracle {target}",
        )
    else:
        add(
            "kernel_normalized_matches_oracle",
            False,
            f"kernel normalized {result.normalized} != oracle {target}",
        )

    # 3c. The reopened kernel shows the fixture's default gain.
    if result is None or result.gain is None:
        add(
            "kernel_gain_is_default",
            False,
            probe_error or "kernel reported no gain value",
        )
    elif result.gain == expected_gain:
        add("kernel_gain_is_default", True, f"gain {result.gain} == default")
    else:
        add(
            "kernel_gain_is_default",
            False,
            f"gain {result.gain} != default {expected_gain}",
        )

    # 3d. A completed run with zero cell errors. The producer's run status is
    # checked FIRST: a partial run-all means a target failed or never finished,
    # so it is a failed check even when the structured error count reads zero.
    if result is None or result.error_count is None:
        add(
            "kernel_zero_cell_errors",
            False,
            probe_error or "kernel reported no error count",
        )
    elif result.run_status not in RUN_OK_STATUSES:
        add(
            "kernel_zero_cell_errors",
            False,
            f"run status {result.run_status!r} is not a completed run (completed ="
            f" {list(RUN_OK_STATUSES)}): {result.error_count} cell error(s) reported",
        )
    elif result.error_count == 0:
        add(
            "kernel_zero_cell_errors",
            True,
            f"no cell errors (run status: {result.run_status})",
        )
    else:
        add(
            "kernel_zero_cell_errors",
            False,
            f"{result.error_count} cell error(s) present (run status:"
            f" {result.run_status})",
        )

    complete = len(checks) == len(CHECK_NAMES) and {c["name"] for c in checks} == set(
        CHECK_NAMES
    )
    passed = complete and all(check["pass"] for check in checks)
    observed["anchor"] = "control_verified" if anchor_verified else "unverified"
    return {"pass": passed, "checks": checks, "observed": observed}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade one finished comparison arm from its manifest."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--state-dir", dest="state_dir", default=None)
    parser.add_argument(
        "--reader",
        choices=list(READERS),
        default=None,
        help=(
            "how to read the fresh kernel: 'native' shells out to"
            " 'marimo pair execute', 'wrapper' drives the repository package"
            " in-process. Defaults to the manifest's arm value, so the read"
            " uses the same marimo version the arm ran under."
        ),
    )
    parser.add_argument(
        "--control-dir",
        dest="control_dir",
        default=None,
        help=(
            "the directory holding the setup's control copy (anchor.json) —"
            " the identity fields plus the SHA-256 of the manifest file's"
            " bytes, written where the arm was never pointed. Grading refuses"
            " unless that copy exists, still matches the manifest bytes, and"
            " agrees with every identity field of the manifest."
        ),
    )
    parser.add_argument(
        "--allow-unverified-anchor",
        dest="allow_unverified_anchor",
        action="store_true",
        help=(
            "grade without --control-dir. The manifest is then the only anchor"
            " and the arm can rewrite it, so the verdict records"
            " observed.anchor = 'unverified'."
        ),
    )
    return parser.parse_args(argv)


def _default_state_dir() -> Path:
    """A fresh isolated state dir when the caller supplies none."""
    return Path(tempfile.mkdtemp(prefix="marimo-grade-state-"))


def _refuse(code: str, detail: str) -> int:
    """Print a JSON error to stderr and return the non-zero exit code."""
    print(json.dumps({"error": code, "detail": detail}), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: grade one arm and exit non-zero on any failed check."""
    args = _parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _refuse("manifest_unreadable", str(exc))
    try:
        reader = resolve_reader(args.reader, manifest)
    except GradeRefusal as exc:
        return _refuse("reader_refused", str(exc))

    # The anchor comes first, before any probe: the manifest is reachable by the
    # arm, so it is only an anchor when a control copy the arm cannot reach still
    # pins it — otherwise the caller must say so explicitly.
    anchor_verified = False
    if args.control_dir:
        try:
            verify_control_anchor(args.control_dir, args.manifest, manifest)
        except GradeRefusal as exc:
            return _refuse("anchor_refused", str(exc))
        anchor_verified = True
    elif not args.allow_unverified_anchor:
        return _refuse(
            "anchor_unverified",
            "grading without --control-dir needs --allow-unverified-anchor: the"
            " manifest would be the only anchor, and the graded arm can rewrite"
            " the manifest it reads",
        )

    state_dir = args.state_dir or _default_state_dir()
    prober = make_prober(reader, args.python, state_dir)
    verdict = grade(manifest, prober, anchor_verified=anchor_verified)
    verdict["observed"]["reader"] = reader
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
