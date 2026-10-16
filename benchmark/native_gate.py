"""Bounded, loopback-only native CLI gate on a disposable fixture copy."""

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
from oracle import expected_metric


def require(condition, *diagnostics):
    """Always-on verdict check; a bare ``assert`` is stripped under ``python -O``."""
    if condition:
        return
    if not diagnostics:
        raise AssertionError("required condition failed")
    raise AssertionError(diagnostics[0] if len(diagnostics) == 1 else diagnostics)


fixture = Path(__file__).resolve().parent / "notebooks" / "mission_clean.py"
scratch = Path(
    os.environ.get(
        "MARIMO_BENCHMARK_SCRATCH", Path.home() / ".cache" / "marimo-inspect"
    )
)
scratch.mkdir(parents=True, exist_ok=True)
work = tempfile.TemporaryDirectory(prefix="marimo-native-gate-", dir=scratch)
root = Path(work.name)
copy = root / "mission_native_work.py"
copy.write_bytes(fixture.read_bytes())
initial = copy.read_bytes()
state = root / "benchmark_state"
state.mkdir(exist_ok=True)
env = os.environ.copy()
env["USERPROFILE"] = str(state)
env["XDG_STATE_HOME"] = str(state)
python = sys.executable


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_start():
    p = port()
    url = f"http://127.0.0.1:{p}"
    cmd = [
        python,
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
        str(p),
    ]
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"server exited {proc.returncode}")
            try:
                with urllib.request.urlopen(url + "/api/version", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server readiness timeout")
        sid = str(uuid.uuid4())
        params = urllib.parse.urlencode({"session_id": sid, "file": str(copy)})
        with urllib.request.urlopen(f"{url}/sse?{params}", timeout=15) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    break
            else:
                raise RuntimeError("no kernel-ready")
        with urllib.request.urlopen(url + "/api/sessions", timeout=3) as r:
            sessions = json.load(r)
        require(list(sessions) == [sid], sessions)
        entry = sessions[sid]
        require(
            Path(entry.get("path") or entry["filename"]).resolve() == copy.resolve(),
            entry,
        )
        return proc, url, sid
    except BaseException:
        server_stop(proc)
        raise


def server_stop(proc):
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
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


def execute(url, code):
    cmd = [
        python,
        "-m",
        "marimo",
        "pair",
        "execute",
        "--url",
        url,
        "--file",
        str(copy),
        "-c",
        code,
    ]
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise AssertionError(
            (proc.returncode, proc.stdout[-1000:], proc.stderr[-1000:])
        )
    if proc.returncode or not data.get("success"):
        raise AssertionError((proc.returncode, str(data)[:2000]))
    return data


def measured_value(payload, label):
    """Require one exact numeric result line, not a substring in a traceback."""
    lines = str(payload.get("stdout", "")).splitlines()
    matches = [line for line in lines if line.startswith(label + " ")]
    require(len(matches) == 1, label, lines)
    return float(matches[0].removeprefix(label + " "))


first = None
second = None
try:
    first, url, sid = server_start()
    original_pid = first.pid
    listed = execute(
        url,
        "import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    print(len(ctx.cells))",
    )
    print("initial cell count:", listed.get("stdout", "")[:200])
    # Separate context exits: queued run and UI updates flush AFTER exit.
    boot = execute(
        url,
        "import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    for cell in ctx.cells.values():\n        ctx.run_cell(cell.id)",
    )
    print("boot:", str(boot.get("stdout", ""))[:200])
    made = execute(
        url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    cid = ctx.create_cell("normalized = float(abs(processed).mean() / abs(input_vector).max())\\nnormalized", hide_code=False)\n    ctx.run_cell(cid)\n    print("created", cid)',
    )
    print("create:", str(made.get("stdout", ""))[:300])
    low = execute(
        url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    print("metric", ctx.globals.get("normalized"))',
    )
    print("low:", str(low.get("stdout", ""))[:300])
    require(measured_value(low, "metric") == expected_metric(2), low)
    moved = execute(
        url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    ctx.set_ui_value(ctx.globals["gain"], 4)',
    )
    print("move:", str(moved.get("stdout", ""))[:200])
    high = execute(
        url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    print("gain", ctx.globals["gain"].value)\n    print("metric", ctx.globals.get("normalized"))',
    )
    print("high:", str(high.get("stdout", ""))[:300])
    require(measured_value(high, "gain") == 4, high)
    require(measured_value(high, "metric") == expected_metric(4), high)
    require(fixture.read_bytes() == initial, "repo fixture mutated")
    deadline = time.monotonic() + 5
    while (
        time.monotonic() < deadline and b"normalized = float(" not in copy.read_bytes()
    ):
        time.sleep(0.1)
    require(
        b"normalized = float(" in copy.read_bytes(),
        "edit not saved to disposable file",
    )
    print("saved artifact present:", len(copy.read_bytes()) > len(initial))
    server_stop(first)
    first = None
    second, fresh_url, fresh_sid = server_start()
    require(
        second.pid != original_pid and fresh_sid != sid,
        "reopened server is not a fresh process/session",
    )
    rerun = execute(
        fresh_url,
        "import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    for cell in ctx.cells.values():\n        ctx.run_cell(cell.id)",
    )
    fresh = execute(
        fresh_url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    print("gain", ctx.globals["gain"].value)\n    print("metric", ctx.globals.get("normalized"))',
    )
    print("reopened:", str(fresh.get("stdout", ""))[:300])
    require(measured_value(fresh, "gain") == 2, fresh)
    require(measured_value(fresh, "metric") == expected_metric(2), fresh)
    healthy = execute(
        fresh_url,
        'import marimo._code_mode as cm\nasync with cm.get_context() as ctx:\n    print("cells", len(ctx.cells))\n    print("errors", sum(len(c.errors) for c in ctx.cells.values()))\n    print("states", [c.status for c in ctx.cells.values()])',
    )
    require(measured_value(healthy, "cells") == 6, healthy)
    require(measured_value(healthy, "errors") == 0, healthy)
    require(fixture.read_bytes() == initial, "repo fixture mutated")
    print("NATIVE_GATE_PASS")
finally:
    if first is not None:
        server_stop(first)
    if second is not None:
        server_stop(second)
    work.cleanup()
