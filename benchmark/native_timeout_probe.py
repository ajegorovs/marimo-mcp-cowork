"""Bounded native unknown-outcome / slow-cell probe (marimo 0.25.1).

Purpose
-------
Characterize, on a disposable byte-identical copy of the committed fixture,
what a **client-side timeout** on a *mutating* native ``marimo pair execute``
call actually proves. The CLI streams ``/api/kernel/execute`` over SSE; its
HTTP socket has **no client timeout** (``client.py`` clears the socket timeout
after connect and reads until the terminal ``done`` event). When the caller
kills the CLI subprocess mid-stream, the cast is genuinely *unknown*: the
kernel may already have created and run the code, may be mid-execution, or may
never have started.

The server does register a disconnect watcher: on client disconnect it calls
``session.try_interrupt()`` (``_server/api/endpoints/execution.py``), so a
disconnect is *expected* to interrupt a running cell. This probe measures
whether that expectation holds by discriminating three facts from readback:

  * did the mutation land (a cell with the unique marker source exist), and
  * did the run **start** (a START global was set before the kill), and
  * did the run **finish** (a DONE global was set after the sleep completes).

Design (bounded, no retry)
--------------------------
- ONE mutating ``marimo pair execute`` (``--stream`` so partial stdout is
  observable) is started with a subprocess timeout SHORTER than the cell's
  own ``time.sleep``. On ``TimeoutExpired`` the CLI is killed; the mutation is
  **never retried**. The partial stdout is kept as evidence of how far the run
  got before the kill.
- After a bounded settle wait, state is inspected **read-only** with a
  SEPARATE CLI call capped by ``READ_TIMEOUT`` (code-mode cell listing and
  scratch ``globals()`` lookup of the START/DONE markers), plus a byte-read of
  the saved source on the disposable copy.
- The timeout outcome is then classified as one of:
    ``COMPLETED``       run finished after the client timed out (timeout is
                        NOT proof of non-execution);
    ``STARTED_ABORTED`` run began, was interrupted before the sleep finished;
    ``NOT_EXECUTED``    cell created but body never ran;
    ``NOT_CREATED``     the mutation never landed.
- Any precondition/infrastructure failure is reported as ``BLOCKER``.

Isolation / bounds
------------------
Boots a headless ``marimo edit`` server on loopback against the disposable
copy, with isolated state (``USERPROFILE``/``XDG_STATE_HOME`` under the scratch
dir). Only the probe's own process tree is stopped (``finally``). The whole
experiment is individually bounded at readiness, mutation and readback; the
observed local run took 18s, but an infrastructure timeout can take longer.
Committed fixture must stay byte-identical. No secrets or absolute paths are
printed.

Exit 0 with ``NATIVE_TIMEOUT_PROBE_<CLASS>`` for a resolved class, or non-zero
with a ``..._BLOCKER`` marker.
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

# The cell sleeps far longer than the client-side subprocess timeout, so the
# kill lands while the sleep is still in progress.
SLEEP_SECONDS = 8.0
CLI_TIMEOUT = 5.0
# Bounded settle wait after the kill: long enough for a full, uninterrupted
# sleep to finish before the read-only inspection.
SETTLE_SECONDS = 8.0
READ_TIMEOUT = 25

CREATED_MARK = "PROBE_CREATED"
STARTED_MARK = "PROBE_CELL_STARTED"
FOUND_MARK = "PROBE_FOUND"
ERR_MARK = "PROBE_ERR"
START_MARK = "PROBE_START"
DONE_MARK = "PROBE_DONE"


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def _run(url, sid, code, *, timeout, stream=False):
    """Invoke the native pair CLI once; return (rc, stdout, stderr, timed_out).

    Never retried. On ``TimeoutExpired`` the partial streamed stdout is
    returned with ``timed_out=True``.
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
        sid,
    ]
    if stream:
        argv.append("--stream")
    argv += ["-c", code]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return None, out, err, True
    return result.returncode, result.stdout, result.stderr, False


