"""Bounded native 0.25.1 safety probes on a disposable notebook copy."""

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


def execute(url, notebook, env, code):
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
    payload = json.loads(result.stdout)
    return result.returncode, payload


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


def main():
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-safety-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        copy = root / "mission_native_safety.py"
        original = FIXTURE.read_bytes()
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
            require(list(sessions) == [sid], sessions)
            require(
                Path(sessions[sid].get("path") or sessions[sid]["filename"]).resolve()
                == copy.resolve(),
                "session normalized path does not match the disposable copy",
            )
            prefix = "import marimo._code_mode as cm\n"
            boot = (
                prefix
                + "async with cm.get_context() as ctx:\n    for cell in ctx.cells.values():\n        ctx.run_cell(cell.id)"
            )
            code, payload = execute(url, copy, env, boot)
            require(code == 0 and payload.get("success"), (code, payload))
            snapshot = (
                prefix
                + "async with cm.get_context() as ctx:\n    print('SNAPSHOT', sorted((str(c.id), c.code) for c in ctx.cells.values()))"
            )
            code, before = execute(url, copy, env, snapshot)
            require(code == 0 and before.get("success"), before)
            disk_before = copy.read_bytes()
            for label, operation in (
                ("missing", 'ctx.edit_cell("missing-a4-cell", code="gain = 99")'),
                ("duplicate", 'ctx.create_cell("input_vector = 999", hide_code=False)'),
            ):
                snippet = (
                    prefix + "async with cm.get_context() as ctx:\n    " + operation
                )
                code, refusal = execute(url, copy, env, snippet)
                error = str(refusal.get("stderr", "")).splitlines()
                print(
                    label,
                    "exit",
                    code,
                    "success",
                    refusal.get("success"),
                    "error",
                    next((line for line in reversed(error) if line.strip()), ""),
                )
                require(code != 0 and refusal.get("success") is False, refusal)
                code, after = execute(url, copy, env, snapshot)
                require(code == 0 and after.get("success"), after)
                require(after.get("stdout") == before.get("stdout"), (before, after))
                require(
                    copy.read_bytes() == disk_before,
                    "refused mutation changed the disposable file",
                )
            # The code-mode writer records its own read baseline process-wide;
            # a second CLI call cannot simulate an independent agent. Do not
            # score concurrent stale edits from this same-actor probe.
            print("stale", "UNVERIFIED: requires an independent frontend actor")
            require(FIXTURE.read_bytes() == original, "repo fixture mutated")
            print("NATIVE_SAFETY_PASS")
        finally:
            stop(proc)


if __name__ == "__main__":
    main()
