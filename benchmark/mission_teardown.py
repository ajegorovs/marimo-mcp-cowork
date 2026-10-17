"""Fail-safe, idempotent teardown for one finished restricted-comparison arm.

``benchmark/mission_setup.py`` deliberately leaves its single loopback marimo
server running while the mission runs against the live session. Nothing in the
toolkit stopped that server afterwards, so a finished arm leaked the process.
This program is the missing stop: it reads the setup manifest, proves the
recorded target is still the one the manifest names, stops it, and reports what
it did.

Contract
--------
* **Fail-safe** — it terminates only when the census is reachable, still maps the
  anchored session id to the manifest notebook, and the recorded pid is alive
  with a command line that names that notebook. Anything else refuses with an
  explicit exit code and touches no process.
* **Scoped** — it never kills by image name and never touches a pid the manifest
  does not name. Child pids are only reached through the recorded process's own
  tree, and each snapshotted child pid is re-proved against *live* state at the
  instant before it is signalled: its parent chain must still climb through live
  processes to the recorded manifest pid. An interpreter path difference is
  normal — marimo boots the kernel under the base interpreter while the recorded
  parent is the venv shim — so executables are never compared. Children are
  signalled deepest-first, so an intermediate process is still alive when its
  own children are disposed of. A pid that fails the re-check is skipped and
  reported, never signalled.
* **Idempotent** — a server that is already gone (census unreachable *and* the
  recorded process gone) is success, not an error.
* **Honest** — it reports one JSON object carrying ``status``, ``stage``,
  ``code``, ``killed_pid``, ``children``, the skipped child pids with their
  reasons, the final ``reachable`` boolean and whether it had to escalate.

Design seam
-----------
Every OS-level action — process lookup, terminate, kill, census read, and the
bounded wait — sits behind :class:`SystemOps`'s method set. :func:`teardown`
takes an injectable ``ops``, so the whole decision path runs in tests with no
process, no server and no network.

``psutil`` is used only where the stdlib cannot do the job: enumerating and
reaping the recorded process's child tree. marimo boots its kernel as a child
process, and on Windows ``Popen.terminate`` reaches only the parent, so the
child would otherwise survive as an orphan. This is the same role psutil
already plays in ``benchmark/native_gate.py``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import psutil

__all__ = [
    "CODE_CENSUS_MISMATCH",
    "CODE_CENSUS_UNREACHABLE_PROCESS_ALIVE",
    "CODE_INTERNAL_FAILURE",
    "CODE_MANIFEST_INVALID",
    "CODE_NOTEBOOK_UNRESOLVED",
    "CODE_OK",
    "CODE_PROCESS_GONE_SERVER_LIVE",
    "CODE_SHA_MISMATCH",
    "CODE_TARGET_UNVERIFIED",
    "CODE_TEARDOWN_FAILED",
    "KILL_GRACE_S",
    "REFUSAL_CODES",
    "REQUIRED_KEYS",
    "SKIP_GONE",
    "SKIP_UNCONFIRMED",
    "TERMINATE_GRACE_S",
    "ProcessInfo",
    "SystemOps",
    "TeardownError",
    "TeardownRefusal",
    "TeardownReport",
    "cmdline_references",
    "main",
    "require",
    "resolve_notebook",
    "revalidate_child",
    "teardown",
    "validate_manifest",
]

# The manifest keys teardown must find to identify its target and prove it.
# ``mission_setup.MANIFEST_KEYS`` is a superset; this list is deliberately
# duplicated here so teardown never imports the setup program it is cleaning up
# after (the same independence ``mission_grade`` keeps).
REQUIRED_KEYS = (
    "server_url",
    "session_id",
    "notebook_path",
    "process_id",
    "sha256",
    "root",
)

# Refusal classes, each with its own exit code. 0 is the only success code.
CODE_OK = 0
CODE_MANIFEST_INVALID = 2
CODE_NOTEBOOK_UNRESOLVED = 3
CODE_SHA_MISMATCH = 4
CODE_CENSUS_UNREACHABLE_PROCESS_ALIVE = 5
CODE_CENSUS_MISMATCH = 6
CODE_PROCESS_GONE_SERVER_LIVE = 7
CODE_TARGET_UNVERIFIED = 8
CODE_TEARDOWN_FAILED = 9
CODE_INTERNAL_FAILURE = 10

REFUSAL_CODES = {
    "manifest_invalid": CODE_MANIFEST_INVALID,
    "notebook_unresolved": CODE_NOTEBOOK_UNRESOLVED,
    "sha_mismatch": CODE_SHA_MISMATCH,
    "census_unreachable_process_alive": CODE_CENSUS_UNREACHABLE_PROCESS_ALIVE,
    "census_mismatch": CODE_CENSUS_MISMATCH,
    "process_gone_server_live": CODE_PROCESS_GONE_SERVER_LIVE,
    "target_unverified": CODE_TARGET_UNVERIFIED,
    "teardown_failed": CODE_TEARDOWN_FAILED,
    "internal_failure": CODE_INTERNAL_FAILURE,
}

TERMINATE_GRACE_S = 5.0
KILL_GRACE_S = 5.0

# Re-verification outcomes for a snapshotted child pid. Eligibility is the live
# parent chain still reaching the recorded process — never an executable
# comparison — and a child that fails it is never signalled; these reason
# strings travel in the report's ``skipped`` entries.
SKIP_GONE = "gone"
SKIP_UNCONFIRMED = "unconfirmed"


class TeardownError(RuntimeError):
    """An internal invariant failed; no supported teardown outcome exists."""


class TeardownRefusal(TeardownError):
    """Teardown refused fail-closed; the process tree was left untouched.

    Attributes:
        report: The :class:`TeardownReport` describing the refusal, its stage
            and its exit code.
    """

    def __init__(self, report: TeardownReport):
        super().__init__(f"{report.stage}: {report.detail}")
        self.report = report


@dataclass(frozen=True)
class TeardownReport:
    """What teardown did (or refused to do), ready to print as JSON.

    Attributes:
        status: ``"ok"`` or ``"refused"`` (or ``"error"`` for an internal
            failure surfaced by :func:`main`).
        stage: The last stage reached — ``manifest`` / ``notebook`` / ``sha256``
            / ``census`` / ``identity`` / ``terminate`` / ``verified``.
        code: The process exit code; 0 only for a verified success.
        killed_pid: The recorded pid once termination was attempted, else None.
        children: The recorded process's child pids, when they were enumerated.
        reachable: Whether the server answered at the end of the run.
        detail: A human-readable account of the outcome.
        escalated: Whether a hard kill was needed after the bounded wait.
        skipped: Rejected child pids, each ``{"pid": int, "reason": str}`` —
            children that failed live-descendant re-verification and so were
            never signalled.
    """

    status: str
    stage: str
    code: int
    killed_pid: int | None
    children: list[int]
    reachable: bool
    detail: str
    escalated: bool = False
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        """The report as a plain JSON-serializable mapping."""
        return {
            "status": self.status,
            "stage": self.stage,
            "code": self.code,
            "killed_pid": self.killed_pid,
            "children": list(self.children),
            "skipped": [dict(entry) for entry in self.skipped],
            "reachable": self.reachable,
            "escalated": self.escalated,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProcessInfo:
    """A live process as observed by the OS seam.

    Attributes:
        pid: The process id.
        cmdline: The argument vector; empty when it could not be read.
        ppid: The parent process id, or None when it could not be read.
        exe: The executable path, or None when it could not be read. Recorded
            for diagnosis only — an interpreter path difference is normal and
            is never used as an eligibility gate.
    """

    pid: int
    cmdline: list[str]
    ppid: int | None = None
    exe: str | None = None


def require(condition: object, message: str) -> None:
    """Always-on check; a bare ``assert`` is stripped under ``python -O``."""
    if not condition:
        raise TeardownError(message)


def _load_sibling(name: str, filename: str):
    """Load a sibling module by path, cached in ``sys.modules`` under *name*."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / filename
    )
    if spec is None or spec.loader is None:
        raise TeardownError(f"cannot load sibling module {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _guard():
    """The pure session guards (``benchmark/session_guard.py``)."""
    return _load_sibling("_mission_session_guard", "session_guard.py")


def _refuse(
    stage: str,
    code: int,
    detail: str,
    *,
    reachable: bool = False,
    killed_pid: int | None = None,
    children: tuple[int, ...] | list[int] = (),
    escalated: bool = False,
    skipped: list[dict] | None = None,
) -> None:
    """Raise a fail-closed refusal carrying an explicit, known code."""
    require(
        code in REFUSAL_CODES.values() and code != CODE_OK,
        f"a refusal needs a known nonzero code, got {code!r}",
    )
    require(bool(stage) and bool(detail), "a refusal must name its stage and detail")
    raise TeardownRefusal(
        TeardownReport(
            "refused",
            stage,
            code,
            killed_pid,
            list(children),
            reachable,
            detail,
            escalated,
            [dict(entry) for entry in (skipped or [])],
        )
    )


def _ok(
    detail: str,
    *,
    killed_pid: int | None = None,
    children: tuple[int, ...] | list[int] = (),
    escalated: bool = False,
    skipped: list[dict] | None = None,
) -> TeardownReport:
    """A verified success; ``reachable`` is always false for one."""
    require(bool(detail), "a success must carry a detail")
    return TeardownReport(
        "ok",
        "verified",
        CODE_OK,
        killed_pid,
        list(children),
        False,
        detail,
        escalated,
        [dict(entry) for entry in (skipped or [])],
    )


def validate_manifest(manifest: object) -> None:
    """Require *manifest* to be an object carrying every key teardown needs.

    Raises:
        TeardownRefusal: With :data:`CODE_MANIFEST_INVALID` if the manifest is
            not a mapping, is missing a required key, carries a non-string
            where a non-empty string is required, or carries a non-integer
            ``process_id``.
    """
    if not isinstance(manifest, dict):
        _refuse(
            "manifest",
            CODE_MANIFEST_INVALID,
            "manifest must be a JSON object",
        )
    missing = [key for key in REQUIRED_KEYS if key not in manifest]
    if missing:
        _refuse(
            "manifest",
            CODE_MANIFEST_INVALID,
            f"manifest is missing required keys: {missing}",
        )
    for key in ("server_url", "session_id", "notebook_path", "sha256", "root"):
        value = manifest[key]
        if not isinstance(value, str) or not value.strip():
            _refuse(
                "manifest",
                CODE_MANIFEST_INVALID,
                f"manifest field {key} must be a non-empty string",
            )
    process_id = manifest["process_id"]
    if isinstance(process_id, bool) or not isinstance(process_id, int):
        _refuse(
            "manifest",
            CODE_MANIFEST_INVALID,
            "manifest field process_id must be an integer",
        )


def resolve_notebook(manifest: dict) -> Path:
    """Resolve the manifest notebook and require it to exist under the root.

    Raises:
        TeardownRefusal: With :data:`CODE_NOTEBOOK_UNRESOLVED` if the recorded
            notebook does not resolve to an existing file inside the manifest
            ``root``.
    """
    notebook = Path(str(manifest["notebook_path"]))
    root = Path(str(manifest["root"]))
    try:
        resolved_notebook = notebook.resolve()
        resolved_root = root.resolve()
    except OSError as exc:
        _refuse(
            "notebook",
            CODE_NOTEBOOK_UNRESOLVED,
            f"cannot resolve the recorded notebook path: {exc}",
        )
    if not resolved_notebook.is_file():
        _refuse(
            "notebook",
            CODE_NOTEBOOK_UNRESOLVED,
            f"recorded notebook does not resolve to an existing file: {notebook}",
        )
    if not resolved_notebook.is_relative_to(resolved_root):
        _refuse(
            "notebook",
            CODE_NOTEBOOK_UNRESOLVED,
            f"recorded notebook is not under the manifest root: {notebook}",
        )
    return resolved_notebook


# Characters that bound a whole path component in a command-line argument:
# a component starts after a separator or the start of the argument (or after a
# quote, since Windows quoting can wrap a token) and ends before a separator, a
# quote, or the end of the argument. A merely-similar path — one that only has
# the target as a prefix or suffix — fails one of those boundaries.
_PATH_SEPARATORS = frozenset({"/", "\\"})
_PATH_QUOTES = frozenset({"'", '"'})


def _has_path_component(argument: str, target: str) -> bool:
    """Whether *argument* (already normcased) carries *target* as a component.

    Every occurrence is tried; one that sits on a whole-component boundary
    (see :data:`_PATH_SEPARATORS` / :data:`_PATH_QUOTES`) is a match.
    """
    start = 0
    while True:
        index = argument.find(target, start)
        if index < 0:
            return False
        end = index + len(target)
        before_ok = index == 0 or argument[index - 1] in (
            _PATH_SEPARATORS | _PATH_QUOTES
        )
        after_ok = end == len(argument) or argument[end] in (
            _PATH_SEPARATORS | _PATH_QUOTES
        )
        if before_ok and after_ok:
            return True
        start = index + 1


def cmdline_references(cmdline: list[str], notebook: Path) -> bool:
    """Whether *cmdline* names *notebook* as a whole path component.

    Case is folded through :func:`os.path.normcase` (which also normalises the
    separator on Windows). The resolved notebook path must appear bounded by a
    separator / string start on the left and a separator / quote / end of
    argument on the right, so a token that merely contains the path as a
    substring — a longer sibling file, an unrelated prefix — no longer passes.
    A bare name never does — this is an identity check, not an image-name match.
    """
    target = os.path.normcase(str(notebook.resolve()))
    if not target:
        return False
    for arg in cmdline:
        if not isinstance(arg, str) or not arg:
            continue
        if _has_path_component(os.path.normcase(arg), target):
            return True
    return False


def _chain_reaches(
    ops: object,
    child_pid: int,
    lineage: frozenset[int],
    *,
    live_link: bool,
) -> tuple[int | None, str]:
    """Walk *child_pid*'s live parent chain until it reaches *lineage*.

    Args:
        ops: The OS seam; needs ``process_info``.
        child_pid: The snapshotted child pid to walk from.
        lineage: The pids that terminate a valid chain — the recorded manifest
            pid, plus (while escalating) every pid this run has already
            signalled.
        live_link: When true every intermediate link must be a live process and
            the reached lineage pid must itself still be live. When false a chain
            may terminate at an ancestor this run already signalled even though
            that ancestor has since died.

    Returns:
        ``(hops, "")`` — the number of parent hops from *child_pid* to the
        lineage, at least 1 — when the chain confirms, else ``(None, reason)``
        with :data:`SKIP_GONE` when the pid is not live and
        :data:`SKIP_UNCONFIRMED` when the chain cannot be confirmed.
    """
    current = ops.process_info(child_pid)
    if current is None:
        return None, SKIP_GONE
    seen = {child_pid}
    hops = 0
    while True:
        parent_pid = current.ppid
        if parent_pid is None or parent_pid <= 0 or parent_pid in seen:
            return None, SKIP_UNCONFIRMED
        if parent_pid in lineage:
            if live_link and ops.process_info(parent_pid) is None:
                return None, SKIP_UNCONFIRMED
            return hops + 1, ""
        seen.add(parent_pid)
        current = ops.process_info(parent_pid)
        if current is None:
            return None, SKIP_UNCONFIRMED
        hops += 1


def revalidate_child(
    ops: object,
    child_pid: int,
    root_pid: int,
) -> tuple[bool, str]:
    """Re-prove at this instant that *child_pid* is still ours to signal.

    A child pid is a point-in-time snapshot; before it is signalled the pid must
    be re-checked against *live* state. It qualifies only when it is a live
    process whose parent chain climbs through live processes to *root_pid*
    (which must itself still be live — an orphan's chain can no longer be
    confirmed). The executable is deliberately not compared: marimo boots its
    kernel under the base interpreter while the recorded parent is the venv
    shim, so a differing interpreter path is normal and is not evidence of a
    foreign process. Anything else is rejected so the caller skips it and
    signals nothing.

    Args:
        ops: The OS seam; needs ``process_info``.
        child_pid: The snapshotted child pid to re-verify.
        root_pid: The recorded manifest process id the chain must reach.

    Returns:
        ``(True, "")`` when the pid is confirmed, else ``(False, reason)`` where
        *reason* is :data:`SKIP_GONE` or :data:`SKIP_UNCONFIRMED`.
    """
    hops, reason = _chain_reaches(ops, child_pid, frozenset({root_pid}), live_link=True)
    return hops is not None, reason


class SystemOps:
    """The live OS seam: psutil for the process tree, urllib for the census."""

    CENSUS_TIMEOUT_S = 3.0
    POLL_INTERVAL_S = 0.1

    def probe_census(self, server_url: str) -> dict | None:
        """The server's session census, or None when it does not answer."""
        url = server_url.rstrip("/") + "/api/sessions"
        try:
            with urllib.request.urlopen(url, timeout=self.CENSUS_TIMEOUT_S) as response:
                if response.status != 200:
                    return None
                payload = json.load(response)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def process_info(self, pid: int) -> ProcessInfo | None:
        """The live process for *pid*, or None when it is gone.

        A process that is alive but whose command line cannot be read is
        reported with an empty ``cmdline`` so the caller fails closed
        (unverified identity) rather than treating it as gone. ``ppid`` and
        ``exe`` are likewise None when the OS refuses to report them.
        """
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return None
        except psutil.Error:
            return ProcessInfo(pid=pid, cmdline=[])
        try:
            if not process.is_running():
                return None
            cmdline = list(process.cmdline())
        except psutil.NoSuchProcess:
            return None
        except psutil.Error:
            cmdline = []
        try:
            ppid = int(process.ppid())
        except psutil.Error:
            ppid = None
        try:
            exe = process.exe()
        except psutil.Error:
            exe = None
        return ProcessInfo(pid=pid, cmdline=cmdline, ppid=ppid, exe=exe)

    def child_pids(self, pid: int) -> list[int]:
        """The recorded process's descendants, or an empty list if unknown."""
        try:
            return [child.pid for child in psutil.Process(pid).children(recursive=True)]
        except psutil.Error:
            return []

    def terminate(self, pid: int) -> None:
        """Ask *pid* to stop; a racing exit is not an error."""
        try:
            psutil.Process(pid).terminate()
        except psutil.Error:
            return

    def kill(self, pid: int) -> None:
        """Hard-kill *pid*; a racing exit is not an error."""
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            return

    def wait_gone(self, pid: int, timeout: float) -> bool:
        """Whether *pid* is gone within *timeout* seconds."""
        deadline = time.monotonic() + timeout
        while True:
            if self.process_info(pid) is None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.POLL_INTERVAL_S)


