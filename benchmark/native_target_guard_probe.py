"""Bounded native 0.25.1 TWO-SESSION gate for the fail-closed adapter.

Question
--------
``benchmark/target_selection.py`` is the benchmark's chosen mitigation for the
native ``marimo pair`` selector hazard: a *single-selector* adapter that
refuses any call supplying **both** ``--file`` and ``--session`` (and any call
supplying **neither** or a **blank** value), so a mismatched pair can never
reach the CLI. This probe is the integration gate for that contract: it drives
the **actual adapter path** against a real two-session loopback server, not a
pure unit test and not a bare native CLI call.

What it proves (loopback only, no installation, no network egress)
-----------------------------------------------------------------
Setup: one disposable workspace, two byte-identical copies of the committed
fixture, one ``marimo edit <dir>`` server holding two sessions via ``/sse``
(session A -> copy a, session B -> copy b).

Phase 1 - BEFORE any CLI write, route four ambiguous requests through the
  adapter (``select_native_target``) and require each to raise
  :class:`SelectorRefusal` **without** spawning a single CLI subprocess
  (subprocess-invocation counter must stay at zero):
    * B file + A session (mismatched pair),
    * a *missing* file + A session,
    * an *agreeing* A file + A session,
    * a *blank* file + A session.
  Then read both live cell maps and both saved byte hashes and require them
  unchanged (no marker, fixture cell count, bytes equal to the fixture).

Phase 2 - a controlled **file-only** write to B (single selector): assert the
  intended mutation is actually observed in B (marker in B's live map and/or
  B's saved bytes changed) and that A is untouched (no marker, bytes
  unchanged). A reported ``success`` with no observed intended mutation is a
  FALSE SUCCESS and FAILS the gate.

Phase 3 - a **session-only** read of A (no write): assert it resolves to A and
  mutates nothing.

Phase 4 - a **missing file-only** selector: assert the native CLI itself
  fails and nothing on disk changes.

Finally assert the committed fixture is byte-unchanged and the server is
stopped. No secrets or absolute user paths are printed.

Exit 0 with ``NATIVE_TARGET_GUARD_PASS``; non-zero with
``NATIVE_TARGET_GUARD_BLOCKER`` (a precondition failed) or
``NATIVE_TARGET_GUARD_FAIL`` (the gate's assertion failed).
"""

from __future__ import annotations

import hashlib
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
from pathlib import Path

import psutil

# The adapter under test lives beside this probe; make it importable when the
# file is run as a script from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from target_selection import SelectorRefusal, select_native_target

