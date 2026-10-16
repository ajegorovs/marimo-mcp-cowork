"""Bounded native 0.25.1 reconnect / stale-session refusal probe.

Question
--------
After a marimo server is stopped and a *fresh* server (new PID, new session)
is started against the *same* disposable notebook copy, what happens when a
native ``marimo pair execute --session <OLD_SESSION>`` targets the session id
that was valid only on the previous server?

Expected (from installed marimo 0.25.1 source):
  - The CLI sends the id as the ``Marimo-Session-Id`` header.
  - The server dependency ``require_current_session`` (``_server/api/deps.py``)
    raises ``ValueError("Invalid session id: ...")`` *before* the endpoint
    handler runs, so no kernel code executes and nothing can be mutated.
  - The CLI maps that to ``StaleSessionError`` -> exit code 2 with payload
    ``{"success": false, "error": "The session is stale.", ...}``.
  - Re-resolving with ``--file <copy>`` (no ``--session``) must yield the
    *new* session id and read the same notebook.

Method (loopback only, no installation, bounded — at most two server runs)
--------------------------------------------------------------------------
1. Copy the committed fixture byte-identically into a disposable dir.
2. Start server #1; open one ``/sse`` session A bound to the copy; confirm
   ``/api/sessions`` lists exactly A -> copy path.
3. Stop server #1 cleanly and assert the loopback endpoint no longer answers.
4. Start server #2 (must be a new PID) on the same copy; open session B;
   assert ``B != A`` and that #2 lists B -> copy path.
5. Attempt one READ-ONLY ``pair execute --session A`` (the stale id). Assert
   it is refused as stale (nonzero exit, ``success: false``, stale wording,
   null stdout/stderr). If A is unexpectedly accepted, FAIL. Do not retry.
6. Re-resolve with ``--file <copy>`` only; assert the payload reports session
   B; run the fixture cells and read a known metric (``gain`` == 2,
   ``processed`` == [4, 8, 12, 16]).
7. Assert the disposable copy and the repo fixture stayed byte-identical.

Exit 0 with ``NATIVE_RECONNECT_PROBE_PASS``, or non-zero with
``NATIVE_RECONNECT_PROBE_BLOCKER`` / ``NATIVE_RECONNECT_PROBE_FAIL``.
No secrets or absolute paths are printed.
"""

from __future__ import annotations

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

PREFIX = "import marimo._code_mode as cm\n"

# Read-only snippet used for the stale attempt; it must never run.
READ_ONLY_CODE = "print('PROBE_RECONNECT_READONLY_WOULD_RUN')"

# A unique in-kernel marker the harness parses out of the CLI stdout.
SNAPSHOT_MARKER = "PROBE_RECONNECT_SNAPSHOT"

EXPECTED_CELLS = 5
EXPECTED_GAIN = 2
EXPECTED_PROCESSED = [4, 8, 12, 16]


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/version", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def fetch_sessions(url: str) -> dict:
    with urllib.request.urlopen(url + "/api/sessions", timeout=5) as response:
        return json.load(response)


