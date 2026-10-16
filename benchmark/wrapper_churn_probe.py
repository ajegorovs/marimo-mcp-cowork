"""Wrapper-arm same-server session-identity probe (marimo 0.24.x).

Question
--------
The native-arm churn probe showed that on one loopback server a stale session id
can be rebound to a **different notebook**, after which the id reaches that other
notebook's kernel. The same question had never been asked of this repo's own
client and tool surface, whose documented promise is narrower: it addresses a
server by URL and a session by id, so a caller records an id and later uses it.

This probe does exactly that on the wrapper arm, on ONE disposable loopback
server holding two disposable copies:

1. materialize session A on copy_a and session B on copy_b;
2. run B's cells so its kernel holds an identifying tag;
3. rotate A's id through a second ``/sse`` on copy_a, so A's old id leaves the
   census;
4. present that **stale A id together with copy_b's file**, which aliases B's
   session onto the stale id;
5. then behave like a wrapped caller that recorded "id A means notebook A":
   resolve the id through ``MarimoClient.resolve_session``, read through
   ``MarimoClient.execute``, and attempt a live mutation.

Assertions
----------
- The census after step 4 maps the stale id to copy_b (the rebind happened).
- Whatever the resolver/execute steps observe is printed verbatim, including the
  notebook tag they reached, so the finding is readable whether or not the id
  reaches the wrong notebook.
- Both disposable copies and the committed fixture are byte-identical at the end.
- The probe never claims a wrapper *fix*; it characterizes exposure.

Exit 0 with ``WRAPPER_CHURN_PROBE_PASS`` when the characterization completed and
nothing on disk changed, non-zero with ``WRAPPER_CHURN_PROBE_BLOCKER`` /
``WRAPPER_CHURN_PROBE_FAIL``.

Run with an isolated marimo 0.24.x interpreter (this repo's supported range) and
``PYTHONPATH`` pointing at this checkout's ``src``. No credentials are
provisioned; failure tracebacks may contain disposable absolute paths.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path

import psutil

from marimo_inspection.client import MarimoClient

FIXTURE = Path(__file__).parent / "notebooks" / "mission_clean.py"
SCRATCH = Path(
    os.environ.get(
        "MARIMO_BENCHMARK_SCRATCH", Path.home() / ".cache" / "marimo-inspect"
    )
)
SCRATCH.mkdir(parents=True, exist_ok=True)

PREFIX = "import marimo._code_mode as cm\n"

TAG_A = "WRAPPER_CHURN_COPY_A"
TAG_B = "WRAPPER_CHURN_COPY_B"

SNAPSHOT_MARKER = "PROBE_WRAPPER_CHURN_SNAPSHOT"


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


def make_copy(tag: str) -> bytes:
    """Return fixture bytes with one extra tag cell inserted before __main__."""
    text = FIXTURE.read_text(encoding="utf-8")
    guard = 'if __name__ == "__main__":'
    if guard not in text:
        raise Blocker("fixture lost its __main__ guard")
    cell = (
        "@app.cell\n"
        "def _():\n"
        f"    probe_churn_tag = {tag!r}\n"
        "    return (probe_churn_tag,)\n\n\n"
    )
    return text.replace(guard, cell + guard, 1).encode("utf-8")


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


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


def active_connections(url: str) -> int | None:
    """Observation only: how many consumers the server thinks it has."""
    try:
        with urllib.request.urlopen(url + "/api/status/connections", timeout=2) as resp:
            return int(json.load(resp).get("active"))
    except (OSError, ValueError):
        return None


def start_server(workspace: Path, env: dict) -> tuple[subprocess.Popen, str]:
    port = free_port()
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
        try:
            with urllib.request.urlopen(url + "/api/version", timeout=1) as resp:
                if resp.status == 200:
                    return proc, url
        except OSError:
            time.sleep(0.1)
    stop(proc)
    raise Blocker("server readiness timeout")


BOOT_CODE = (
    PREFIX
    + "async with cm.get_context() as ctx:\n"
    + "    for cell in list(ctx.cells.values()):\n"
    + "        ctx.run_cell(cell.id)\n"
)

READ_CODE = (
    PREFIX
    + "import json as _json\n"
    + "async with cm.get_context() as ctx:\n"
    + "    _t = ctx.globals.get('probe_churn_tag')\n"
    + "    _t = getattr(_t, 'value', _t)\n"
    + "    _m = ctx.globals.get('PROBE_WRAPPER_CHURN_MARKER')\n"
    + "    print("
    + repr(SNAPSHOT_MARKER)
    + ", _json.dumps([str(_t), len(ctx.cells), _m is not None]))\n"
)

MARKER = "PROBE_WRAPPER_CHURN_MARKER"


WRITE_CODE = (
    PREFIX
    + "async with cm.get_context() as ctx:\n"
    + f"    ctx.create_cell({(MARKER + ' = 1')!r}, hide_code=False)\n"
    + "    for cell in list(ctx.cells.values()):\n"
    + "        ctx.run_cell(cell.id)\n"
)


async def census(client: MarimoClient) -> dict[str, str]:
    """Map session id -> normalised notebook path, via the wrapper's own view."""
    return {info.session_id: norm(info.file) for info in await client.list_sessions()}


