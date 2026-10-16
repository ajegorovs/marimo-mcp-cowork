"""Bounded native 0.25.1 WRONG-TARGET WRITE probe (characterization only).

Question: when the native CLI is given BOTH selectors at once, a MISMATCHED
pair ``--file B --session A`` (the ``--file`` names the notebook the operator
*intended*, the ``--session`` names a *different* live session), does a
MUTATING code-mode call silently change the session that ``--file`` did NOT
name?

Companion to ``native_target_probe.py``. That probe established, READ-ONLY,
that ``--session`` takes precedence over ``--file`` for ``execute``. This
probe goes one step further and asks the safety question the read-only probe
explicitly declined: is a mismatched selector pair *silent* when the operation
MUTATES a notebook? A write that lands where ``--file`` did not point, with no
refusal and no error, is a wrong-target hazard.

Method (loopback only, no installation, no network egress):
  1. Copy the disposable fixture twice into ONE directory workspace, so a
     single ``marimo edit <dir>`` server serves two distinct notebooks.
  2. Open two sessions on that one server (session A -> copy a, session B ->
     copy b) via the ``/sse`` handshake; confirm ``/api/sessions`` lists both
     and maps each copy to the expected session id.
  3. READ-ONLY mismatch verify: run ``--file B --session A`` with read-only
     code and assert the payload's ``session.id`` is A (i.e. the mismatch is
     resolved to A before any write).
  4. ONE bounded write attempt: ``--file B --session A`` running a single
     ``ctx.create_cell("a4_wrong_target_marker = 123456")`` (no run, no run of
     any cell). Record exit code, JSON success, and the payload's session id.
  5. Verify the live cell map via EACH explicit ``--session`` separately
     (read-only) to see which session actually received the new cell.
  6. Bounded wait for autosave, then hash the on-disk bytes of A and B, and
     assert the repo fixture is byte-unchanged.

Decision / labelling (honest, per scope):
  * Marker present in session A (live map and/or A's disk bytes changed) while
    ``--file`` named B  ->  ``FAIL`` / SILENT WRONG-TARGET HAZARD: a mutating
    call applied to a notebook the explicit ``--file`` selector did not name,
    with no refusal. This is NOT a PASS.
  * Marker present in session B only  ->  ``FAIL`` (reverse): the write
    ignored ``--session`` even though read-only resolution honoured it.
  * Neither session mutated  ->  ``PASS``: the mismatched pair was refused /
    produced no mutation (fail-closed).

Bounds: at most ONE write attempt and no retry of an unknown-outcome
mutation; the process tree is always stopped in ``finally``.

Exit 0 with NATIVE_WRONG_WRITE_PASS, 3 with ..._BLOCKER, 4 with ..._FAIL.
"""

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