FIXTURE = Path(__file__).parent / "notebooks" / "mission_clean.py"
SCRATCH = Path(
    os.environ.get(
        "MARIMO_BENCHMARK_SCRATCH", Path.home() / ".cache" / "marimo-inspect"
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)

PREFIX = "import marimo._code_mode as cm\n"

MARKER = "guard_probe_target_marker"
MARKER_SOURCE = f"{MARKER} = 98765"

READ_ONLY_CODE = "print('GUARD_PROBE_SESSION_ONLY_READ_WOULD_RUN')"

# Read-only live cell map: dump every cell's source so we can look for MARKER.
MAP_CODE = (
    PREFIX
    + "async with cm.get_context() as ctx:\n"
    + "    import json\n"
    + "    print('CELLS', json.dumps(sorted(c.code for c in ctx.cells.values())))"
)

# The single controlled write: create exactly one marker cell and run it.
WRITE_CODE = (
    PREFIX
    + "async with cm.get_context() as ctx:\n"
    + f'    cid = ctx.create_cell("{MARKER_SOURCE}", hide_code=False)\n'
    + "    ctx.run_cell(cid)\n"
    + '    print("CREATED", cid)'
)

EXPECTED_CELLS = 5
MARKER_WAIT_S = 8
AUTOSAVE_WAIT_S = 30

# Counts only actual ``marimo pair execute`` subprocess spawns; the adapter is
# consulted before this is incremented, so a refusal leaves it at zero. This is
# the evidence that a refused request never reaches the CLI.
CLI_INVOCATIONS = 0


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def run_adapter_cli(url, code, env, *, file_path=None, session_id=None):
    """Build argv via the adapter, then (only on a valid single selector) run
    native ``pair execute``.

    Raises :class:`SelectorRefusal` *before* any subprocess is spawned for an
    ambiguous/missing/blank selector; only a successful adapter return reaches
    ``subprocess.run``. Returns ``(returncode, payload, stdout, stderr)``.
    """
    global CLI_INVOCATIONS
    fragment = select_native_target(file_path=file_path, session_id=session_id)
    argv = [sys.executable, "-m", "marimo", "pair", "execute", "--url", url]
    argv += fragment
    argv += ["-c", code]
    CLI_INVOCATIONS += 1
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = None
    return result.returncode, payload, result.stdout, result.stderr


def read_map(url, session_id, env):
    """Read a session's live cell sources via the adapter (session-only)."""
    rc, payload, raw, err = run_adapter_cli(url, MAP_CODE, env, session_id=session_id)
    if rc != 0 or not payload or payload.get("success") is not True:
        raise Blocker(
            f"live map read failed for session {session_id}: rc={rc} "
            f"raw={raw[:200]!r} err={err[:200]!r}"
        )
    printed = str(payload.get("stdout") or "")
    rows = [
        line.removeprefix("CELLS ")
        for line in printed.splitlines()
        if line.startswith("CELLS ")
    ]
    if len(rows) != 1:
        raise Blocker("live map did not contain exactly one CELLS record")
    return json.loads(rows[0])


def wait_for_change(path: Path, before: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    current = sha256(path)
    while time.monotonic() < deadline and current == before:
        time.sleep(0.25)
        current = sha256(path)
    return current


def stop(proc: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    if proc.poll() is None:
        proc.terminate()
    psutil.wait_procs(children, timeout=5)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def open_session(url: str, session_id: str, file_path: Path) -> bool:
    query = urllib.parse.urlencode({"session_id": session_id, "file": str(file_path)})
    try:
        with urllib.request.urlopen(f"{url}/sse?{query}", timeout=30) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    return True
    except OSError:
        return False
    return False


def fetch_sessions(url: str) -> dict:
    with urllib.request.urlopen(url + "/api/sessions", timeout=5) as response:
        return json.load(response)


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    fixture_hash = hashlib.sha256(fixture_before).hexdigest()
    proc = None
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-target-guard-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        workspace = root / "ws"
        workspace.mkdir()
        copy_a = workspace / "guard_target_a.py"
        copy_b = workspace / "guard_target_b.py"
        copy_a.write_bytes(fixture_before)
        copy_b.write_bytes(fixture_before)
        a_before, b_before = sha256(copy_a), sha256(copy_b)

        state = root / "state"
        state.mkdir()
        env = os.environ.copy()
        env["USERPROFILE"] = str(state)
        env["XDG_STATE_HOME"] = str(state)

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "marimo",
                "edit",
                str(workspace),
                "--headless",
                "--no-token",
                "--no-skew-protection",
                "--skip-update-check",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(150):
                if proc.poll() is not None:
                    raise Blocker(f"server exited {proc.returncode}")
                try:
                    with urllib.request.urlopen(
                        url + "/api/version", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise Blocker("server readiness timeout")

            sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
            if not open_session(url, sid_a, copy_a):
                raise Blocker("session A (guard_target_a) did not reach ready")
            if not open_session(url, sid_b, copy_b):
                raise Blocker("session B (guard_target_b) did not reach ready")

            sessions = fetch_sessions(url)
            if len(sessions) != 2:
                raise Blocker(
                    f"one server held {len(sessions)} session(s), not 2: "
                    f"{sorted(sessions)}"
                )
            by_norm = {}
            for sid, info in sessions.items():
                path = info.get("path") or info.get("filename")
                if not path:
                    raise Blocker(f"session {sid} reported no path")
                by_norm[norm(path)] = sid
            if by_norm.get(norm(str(copy_a))) != sid_a:
                raise Blocker("session A mapping mismatch: expected A -> copy_a")
            if by_norm.get(norm(str(copy_b))) != sid_b:
                raise Blocker("session B mapping mismatch: expected B -> copy_b")
            print("SETUP two_sessions", 2, "session_A", sid_a, "session_B", sid_b)

            # --- Phase 1: ambiguous selectors refuse with ZERO CLI spawns ----
            if CLI_INVOCATIONS != 0:
                raise Blocker("CLI was invoked before the refusal phase")
            refusals = [
                ("file_B+session_A", {"file_path": str(copy_b), "session_id": sid_a}),
                (
                    "missing_file+session_A",
                    {
                        "file_path": str(workspace / "nope_guard.py"),
                        "session_id": sid_a,
                    },
                ),
                (
                    "agreeing_file_A+session_A",
                    {"file_path": str(copy_a), "session_id": sid_a},
                ),
                ("blank+session_A", {"file_path": "", "session_id": sid_a}),
            ]
            for label, selectors in refusals:
                before = CLI_INVOCATIONS
                try:
                    run_adapter_cli(url, READ_ONLY_CODE, env, **selectors)
                except SelectorRefusal as exc:
                    if CLI_INVOCATIONS != before:
                        raise AssertionError(
                            f"{label}: refusal still spawned a CLI subprocess "
                            f"({before} -> {CLI_INVOCATIONS})"
                        )
                    print(
                        "REFUSAL",
                        label,
                        "SelectorRefusal",
                        repr(str(exc))[:80],
                        "cli_invocations",
                        CLI_INVOCATIONS,
                    )
                    continue
                raise AssertionError(
                    f"{label}: adapter did NOT refuse an ambiguous selector pair"
                )
            if CLI_INVOCATIONS != 0:
                raise AssertionError(
                    f"refusal phase invoked the CLI {CLI_INVOCATIONS} time(s), expected 0"
                )
            print("REFUSAL_PHASE cli_invocations", CLI_INVOCATIONS, "expected", 0)

            # Live maps and saved bytes must be unchanged after the refusals.
            map_a = read_map(url, sid_a, env)
            map_b = read_map(url, sid_b, env)
            if MARKER_SOURCE in map_a or MARKER_SOURCE in map_b:
                raise AssertionError(
                    "refusal phase introduced the marker into a live map"
                )
            if len(map_a) != EXPECTED_CELLS or len(map_b) != EXPECTED_CELLS:
                raise AssertionError(
                    f"live map cell counts {len(map_a)}/{len(map_b)} != "
                    f"{EXPECTED_CELLS} (fixture)"
                )
            if sha256(copy_a) != a_before or sha256(copy_b) != b_before:
                raise AssertionError("refusal phase mutated saved bytes")
            print(
                "POST_REFUSAL_UNCHANGED cells_A",
                len(map_a),
                "cells_B",
                len(map_b),
                "marker_absent",
                True,
                "bytes_unchanged",
                True,
            )

            # --- Phase 2: controlled FILE-ONLY write must land on B only ----
            rc, payload, raw, err = run_adapter_cli(
                url, WRITE_CODE, env, file_path=str(copy_b)
            )
            write_success = (payload or {}).get("success")
            if rc != 0 or write_success is not True:
                raise AssertionError(
                    f"file-only write to B failed: rc={rc} success={write_success} "
                    f"raw={raw[:300]!r} err={err[:300]!r}"
                )
            # The intended mutation MUST be observed (else false success).
            deadline = time.monotonic() + MARKER_WAIT_S
            marker_in_b = False
            while time.monotonic() < deadline:
                if MARKER_SOURCE in read_map(url, sid_b, env):
                    marker_in_b = True
                    break
                time.sleep(1.0)
            b_after = wait_for_change(copy_b, b_before, AUTOSAVE_WAIT_S)
            b_disk_changed = b_after != b_before

            map_a = read_map(url, sid_a, env)
            a_after = sha256(copy_a)
            marker_in_a = MARKER_SOURCE in map_a
            a_unchanged = a_after == a_before and not marker_in_a
            print(
                "FILE_ONLY_WRITE",
                "rc",
                rc,
                "success",
                write_success,
                "marker_in_B",
                marker_in_b,
                "B_disk_changed",
                b_disk_changed,
                "marker_in_A",
                marker_in_a,
                "A_unchanged",
                a_unchanged,
            )
            if not marker_in_b or not b_disk_changed:
                raise AssertionError(
                    "FALSE SUCCESS: file-only write did not appear in both "
                    "B's live map and its saved copy"
                )
            if MARKER_SOURCE not in copy_b.read_text(encoding="utf-8"):
                raise AssertionError("B's saved copy lacks the intended marker source")
            if not a_unchanged:
                raise AssertionError(
                    "file-only write leaked into session A (marker or bytes changed)"
                )

            # --- Phase 3: session-only READ of A must resolve to A, no write -
            b_before_read = sha256(copy_b)
            rc, payload, raw, err = run_adapter_cli(
                url, READ_ONLY_CODE, env, session_id=sid_a
            )
            read_session = (payload or {}).get("session", {}).get("id")
            if rc != 0 or (payload or {}).get("success") is not True:
                raise AssertionError(
                    f"session-only read of A failed: rc={rc} raw={raw[:300]!r} "
                    f"err={err[:300]!r}"
                )
            if read_session != sid_a:
                raise AssertionError(
                    f"session-only read resolved to {read_session}, expected {sid_a}"
                )
            if sha256(copy_a) != a_before or sha256(copy_b) != b_before_read:
                raise AssertionError("session-only read mutated saved bytes")
            map_a = read_map(url, sid_a, env)
            if MARKER_SOURCE in map_a:
                raise AssertionError("session-only read left a marker in A")
            print(
                "SESSION_ONLY_READ",
                "rc",
                rc,
                "success",
                payload.get("success"),
                "session",
                read_session,
                "no_write",
                True,
            )

            # --- Phase 4: missing FILE-ONLY selector must fail in the CLI ----
            b_before_missing = sha256(copy_b)
            rc, payload, raw, err = run_adapter_cli(
                url, READ_ONLY_CODE, env, file_path=str(workspace / "absent_guard.py")
            )
            if rc == 0 or not payload or payload.get("success") is not False:
                raise AssertionError(
                    "missing file-only selector lacked explicit CLI refusal: "
                    f"rc={rc} payload={payload!r}"
                )
            if sha256(copy_a) != a_before or sha256(copy_b) != b_before_missing:
                raise AssertionError("missing file-only selector mutated saved bytes")
            print(
                "MISSING_FILE_ONLY",
                "rc",
                rc,
                "success",
                (payload or {}).get("success"),
                "no_change",
                True,
            )

            # --- Final: committed fixture intact; server stopped in finally ---
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("committed repo fixture was mutated")
            print("REPO_FIXTURE_INTACT", True, fixture_hash[:12])
            print(
                "CONCLUSION adapter refused every ambiguous selector with zero "
                "CLI spawns; file-only write landed on B only; session-only read "
                "and missing file-only caused no mutation"
            )
            print("NATIVE_TARGET_GUARD_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_TARGET_GUARD_BLOCKER: {error}")
            return 3
        except AssertionError as error:
            print(f"NATIVE_TARGET_GUARD_FAIL: {error}")
            return 1
        finally:
            if proc is not None:
                stop(proc)


if __name__ == "__main__":
    sys.exit(main())
