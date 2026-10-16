"""Bounded native 0.25.1 IN-KERNEL expected-file PRECONDITION probe.

Question
--------
``benchmark/native_target_guard_probe.py`` proves the *adapter* refuses an
ambiguous selector pair before any CLI subprocess is spawned. But once a
single, unambiguous selector does reach the native CLI, is there anything
**inside the kernel** that re-checks *which notebook the running kernel is
actually serving* before a mutation is queued? A fail-closed adapter is only
half a story if a *routed* request can still mutate a notebook the caller did
not intend — e.g. after a session id is rebound to a different file, or when
only ``--session`` is supplied and it silently addresses the wrong notebook.

This probe is the integration gate for an **in-kernel expected-file
precondition**: a guarded mutation snippet that, inside the *same*
``async with cm.get_context()`` block, reads the running kernel's file
identity **before** queuing any write and raises if it does not match the
caller's expected file. It exercises the snippet exactly as a fail-closed
tool would — through the native CLI, against a real two-session loopback
server, on disposable copies.

Where the identity is read (source-verified, read before running)
----------------------------------------------------------------
``ctx = cm.get_context()`` returns an ``AsyncCodeModeContext`` whose
``__init__`` stores ``self._kernel = kernel``
(installed marimo 0.25.1, ``_code_mode/_context.py:733``, no proxy wrapper).
The kernel carries ``app_metadata.filename`` — the notebook path string —
set by the server from ``app_file_manager.path`` = ``os.path.abspath(...)``
(``_server/session_manager.py:373-375``; ``_session/notebook/file_manager.py``
``path`` property). On context exit, ``__aexit__`` discards all queued ops if
an exception propagated, so a guard that raises *before* ``create_cell`` /
``run_cell`` cannot leave a partial mutation (``_code_mode/_context.py``
``__aexit__`` — ``if exc_type is not None: ... return``).

The guarded snippet therefore is::

    import marimo._code_mode as cm
    import os as _os
    async with cm.get_context() as ctx:
        _fn = ctx._kernel.app_metadata.filename
        _got = _os.path.normcase(_os.path.abspath(_fn)) if _fn else None
        _exp = _os.path.normcase(_os.path.abspath(<EXPECTED>))
        if _got != _exp:
            raise RuntimeError("GUARD_IDENTITY_MISMATCH")
        _cid = ctx.create_cell(<MARKER>)
        ctx.run_cell(_cid)

Method (loopback only, no installation, no network egress, <=2 runs)
--------------------------------------------------------------------
Setup: one disposable workspace, two byte-identical copies of the committed
fixture, one ``marimo edit <dir>`` server holding two sessions via ``/sse``
(session A -> copy_a, session B -> copy_b).

Phase 1 - WRONG routing refused in the kernel. ``pair execute --session A``
  (no ``--file``) running the guarded snippet with expected = copy_b. The
  snippet runs in A's kernel, whose filename is copy_a, so the guard raises
  *before* any write. Assert: nonzero exit, ``success: false``, payload
  session id is A, and NEITHER A nor B changed — no marker in either live map
  and both disk bytes unchanged.

Phase 2 - CORRECT routing applies. ``pair execute --session B`` with
  expected = copy_b. Assert: exit 0, ``success: true``, payload session id is
  B, the marker appears in B's LIVE map AND B's saved bytes change, while A is
  untouched (no marker, bytes unchanged). A reported success with no observed
  mutation is a FALSE SUCCESS and FAILS.

Phase 3 - re-used old-A id rebound to B (required). Rotate A's id via a new
  ``/sse`` on copy_a, then cross-alias the now-stale old-A id onto copy_b's
  orphaned kernel (the deterministic rename characterized by
  ``native_churn_probe.py``). If the census confirms the old-A id now maps to
  copy_b, run the guarded snippet ``--session <old_A>`` expecting copy_a: the
  kernel is copy_b, so the guard must refuse and mutate nothing. If the
  cross-alias does not behave as predicted, record a BLOCKER (no PASS) rather
  than fail the gate.

Finally assert the committed fixture is byte-unchanged and stop the server.
No secrets, tokens, or absolute user paths are printed.

Exit 0 with ``NATIVE_IN_KERNEL_GUARD_PASS``; non-zero with
``NATIVE_IN_KERNEL_GUARD_BLOCKER`` (a precondition failed, e.g. ``_kernel``
inaccessible) or ``NATIVE_IN_KERNEL_GUARD_FAIL`` (a gate assertion failed).
"""

from __future__ import annotations

import hashlib
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

MARKER = "in_kernel_guard_marker"
MARKER_SOURCE = f"{MARKER} = 20260"
MISMATCH_TOKEN = "GUARD_IDENTITY_MISMATCH"

