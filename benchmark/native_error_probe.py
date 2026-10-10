"""Native error-channel probe for the raising benchmark cell (marimo 0.25.1).

Purpose
-------
Characterize, on a disposable byte-identical copy of the committed fixture,
how a *raising* document cell — the SAME source the A/B wrapper uses
(``raise RuntimeError("a4 fault marker")``) — surfaces across marimo's two
error channels when driven through the **native** ``marimo pair execute`` CLI
and ``marimo._code_mode`` (no MCP wrapper, no browser):

  * structured channel: ``NotebookCell.errors`` (``kind`` / ``msg`` /
    ``exception``), and
  * console channel:    ``NotebookCell.console_outputs`` (the stderr traceback
    stream recorded for the cell).

The MCP-facing companion is
``tests/marimo_inspect/live/test_benchmark_error_probe.py``; this probe checks
that the platform underneath it reports the same split.

Isolation and bounds
--------------------
- Boots a headless ``marimo edit`` server on loopback against a disposable
  copy, with isolated state (``USERPROFILE`` / ``XDG_STATE_HOME`` under the
  scratch dir).
- Exactly **two** kernel executions (``marimo pair execute`` calls):
  1. code-mode: create the fault cell and run it in one batch, printing its id;
  2. a SEPARATE execution, launched only after the first has exited — because
     the code-mode output snapshot is frozen at scratchpad start — that reads
     the cell's ``status``, ``errors``, and ``console_outputs``.
- The session is bound explicitly with ``--session`` and confirmed to point at
  the disposable copy via ``/api/sessions``.
- The process tree is stopped in a ``finally`` block. The committed fixture
  must stay byte-identical.

Exit 0 with ``NATIVE_ERROR_PROBE_PASS``, or non-zero with a ``...FAIL`` /
``...BLOCKER`` marker. No secrets or absolute paths are printed.
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

# The exact source string the A/B wrapper carries, and its in-message marker.
FAULT_SOURCE = 'raise RuntimeError("a4 fault marker")'
FAULT_MARKER = "a4 fault marker"

# In-kernel print markers the harness parses out of the CLI's stdout.
CREATE_MARKER = "PROBE_CREATED_CELL_ID"
STATUS_MARKER = "PROBE_CELL_STATUS"
CODE_MARKER = "PROBE_CELL_CODE"
STRUCTURED_MARKER = "PROBE_STRUCTURED_ERROR"
CONSOLE_MARKER = "PROBE_CONSOLE_OUTPUT"


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def execute(url, session_id, env, code):
    """Run *code* in the live kernel via the native pair CLI; parse JSON.

    Returns ``(returncode, payload)``. The payload is the CLI's JSON
    envelope: ``{success, output, stdout, stderr, session:{id}, [next]}``.
    """
    result = subprocess.run(
        [
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
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"success": None, "stdout": result.stdout, "stderr": result.stderr}
    return result.returncode, payload


def _marker(payload, name):
    """Return the value on the first stdout line ``<name> <value>``."""
    prefix = name + " "
    for line in str(payload.get("stdout", "")).splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _marker_rows(payload, name):
    """Return the space-free remainder of every ``<name> ...`` stdout line."""
    prefix = name + " "
    return [
        line[len(prefix) :]
        for line in str(payload.get("stdout", "")).splitlines()
        if line.startswith(prefix)
    ]


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
        prefix="marimo-native-error-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_error.py"
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

            # Open one session bound to the disposable copy.
            sid = str(uuid.uuid4())
            query = urllib.parse.urlencode({"session_id": sid, "file": str(copy)})
            with urllib.request.urlopen(f"{url}/sse?{query}", timeout=15) as stream:
                assert any(b"kernel-ready" in line for line in stream)

            # Confirm the binding: exactly this session, pointing at the copy.
            with urllib.request.urlopen(url + "/api/sessions", timeout=3) as response:
                sessions = json.load(response)
            assert list(sessions) == [sid], sessions
            bound_path = sessions[sid].get("path") or sessions[sid]["filename"]
            assert Path(bound_path).resolve() == copy.resolve(), sessions

            # --- Execution 1: create + run the fault cell (one batch) ----------
            create_code = (
                "import marimo._code_mode as cm\n"
                "async with cm.get_context() as ctx:\n"
                f"    _cid = ctx.create_cell({FAULT_SOURCE!r}, hide_code=False)\n"
                "    ctx.run_cell(_cid)\n"
                f"    print({CREATE_MARKER!r}, _cid)\n"
            )
            create_rc, created = execute(url, sid, env, create_code)
            cell_id = _marker(created, CREATE_MARKER)
            if not cell_id:
                raise Blocker(
                    f"execution 1 did not report a created cell id: {created!r}"
                )

            # --- Execution 2 (SEPARATE call): read the frozen snapshot ---------
            read_code = (
                "import marimo._code_mode as cm\n"
                "async with cm.get_context() as ctx:\n"
                f"    cell = ctx.cells[{cell_id!r}]\n"
                f"    print({STATUS_MARKER!r}, cell.status)\n"
                f"    print({CODE_MARKER!r}, repr(cell.code))\n"
                "    for _e in cell.errors:\n"
                f"        print({STRUCTURED_MARKER!r}, _e.kind, repr(str(_e.msg)))\n"
                "    for _o in cell.console_outputs:\n"
                f"        print({CONSOLE_MARKER!r}, _o.channel.value, repr(str(_o.data)))\n"
            )
            read_rc, observed = execute(url, sid, env, read_code)

            status = _marker(observed, STATUS_MARKER)
            code_repr = _marker(observed, CODE_MARKER)
            structured = _marker_rows(observed, STRUCTURED_MARKER)
            console = _marker_rows(observed, CONSOLE_MARKER)

            print(
                "EXEC1 exit",
                create_rc,
                "success",
                created.get("success"),
                "cell_id_present",
                bool(cell_id),
            )
            print(
                "EXEC2 exit",
                read_rc,
                "success",
                observed.get("success"),
                "status",
                status,
            )
            print("STRUCTURED_ERRORS", structured)
            # Do not echo the raw traceback: it embeds an absolute temp path.
            print(
                "CONSOLE_OUTPUTS",
                [
                    (
                        row.split(" ", 1)[0],
                        FAULT_MARKER in row,
                        "Traceback (most recent call last)" in row,
                    )
                    for row in console
                ],
            )

            # --- Assert real observed channels ---------------------------------
            if status != "exception":
                raise AssertionError(f"expected exception status, got {status!r}")

            # Live source of the created cell is the wrapper's fault source.
            if code_repr is None or FAULT_SOURCE not in code_repr:
                raise AssertionError(f"live cell source mismatch: {code_repr!r}")

            # Structured channel: exactly one runtime error carrying the marker.
            runtime_errors = [row for row in structured if row.startswith("runtime ")]
            if len(runtime_errors) != 1:
                raise AssertionError(
                    f"expected one runtime structured error, got {structured!r}"
                )
            if FAULT_MARKER not in runtime_errors[0]:
                raise AssertionError(
                    f"marker missing from structured error: {runtime_errors[0]!r}"
                )

            # Console channel: a stderr output carrying marker + traceback.
            stderr_outputs = [row for row in console if row.startswith("stderr ")]
            if not stderr_outputs:
                raise AssertionError(f"no stderr console output observed: {console!r}")
            console_data = "\n".join(stderr_outputs)
            if FAULT_MARKER not in console_data:
                raise AssertionError(
                    f"marker missing from console stderr: {console_data!r}"
                )
            if "Traceback (most recent call last)" not in console_data:
                raise AssertionError(
                    f"traceback missing from console stderr: {console_data!r}"
                )

            # --- Saved source on the disposable notebook, fixture pristine -----
            saved = copy.read_text(encoding="utf-8")
            if FAULT_SOURCE not in saved:
                raise AssertionError("fault source not saved to disposable copy")
            if FIXTURE.read_bytes() != original:
                raise AssertionError("repo fixture was mutated")

            print("SAVED_SOURCE_PRESENT", FAULT_SOURCE in saved)
            print("FIXTURE_UNCHANGED", FIXTURE.read_bytes() == original)
            print("NATIVE_ERROR_PROBE_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_ERROR_PROBE_BLOCKER: {error}")
            return 3
        except Exception as error:  # noqa: BLE001 - report, don't mask
            print(f"NATIVE_ERROR_PROBE_FAIL: {type(error).__name__}: {error}")
            return 1
        finally:
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