def id_for_path(mapping: dict[str, str], path: Path) -> str | None:
    """Current session id serving *path*, or None.

    After an id is aliased onto another notebook the ids move, so every step
    re-derives the id it needs from the census instead of caching one.
    """
    wanted = norm(str(path))
    for session_id, served in mapping.items():
        if served == wanted:
            return session_id
    return None


async def wait_for_census(
    client: MarimoClient, predicate, timeout: float = 45.0
) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    latest: dict[str, str] = {}
    while time.monotonic() < deadline:
        latest = await census(client)
        if predicate(latest):
            return latest
        await asyncio.sleep(0.25)
    return latest


async def try_execute(client: MarimoClient, session_id: str, code: str):
    """Run code, returning (result, error_text); never raises for a refusal."""
    try:
        return await client.execute(session_id, code), None
    except (RuntimeError, ValueError, OSError) as error:
        return None, f"{type(error).__name__}: {error}"


def marker_line(result) -> str | None:
    prefix = SNAPSHOT_MARKER + " "
    for line in result.stdout:
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


async def run() -> int:
    fixture_before = FIXTURE.read_bytes()
    server = None
    client = None

    def stop_server() -> None:
        nonlocal server
        if server is not None:
            stop(server)
            server = None

    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="marimo-wrapper-churn-", dir=SCRATCH
            ) as directory,
            ExitStack() as cleanup,
        ):
            root = Path(directory)
            workspace = root / "ws"
            workspace.mkdir()
            copy_a = workspace / "wrapper_churn_a.py"
            copy_b = workspace / "wrapper_churn_b.py"
            copy_a.write_bytes(make_copy(TAG_A))
            copy_b.write_bytes(make_copy(TAG_B))
            a_before = copy_a.read_bytes()

            state = root / "state"
            state.mkdir()
            env = dict(os.environ, USERPROFILE=str(state), XDG_STATE_HOME=str(state))

            server, url = start_server(workspace, env)
            # ExitStack unwinds before TemporaryDirectory on every path, so a
            # failure still releases the server's log file on Windows.
            cleanup.callback(stop_server)
            client = MarimoClient(url)

            # --- Two sessions on one server -------------------------------
            sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
            if not await client.materialize_session(sid_a, str(copy_a)):
                raise Blocker("session A did not reach kernel-ready")
            if not await client.materialize_session(sid_b, str(copy_b)):
                raise Blocker("session B did not reach kernel-ready")
            first = await wait_for_census(client, lambda c: len(c) == 2)
            if first.get(sid_a) != norm(str(copy_a)):
                raise Blocker(f"census 1: A not bound to copy_a: {first!r}")
            if first.get(sid_b) != norm(str(copy_b)):
                raise Blocker(f"census 1: B not bound to copy_b: {first!r}")
            print("CENSUS1", "count", len(first), "A_bound_copy_a", True)

            # --- Each kernel must hold its identifying tag ---------------
            boot_a = await client.execute(sid_a, BOOT_CODE)
            if boot_a.status != "ok":
                raise Blocker(f"boot of A failed: {boot_a!r}")
            boot = await client.execute(sid_b, BOOT_CODE)
            if boot.status != "ok":
                raise Blocker(f"boot of B failed: {boot!r}")
            probe = await client.execute(sid_b, READ_CODE)
            line = marker_line(probe)
            if line is None or json.loads(line)[0] != TAG_B:
                raise Blocker(f"B's kernel did not expose its tag: {probe!r}")
            print("BOOT_B", "tag", TAG_B, "cells", json.loads(line)[1])

            # --- Rotate A's id through a second /sse on copy_a ------------
            sid_a2 = str(uuid.uuid4())
            if not await client.materialize_session(sid_a2, str(copy_a)):
                raise Blocker("rotation /sse did not reach kernel-ready")
            rotated = await wait_for_census(
                client, lambda c: c.get(sid_a2) == norm(str(copy_a))
            )
            print(
                "ROTATION",
                "old_A_present",
                sid_a in rotated,
                "new_A_maps_copy_a",
                rotated.get(sid_a2) == norm(str(copy_a)),
                "count",
                len(rotated),
            )
            if sid_a in rotated:
                raise AssertionError("rotation left the old A id in the census")

            # --- Aliasing: stale A id + copy_b's file ---------------------
            if not await client.materialize_session(sid_a, str(copy_b)):
                raise Blocker("cross /sse did not reach kernel-ready")
            aliased = await wait_for_census(
                client, lambda c: c.get(sid_a) == norm(str(copy_b))
            )
            print(
                "ALIAS",
                "stale_A_now_maps_copy_b",
                aliased.get(sid_a) == norm(str(copy_b)),
                "count",
                len(aliased),
                "active_connections",
                active_connections(url),
            )
            if aliased.get(sid_a) != norm(str(copy_b)):
                raise AssertionError(
                    f"stale id was not aliased onto copy_b: {aliased!r}"
                )

            # --- Wrapper-facing steps: the caller recorded "id A = notebook A"
            resolved = await client.resolve_session(session_id=sid_a)
            print(
                "RESOLVE_BY_ID",
                "session_id_is_stale_A",
                resolved.session_id == sid_a,
                "resolved_basename",
                resolved.basename,
                "caller_expected_basename",
                copy_a.name,
            )

            read, read_error = await try_execute(client, sid_a, READ_CODE)
            read_line = marker_line(read) if read else None
            read_tag = json.loads(read_line)[0] if read_line else None
            print(
                "READ_BY_STALE_ID",
                "status",
                read.status if read else None,
                "error",
                read_error,
                "tag_reached",
                read_tag,
                "expected_if_targeting_held",
                TAG_A,
                "WRONG_NOTEBOOK" if read_tag == TAG_B else "no-wrong-target",
            )

            # The caller's intended notebook still exists under its rotated id,
            # so the recorded id and the intended notebook have separated.
            intended, intended_error = await try_execute(client, sid_a2, READ_CODE)
            intended_line = marker_line(intended) if intended else None
            print(
                "INTENDED_NOTEBOOK_UNDER_ROTATED_ID",
                "session_id",
                sid_a2,
                "error",
                intended_error,
                "tag_reached",
                json.loads(intended_line)[0] if intended_line else None,
            )

            # A live mutation sent to the recorded id. The on-disk delta is
            # snapshotted immediately around it so the change is attributed to
            # this step and not to anything earlier.
            b_before_write = copy_b.read_bytes()
            a_before_write = copy_a.read_bytes()
            current_b = id_for_path(aliased, copy_b)
            if current_b is None:
                raise AssertionError("no session serves copy_b")
            before, _ = await try_execute(client, current_b, READ_CODE)
            before_line = marker_line(before) if before else None
            write, write_error = await try_execute(client, sid_a, WRITE_CODE)
            after, after_error = await try_execute(client, current_b, READ_CODE)
            after_line = marker_line(after) if after else None
            b_before_cells = json.loads(before_line)[1] if before_line else None
            b_after_cells = json.loads(after_line)[1] if after_line else None
            b_after_write = copy_b.read_bytes()
            a_after_stale_write = copy_a.read_bytes()
            print(
                "WRITE_BY_STALE_ID",
                "status",
                write.status if write else None,
                "error",
                write_error,
                "b_cells_before",
                b_before_cells,
                "b_cells_after",
                b_after_cells,
                "marker_present_in_b",
                bool(after_line) and json.loads(after_line)[2],
                "after_read_error",
                after_error,
                "copy_b_changed_on_disk_by_this_write",
                b_after_write != b_before_write,
                "copy_a_changed_on_disk_by_this_write",
                a_after_stale_write != a_before_write,
            )

            # --- Mitigation candidate: anchor on the notebook path ---------
            # The census maps each notebook's *current* id, so re-resolving the
            # caller's intended notebook by path at call time cannot be
            # displaced by a rotated id the way a recorded id can.
            path_resolved = await client.resolve_session(file_path=str(copy_a))
            print(
                "RESOLVE_BY_PATH",
                "resolved_basename",
                path_resolved.basename,
                "caller_intended_basename",
                copy_a.name,
                "differs_from_recorded_id",
                path_resolved.session_id != sid_a,
            )
            a_before_path_write = copy_a.read_bytes()
            b_before_path_write = copy_b.read_bytes()
            path_read, path_read_error = await try_execute(
                client, path_resolved.session_id, READ_CODE
            )
            path_line = marker_line(path_read) if path_read else None
            path_tag = json.loads(path_line)[0] if path_line else None
            path_write, path_write_error = await try_execute(
                client, path_resolved.session_id, WRITE_CODE
            )
            a_after_path_write = copy_a.read_bytes()
            b_after_path_write = copy_b.read_bytes()
            print(
                "PATH_ANCHORED_CALL",
                "read_status",
                path_read.status if path_read else None,
                "read_error",
                path_read_error,
                "read_tag_reached",
                path_tag,
                "intended_tag",
                TAG_A,
                "write_status",
                path_write.status if path_write else None,
                "write_error",
                path_write_error,
                "copy_a_changed_on_disk_by_this_write",
                a_after_path_write != a_before_path_write,
                "copy_b_changed_on_disk_by_this_write",
                b_after_path_write != b_before_path_write,
            )

            # --- Integrity contract --------------------------------------
            # This probe deliberately writes twice, so each write's on-disk
            # effect is attributed rather than both copies asserted frozen.
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("committed fixture changed")
            if copy_a.read_bytes() != a_after_path_write:
                raise AssertionError("copy_a changed after its attributed write")
            if copy_b.read_bytes() != b_after_path_write:
                raise AssertionError("copy_b changed after its attributed write")
            if a_after_stale_write != a_before:
                raise AssertionError("stale-id write touched the intended notebook")
            if b_after_path_write != b_before_path_write:
                raise AssertionError("path-anchored write touched copy_b")
            print("ATTRIBUTION intended_notebook_touched_by_stale_id_write", False)
            print("ATTRIBUTION copy_b_touched_by_path_anchored_write", False)
            print("FIXTURE_UNCHANGED True")
            print(
                "WRAPPER_CHURN_VERDICT exposition-characterization-not-a-fix",
                "recorded_id_reached_wrong_notebook",
                read_tag == TAG_B,
                "recorded_id_disk_change_in_wrong_notebook",
                b_after_write != b_before_write,
                "path_anchor_reached_intended_notebook",
                path_tag == TAG_A,
                "path_anchor_disk_change_in_intended_notebook",
                a_after_path_write != a_before_path_write,
            )
            print("WRAPPER_CHURN_PROBE_PASS")
            return 0
    except Blocker as error:
        print(f"WRAPPER_CHURN_PROBE_BLOCKER: {error}")
        return 3
    except AssertionError as error:
        print(f"WRAPPER_CHURN_PROBE_FAIL: {error}")
        return 1
    finally:
        if client is not None:
            await client.close()
        stop_server()


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