EXPECTED_CELLS = 5
MARKER_WAIT_S = 8
AUTOSAVE_WAIT_S = 30
ORPHAN_WAIT_S = 45


class Blocker(RuntimeError):
    """A precondition failed; the experiment cannot proceed as scoped."""


class Gap(Exception):
    """A best-effort sub-phase could not be exercised; make no claim."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def guard_code(expected: Path) -> str:
    """Guarded mutation snippet: identity check strictly before any write."""
    return (
        PREFIX
        + "import os as _os\n"
        + "async with cm.get_context() as ctx:\n"
        + "    _fn = ctx._kernel.app_metadata.filename\n"
        + "    _got = _os.path.normcase(_os.path.abspath(_fn)) if _fn else None\n"
        + f"    _exp = _os.path.normcase(_os.path.abspath({str(expected)!r}))\n"
        + "    if _got != _exp:\n"
        + f"        raise RuntimeError({MISMATCH_TOKEN!r})\n"
        + f"    _cid = ctx.create_cell({MARKER_SOURCE!r}, hide_code=False)\n"
        + "    ctx.run_cell(_cid)\n"
        + "    print('GUARD_APPLIED', _cid)\n"
    )


# Read-only live cell map: dump every cell's source so we can look for MARKER.
MAP_CODE = (
    PREFIX
    + "async with cm.get_context() as ctx:\n"
    + "    import json\n"
    + "    print('CELLS', json.dumps(sorted(c.code for c in ctx.cells.values())))"
)


def run_cli(url, *, session_id, file_path, code, env):
    """Run native ``pair execute``; return (rc, payload, stdout, stderr)."""
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
    return result.returncode, payload, result.stdout, result.stderr


def read_map(url, session_id, env) -> list[str]:
    rc, payload, raw, err = run_cli(
        url, session_id=session_id, file_path=None, code=MAP_CODE, env=env
    )
    if rc != 0 or not payload or payload.get("success") is not True:
        raise Blocker(
            f"live map read failed for a session: rc={rc} "
            f"raw={raw[:160]!r} err={err[:160]!r}"
        )
    printed = str(payload.get("stdout") or "")
    rows = [
        line.removeprefix("CELLS ")
        for line in printed.splitlines()
        if line.startswith("CELLS ")
    ]
    if len(rows) != 1:
        raise Blocker("live map did not contain exactly one CELLS record")
    return json.loads(rows[0])


def open_session(url: str, session_id: str, file_path: Path) -> bool:
    query = urllib.parse.urlencode({"session_id": session_id, "file": str(file_path)})
    try:
        with urllib.request.urlopen(f"{url}/sse?{query}", timeout=45) as stream:
            for line in stream:
                if b"kernel-ready" in line:
                    return True
    except OSError:
        return False
    return False


def fetch_sessions(url: str) -> dict:
    with urllib.request.urlopen(url + "/api/sessions", timeout=5) as response:
        return json.load(response)


def active_connections(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url + "/api/status/connections", timeout=2) as resp:
            return int(json.load(resp).get("active"))
    except (OSError, ValueError):
        return None


def wait_orphans(url: str, timeout: float = ORPHAN_WAIT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_connections(url) == 0:
            return True
        time.sleep(0.25)
    return False


def path_map(sessions: dict) -> dict:
    out = {}
    for sid, info in sessions.items():
        path = info.get("path") or info.get("filename")
        if not path:
            raise Blocker(f"session {sid[:8]} reported no path")
        out[norm(path)] = sid
    return out


def wait_for_change(path: Path, before: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    current = sha256(path)
    while time.monotonic() < deadline and current == before:
        time.sleep(0.25)
        current = sha256(path)
    return current


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


def guard_refused(result) -> tuple[bool, str | None]:
    """True when the guarded snippet refused by the in-kernel identity check."""
    rc, payload, _raw, err = result
    if rc == 0:
        return False, None
    if payload is None:
        # Unparseable nonzero exit: could be refusal or a real error.
        return MISMATCH_TOKEN in err, err[:200]
    if payload.get("success") is not False:
        return False, None
    blob = f"{payload.get('stderr') or ''}\n{err}"
    return MISMATCH_TOKEN in blob, (payload.get("stderr") or err)[-200:]


def main() -> int:
    fixture_before = FIXTURE.read_bytes()
    proc = None
    gap_notes: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="marimo-native-inkernel-guard-", dir=SCRATCH
    ) as directory:
        root = Path(directory)
        workspace = root / "ws"
        workspace.mkdir()
        copy_a = workspace / "guard_inkernel_a.py"
        copy_b = workspace / "guard_inkernel_b.py"
        copy_a.write_bytes(fixture_before)
        copy_b.write_bytes(fixture_before)
        a_before, b_before = sha256(copy_a), sha256(copy_b)

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

            sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
            if not open_session(url, sid_a, copy_a):
                raise Blocker("session A did not reach kernel-ready")
            if not open_session(url, sid_b, copy_b):
                raise Blocker("session B did not reach kernel-ready")
            if not wait_orphans(url):
                raise Blocker("sessions did not settle to orphaned")

            sessions = fetch_sessions(url)
            if len(sessions) != 2:
                raise Blocker(f"one server held {len(sessions)} session(s), not 2")
            by_norm = path_map(sessions)
            if by_norm.get(norm(str(copy_a))) != sid_a:
                raise Blocker("session A mapping mismatch")
            if by_norm.get(norm(str(copy_b))) != sid_b:
                raise Blocker("session B mapping mismatch")
            print("SETUP two_sessions", 2, "A", sid_a[:8], "B", sid_b[:8])

            # Sanity: fixture cell count, no marker yet.
            if MARKER_SOURCE in read_map(url, sid_a, env) or MARKER_SOURCE in (
                read_map(url, sid_b, env)
            ):
                raise Blocker("marker present before any write")

            # --- Phase 1: WRONG routing (session A, expected copy_b) -------
            rc, payload, _, err = run_cli(
                url,
                session_id=sid_a,
                file_path=None,
                code=guard_code(copy_b),
                env=env,
            )
            refused, detail = guard_refused((rc, payload, None, err))
            payload_session = (payload or {}).get("session", {}).get("id")
            print(
                "WRONG_ROUTE rc",
                rc,
                "success",
                (payload or {}).get("success"),
                "session_is_A",
                payload_session == sid_a,
                "guard_refused",
                refused,
            )
            # Detect the scoped blocker: _kernel inaccessible in code mode.
            blob = f"{err}\n{(payload or {}).get('stderr') or ''}"
            if not refused and (
                "_kernel" in blob
                or "has no attribute" in blob
                or "app_metadata" in blob
            ):
                raise Blocker(
                    "ctx._kernel / app_metadata not reachable in code mode; "
                    "identity precondition cannot be established"
                )
            if rc == 0 or (payload or {}).get("success") is not False:
                raise AssertionError(
                    f"wrong-route request was NOT refused: rc={rc} payload={payload!r}"
                )
            if payload_session != sid_a:
                raise AssertionError(
                    f"wrong-route request did not run in session A: {payload_session!r}"
                )
            if not refused:
                raise AssertionError(
                    "wrong-route request failed but not with the in-kernel "
                    f"identity guard: {detail!r}"
                )
            # No marker in either live map, no disk change in either copy.
            map_a = read_map(url, sid_a, env)
            map_b = read_map(url, sid_b, env)
            if MARKER_SOURCE in map_a or MARKER_SOURCE in map_b:
                raise AssertionError(
                    "wrong-route refusal introduced the marker into a live map"
                )
            if len(map_a) != EXPECTED_CELLS or len(map_b) != EXPECTED_CELLS:
                raise AssertionError(
                    f"live map cell counts {len(map_a)}/{len(map_b)} != "
                    f"{EXPECTED_CELLS}"
                )
            if sha256(copy_a) != a_before or sha256(copy_b) != b_before:
                raise AssertionError("wrong-route refusal mutated saved bytes")
            print(
                "WRONG_ROUTE_NO_CHANGE marker_absent True bytes_unchanged True cells_A",
                len(map_a),
                "cells_B",
                len(map_b),
            )

            # --- Phase 2: CORRECT routing (session B, expected copy_b) -----
            rc, payload, _, err = run_cli(
                url,
                session_id=sid_b,
                file_path=None,
                code=guard_code(copy_b),
                env=env,
            )
            payload_session = (payload or {}).get("session", {}).get("id")
            success = (payload or {}).get("success")
            print(
                "CORRECT_ROUTE rc",
                rc,
                "success",
                success,
                "session_is_B",
                payload_session == sid_b,
                "stdout",
                repr((payload or {}).get("stdout")),
            )
            if rc != 0 or success is not True:
                raise AssertionError(
                    f"correct-route guarded write failed: rc={rc} "
                    f"success={success} err={err[:200]!r}"
                )
            if payload_session != sid_b:
                raise AssertionError(
                    f"correct-route write ran in {payload_session!r}, not B"
                )
            # Intended mutation MUST land in B live AND on disk.
            deadline = time.monotonic() + MARKER_WAIT_S
            marker_in_b = False
            while time.monotonic() < deadline:
                if MARKER_SOURCE in read_map(url, sid_b, env):
                    marker_in_b = True
                    break
                time.sleep(1.0)
            b_after = wait_for_change(copy_b, b_before, AUTOSAVE_WAIT_S)
            b_disk_changed = b_after != b_before
            map_a = read_map(url, sid_a, env)
            a_after = sha256(copy_a)
            marker_in_a = MARKER_SOURCE in map_a
            a_unchanged = a_after == a_before and not marker_in_a
            print(
                "CORRECT_ROUTE_LANDED marker_in_B",
                marker_in_b,
                "B_disk_changed",
                b_disk_changed,
                "marker_in_A",
                marker_in_a,
                "A_unchanged",
                a_unchanged,
            )
            if not marker_in_b or not b_disk_changed:
                raise AssertionError(
                    "FALSE SUCCESS: correct-route write did not appear in both "
                    "B's live map and its saved copy"
                )
            if MARKER_SOURCE not in copy_b.read_text(encoding="utf-8"):
                raise AssertionError("B's saved copy lacks the intended marker")
            if not a_unchanged:
                raise AssertionError("correct-route write leaked into session A")

            # --- Phase 3 (best effort): reused old-A id rebound to B -------
            print("PHASE3_REUSED_ID_BEGIN")
            try:
                sid_a2 = str(uuid.uuid4())
                if not open_session(url, sid_a2, copy_a):
                    raise Gap("rotation /sse did not reach kernel-ready")
                if not wait_orphans(url):
                    raise Gap("rotation did not settle to orphaned")
                census = fetch_sessions(url)
                cmap = path_map(census)
                if sid_a in census or cmap.get(norm(str(copy_a))) != sid_a2:
                    raise Gap("rotation did not rename A to the new id as predicted")
                # Cross-alias the stale old-A id onto copy_b's orphaned kernel.
                cross_ok = open_session(url, sid_a, copy_b)
                if not wait_orphans(url):
                    raise Gap("cross-alias did not settle to orphaned")
                census2 = fetch_sessions(url)
                cmap2 = path_map(census2)
                if not cross_ok or cmap2.get(norm(str(copy_b))) != sid_a:
                    raise Gap(
                        "old-A id was not rebound to copy_b as predicted; "
                        f"accepted={cross_ok}"
                    )
                print(
                    "PHASE3_REBOUND old_A_now_maps_copy_b True count",
                    len(census2),
                )
                # Now sid_a addresses copy_b's kernel. Guard expects copy_a
                # (the wrong expectation): the guard must refuse, no mutation.
                b_pre = sha256(copy_b)
                rc, payload, _, err = run_cli(
                    url,
                    session_id=sid_a,
                    file_path=None,
                    code=guard_code(copy_a),
                    env=env,
                )
                refused, detail = guard_refused((rc, payload, None, err))
                print(
                    "PHASE3_WRONG_EXPECTED rc",
                    rc,
                    "success",
                    (payload or {}).get("success"),
                    "guard_refused",
                    refused,
                )
                if rc == 0 or (payload or {}).get("success") is not False:
                    raise AssertionError(
                        "rebound id with wrong expectation was NOT refused"
                    )
                if not refused:
                    raise AssertionError(
                        f"rebound id failed but not via the identity guard: {detail!r}"
                    )
                if sha256(copy_b) != b_pre:
                    raise AssertionError(
                        "guarded wrong-expectation on rebound id mutated copy_b"
                    )
                # B's live map must still hold exactly the one marker from
                # Phase 2 (no second marker, no new cell).
                map_b = read_map(url, sid_a, env)
                if sum(1 for c in map_b if MARKER_SOURCE in c) != 1:
                    raise AssertionError("rebound-id guard changed B's live cell set")
                print(
                    "PHASE3_WRONG_EXPECTED_NO_CHANGE bytes_unchanged True "
                    "single_marker True"
                )
            except Gap as g:
                gap_notes.append(str(g))
                print("PHASE3_GAP", str(g))

            # --- Final: fixture intact; server stopped in finally ----------
            if FIXTURE.read_bytes() != fixture_before:
                raise AssertionError("committed repo fixture was mutated")
            print("REPO_FIXTURE_INTACT True")
            if gap_notes:
                raise Blocker(
                    "reused-ID guard sub-probe not exercised: " + "; ".join(gap_notes)
                )
            print(
                "CONCLUSION in-kernel expected-file guard refused wrong "
                "routing with zero mutation and applied the correct-route "
                "write to B only"
            )
            print("NATIVE_IN_KERNEL_GUARD_PASS")
            return 0
        except Blocker as error:
            print(f"NATIVE_IN_KERNEL_GUARD_BLOCKER: {error}")
            return 3
        except AssertionError as error:
            print(f"NATIVE_IN_KERNEL_GUARD_FAIL: {error}")
            return 1
        finally:
            if proc is not None:
                stop(proc)


if __name__ == "__main__":
    sys.exit(main())
