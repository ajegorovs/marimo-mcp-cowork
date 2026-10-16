"""Characterize native 0.25.1 SSE disconnect timing on a disposable notebook.

Run each mode separately with an isolated native interpreter::

    python benchmark/native_sse_disconnect_probe.py --close-after kernel-ready
    python benchmark/native_sse_disconnect_probe.py --close-after heartbeat

Both modes close a real resumed SSE stream, then use the same bounded 45-second
orphan wait as native_churn_probe. The heartbeat mode is a diagnostic control,
NOT a replacement transport or clearance for the canonical churn benchmark.
A retained consumer is reported as an observation, never NATIVE_CHURN_PROBE_PASS.
No product files are patched. Private failure tracebacks may contain paths.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import native_churn_probe as churn


def rotated_stream(url: str, sid: str, path: Path, close_after: str) -> dict:
    """Close at the requested observed event, without mutating notebook content."""
    query = urllib.parse.urlencode({"session_id": sid, "file": str(path)})
    ready = False
    heartbeat = False
    with urllib.request.urlopen(f"{url}/sse?{query}", timeout=45) as stream:
        for line in stream:
            ready = ready or b"kernel-ready" in line
            heartbeat = heartbeat or line.startswith(b": keep-alive")
            if ready and (close_after == "kernel-ready" or heartbeat):
                break
    if not ready or (close_after == "heartbeat" and not heartbeat):
        raise churn.Blocker("requested SSE event was not observed")
    return {"kernel_ready": ready, "heartbeat": heartbeat}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--close-after", required=True, choices=("kernel-ready", "heartbeat")
    )
    parser.add_argument(
        "--session",
        default="resumed",
        choices=("resumed", "initial", "second"),
        help=(
            "resumed: id-rotated stream after a booted session (default); "
            "initial: first stream on this file; "
            "second: new-id stream after a prior orphaned session, no boot."
        ),
    )
    args = parser.parse_args()
    fixture_before = churn.FIXTURE.read_bytes()
    versions = {
        name: importlib.metadata.version(name)
        for name in ("marimo", "starlette", "uvicorn", "anyio")
    }
    print("DEPENDENCIES", json.dumps(versions, sort_keys=True))
    with tempfile.TemporaryDirectory(
        prefix="marimo-sse-disconnect-", dir=churn.SCRATCH
    ) as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        copy = workspace / "disconnect.py"
        copy.write_bytes(churn.make_copy(churn.TAG_A))
        copy_before = copy.read_bytes()
        state = root / "state"
        state.mkdir()
        env = dict(os.environ, USERPROFILE=str(state), XDG_STATE_HOME=str(state))
        server, url = churn.start_server(workspace, env)
        try:
            sid = str(uuid.uuid4())
            if args.session == "initial":
                events = rotated_stream(url, sid, copy, args.close_after)
                census = churn.fetch_sessions(url)
                mapping = churn.path_map(census)
                identity = (
                    list(census) == [sid] and mapping.get(churn.norm(str(copy))) == sid
                )
            else:
                if not churn.open_session(url, sid, copy) or not churn.wait_orphans(
                    url
                ):
                    raise churn.Blocker("initial session did not become orphaned")
                if args.session == "resumed":
                    rc, payload, _, _ = churn.run_cli(
                        url,
                        session_id=None,
                        file_path=copy,
                        code=churn.BOOT_CODE,
                        env=env,
                    )
                    if rc != 0 or not payload or payload.get("success") is not True:
                        raise churn.Blocker("initial fixture execution failed")
                new_sid = str(uuid.uuid4())
                events = rotated_stream(url, new_sid, copy, args.close_after)
                census = churn.fetch_sessions(url)
                mapping = churn.path_map(census)
                identity = (
                    sid not in census
                    and len(census) == 1
                    and mapping.get(churn.norm(str(copy))) == new_sid
                )
            initial_active = churn.active_connections(url)
            begin = time.monotonic()
            settled = churn.wait_orphans(url)
            elapsed = time.monotonic() - begin
            final_active = churn.active_connections(url)
            unchanged = (
                copy.read_bytes() == copy_before
                and churn.FIXTURE.read_bytes() == fixture_before
            )
            if not identity or not unchanged:
                raise AssertionError("session identity or notebook integrity failed")
            observation = {
                "session": args.session,
                "close_after": args.close_after,
                "events": events,
                "initial_active": initial_active,
                "final_active": final_active,
                "orphaned": settled,
                "wait_seconds": round(elapsed, 2),
                "identity_held": identity,
                "copies_unchanged": unchanged,
            }
            print("SSE_DISCONNECT_OBSERVATION", json.dumps(observation, sort_keys=True))
            if final_active is None:
                raise churn.Blocker("active-connection endpoint unreadable")
            print("DIAGNOSTIC_ONLY_NOT_A4_CLEARANCE")
            return 0
        finally:
            churn.stop(server)


if __name__ == "__main__":
    raise SystemExit(main())
