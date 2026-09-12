"""Hermetic live regressions for `restart_kernel` (Wave 3).

These drive the REAL MCP handler (`marimo_inspection.tools.lifecycle.restart_kernel`)
against a real marimo 0.24 kernel on a **purpose-built** notebook written into
``tmp_path`` (the ``notebook_server`` factory in ``live/conftest.py``), so the
document is deterministic and the repo fixture is never mounted.

What is pinned:

1. **A restart closes the kernel and re-materializes a replacement.** The tool
   must not report success from the `POST /api/kernel/restart_session` 200: the
   endpoint only *closes* the session (the server is left at 0 sessions until a
   client reconnects), so the handshake is part of the contract —
   `re_materialized: true`, `sessions_after == 1`, and the expected session id
   live in `/api/sessions` again.
2. **The kernel is genuinely new.** The scratchpad reports the kernel process's
   `os.getpid()`; it changes across the restart, which is the identity assertion
   a mere "session is live" check cannot make.
3. **Execution state is reset.** A kernel global defined by a run cell is gone
   (scratchpad `NameError`) and the cell the restart invalidated reports
   `stale`, while the document itself survived on disk.
4. **The local change tracker is cleared.** Cell ids are not a stable handle
   across a restart, so the pre-restart read baseline is dropped and the first
   `edit_cell` of a file-parsed cell is refused `needs_read` again.
5. **The server process survives.** The skew-protection token is re-read from
   the page HTML after the restart and is unchanged (a server *relaunch* would
   rotate it, which is why the kernel restart is the right instrument).
6. **The id is point-in-time, not durable.** The payload reports
   `session_id_stable: false` / `session_verification: "point_in_time"`, and a
   later browser-like reconnect (a fresh-id `/sse` handshake; marimo edit mode
   is single-session) re-keys the session, after which the tool's verified id is
   dead with `Invalid session id`.
7. **The zero-session / unknown-id guard reports, never restarts.** An id no
   live session reports changes nothing and names what is actually live.

Teardown re-checks that ``notebooks/test_marimo.py`` is byte-identical to what
it was at boot, so no repo fixture is touched.
"""

from __future__ import annotations

import uuid

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.cells import get_cell_data, get_cell_map
from marimo_inspection.tools.change_tracking import get_tracker
from marimo_inspection.tools.lifecycle import restart_kernel
from marimo_inspection.tools.mutation import create_cell, edit_cell, run_cell

# A purpose-built document with only file-parsed cells: those keep their cell
# ids across a restart, which lets the test address a cell by the id it read
# before the restart.
RESTART_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    seed = 1
    return (seed,)


@app.cell
def _(seed):
    doubled = seed * 2
    return (doubled,)
