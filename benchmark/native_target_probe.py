"""Bounded native 0.25.1 target-selection probe: does CLI ``--session`` win?

Question: when ``marimo pair execute`` is given both ``--file B`` and
``--session A``, which target does it use?

Method (READ-ONLY, loopback only, no installation):
  1. Copy the disposable fixture twice into ONE directory workspace, so a
     single ``marimo edit <dir>`` server can serve two distinct notebooks.
  2. Open two sessions on that one server (session A -> copy a, session B ->
     copy b) via the ``/sse`` handshake, and confirm ``/api/sessions`` lists
     both. If one server cannot hold two sessions, stop with a blocker.
  3. Run the native CLI with a MISMATCHED selector ``--file B --session A``
     (plus symmetric and control runs) using read-only code, and inspect the
     JSON payload's ``session.id``.
  4. Assert both disposable files (and the repo fixture) are byte-unchanged.

What this DOES prove: for read-only execution, ``--session`` overrides
``--file`` (the payload identifies A, not B) and no file is mutated.
What this does NOT prove: wrong-WRITE safety. No mutating mismatch is
attempted, so nothing here shows that a mis-targeted write is safe or
refused. That requires a separate, explicitly bounded write probe.

Exit 0 with NATIVE_TARGET_PROBE_PASS, or non-zero with
NATIVE_TARGET_PROBE_BLOCKER / NATIVE_TARGET_PROBE_FAIL.
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

READ_ONLY_CODE = "print('PROBE_READONLY_PROBE_TARGET_HIT')"


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def run_cli(url, file_path, session_id, env):
    """Run native ``pair execute`` read-only; return (code, stdout, stderr)."""
    argv = [sys.executable, "-m", "marimo", "pair", "execute", "--url", url]
    if session_id is not None:
        argv += ["--session", session_id]
    if file_path is not None:
        argv += ["--file", str(file_path)]
    argv += ["-c", READ_ONLY_CODE]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=30, check=False
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


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-target-", dir=SCRATCH
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

        # Directory workspace: one server, two notebooks.
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

            path_a = sessions[sid_a].get("path") or sessions[sid_a]["filename"]
            path_b = sessions[sid_b].get("path") or sessions[sid_b]["filename"]

            results = {}

            # Control: --file B alone must resolve to session B.
            code, out, err = run_cli(url, path_b, None, env)
            payload = parse_payload(out)
            results["file_only_B"] = (code, payload, err)
            if code != 0 or not payload or not payload.get("success"):
                raise Blocker(f"--file B control failed: code={code} {err!r}")
            if payload.get("session", {}).get("id") != sid_b:
                raise Blocker(
                    f"--file B did not resolve to session B: {payload.get('session')}"
                )

            # Mismatched: --file B --session A must target session A.
            code, out, err = run_cli(url, path_b, sid_a, env)
            payload = parse_payload(out)
            results["file_B_session_A"] = (code, payload, err)
            if code != 0 or not payload or not payload.get("success"):
                raise Blocker(
                    f"mismatched --file B --session A failed: code={code} {err!r}"
                )
            if payload.get("session", {}).get("id") != sid_a:
                raise Blocker(
                    "--session did NOT override --file: "
                    f"got {payload.get('session')}, expected {sid_a}"
                )

            # Symmetric: --file A --session B must target session B.
            code, out, err = run_cli(url, path_a, sid_b, env)
            payload = parse_payload(out)
            results["file_A_session_B"] = (code, payload, err)
            if code != 0 or not payload or not payload.get("success"):
                raise Blocker(
                    f"mismatched --file A --session B failed: code={code} {err!r}"
                )
            if payload.get("session", {}).get("id") != sid_b:
                raise Blocker(
                    "--session did NOT override --file (symmetric): "
                    f"got {payload.get('session')}, expected {sid_b}"
                )

            for label, (code, payload, _err) in results.items():
                print(
                    "RESULT",
                    label,
                    "exit",
                    code,
                    "success",
                    (payload or {}).get("success"),
                    "session",
                    ((payload or {}).get("session") or {}).get("id"),
                    "stdout",
                    repr((payload or {}).get("stdout")),
                )

            if sha256(copy_a) != a_before:
                raise Blocker("copy_a was mutated")
            if sha256(copy_b) != b_before:
                raise Blocker("copy_b was mutated")
            if FIXTURE.read_bytes() != fixture_before:
                raise Blocker("repo fixture was mutated")
            print("FILES_UNCHANGED", a_before[:12], b_before[:12])
            print(
                "CONCLUSION --session takes precedence over --file for "
                "execute; mismatched selector targeted the named session; "
                "no file mutation observed (read-only evidence only)"
            )
            print("NATIVE_TARGET_PROBE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_TARGET_PROBE_BLOCKER: {error}")
            return 3
        finally:
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