def _all_gone(ops: object, pid: int, children: list[int], timeout: float) -> bool:
    """Whether the recorded process and every enumerated child are gone."""
    if not ops.wait_gone(pid, timeout):
        return False
    return all(ops.process_info(child) is None for child in children)


def teardown(
    manifest: dict,
    ops: object,
    *,
    expect_sha256: str | None = None,
    terminate_timeout: float | None = None,
) -> TeardownReport:
    """Stop the server one manifest names, fail-closed and idempotently.

    Args:
        manifest: The setup manifest mapping (see :mod:`mission_setup`).
        ops: The OS seam — an object exposing ``probe_census``,
            ``process_info``, ``child_pids``, ``terminate``, ``kill`` and
            ``wait_gone``.
        expect_sha256: When supplied, the canonical digest the caller expects;
            a mismatch with the manifest value refuses before any process work.
        terminate_timeout: Override for the bounded post-terminate wait.

    Returns:
        A :class:`TeardownReport` on a verified success.

    Raises:
        TeardownRefusal: On any refusal class, with the matching exit code.
    """
    stage = "manifest"
    validate_manifest(manifest)

    stage = "notebook"
    notebook = resolve_notebook(manifest)

    stage = "sha256"
    recorded_sha = str(manifest["sha256"]).strip().lower()
    if expect_sha256 is not None:
        supplied = expect_sha256.strip().lower()
        if supplied != recorded_sha:
            _refuse(
                stage,
                CODE_SHA_MISMATCH,
                f"--expect-sha256 {supplied or '<empty>'} differs from the "
                f"manifest sha256 {recorded_sha}",
            )

    server_url = str(manifest["server_url"])
    pid = int(manifest["process_id"])
    session_id = str(manifest["session_id"])

    stage = "census"
    census = ops.probe_census(server_url)
    if census is None:
        if ops.process_info(pid) is None:
            return _ok(
                "census unreachable and the recorded process is already gone: "
                "nothing to stop (idempotent)"
            )
        _refuse(
            stage,
            CODE_CENSUS_UNREACHABLE_PROCESS_ALIVE,
            "the census is unreachable but the recorded process is still "
            "running; refusing to terminate a server that cannot be identified",
        )

    stage = "identity"
    anchor = _guard().SessionAnchor(session_id, str(notebook), pid)
    try:
        _guard().validate_census(census, anchor)
    except ValueError as exc:
        _refuse(
            stage,
            CODE_CENSUS_MISMATCH,
            f"census does not match the anchored session: {exc}",
            reachable=True,
        )
    info = ops.process_info(pid)
    if info is None:
        _refuse(
            stage,
            CODE_PROCESS_GONE_SERVER_LIVE,
            "the census is reachable but the recorded process is gone: a "
            "different server now owns this URL; refusing to kill a process "
            "the manifest does not name",
            reachable=True,
        )
    if not cmdline_references(info.cmdline, notebook):
        _refuse(
            stage,
            CODE_TARGET_UNVERIFIED,
            "the recorded pid is alive but its command line does not name the "
            "manifest notebook",
            reachable=True,
        )

    stage = "terminate"
    children = [child for child in dict.fromkeys(ops.child_pids(pid)) if child != pid]

    # The child list is a point-in-time snapshot; each pid is re-proved eligible
    # against *live* state before anything is signalled. Eligibility is the live
    # parent chain still reaching the recorded process. The executable is never
    # compared: the kernel legitimately runs under a different interpreter than
    # the recorded venv shim, and its command line need not name the notebook.
    # A pid whose chain no longer reaches the recorded process — a reused pid, a
    # reparented orphan — is skipped and reported, never signalled.
    eligible: list[tuple[int, int]] = []
    skipped: list[dict] = []
    for child in children:
        hops, reason = _chain_reaches(ops, child, frozenset({pid}), live_link=True)
        if hops is None:
            skipped.append({"pid": child, "reason": reason})
            continue
        eligible.append((hops, child))
    # Deepest first: an intermediate process must still be alive when its own
    # children are disposed of, so a child is signalled before its parent.
    eligible.sort(key=lambda item: item[0], reverse=True)
    verified = [child for _hops, child in eligible]
    for child in verified:
        ops.terminate(child)
    ops.terminate(pid)
    grace = TERMINATE_GRACE_S if terminate_timeout is None else float(terminate_timeout)

    escalated = False
    if not _all_gone(ops, pid, verified, grace):
        escalated = True
        # Our own deepest-first terminate may have stopped an intermediate and
        # orphaned its child, so the re-check accepts a chain reaching any pid
        # this run has already signalled. A foreign parent still rejects the pid.
        lineage = frozenset({pid, *verified})
        for child in verified:
            if ops.process_info(child) is None:
                continue
            hops, reason = _chain_reaches(ops, child, lineage, live_link=False)
            if hops is None:
                skipped.append({"pid": child, "reason": reason})
                continue
            ops.kill(child)
        ops.kill(pid)
        ops.wait_gone(pid, KILL_GRACE_S)

    stage = "verified"
    reachable = ops.probe_census(server_url) is not None
    survivors = [child for child in verified if ops.process_info(child) is not None]
    if reachable or ops.process_info(pid) is not None or survivors:
        _refuse(
            stage,
            CODE_TEARDOWN_FAILED,
            "teardown did not clear the arm: "
            f"reachable={reachable} pid_alive={ops.process_info(pid) is not None} "
            f"children_alive={survivors}",
            reachable=reachable,
            killed_pid=pid,
            children=children,
            escalated=escalated,
            skipped=skipped,
        )

    require(
        ops.process_info(pid) is None,
        "a verified success must not leave the recorded process alive",
    )
    verb = "hard-killed" if escalated else "terminated"
    detail = (
        f"{verb} pid {pid} and {len(verified)} child process(es); the server "
        "no longer answers"
    )
    if skipped:
        detail += f"; skipped {len(skipped)} child pid(s) that failed re-verification"
    return _ok(
        detail,
        killed_pid=pid,
        children=children,
        escalated=escalated,
        skipped=skipped,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stop the loopback server one mission-setup manifest names, "
            "fail-closed and idempotently."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-sha256", dest="expect_sha256", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, ops_factory=None) -> int:
    """CLI entry point: print one JSON object and return its exit code."""
    args = _parse_args(argv)
    factory = ops_factory or SystemOps
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report = TeardownReport(
            "refused",
            "manifest",
            CODE_MANIFEST_INVALID,
            None,
            [],
            False,
            f"manifest unreadable: {exc}",
        )
        print(json.dumps(report.as_dict(), sort_keys=True))
        return report.code
    try:
        report = teardown(manifest, factory(), expect_sha256=args.expect_sha256)
    except TeardownRefusal as exc:
        report = exc.report
    except (TeardownError, OSError) as exc:
        report = TeardownReport(
            "error",
            "internal",
            CODE_INTERNAL_FAILURE,
            None,
            [],
            False,
            f"internal failure: {exc}",
        )
    print(json.dumps(report.as_dict(), sort_keys=True))
    return report.code


if __name__ == "__main__":
    raise SystemExit(main())