"""

MARKER_SOURCE = "restart_marker = 123"

_PID_PROBE = "import os\nprint(f'KERNEL_PID={os.getpid()}')"


def _find(cell_map: dict, needle: str) -> dict:
    """Return the first cell whose preview contains ``needle``."""
    for cell in cell_map["cells"]:
        if needle in (cell.get("preview") or ""):
            return cell
    raise AssertionError(f"no cell previewing {needle!r} in {cell_map}")


async def _kernel_pid(client: MarimoClient, session_id: str) -> int:
    """The kernel process id, read back through the scratchpad.

    Two different values across a restart prove a NEW kernel, not merely a
    still-live session (a re-materialization that reused the old kernel would
    pass every other check here).
    """
    probe = await client.execute(session_id, _PID_PROBE)
    assert probe.status == "ok", probe.stderr
    text = "".join(probe.stdout)
    assert "KERNEL_PID=" in text, text
    return int(text.split("KERNEL_PID=", 1)[1].split()[0])


@pytest.mark.live
async def test_restart_rematerializes_and_resets_execution_state(notebook_server):
    """The full contract: close + re-materialize, new kernel, tracker cleared."""
    _manager, server_url, session_id, _notebook = await notebook_server(
        RESTART_NOTEBOOK
    )

    # ── before: read a file-parsed cell's source (this records the baseline the
    # restart must invalidate), create + run a cell that defines a global, and
    # capture the kernel's identity.
    before_map = await get_cell_map(session_id=session_id, server_url=server_url)
    seed_id = _find(before_map, "seed = 1")["cell_id"]
    await get_cell_data(session_id=session_id, cell_ids=seed_id, server_url=server_url)
    tracker = get_tracker()
    assert tracker.get_cell_fingerprint(session_id, seed_id) is not None

    created = await create_cell(
        MARKER_SOURCE,
        name="restart_marker",
        session_id=session_id,
        server_url=server_url,
    )
    assert created["status"] == "ok", created
    marker_id = created["cell_id"]

    ran = await run_cell(marker_id, session_id=session_id, server_url=server_url)
    assert ran["status"] == "ok", ran
    assert ran["cells"][0]["runtime_state"] == "idle", ran

    client = MarimoClient(server_url)
    try:
        probe = await client.execute(session_id, "print(f'MARKER={restart_marker}')")
        assert probe.status == "ok", probe.stderr
        assert "MARKER=123" in "".join(probe.stdout)
        pid_before = await _kernel_pid(client, session_id)
    finally:
        await client.close()

    # ── the restart
    result = await restart_kernel(session_id=session_id, server_url=server_url)

    assert result["status"] == "ok", result
    assert result["reason"] == "", result
    assert result["session_id"] == session_id, result
    assert result["live_session_id"] == session_id, result
    assert result["server_url"] == server_url, result
    assert result["restarted"] is True, result
    assert result["re_materialized"] is True, result
    assert result["sessions_before"] == 1, result
    assert result["sessions_after"] == 1, result
    # The server process survived: its token is scraped from the page HTML and
    # is unchanged (a relaunch would rotate it).
    assert result["skew_token_source"] == "page_html", result
    assert result["skew_token_rotated"] is False, result
    assert result["server_process_preserved"] is True, result
    # The reset is stated, not implied. Cell ids are not a stable handle, and
    # neither is the verified session id — this success is point-in-time.
    assert result["execution_state_reset"] is True, result
    assert result["widget_values_reset"] is True, result
    assert result["cell_ids_stable"] is False, result
    assert result["session_id_stable"] is False, result
    assert result["session_verification"] == "point_in_time", result
    assert result["change_tracking_cleared"] is True, result
    assert result["message"] and result["next_steps"], result

    # ── after: the session is live under the same id, and the kernel is NEW
    client = MarimoClient(server_url)
    try:
        sessions = await client.list_sessions()
        assert [s.session_id for s in sessions] == [session_id], sessions
        probe_after = await client.execute(
            session_id, "print(f'MARKER={restart_marker}')"
        )
        assert probe_after.status == "error", probe_after
        assert "NameError" in "".join(probe_after.stderr), probe_after
        # Identity, not just liveness: a new kernel process.
        pid_after = await _kernel_pid(client, session_id)
        assert pid_after != pid_before, (pid_before, pid_after)
    finally:
        await client.close()

    # The pre-restart read baseline is gone: the guard is cold again.
    assert tracker.get_cell_fingerprint(session_id, seed_id) is None

    after_map = await get_cell_map(session_id=session_id, server_url=server_url)
    # The document survived (the created cell is still there) and is stale,
    # because nothing has been executed since the restart.
    marker_cell = _find(after_map, MARKER_SOURCE)
    assert marker_cell["runtime_state"] == "stale", marker_cell
    # A file-parsed cell keeps its id; the guard refuses the first edit again.
    assert _find(after_map, "seed = 1")["cell_id"] == seed_id, after_map
    refused = await edit_cell(
        seed_id, "seed = 2", session_id=session_id, server_url=server_url
    )
    assert refused["status"] == "needs_read", refused

    # ── and the document re-runs green through the documented run-all route
    rerun = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert rerun["status"] == "ok", rerun
    assert rerun["failed_cell_ids"] == [], rerun
    assert rerun["not_run_cell_ids"] == [], rerun


@pytest.mark.live
async def test_later_browser_reconnect_rekeys_the_id(notebook_server):
    """A point-in-time id: a later browser-like reconnect re-keys the session.

    marimo edit mode is single-session, so a fresh frontend handshake (a new
    session id) *replaces* the session the tool re-materialized. The payload
    already flagged this (`session_id_stable: false`), and the tool's verified
    id is dead afterwards — callers must re-run `list_active_notebooks`.
    """
    _manager, server_url, session_id, notebook = await notebook_server(RESTART_NOTEBOOK)

    result = await restart_kernel(session_id=session_id, server_url=server_url)
    assert result["status"] == "ok", result
    # The payload never promises a durable id.
    assert result["session_id_stable"] is False, result
    assert result["session_verification"] == "point_in_time", result
    assert result["live_session_id"] == session_id, result

    # A later browser-like reconnect: a fresh session id replaces the session.
    browser_id = str(uuid.uuid4())
    client = MarimoClient(server_url)
    try:
        handshake = await client.materialize_session(browser_id, str(notebook))
        assert handshake is True, "browser-like handshake did not report kernel-ready"
        live_ids = [s.session_id for s in await client.list_sessions()]
        assert browser_id in live_ids, live_ids
        assert session_id not in live_ids, live_ids

        # The tool's verified id is now dead — exactly the risk the payload
        # flagged as point-in-time.
        with pytest.raises(RuntimeError) as excinfo:
            await client.execute(session_id, "1 + 1")
        assert "Invalid session id" in str(excinfo.value), excinfo.value
    finally:
        await client.close()


@pytest.mark.live
async def test_unknown_session_id_is_refused_and_changes_nothing(notebook_server):
    """The zero-session / unknown-id guard: never a restart, never a success."""
    _manager, server_url, session_id, _notebook = await notebook_server(
        RESTART_NOTEBOOK
    )
    invented = "00000000-0000-0000-0000-000000000000"

    result = await restart_kernel(session_id=invented, server_url=server_url)

    assert result["status"] == "error", result
    assert result["reason"] == "session_not_found", result
    assert result["state_changed"] is False, result
    assert result["restarted"] is False, result
    assert result["sessions_before"] == 1, result
    # The live session is named, so the caller can correct the id.
    assert result["available_sessions"] == [
        {"session_id": session_id, "file": str(_notebook)}
    ], result

    # Nothing was closed: the real session is still live and still the only one.
    client = MarimoClient(server_url)
    try:
        sessions = await client.list_sessions()
        assert [s.session_id for s in sessions] == [session_id], sessions
    finally:
        await client.close()

    # And the session still works: a read through the tools succeeds.
    after_map = await get_cell_map(session_id=session_id, server_url=server_url)
    assert _find(after_map, "seed = 1"), after_map
