"""Native independent-actor stale-refusal probe (marimo 0.25.1).

Purpose
-------
Demonstrate, on a disposable byte-identical copy of the committed fixture,
that a *guarded* code-mode edit refuses with ``StaleCellError`` when the cell's
version was advanced out-of-band by an **independent actor**: a raw
``POST /api/document/transaction`` (header ``Marimo-Session-Id``) carrying a
``set-code`` change.

The kernel-global agent read tracker is only advanced by explicit reads (e.g.
``ctx.cells[cell_id].code``). The HTTP transaction bumps ``cell.version`` via
``NotebookDocument.apply`` but does **not** touch that tracker, so a guarded
edit whose baseline was recorded at the older version must trip the guard.

Isolation
---------
Boots a headless marimo server on loopback against a disposable copy, with
isolated state (``USERPROFILE``/``XDG_STATE_HOME`` under the scratch dir), and
stops the process tree in a ``finally`` block. The committed fixture must stay
byte-identical.

Assertions
----------
- The independent transaction applies (HTTP 200) and the live source changes.
- The following guarded edit fails, and the failure is specifically
  ``StaleCellError`` (distinguished from a generic kernel failure).
- Around the rejected edit: live source and the saved copy are unchanged.
- The committed fixture bytes remain intact.

No secrets or absolute paths are printed.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
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

# A unique marker printed inside the kernel so the harness can parse cells.
LIST_MARKER = "PROBE_CELLS_JSON"


def execute(url, notebook, env, code):
    """Run *code* in the live kernel via `marimo pair execute`; parse JSON."""
    result = subprocess.run(
        [
            sys.executable,
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
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"success": False, "stdout": "", "stderr": result.stderr}
    return result.returncode, payload


def transaction(url, session_id, cell_id, code):
    """Apply a `set-code` document transaction as an independent actor."""
    body = json.dumps(
        {"changes": [{"type": "set-code", "cellId": cell_id, "code": code}]}
    ).encode("utf-8")
    request = urllib.request.Request(
        url + "/api/document/transaction",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Marimo-Session-Id": session_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {"detail": error.read().decode("utf-8", "replace")}


def stop(proc):
    parent = psutil.Process(proc.pid)
    children = parent.children(recursive=True)
    for child in children:
        child.terminate()
    if proc.poll() is None:
        proc.terminate()
    psutil.wait_procs(children, timeout=5)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _live_code(url, copy, env, cell_id):
    """Observe a cell's live source WITHOUT recording an agent read.

    Reads the document snapshot directly (private) so the staleness probe is
    not disturbed by the observation itself.
    """
    snippet = (
        PREFIX
        + "async with cm.get_context() as ctx:\n"
        + f"    _c = ctx._document.get({cell_id!r})\n"
        + "    print('PROBE_LIVE', repr(_c.code if _c else None))"
    )
    code, payload = execute(url, copy, env, snippet)
    assert code == 0 and payload.get("success"), payload
    for line in payload.get("stdout", "").splitlines():
        if line.startswith("PROBE_LIVE "):
            value = line[len("PROBE_LIVE ") :]
            return None if value == "None" else ast.literal_eval(value)
    raise AssertionError(f"no live marker in {payload!r}")


def main():
    original = FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-stale-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_stale.py"
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
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError(f"server exited {proc.returncode}")
                try:
                    with urllib.request.urlopen(
                        url + "/api/version", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("server readiness timeout")

            sid = str(uuid.uuid4())
            query = urllib.parse.urlencode({"session_id": sid, "file": str(copy)})
            with urllib.request.urlopen(f"{url}/sse?{query}", timeout=15) as stream:
                assert any(b"kernel-ready" in line for line in stream)
            with urllib.request.urlopen(url + "/api/sessions", timeout=3) as response:
                sessions = json.load(response)
            assert list(sessions) == [sid], sessions
            assert (
                Path(sessions[sid].get("path") or sessions[sid]["filename"]).resolve()
                == copy.resolve()
            )

            boot = (
                PREFIX
                + "async with cm.get_context() as ctx:\n"
                + "    for cell in ctx.cells.values():\n"
                + "        ctx.run_cell(cell.id)"
            )
            code, payload = execute(url, copy, env, boot)
            assert code == 0 and payload.get("success"), (code, payload)

            # Step 1: list cells. Accessing `.code` records the agent read
            # baseline at the current cell version for every non-empty cell.
            listing = (
                PREFIX
                + "import json as _json\n"
                + "async with cm.get_context() as ctx:\n"
                + f"    print({LIST_MARKER!r}, _json.dumps([[str(c.id), c.code]"
                + " for c in ctx.cells.values()]))"
            )
            code, snapshot = execute(url, copy, env, listing)
            assert code == 0 and snapshot.get("success"), snapshot
            cells = None
            for line in snapshot.get("stdout", "").splitlines():
                if line.startswith(LIST_MARKER + " "):
                    cells = json.loads(line[len(LIST_MARKER) + 1 :])
            assert cells, snapshot
            target_id = None
            target_code = None
            for cell_id, cell_code in cells:
                if "input_vector = np.array(" in cell_code:
                    target_id, target_code = cell_id, cell_code
                    break
            assert target_id and target_code, cells
            assert "[2, 4, 6, 8]" in target_code, target_code

            # Step 2: independent actor advances the cell version out-of-band.
            actor_code = target_code.replace("[2, 4, 6, 8]", "[9, 9, 9, 9]")
            assert actor_code != target_code
            status, response = transaction(url, sid, target_id, actor_code)
            assert status == 200, (status, response)

            # Let the kernel's document reflect the transaction.
            live = None
            for _ in range(20):
                live = _live_code(url, copy, env, target_id)
                if live == actor_code:
                    break
                time.sleep(0.2)
            assert live == actor_code, (live, actor_code)

            # Step 3: guarded edit must refuse specifically as stale.
            attempt = target_code.replace("[2, 4, 6, 8]", "[7, 7, 7, 7]")
            live_before = _live_code(url, copy, env, target_id)
            disk_before = copy.read_bytes()
            edit = (
                PREFIX
                + "async with cm.get_context() as ctx:\n"
                + f"    ctx.edit_cell({target_id!r}, code={attempt!r})"
            )
            code, refusal = execute(url, copy, env, edit)
            stderr = str(refusal.get("stderr", ""))
            live_after = _live_code(url, copy, env, target_id)
            disk_after = copy.read_bytes()

            print(
                "STALE_REFUSAL",
                "exit",
                code,
                "success",
                refusal.get("success"),
                "stale_error",
                "StaleCellError" in stderr,
                "generic_keyerror",
                "KeyError" in stderr,
            )

            # The refusal must be a genuine stale-guard trip, not a generic
            # failure (missing cell, syntax error, transport error).
            assert code != 0 and refusal.get("success") is False, refusal
            assert "StaleCellError" in stderr, stderr
            assert "was modified since the agent last read it" in stderr, stderr
            assert "KeyError" not in stderr, stderr

            # Around the rejected edit, the live source and saved copy are
            # unchanged, and the actor's change is still what is live.
            assert live_before == actor_code, (live_before, actor_code)
            assert live_after == live_before, (live_after, live_before)
            assert disk_after == disk_before, "saved copy changed"
            assert live_after != target_code

            assert FIXTURE.read_bytes() == original
            print("NATIVE_STALE_PROBE_PASS")
        finally:
            stop(proc)


if __name__ == "__main__":
    main()
