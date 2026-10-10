"""Bounded native 0.25.1 cycle-structural-refusal probe (disposable copy).

Question
--------
When a *single* ``marimo._code_mode`` context queues two cells that close a
dependency cycle — ``a4_cycle_left = a4_cycle_right + 1`` and
``a4_cycle_right = a4_cycle_left + 1`` — with validation NOT skipped
(``cm.get_context()`` default, ``skip_validation=False``), does the native
code mode refuse the whole batch with the specific cycle exception, and does
it leave **no** partial graph mutation and **no** disk change?

Mechanism under test (installed marimo 0.25.1)
----------------------------------------------
``AsyncCodeModeContext.__aexit__`` runs ``_dry_run_compile(ops)`` before
``_apply_ops``. ``_dry_run_compile`` compiles and temporarily registers every
queued cell, computes ``new_cycles = set(graph.cycles) - existing_cycles``,
raises ``RuntimeError(f"Cycles detected: {new_cycles}...")`` *before* any real
mutation, and its ``finally`` block deletes the dry-run cells — so the live
graph and the on-disk file must be untouched.

Method (loopback only, no installation, four bounded kernel executions)
----------------------------------------------------------------------
1. Copy the committed fixture byte-identically into a disposable temp dir.
2. Boot headless ``marimo edit`` on loopback with isolated state.
3. Open one ``/sse`` session bound to the copy; confirm binding.
4. Precondition: run the *original* cells so the base graph is instantiated;
   separately snapshot live cell IDs and code and the on-disk bytes.
5. The probe: one context queues the two cycle cells with default validation.
   Assert the CLI exits nonzero with ``success: false`` and ``Cycles detected``.
6. Re-enter a fresh context and snapshot the live cell set again; assert it is
   byte-for-byte identical to the precondition snapshot (no partial graph),
   that neither cycle variable is present, that the disposable file is
   byte-unchanged, and that the repo fixture is pristine.
7. Stop the process tree and assert the loopback server is no longer serving.

Exit 0 with ``NATIVE_CYCLE_PROBE_PASS``, or non-zero with
``NATIVE_CYCLE_PROBE_BLOCKER`` / ``NATIVE_CYCLE_PROBE_FAIL``. No secrets or
absolute paths are printed.
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

# In-kernel marker the harness parses out of the CLI's stdout.
SNAPSHOT_MARKER = "PROBE_SNAPSHOT"

# The exact cycle sources; each defines one name and reads the other.
CYCLE_LEFT = "a4_cycle_left = a4_cycle_right + 1"
CYCLE_RIGHT = "a4_cycle_right = a4_cycle_left + 1"

EXPECTED_ERROR = "Cycles detected"
EXPECTED_EXC = "RuntimeError"


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def execute(url, session_id, env, code):
    """Run *code* via the native ``marimo pair execute`` CLI; parse JSON.

    Returns ``(returncode, payload, argv)``.
    """
    argv = [
        sys.executable,
        "-m",
        "marimo",
        "pair",
        "execute",
        "--url",
        url,
        "--session",
        session_id,
        "-c",
        code,
    ]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "success": None,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    return result.returncode, payload, argv


def _snapshot_line(payload):
    """Return the single ``PROBE_SNAPSHOT`` stdout line, or None."""
    prefix = SNAPSHOT_MARKER + " "
    for line in str(payload.get("stdout", "")).splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def stop(proc):
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


def main():
    original = FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-cycle-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_cycle.py"
        copy.write_bytes(original)
        disk_before = copy.read_bytes()
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

            # Open one session bound to the disposable copy.
            sid = str(uuid.uuid4())
            query = urllib.parse.urlencode({"session_id": sid, "file": str(copy)})
            with urllib.request.urlopen(f"{url}/sse?{query}", timeout=15) as stream:
                assert any(b"kernel-ready" in line for line in stream)

            with urllib.request.urlopen(url + "/api/sessions", timeout=3) as response:
                sessions = json.load(response)
            if list(sessions) != [sid]:
                raise Blocker(f"unexpected sessions: {sorted(sessions)}")
            bound_path = sessions[sid].get("path") or sessions[sid]["filename"]
            if Path(bound_path).resolve() != copy.resolve():
                raise Blocker("session bound to an unexpected path")

            prefix = "import marimo._code_mode as cm\n"

            # --- Precondition (execution 1): instantiate originals + snapshot.
            boot = (
                prefix
                + "async with cm.get_context() as ctx:\n"
                + "    for cell in list(ctx.cells.values()):\n"
                + "        ctx.run_cell(cell.id)\n"
            )
            boot_rc, boot_payload, _boot_argv = execute(url, sid, env, boot)
            if boot_rc != 0 or not boot_payload.get("success"):
                raise Blocker(
                    f"original-cell precondition failed: "
                    f"code={boot_rc} {boot_payload.get('stderr')!r}"
                )

            snapshot = (
                prefix
                + "async with cm.get_context() as ctx:\n"
                + "    print("
                + repr(SNAPSHOT_MARKER)
                + ", sorted((str(c.id), c.code) for c in ctx.cells.values()))\n"
            )
            before_rc, before_payload, _ = execute(url, sid, env, snapshot)
            if before_rc != 0 or not before_payload.get("success"):
                raise Blocker(f"pre-snapshot failed: {before_payload!r}")
            before_line = _snapshot_line(before_payload)
            if before_line is None:
                raise Blocker("pre-snapshot reported no cell set")

            # --- Probe (execution 2): queue the cycle, default validation.
            cycle_code = (
                prefix
                + "async with cm.get_context() as ctx:\n"
                + f"    ctx.create_cell({CYCLE_LEFT!r}, hide_code=False)\n"
                + f"    ctx.create_cell({CYCLE_RIGHT!r}, hide_code=False)\n"
            )
            (
                cycle_rc,
                cycle_payload,
                cycle_argv,
            ) = execute(url, sid, env, cycle_code)

            error_channel = "\n".join(
                str(cycle_payload.get(k, "")) for k in ("stderr", "stdout", "error")
            )
            summary = next(
                (line for line in reversed(error_channel.splitlines()) if line.strip()),
                "",
            )
            print("PRECONDITION_EXECUTED", boot_rc == 0)
            print("CYCLE_SOURCE_SUBMITTED", bool(cycle_argv))
            print(
                "PRE_SNAPSHOT",
                before_line,
            )
            print(
                "CYCLE exit",
                cycle_rc,
                "success",
                cycle_payload.get("success"),
                "error",
                summary,
            )

            # --- Assert the specific structural refusal --------------------
            if cycle_rc == 0:
                raise AssertionError(
                    f"cycle batch unexpectedly exited 0: {cycle_payload!r}"
                )
            if cycle_payload.get("success") is not False:
                raise AssertionError(
                    f"expected success:false, got {cycle_payload.get('success')!r}"
                )
            if EXPECTED_ERROR not in error_channel:
                raise AssertionError(
                    f"{EXPECTED_ERROR!r} missing from error channel: {error_channel!r}"
                )
            if EXPECTED_EXC not in error_channel:
                raise AssertionError(
                    f"{EXPECTED_EXC!r} (cycle exception type) missing from "
                    f"error channel: {error_channel!r}"
                )

            # --- No partial graph: fresh snapshot must equal the pre one ----
            after_rc, after_payload, _ = execute(url, sid, env, snapshot)
            if after_rc != 0 or not after_payload.get("success"):
                raise AssertionError(f"post-snapshot failed: {after_payload!r}")
            after_line = _snapshot_line(after_payload)
            print("POST_SNAPSHOT", after_line)
            if after_line != before_line:
                raise AssertionError(
                    "live graph changed after refused cycle batch:\n"
                    f"  before={before_line!r}\n  after ={after_line!r}"
                )
            for name in ("a4_cycle_left", "a4_cycle_right"):
                if name in str(after_line):
                    raise AssertionError(
                        f"refused cycle cell {name!r} leaked into the live graph"
                    )

            # --- No disk change -------------------------------------------
            if copy.read_bytes() != disk_before:
                raise AssertionError("disposable copy was mutated on disk")
            if FIXTURE.read_bytes() != original:
                raise AssertionError("repo fixture was mutated")

            # --- Server must be stopped after we tear it down ---------------
            if proc.poll() is not None:
                raise AssertionError(
                    f"loopback server exited prematurely ({proc.returncode})"
                )
            print("GRAPH_UNCHANGED True")
            print("DISK_UNCHANGED True")
            print("FIXTURE_UNCHANGED True")
            print("NATIVE_CYCLE_PROBE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_CYCLE_PROBE_BLOCKER: {error}")
            return 3
        except Exception as error:  # noqa: BLE001 - report, don't mask
            print(f"NATIVE_CYCLE_PROBE_FAIL: {type(error).__name__}: {error}")
            return 1
        finally:
            stop(proc)
            # Confirm the loopback endpoint no longer answers.
            stopped = True
            try:
                with urllib.request.urlopen(url + "/api/version", timeout=1):
                    stopped = False
            except OSError:
                stopped = True
            print("SERVER_STOPPED", stopped)
            if not stopped:
                raise RuntimeError(
                    "NATIVE_CYCLE_PROBE_FAIL: loopback server still serving"
                )


if __name__ == "__main__":
    sys.exit(main())
