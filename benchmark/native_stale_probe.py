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

The verdict/setup checks are factored into ``verify_stale_refusal``,
``verify_session_binding`` and ``load_execute_payload`` so they can be exercised
without a kernel (see ``tests/marimo_inspect/test_benchmark_stale_verdict.py``).

No credentials are provisioned by this probe. Failure diagnostics and Python
tracebacks can contain disposable absolute paths; sanitize private run logs
before publishing them.
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


def require(condition, *diagnostics):
    """Always-on verdict check; a bare ``assert`` is stripped under ``python -O``."""
    if condition:
        return
    if not diagnostics:
        raise AssertionError("required condition failed")
    raise AssertionError(diagnostics[0] if len(diagnostics) == 1 else diagnostics)


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


def load_execute_payload(returncode, stdout, stderr):
    """Decode a ``marimo pair execute`` result into its structured payload.

    A CLI that did not emit JSON is a setup failure, never a silent
    ``success=False`` payload: the raw stdout/stderr are preserved in the raised
    error, so no structured check below can pass on unparsed output.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            "marimo pair execute emitted non-JSON stdout "
            f"(exit {returncode}); stdout={stdout!r}; stderr={stderr!r}"
        ) from error
    require(
        isinstance(payload, dict),
        f"pair execute payload is not an object: {payload!r}",
    )
    return payload


def verify_session_binding(sessions, session_id, copy_path):
    """Setup gate: exactly the disposable copy's single session, on this file."""
    require(
        list(sessions) == [session_id],
        f"session census {sessions!r} is not exactly [{session_id!r}]",
    )
    reported = sessions[session_id].get("path") or sessions[session_id]["filename"]
    require(
        Path(reported).resolve() == Path(copy_path).resolve(),
        f"session file {reported!r} does not resolve to the disposable copy",
    )


def verify_stale_refusal(obs):
    """Guarded edit must refuse *specifically* as stale, changing nothing."""
    payload = obs["attempt_payload"]
    stderr = str(obs["attempt_kernel_stderr"])
    require(isinstance(payload, dict), f"refusal payload is not an object: {payload!r}")
    # The refusal must be a genuine stale-guard trip, not a generic failure
    # (missing cell, syntax error, transport error).
    require(
        obs["attempt_exit"] != 0 and payload.get("success") is False,
        f"guarded edit did not fail: exit={obs['attempt_exit']} payload={payload!r}",
    )
    require(
        "StaleCellError" in stderr,
        f"refusal is not a stale-guard trip: {stderr!r}",
    )
    require(
        "was modified since the agent last read it" in stderr,
        f"stale refusal message missing: {stderr!r}",
    )
    require(
        "KeyError" not in stderr,
        f"refusal is a generic missing-cell failure, not stale: {stderr!r}",
    )

    # Around the rejected edit the live source and saved copy are unchanged,
    # and the independent actor's change is still what is live.
    require(
        obs["live_before"] == obs["actor_code"],
        f"live source before the refusal is not the actor's: {obs['live_before']!r}",
    )
    require(
        obs["live_after"] == obs["live_before"],
        f"refused edit changed the live source: {obs['live_after']!r}",
    )
    require(
        obs["disk_after"] == obs["disk_before"],
        "refused edit changed the saved disposable copy",
    )
    require(
        obs["live_after"] != obs["original_code"],
        "live source reverted to the pre-transaction code",
    )
    require(
        obs["canonical_after"] == obs["canonical_before"],
        "committed fixture bytes changed",
    )


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
    payload = load_execute_payload(result.returncode, result.stdout, result.stderr)
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
    require(code == 0 and payload.get("success"), f"_live_code failed: {payload!r}")
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
                require(
                    any(b"kernel-ready" in line for line in stream),
                    "no kernel-ready event in /sse stream",
                )
            with urllib.request.urlopen(url + "/api/sessions", timeout=3) as response:
                sessions = json.load(response)
            verify_session_binding(sessions, sid, copy)

            boot = (
                PREFIX
                + "async with cm.get_context() as ctx:\n"
                + "    for cell in ctx.cells.values():\n"
                + "        ctx.run_cell(cell.id)"
            )
            code, payload = execute(url, copy, env, boot)
            require(
                code == 0 and payload.get("success"),
                f"boot run failed: exit={code} payload={payload!r}",
            )

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
            require(
                code == 0 and snapshot.get("success"),
                f"cell listing failed: exit={code} payload={snapshot!r}",
            )
            cells = None
            for line in snapshot.get("stdout", "").splitlines():
                if line.startswith(LIST_MARKER + " "):
                    cells = json.loads(line[len(LIST_MARKER) + 1 :])
            require(cells, f"no {LIST_MARKER} line in listing: {snapshot!r}")
            target_id = None
            target_code = None
            for cell_id, cell_code in cells:
                if "input_vector = np.array(" in cell_code:
                    target_id, target_code = cell_id, cell_code
                    break
            require(target_id and target_code, f"no target cell in census: {cells!r}")
            require(
                "[2, 4, 6, 8]" in target_code,
                f"unexpected target source: {target_code!r}",
            )

            # Step 2: independent actor advances the cell version out-of-band.
            actor_code = target_code.replace("[2, 4, 6, 8]", "[9, 9, 9, 9]")
            require(actor_code != target_code, "actor source equals the original")
            status, response = transaction(url, sid, target_id, actor_code)
            require(
                status == 200, f"independent transaction refused: {status} {response!r}"
            )

            # Let the kernel's document reflect the transaction.
            live = None
            for _ in range(20):
                live = _live_code(url, copy, env, target_id)
                if live == actor_code:
                    break
                time.sleep(0.2)
            require(
                live == actor_code, f"live source did not adopt the actor's: {live!r}"
            )

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
            verify_stale_refusal(
                {
                    "attempt_exit": code,
                    "attempt_payload": refusal,
                    "attempt_kernel_stderr": stderr,
                    "live_before": live_before,
                    "live_after": live_after,
                    "actor_code": actor_code,
                    "original_code": target_code,
                    "disk_before": disk_before,
                    "disk_after": disk_after,
                    "canonical_before": original,
                    "canonical_after": FIXTURE.read_bytes(),
                }
            )
            print("NATIVE_STALE_PROBE_PASS")
        finally:
            stop(proc)


if __name__ == "__main__":
    main()