FIXTURE = Path(__file__).parent / "notebooks" / "mission_clean.py"
SCRATCH = Path(
    os.environ.get(
        "MARIMO_BENCHMARK_SCRATCH", Path.home() / ".cache" / "marimo-inspect"
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)

MARKER = "a4_wrong_target_marker"
MARKER_SOURCE = f"{MARKER} = 123456"

READ_ONLY_CODE = "print('PROBE_READONLY_WRONG_WRITE_TARGET_HIT')"

CODE_MODE_PREFIX = "import marimo._code_mode as cm\n"

# The single bounded write: create exactly one cell, do not run it.
WRITE_CODE = (
    CODE_MODE_PREFIX
    + "async with cm.get_context() as ctx:\n"
    + f'    cid = ctx.create_cell("{MARKER_SOURCE}", hide_code=False)\n'
    + '    print("CREATED", cid)'
)

# Read-only live cell map: dump every cell's code so we can look for MARKER.
MAP_CODE = (
    CODE_MODE_PREFIX
    + "async with cm.get_context() as ctx:\n"
    + "    import json\n"
    + "    print('CELLS', json.dumps(sorted(c.code for c in ctx.cells.values())))"
)

AUTOSAVE_WAIT_S = 30
MARKER_WAIT_S = 6


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def run_cli(url, file_path, session_id, code, env):
    """Run native ``pair execute``; return (code, stdout, stderr)."""
    argv = [sys.executable, "-m", "marimo", "pair", "execute", "--url", url]
    if session_id is not None:
        argv += ["--session", session_id]
    if file_path is not None:
        argv += ["--file", str(file_path)]
    argv += ["-c", code]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    return result.returncode, result.stdout, result.stderr


def parse_payload(stdout: str):
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        return None


def open_session(url: str, session_id: str, file_path: Path) -> bool:
    """Create one notebook session via /sse; True once the kernel is ready."""
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


def marker_in_map(result) -> bool:
    """Require a successful live map and find the marker in parsed cell source."""
    code, stdout, _err = result
    payload = parse_payload(stdout)
    if code != 0 or not payload or payload.get("success") is not True:
        raise Blocker("live cell map could not be read after the write")
    printed = str(payload.get("stdout") or "")
    rows = [
        line.removeprefix("CELLS ")
        for line in printed.splitlines()
        if line.startswith("CELLS ")
    ]
    if len(rows) != 1:
        raise Blocker("live cell map did not contain exactly one CELLS record")
    return any(MARKER_SOURCE == source for source in json.loads(rows[0]))


def wait_for_change(path: Path, before: str, timeout: float) -> str:
    """Poll until ``path``'s hash differs from ``before`` (bounded)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = sha256(path)
        if current != before:
            return current
        time.sleep(0.25)
    return sha256(path)


def wait_for_marker(copy_path: Path, url: str, session_id: str, env, timeout: float):
    """Poll the live map until MARKER appears (bounded); return last result."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = run_cli(url, None, session_id, MAP_CODE, env)
        if marker_in_map(last):
            return last
        time.sleep(1.0)
    return last


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-wrong-write-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        workspace = root / "ws"
        workspace.mkdir()
        copy_a = workspace / "probe_target_a.py"
        copy_b = workspace / "probe_target_b.py"
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
                raise Blocker("session A (probe_target_a) did not reach ready")
            if not open_session(url, sid_b, copy_b):
                raise Blocker("session B (probe_target_b) did not reach ready")

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

            path_b = sessions[sid_b].get("path") or sessions[sid_b]["filename"]

            # --- Step 1: READ-ONLY mismatch verify: --file B --session A -> A.
            code, out, err = run_cli(url, path_b, sid_a, READ_ONLY_CODE, env)
            payload = parse_payload(out)
            readonly_session = (payload or {}).get("session", {}).get("id")
            print(
                "READONLY_MISMATCH exit",
                code,
                "success",
                (payload or {}).get("success"),
                "session",
                readonly_session,
                "expected",
                sid_a,
                "matches_A",
                readonly_session == sid_a,
            )
            if code != 0 or not payload or not payload.get("success"):
                raise Blocker(
                    f"read-only --file B --session A failed: code={code} {err!r}"
                )
            if readonly_session != sid_a:
                raise Blocker(
                    "read-only mismatch did not resolve to A: "
                    f"got {readonly_session}, expected {sid_a}"
                )

            # --- Step 2: ONE bounded WRITE attempt: --file B --session A.
            write_code_rc, write_out, _write_err = run_cli(
                url, path_b, sid_a, WRITE_CODE, env
            )
            write_payload = parse_payload(write_out)
            write_session = (write_payload or {}).get("session", {}).get("id")
            write_success = (write_payload or {}).get("success")
            print(
                "WRITE_ATTEMPT exit",
                write_code_rc,
                "success",
                write_success,
                "session",
                write_session,
                "stdout",
                repr((write_payload or {}).get("stdout")),
            )
            if write_payload is None:
                raise Blocker(
                    "write response unparseable: outcome unknown; inspect, do not retry"
                )

            # --- Step 3: Which session actually received the marker? Read-only
            # maps via EACH explicit --session (no --file), and bounded wait in
            # case the flush/autosave lags.
            result_a = wait_for_marker(copy_a, url, sid_a, env, MARKER_WAIT_S)
            result_b = wait_for_marker(copy_b, url, sid_b, env, MARKER_WAIT_S)
            marker_in_a = marker_in_map(result_a)
            marker_in_b = marker_in_map(result_b)
            print("LIVE_MAP session_A has_marker", marker_in_a)
            print("LIVE_MAP session_B has_marker", marker_in_b)

            # --- Step 4: bounded autosave wait, then disk hashes.
            a_after = wait_for_change(copy_a, a_before, AUTOSAVE_WAIT_S)
            b_after = wait_for_change(copy_b, b_before, AUTOSAVE_WAIT_S)
            # Give the second file a moment too if the first already settled.
            if a_after != a_before and b_after == b_before:
                b_after = wait_for_change(copy_b, b_before, 5)
            disk_a_changed = a_after != a_before
            disk_b_changed = b_after != b_before
            print(
                "DISK copy_a_changed",
                disk_a_changed,
                a_before[:12],
                a_after[:12],
                "copy_b_changed",
                disk_b_changed,
                b_before[:12],
                b_after[:12],
            )

            fixture_intact = FIXTURE.read_bytes() == fixture_before
            print("REPO_FIXTURE_INTACT", fixture_intact)

            landed_a = marker_in_a or disk_a_changed
            landed_b = marker_in_b or disk_b_changed

            print(
                "WRITE_SESSION",
                write_session,
                "readonly_session",
                readonly_session,
                "landed_A",
                landed_a,
                "landed_B",
                landed_b,
            )

            if landed_a or landed_b:
                print(
                    "CONCLUSION FAIL / WRONG TARGET: combined selectors were not "
                    "cross-checked; write landed_A",
                    landed_a,
                    "landed_B",
                    landed_b,
                    "payload_session_A",
                    write_session == sid_a,
                )
                print("NATIVE_WRONG_WRITE_FAIL")
                return 4
            if write_success is not False or write_code_rc == 0:
                print(
                    "CONCLUSION FAIL / FALSE SUCCESS: no observed mutation, but the write was not refused"
                )
                print("NATIVE_WRONG_WRITE_FAIL")
                return 4
            print("CONCLUSION PASS: mismatch refused, no mutation in either session")
            print("NATIVE_WRONG_WRITE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_WRONG_WRITE_BLOCKER: {error}")
            return 3
        finally:
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
