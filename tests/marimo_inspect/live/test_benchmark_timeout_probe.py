"""Bounded wrapper unknown-outcome characterization for a slow (>timeout) cell.

What this characterizes
-----------------------
``MarimoClient.execute`` (``src/marimo_inspection/client.py``) opens the SSE
``POST /api/kernel/execute`` stream with a hard ``timeout=30`` and reads until
the terminal ``done`` event. A single ``run_cell`` operation that queues a cell
whose body sleeps **longer than that 30s HTTP timeout** therefore raises the
wrapper's own client-side timeout *while the kernel may still be running the
cell*. The outcome of the mutation is then genuinely **unknown**: the cell was
created and its body may have started, finished after the client gave up, or
been interrupted by the server's disconnect watcher
(``session.try_interrupt()`` in marimo's execution endpoint).

Design (bounded, single-shot, no retry)
---------------------------------------
- On a disposable copy of the committed fixture, create ONE marked cell that
  sets a START global, sleeps ``SLEEP_S > wrapper timeout``, then sets a DONE
  global. Then issue exactly ONE ``run_cell`` for that cell.
- Catch its *actual* timeout exception (``httpx2.TimeoutException``), never
  retry, and record the exception type/message and the wall time to it.
- After a bounded settle wait, inspect state **read-only**: the START/DONE
  globals (``get_variables``), the cell's runtime state (``get_cell_data``),
  and the disposable file on disk — then classify as COMPLETED /
  STARTED_ABORTED / NOT_EXECUTED / NOT_CREATED, matching
  ``benchmark/native_timeout_probe.py``.
- The whole test asserts a wall-clock cap so a hang cannot silently pass; it is
  additionally run under an external ``timeout`` in the harness.

No product timeout, production code, or shared fixture is touched. The
committed fixture is read only for its bytes to seed the copy.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import httpx2 as httpx
import pytest

from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.mutation import create_cell, run_cell
from marimo_inspection.tools.variables import get_variables

FIXTURE = Path(__file__).parents[3] / "benchmark" / "notebooks" / "mission_clean.py"

#: ``MarimoClient.execute`` default HTTP timeout (the bound under test).
WRAPPER_HTTP_TIMEOUT_S = 30.0
#: Cell sleep must exceed the wrapper timeout so the client gives up mid-cell.
SLEEP_S = 40.0
#: Bounded settle after the timeout: long enough for an uninterrupted 40s
#: sleep to finish before the read-only inspection.
SETTLE_S = 15.0
#: Hard internal wall-clock cap for the whole test body (boot is excluded from
#: this budget on purpose; the harness wraps pytest in an external timeout too).
TOTAL_CAP_S = 75.0


def _var_is_set(payload: dict, name: str) -> bool:
    """True when ``get_variables`` reported the name (present == bound)."""
    variables = payload.get("variables")
    return isinstance(variables, dict) and name in variables


@pytest.mark.live
async def test_wrapper_run_cell_timeout_is_not_proof_of_non_execution(notebook_server):
    """Characterize the wrapper's client-side timeout on a >30s kernel cell."""
    original = FIXTURE.read_bytes()
    _manager, url, sid, copy = await notebook_server(
        FIXTURE.read_text(encoding="utf-8"), name="mission_wrapper_timeout.py"
    )
    assert copy.read_bytes() == original

    digest = uuid.uuid4().hex[:8]
    start_var = f"probe_wt_start_{digest}"
    done_var = f"probe_wt_done_{digest}"
    token = f"TOKEN_{digest}"

    # The marked slow cell. Unique names only, so it conflicts with nothing in
    # the copied fixture.
    cell_source = (
        "import time\n"
        f"{start_var} = {token!r}\n"
        f"time.sleep({SLEEP_S})\n"
        f"{done_var} = {token!r}\n"
    )

    created = await create_cell(cell_source, session_id=sid, server_url=url)
    assert created["status"] == "ok", created
    cell_id = created["cell_id"]

    # --- The ONE run_cell, never retried -----------------------------------
    t0 = time.monotonic()
    outcome_exc: BaseException | None = None
    returned: dict | None = None
    try:
        returned = await run_cell(cell_id, session_id=sid, server_url=url)
    except Exception as exc:  # noqa: BLE001 - record the ACTUAL exception
        outcome_exc = exc
    elapsed = time.monotonic() - t0

    print("EXC_TYPE", type(outcome_exc).__name__ if outcome_exc else None)
    print("EXC_MSG", str(outcome_exc) if outcome_exc else None)
    print("IS_TIMEOUT", isinstance(outcome_exc, httpx.TimeoutException))
    print("ELAPSED_TO_EXC_S", round(elapsed, 1))
    if returned is not None:
        print("WRAPPER_RETURNED", sorted(returned))

    assert outcome_exc is not None, (
        f"run_cell on a {SLEEP_S}s cell returned instead of hitting the "
        f"{WRAPPER_HTTP_TIMEOUT_S}s wrapper timeout: {returned!r}"
    )
    assert isinstance(outcome_exc, httpx.TimeoutException), (
        f"expected httpx2.TimeoutException, got {type(outcome_exc).__name__}: "
        f"{outcome_exc}"
    )
    # The timeout fired at (approximately) the wrapper's own bound, not earlier.
    assert elapsed >= WRAPPER_HTTP_TIMEOUT_S - 1.0, elapsed

    # --- Bounded settle, then read-only inspection -------------------------
    await asyncio.sleep(SETTLE_S)
    read_start = await get_variables(
        variable_names=start_var, session_id=sid, server_url=url
    )
    read_done = await get_variables(
        variable_names=done_var, session_id=sid, server_url=url
    )
    data = await get_cell_data(cell_ids=[cell_id], session_id=sid, server_url=url)
    saved = copy.read_text(encoding="utf-8")
    total = time.monotonic() - t0

    start_set = _var_is_set(read_start, start_var)
    done_set = _var_is_set(read_done, done_var)
    rows = {row.get("cell_id"): row for row in data.get("data", [])}
    row = rows.get(cell_id)
    runtime_state = row.get("runtime_state") if row else None
    cell_present = row is not None
    saved_present = start_var in saved

    print("START_GLOBAL_SET", start_set, "DONE_GLOBAL_SET", done_set)
    print("CELL_PRESENT", cell_present, "CELL_RUNTIME_STATE", runtime_state)
    print("SAVED_SOURCE_PRESENT", saved_present)
    print("TOTAL_TO_READBACK_S", round(total, 1))
    print("READBACK_POLL_RESPONSE", read_start.get("error"), read_done.get("error"))

    # --- Classification (timeout outcome resolved by readback) -------------
    if done_set:
        klass = "COMPLETED"
        note = "run finished server-side after the client timed out"
    elif start_set:
        klass = "STARTED_ABORTED"
        note = "body began before the kill and did not finish (disconnect interrupt)"
    elif cell_present or saved_present:
        klass = "NOT_EXECUTED"
        note = "cell exists but its body never set the START global"
    else:
        klass = "NOT_CREATED"
        note = "no cell carrying the marker source exists"
    print("CLASSIFICATION", klass)
    print("CLASSIFICATION_NOTE", note)

    assert klass == "STARTED_ABORTED", (klass, read_start, read_done, runtime_state)
    assert runtime_state == "interrupted", runtime_state
    assert cell_present and saved_present
    # The bound holds end to end and the copy (not the repo fixture) was used.
    assert total <= TOTAL_CAP_S, (total, klass)
    assert FIXTURE.read_bytes() == original