def start_server(copy: Path, env: dict) -> tuple[subprocess.Popen, str]:
    """Boot a headless server on loopback against *copy*; return (proc, url)."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            str(copy),
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
    for _ in range(150):
        if proc.poll() is not None:
            raise Blocker(f"server exited {proc.returncode}")
        if server_alive(url):
            return proc, url
        time.sleep(0.1)
    stop(proc)
    raise Blocker("server readiness timeout")


def open_session(url: str, session_id: str, file_path: Path) -> None:
    """Create one notebook session via /sse; returns once the kernel is ready."""
    query = urllib.parse.urlencode({"session_id": session_id, "file": str(file_path)})
    try:
        with urllib.request.urlopen(f"{url}/sse?{query}", timeout=30) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    return
    except OSError as error:
        raise Blocker(f"session open failed: {error}") from error
    raise Blocker("session did not reach kernel-ready")


def run_cli(
    url: str, *, session_id: str | None, file_path: Path | None, code: str, env: dict
) -> tuple[int, dict, str]:
    """Run native ``pair execute``; return (returncode, payload, raw_stdout)."""
    argv = [sys.executable, "-m", "marimo", "pair", "execute", "--url", url]
    if session_id is not None:
        argv += ["--session", session_id]
    if file_path is not None:
        argv += ["--file", str(file_path)]
    argv += ["-c", code]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = None
    return result.returncode, payload, result.stdout


def _marker_line(payload: dict) -> str | None:
    prefix = SNAPSHOT_MARKER + " "
    for line in str(payload.get("stdout", "")).splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    first_proc = None
    second_proc = None
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-reconnect-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_reconnect.py"
        copy.write_bytes(fixture_before)
        copy_before = copy.read_bytes()

        state = root / "state"
        state.mkdir()
        env = os.environ.copy()
        env["USERPROFILE"] = str(state)
        env["XDG_STATE_HOME"] = str(state)

        try:
            # --- Server #1: session A bound to the copy -------------------
            first_proc, url1 = start_server(copy, env)
            pid1 = first_proc.pid
            sid_a = str(uuid.uuid4())
            open_session(url1, sid_a, copy)
            sessions1 = fetch_sessions(url1)
            if list(sessions1) != [sid_a]:
                raise Blocker(f"server #1 sessions unexpected: {sorted(sessions1)}")
            bound1 = sessions1[sid_a].get("path") or sessions1[sid_a]["filename"]
            if Path(bound1).resolve() != copy.resolve():
                raise Blocker("server #1 session A bound to an unexpected path")
            print("SERVER1_PID", pid1, "SESSION_A", sid_a)

            # --- Stop server #1 cleanly -----------------------------------
            stop(first_proc)
            first_proc = None
            if server_alive(url1):
                raise Blocker("server #1 still serving after stop")
            print("SERVER1_STOPPED", True)

            # --- Server #2: new PID, session B on the SAME copy -----------
            second_proc, url2 = start_server(copy, env)
            pid2 = second_proc.pid
            if pid2 == pid1:
                raise Blocker(f"server #2 reused PID {pid2}")
            sid_b = str(uuid.uuid4())
            open_session(url2, sid_b, copy)
            sessions2 = fetch_sessions(url2)
            if list(sessions2) != [sid_b]:
                raise Blocker(f"server #2 sessions unexpected: {sorted(sessions2)}")
            if sid_b == sid_a:
                raise Blocker("fresh server reused the stale session id")
            bound2 = sessions2[sid_b].get("path") or sessions2[sid_b]["filename"]
            if Path(bound2).resolve() != copy.resolve():
                raise Blocker("server #2 session B bound to an unexpected path")
            print("SERVER2_PID", pid2, "SESSION_B", sid_b)

            # --- Probe: stale id A must be refused (read-only) ------------
            stale_rc, stale_payload, stale_raw = run_cli(
                url2,
                session_id=sid_a,
                file_path=None,
                code=READ_ONLY_CODE,
                env=env,
            )
            stale_error = (stale_payload or {}).get("error")
            stale_stdout = (stale_payload or {}).get("stdout")
            print(
                "STALE_ATTEMPT",
                "exit",
                stale_rc,
                "success",
                (stale_payload or {}).get("success"),
                "error",
                repr(stale_error),
                "stdout",
                repr(stale_stdout),
                "session",
                ((stale_payload or {}).get("session") or {}).get("id"),
            )

            # If the old id was unexpectedly accepted, mark FAIL.
            if stale_rc == 0 or (stale_payload or {}).get("success") is not False:
                raise AssertionError(
                    "stale session id was ACCEPTED by the fresh server: "
                    f"exit={stale_rc} payload={stale_payload!r} raw={stale_raw!r}"
                )
            if stale_payload is None:
                raise AssertionError(
                    f"stale attempt produced no JSON payload: {stale_raw!r}"
                )
            if "stale" not in str(stale_error).lower():
                raise AssertionError(
                    f"refusal did not name a stale session: {stale_error!r}"
                )
            if stale_stdout is not None:
                raise AssertionError(
                    f"stale attempt reported kernel stdout (code may have run): "
                    f"{stale_stdout!r}"
                )
            if READ_ONLY_CODE.split("'")[1] in stale_raw:
                raise AssertionError("stale read-only code appears to have run")

            # --- Re-resolve via --file (no --session) ---------------------
            boot = (
                PREFIX
                + "async with cm.get_context() as ctx:\n"
                + "    for cell in list(ctx.cells.values()):\n"
                + "        ctx.run_cell(cell.id)\n"
            )
            boot_rc, boot_payload, _ = run_cli(
                url2, session_id=None, file_path=copy, code=boot, env=env
            )
            if boot_rc != 0 or not boot_payload or not boot_payload.get("success"):
                raise AssertionError(f"boot via --file failed: {boot_payload!r}")
            if (boot_payload.get("session") or {}).get("id") != sid_b:
                raise AssertionError(
                    "--file did not resolve to the fresh session B: "
                    f"{boot_payload.get('session')!r}"
                )

            read = (
                PREFIX
                + "import json as _json\n"
                + "async with cm.get_context() as ctx:\n"
                + "    _g = ctx.globals['gain'].value\n"
                + "    _g = int(_g) if hasattr(_g, 'item') else _g\n"
                + "    _p = [int(_x) for _x in ctx.globals['processed']]\n"
                + "    print("
                + repr(SNAPSHOT_MARKER)
                + ", _json.dumps([len(ctx.cells), _g, _p]))\n"
            )
            read_rc, read_payload, _ = run_cli(
                url2, session_id=None, file_path=copy, code=read, env=env
            )
            if read_rc != 0 or not read_payload or not read_payload.get("success"):
                raise AssertionError(f"read via --file failed: {read_payload!r}")
            if (read_payload.get("session") or {}).get("id") != sid_b:
                raise AssertionError("read via --file bound to the wrong session")
            line = _marker_line(read_payload)
            if line is None:
                raise AssertionError(f"no snapshot marker: {read_payload!r}")
            cells, gain, processed = json.loads(line)
            print(
                "RESOLVED_VIA_FILE",
                "session",
                sid_b,
                "cells",
                cells,
                "gain",
                gain,
                "processed",
                processed,
            )
            if cells != EXPECTED_CELLS:
                raise AssertionError(f"cell count {cells} != {EXPECTED_CELLS}")
            if gain != EXPECTED_GAIN:
                raise AssertionError(f"gain {gain!r} != {EXPECTED_GAIN}")
            if processed != EXPECTED_PROCESSED:
                raise AssertionError(f"processed {processed!r} != {EXPECTED_PROCESSED}")

            # --- Zero mutation --------------------------------------------
            if copy.read_bytes() != copy_before:
                raise AssertionError("disposable copy was mutated")
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("repo fixture was mutated")
            print("COPY_UNCHANGED True")
            print("FIXTURE_UNCHANGED True")
            print("NEW_PID_DISTINCT True")
            print("NEW_SESSION_DISTINCT True")
            print("NATIVE_RECONNECT_PROBE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_RECONNECT_PROBE_BLOCKER: {error}")
            return 3
        except AssertionError as error:
            print(f"NATIVE_RECONNECT_PROBE_FAIL: {error}")
            return 1
        finally:
            if first_proc is not None:
                stop(first_proc)
            if second_proc is not None:
                stop(second_proc)


if __name__ == "__main__":
    sys.exit(main())