def _payload(stdout):
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _marker_value(stdout, name):
    prefix = name + " "
    for line in str(stdout).splitlines():
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
    started = time.monotonic()
    original = FIXTURE.read_bytes()
    digest = uuid.uuid4().hex[:10]
    start_var = f"probe_to_start_{digest}"
    done_var = f"probe_to_done_{digest}"
    token = f"TOKEN_{digest}"

    cmd_shape = (
        "<venv>/python -m marimo pair execute --url <loopback-url> "
        "--session <sid> --stream -c <mutation>"
    )

    with tempfile.TemporaryDirectory(
        prefix="marimo-native-timeout-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_timeout.py"
        copy.write_bytes(original)
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

            sid = str(uuid.uuid4())
            query = urllib.parse.urlencode({"session_id": sid, "file": str(copy)})
            with urllib.request.urlopen(f"{url}/sse?{query}", timeout=15) as stream:
                assert any(b"kernel-ready" in line for line in stream)
            with urllib.request.urlopen(url + "/api/sessions", timeout=3) as response:
                sessions = json.load(response)
            if list(sessions) != [sid]:
                raise Blocker(f"unexpected sessions {list(sessions)}")
            bound = sessions[sid].get("path") or sessions[sid]["filename"]
            if Path(bound).resolve() != copy.resolve():
                raise Blocker("session bound to wrong notebook")

            prefix = "import marimo._code_mode as cm\n"

            def read_code():
                return (
                    prefix
                    + "async with cm.get_context() as ctx:\n"
                    + "    for _c in ctx.cells.values():\n"
                    + f"        if {start_var!r} in _c.code:\n"
                    + f"            print({FOUND_MARK!r}, str(_c.id), _c.status)\n"
                    + "            for _e in _c.errors:\n"
                    + f"                print({ERR_MARK!r}, _e.kind)\n"
                    + f"print({START_MARK!r}, repr(globals().get({start_var!r})))\n"
                    + f"print({DONE_MARK!r}, repr(globals().get({done_var!r})))\n"
                )

            # --- Baseline read-only: markers absent, no marker cell yet ------
            rc, out, _err, timed = _run(url, sid, read_code(), timeout=READ_TIMEOUT)
            base = _payload(out)
            if timed or base is None or not base.get("success"):
                raise Blocker(f"baseline read failed rc={rc} timed={timed}")
            base_start = _marker_value(base.get("stdout", ""), START_MARK)
            base_done = _marker_value(base.get("stdout", ""), DONE_MARK)
            base_found = _marker_value(base.get("stdout", ""), FOUND_MARK)
            print(
                "BASELINE_START",
                base_start,
                "BASELINE_DONE",
                base_done,
                "BASELINE_FOUND",
                base_found,
            )
            if base_found is not None or base_start != "None" or base_done != "None":
                raise Blocker("marker already present before mutation")

            # --- The one mutating call, killed before the sleep completes ----
            cell_source = (
                "import time\n"
                f"print({STARTED_MARK!r}, flush=True)\n"
                f"{start_var} = {token!r}\n"
                f"time.sleep({SLEEP_SECONDS})\n"
                f"{done_var} = {token!r}\n"
            )
            mutation = (
                prefix
                + "async with cm.get_context() as ctx:\n"
                + f"    _cid = ctx.create_cell({cell_source!r}, hide_code=False)\n"
                + "    ctx.run_cell(_cid)\n"
                + f"    print({CREATED_MARK!r}, _cid)\n"
            )
            t_mutation = time.monotonic()
            rc, m_out, _m_err, timed = _run(
                url, sid, mutation, timeout=CLI_TIMEOUT, stream=True
            )
            elapsed = time.monotonic() - t_mutation
            if not timed:
                # The call returned before the timeout: the mutation is
                # complete, but that is not the unknown-outcome case under
                # test. Report it rather than silently reclassifying.
                raise Blocker(
                    f"mutation returned before {CLI_TIMEOUT}s (rc={rc}); "
                    "slow-cell window not exercised"
                )
            print("MUTATION_TIMED_OUT", True, "elapsed_s", round(elapsed, 1))
            print("COMMAND", cmd_shape)
            print("PARTIAL_CREATED", CREATED_MARK in m_out)
            print("PARTIAL_STARTED", STARTED_MARK in m_out)

            # --- Bounded settle wait, then read-only inspection --------------
            time.sleep(SETTLE_SECONDS)
            rc, out, _err, timed = _run(url, sid, read_code(), timeout=READ_TIMEOUT)
            after = _payload(out)
            if timed or rc != 0 or after is None or after.get("success") is not True:
                raise Blocker(f"readback failed rc={rc} timed={timed}")
            stdout = after.get("stdout", "")
            found = _marker_value(stdout, FOUND_MARK)
            found_parts = found.split(" ") if found else None
            errs = [
                line[len(ERR_MARK) + 1 :]
                for line in stdout.splitlines()
                if line.startswith(ERR_MARK + " ")
            ]
            start_v = _marker_value(stdout, START_MARK)
            done_v = _marker_value(stdout, DONE_MARK)
            saved = copy.read_text(encoding="utf-8")

            print("READBACK_EXIT", rc, "SUCCESS", after.get("success"))
            print("CELL_FOUND", bool(found_parts))
            if found_parts:
                print("CELL_STATUS", found_parts[1] if len(found_parts) > 1 else None)
            print("CELL_ERRORS", errs)
            print("START_GLOBAL_SET", start_v == repr(token))
            print("DONE_GLOBAL_SET", done_v == repr(token))
            print("SAVED_SOURCE_PRESENT", start_var in saved)

            if FIXTURE.read_bytes() != original:
                raise Blocker("repo fixture was mutated")

            # --- Classification (timeout outcome resolved by readback) -------
            if done_v == repr(token):
                klass = "COMPLETED"
                note = (
                    "client timed out but the run finished server-side: "
                    "a timeout is NOT proof of non-execution"
                )
            elif start_v == repr(token):
                klass = "STARTED_ABORTED"
                note = (
                    "run began before the kill and did not finish "
                    "(disconnect interrupt); execution started and was aborted"
                )
            elif found_parts:
                klass = "NOT_EXECUTED"
                note = "cell was created but its body never set the START global"
            else:
                klass = "NOT_CREATED"
                note = (
                    "no cell carrying the marker source exists; mutation did not land"
                )

            print("CLASSIFICATION", klass)
            print("CLASSIFICATION_NOTE", note)
            print("TOTAL_SECONDS", round(time.monotonic() - started, 1))
            print(f"NATIVE_TIMEOUT_PROBE_{klass}")
            return 0
        except Blocker as error:
            print(f"NATIVE_TIMEOUT_PROBE_BLOCKER: {error}")
            return 3
        except Exception as error:  # noqa: BLE001 - report, don't mask
            print(f"NATIVE_TIMEOUT_PROBE_FAIL: {type(error).__name__}: {error}")
            return 1
        finally:
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
