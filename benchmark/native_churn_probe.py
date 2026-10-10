"""Bounded native 0.25.1 same-server session-CHURN / rotation probe.

Question
--------
On ONE loopback ``marimo edit`` server holding two disposable notebooks
(copy_a served as session A, copy_b as session B), what does the server do
when a *new* ``/sse`` connection is opened for A's file under a brand-new
session id? Does the old A id survive, or does the server alias the kernel to
the new id and drop the old one? And what happens if a stale id is later
presented to ``/sse`` together with the OTHER notebook's file?

Determined from the installed marimo 0.25.1 source (read before running):

- Edit mode allows exactly one session per file and imposes no TTL close, so
  after an ``/sse`` stream closes the session stays, ``room.main_consumer``
  becomes ``None`` and ``Session.connection_state()`` reports ``ORPHANED``
  (``_session/session.py``; ``_server/api/endpoints/ws/session_handler.py``).
- A new ``/sse`` for A's file: ``SessionConnector._connect`` finds no session
  by the new id, finds A by file key, and — because A is ORPHANED, so
  ``initial_capabilities`` returns the EDITOR tier rather than a kiosk — skips
  the read-only path and calls ``maybe_resume_session``
  (``_server/api/endpoints/ws/ws_session_connector.py``,
  ``_session/consumer_policy.py``).
- ``EditModeResumeStrategy.try_resume`` then calls
  ``SessionRepository.update_session_id_sync(old_A, new_id)``, which writes
  ``sessions[new_id] = session`` and ``del sessions[old_A]``
  (``_server/resume_strategies.py``, ``_session/session_repository.py``).
  Prediction: the kernel is preserved under the NEW id; the OLD id is removed
  from the census (a rename, not a duplicate).
- The pair client's ``resolve_session`` matches ``/api/sessions`` by path, so
  a file-only read after rotation must resolve to the NEW id and land the same
  (renamed) kernel (``_cli/pair/client.py``).
- Cross case: a ``/sse`` presenting the OLD A id together with B's file has no
  session for that id, finds B by file key, and — B also being ORPHANED —
  resumes via the same strategy, renaming B's session to the OLD A id
  (``update_session_id_sync(B_id, old_A)``). Deterministic, read-only for
  notebook bytes, on disposable copies only.

Method (loopback only, no installation, at most one server, no retry)
---------------------------------------------------------------------
1. Build two disposable copies of the committed fixture in one workspace
   directory; each copy gains ONE extra cell defining a distinct tag
   (``PROBE_CHURN_COPY_A`` / ``..._B``) so a live read can tell the two
   kernels apart even though both derive from the same fixture.
2. Start ONE ``marimo edit <workspace>`` server; open session A on copy_a and
   session B on copy_b via ``/sse``; wait until both streams have closed and
   both sessions are ORPHANED. Assert the census is exactly
   ``A -> copy_a`` and ``B -> copy_b``.
3. Run the fixture cells in A (file-only ``pair execute``), so A's kernel holds
   a tag we can re-read later; assert the payload names A.
4. Rotate: open a NEW ``/sse`` for copy_a under a NEW id. Wait for orphan.
   Assert the census now holds ``new_id -> copy_a`` and NOT the old A id — the
   rotation is an in-place rename, so the OLD id is gone from the census.
5. Old-id refusal: RUN-ONLY there is no ``/api/sessions`` post for this; drive
   the native ``pair execute --session <OLD_A>`` and assert it is refused as
   stale (nonzero exit, ``success: false``, stale wording, no kernel stdout).
6. File-only read: ``pair execute --file copy_a`` with no ``--session``; assert
   it resolves to the NEW id, returns tag ``COPY_A`` and ``gain == 2`` — i.e.
   it landed A's renamed kernel, not B's.
7. Cross attempt (deterministic per source, disposable only): open ``/sse``
   with the OLD A id and B's file; inspect the census before/after and assert
   the old A id now maps to copy_b (the stale id was aliased onto B's kernel).
8. Assert both disposable copies and the repo fixture stayed byte-identical.

This is a finite, READ-ONLY churn characterization: it shows what these
specific reconnects do. A finite sequence cannot prove atomicity or absence of
a race under concurrent clients; no same-id parallel race is attempted.

Exit 0 with ``NATIVE_CHURN_PROBE_PASS``, or non-zero with
``NATIVE_CHURN_PROBE_BLOCKER`` / ``NATIVE_CHURN_PROBE_FAIL``.
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

# Unique harness marker parsed out of the CLI stdout.
SNAPSHOT_MARKER = "PROBE_CHURN_SNAPSHOT"

# Distinct per-copy tag cells so a live read can identify the kernel.
TAG_A = "PROBE_CHURN_COPY_A"
TAG_B = "PROBE_CHURN_COPY_B"

EXPECTED_GAIN = 2


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


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/version", timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def fetch_sessions(url: str) -> dict:
    with urllib.request.urlopen(url + "/api/sessions", timeout=5) as resp:
        return json.load(resp)


def active_connections(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url + "/api/status/connections", timeout=2) as resp:
            return int(json.load(resp).get("active"))
    except (OSError, ValueError):
        return None


def wait_orphans(url: str, timeout: float = 45.0) -> bool:
    """Wait until no session reports an open (main) connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = active_connections(url)
        if active == 0:
            return True
        time.sleep(0.25)
    return False


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


def open_session(url: str, session_id: str, file_path: Path) -> bool:
    """Open one /sse session and return once kernel-ready; False if rejected."""
    query = urllib.parse.urlencode({"session_id": session_id, "file": str(file_path)})
    try:
        with urllib.request.urlopen(f"{url}/sse?{query}", timeout=45) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    return True
    except OSError:
        return False
    return False


def run_cli(
    url: str, *, session_id: str | None, file_path: Path | None, code: str, env: dict
) -> tuple[int, dict | None, str]:
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


def path_map(sessions: dict) -> dict:
    """Map normalised notebook path -> session id."""
    out = {}
    for sid, info in sessions.items():
        path = info.get("path") or info.get("filename")
        if not path:
            raise Blocker(f"session {sid} reported no path")
        out[norm(path)] = sid
    return out


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
    + "    _g = ctx.globals.get('gain')\n"
    + "    _g = getattr(_g, 'value', _g)\n"
    + "    print("
    + repr(SNAPSHOT_MARKER)
    + ", _json.dumps([str(_t), int(_g) if _g is not None else None,"
    + " len(ctx.cells)]))\n"
)


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    server = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="marimo-native-churn-",
            dir=SCRATCH,
        ) as directory:
            root = Path(directory)
            workspace = root / "ws"
            workspace.mkdir()
            copy_a = workspace / "probe_churn_a.py"
            copy_b = workspace / "probe_churn_b.py"
            copy_a.write_bytes(make_copy(TAG_A))
            copy_b.write_bytes(make_copy(TAG_B))
            a_before, b_before = copy_a.read_bytes(), copy_b.read_bytes()

            state = root / "state"
            state.mkdir()
            env = os.environ.copy()
            env["USERPROFILE"] = str(state)
            env["XDG_STATE_HOME"] = str(state)

            server, url = start_server(workspace, env)

            # --- Two sessions A/B on one server ---------------------------
            sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
            if not open_session(url, sid_a, copy_a):
                raise Blocker("session A did not reach kernel-ready")
            if not open_session(url, sid_b, copy_b):
                raise Blocker("session B did not reach kernel-ready")
            if not wait_orphans(url):
                raise Blocker("sessions did not settle to orphaned")

            census1 = fetch_sessions(url)
            map1 = path_map(census1)
            if len(census1) != 2:
                raise Blocker(
                    f"expected 2 sessions, got {len(census1)}: {sorted(census1)}"
                )
            if map1.get(norm(str(copy_a))) != sid_a:
                raise Blocker("census 1: copy_a not bound to session A")
            if map1.get(norm(str(copy_b))) != sid_b:
                raise Blocker("census 1: copy_b not bound to session B")
            print("CENSUS1", "A", sid_a, "B", sid_b, "count", len(census1))

            # --- Run A's cells so its kernel holds a re-readable tag ------
            boot_rc, boot_payload, _ = run_cli(
                url, session_id=None, file_path=copy_a, code=BOOT_CODE, env=env
            )
            if boot_rc != 0 or not boot_payload or not boot_payload.get("success"):
                raise Blocker(f"boot of A failed: {boot_payload!r}")
            if (boot_payload.get("session") or {}).get("id") != sid_a:
                raise Blocker(
                    "--file copy_a did not resolve to A before rotation: "
                    f"{boot_payload.get('session')!r}"
                )
            print("BOOT_A", "session", sid_a)

            # --- Rotate A's id via a new /sse on the same file ------------
            sid_a2 = str(uuid.uuid4())
            if not open_session(url, sid_a2, copy_a):
                raise Blocker("rotation /sse did not reach kernel-ready")
            if not wait_orphans(url):
                raise Blocker("rotation did not settle to orphaned")

            census2 = fetch_sessions(url)
            map2 = path_map(census2)
            print(
                "CENSUS2",
                "old_A_present",
                sid_a in census2,
                "new_A_maps_copy_a",
                map2.get(norm(str(copy_a))) == sid_a2,
                "B_unchanged",
                map2.get(norm(str(copy_b))) == sid_b,
                "count",
                len(census2),
            )
            if sid_a in census2:
                raise AssertionError(
                    "rotation left the OLD id in the census (no rename): "
                    f"{sorted(census2)}"
                )
            if map2.get(norm(str(copy_a))) != sid_a2:
                raise AssertionError(
                    f"rotation did not alias the kernel to the new id: {map2!r}"
                )
            if map2.get(norm(str(copy_b))) != sid_b:
                raise AssertionError("rotation disturbed session B")
            if len(census2) != 2:
                raise AssertionError(
                    f"rotation changed session count to {len(census2)}"
                )
            print(
                "ROTATION_VERDICT old_id_removed_from_census=True "
                "kernel_preserved_under_new_id=True"
            )

            # --- Old id must be REFUSED by the CLI ------------------------
            stale_rc, stale_payload, stale_raw = run_cli(
                url,
                session_id=sid_a,
                file_path=None,
                code="print('PROBE_CHURN_WOULD_RUN')",
                env=env,
            )
            stale_error = (stale_payload or {}).get("error")
            print(
                "STALE_CLI",
                "exit",
                stale_rc,
                "success",
                (stale_payload or {}).get("success"),
                "error",
                repr(stale_error),
            )
            if stale_rc == 0 or (stale_payload or {}).get("success") is not False:
                raise AssertionError(
                    f"stale id was ACCEPTED by execute: rc={stale_rc} "
                    f"payload={stale_payload!r}"
                )
            if stale_payload is None or "stale" not in str(stale_error).lower():
                raise AssertionError(
                    f"stale refusal did not name a stale session: {stale_raw!r}"
                )

            # --- File-only read after rotation must land A ----------------
            read_rc, read_payload, _ = run_cli(
                url, session_id=None, file_path=copy_a, code=READ_CODE, env=env
            )
            if read_rc != 0 or not read_payload or not read_payload.get("success"):
                raise AssertionError(
                    f"file-only read after rotation failed: {read_payload!r}"
                )
            if (read_payload.get("session") or {}).get("id") != sid_a2:
                raise AssertionError(
                    "file-only read did not resolve to the rotated id: "
                    f"{read_payload.get('session')!r}"
                )
            line = _marker_line(read_payload)
            if line is None:
                raise AssertionError(f"no snapshot marker: {read_payload!r}")
            tag, gain, cells = json.loads(line)
            print(
                "READ_AFTER_ROTATION",
                "session",
                sid_a2,
                "tag",
                repr(tag),
                "gain",
                gain,
                "cells",
                cells,
            )
            if tag != TAG_A:
                raise AssertionError(
                    f"file-only read landed tag {tag!r}, expected {TAG_A!r}"
                )
            if gain != EXPECTED_GAIN:
                raise AssertionError(f"gain {gain!r} != {EXPECTED_GAIN}")
            print("LANDS_A True")

            # Pre-run B's fixture cells, so a later old-A-id read can
            # identify B's kernel rather than relying on the census alone.
            boot_b_rc, boot_b_payload, _ = run_cli(
                url, session_id=None, file_path=copy_b, code=BOOT_CODE, env=env
            )
            if (
                boot_b_rc != 0
                or not boot_b_payload
                or not boot_b_payload.get("success")
            ):
                raise Blocker("boot of B failed before cross-alias probe")
            if (boot_b_payload.get("session") or {}).get("id") != sid_b:
                raise Blocker("boot of B resolved to another session")

            # --- Cross attempt: OLD A id + B's file ----------------------
            # Deterministic per source: B is orphaned, so the stale id is
            # aliased onto B's kernel via update_session_id_sync(B, old_A).
            before_cross = path_map(fetch_sessions(url))
            cross_ok = open_session(url, sid_a, copy_b)
            if not wait_orphans(url):
                raise Blocker("cross attempt did not settle to orphaned")
            after_cross = fetch_sessions(url)
            after_map = path_map(after_cross)
            print(
                "CROSS_SSE",
                "accepted",
                cross_ok,
                "old_A_now_maps_copy_b",
                after_map.get(norm(str(copy_b))) == sid_a,
                "copy_a_still",
                after_map.get(norm(str(copy_a))),
                "count",
                len(after_cross),
            )
            before_paths = set(before_cross)
            after_paths = set(after_map)
            if after_paths != {norm(str(copy_a)), norm(str(copy_b))}:
                raise AssertionError(
                    "cross attempt changed which notebooks hold sessions: "
                    f"{after_map!r}"
                )
            if before_paths != after_paths or len(after_cross) != 2:
                raise AssertionError(
                    f"cross attempt changed the session set: {after_cross!r}"
                )
            if not cross_ok or after_map.get(norm(str(copy_b))) != sid_a:
                raise AssertionError(
                    "expected stale A id to be accepted for B's orphaned kernel; "
                    f"accepted={cross_ok} census={after_map!r}"
                )
            print(
                "CROSS_VERDICT stale_id_ALIASED_onto_other_notebook"
                "_kernel (deterministic, no file write)"
            )

            # The formerly stale ID now succeeds against B's kernel. This is
            # a read, never a retry of a mutating request.
            cross_rc, cross_payload, _ = run_cli(
                url, session_id=sid_a, file_path=None, code=READ_CODE, env=env
            )
            if (
                cross_rc != 0
                or not cross_payload
                or cross_payload.get("success") is not True
            ):
                raise AssertionError("reused old A id did not execute read-only code")
            cross_line = _marker_line(cross_payload)
            if cross_line is None:
                raise AssertionError("reused ID read returned no snapshot")
            cross_tag, cross_gain, _ = json.loads(cross_line)
            if cross_tag != TAG_B or cross_gain != EXPECTED_GAIN:
                raise AssertionError("reused old A id did not read B's kernel")
            print("REUSED_OLD_A_ID_READS_B", True)

            # --- Zero mutation -------------------------------------------
            if copy_a.read_bytes() != a_before:
                raise AssertionError("disposable copy_a was mutated")
            if copy_b.read_bytes() != b_before:
                raise AssertionError("disposable copy_b was mutated")
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("repo fixture was mutated")
            print("COPIES_UNCHANGED True")
            print("FIXTURE_UNCHANGED True")
            print("NATIVE_CHURN_PROBE_PASS")
            # Stop the server before the temp tree is removed: a live marimo
            # server holds its log file open, which blocks Windows cleanup.
            stop(server)
            server = None
            return 0
    except Blocker as error:
        print(f"NATIVE_CHURN_PROBE_BLOCKER: {error}")
        return 3
    except AssertionError as error:
        print(f"NATIVE_CHURN_PROBE_FAIL: {error}")
        return 1
    finally:
        if server is not None:
            stop(server)


if __name__ == "__main__":
    sys.exit(main())
