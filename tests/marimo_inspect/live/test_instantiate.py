"""Hermetic live coverage of original-cell instantiation (Task 03).

`GET /sse` **materializes** a kernel session but executes no cell of the
notebook file. A frontend performs a second step: it reads the
skew-protection token out of the served page
(``<marimo-server-token data-token="…">``) and POSTs
``/api/kernel/instantiate`` with that token as ``Marimo-Server-Token`` plus
``Marimo-Session-Id``, body ``{"objectIds": [], "values": [], "autoRun": true}``
(``objectIds``/``values`` are required by marimo's request model; ``autoRun`` is
its camelCase wire key and defaults to true). Only then do the file's own cells
run. ``--no-token`` disables **auth**, not skew protection — the header is
required on a normal headless server — while ``--mcp``/``--no-skew-protection``
switch the middleware off.

Two separate regressions close the coverage gap, deliberately not merged:

* :func:`test_sse_alone_does_not_execute_then_instantiate_does` uses a
  **generated** temporary notebook whose cell writes a sentinel file. It is the
  mechanism proof: the `/sse` session alone leaves the sentinel absent, and
  instantiation makes it appear with the expected contents.
* :func:`test_committed_fixture_original_cells_execute_after_instantiate` boots
  a byte-identical **copy** of the committed ``notebooks/test_marimo.py`` and
  reads its original cells back through the real MCP handlers. A generated
  sentinel notebook cannot stand in for this: only the committed document's own
  cells can be said to be covered.

The committed fixture's instantiated state is not all-green, and this file says
so rather than smoothing it over: the hidden setup cell's helper ``_double`` is
a **leading-underscore name**, which marimo classifies as a cell temporary
(``marimo._ast.variables.is_local``; ``_ast/compiler.py`` keeps only
non-underscore defs as nonlocals), so it never reaches the app namespace and the
cell reading it raises ``NameError`` — which in turn cancels its dependent table
cell. The intentional ``ValueError("integration_test_error")`` cell is the one
structured error. Before this task the suite could not see any of that.

Teardown re-checks the committed fixture is byte-identical to what it was at
boot, so no repo fixture is ever mounted or rewritten.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.cells import (
    get_cell_data,
    get_cell_map,
    get_cell_outputs,
)
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.variables import get_variables

#: The committed fixture this module covers (a copy is booted, never the file).
NOTEBOOK_PATH = Path(__file__).parents[3] / "notebooks" / "test_marimo.py"

#: Instantiation is asynchronous: the POST is accepted before the cells run, so
#: the executed state is polled rather than assumed.
POLL_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.2

#: A generated notebook whose single cell writes a sentinel file. It proves the
#: endpoint mechanism only — never that the committed fixture is covered.
SENTINEL_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    from pathlib import Path

    Path({sentinel!r}).write_text({payload!r})

    sentinel_written = True
    return (sentinel_written,)
"""

SENTINEL_PAYLOAD = "instantiated-original-cell"

#: Preview fragments that locate each original fixture cell.
SETUP = "def _double(x)"
IMPORTS = "import marimo as mo"
COMPUTED = "value_a = 1"
DEPENDENT_ON_SETUP = "doubled = _double(value_c)"
TABLE = "table = pl.DataFrame("
ERROR_CELL = 'raise ValueError("integration_test_error")'


def _find(cell_map: dict, needle: str) -> dict:
    """Return the first cell whose preview contains ``needle``."""
    for cell in cell_map["cells"]:
        if needle in (cell.get("preview") or ""):
            return cell
    raise AssertionError(f"no cell previewing {needle!r} in {cell_map}")


async def _poll(
    probe: Callable[[], Awaitable[Any]],
    done: Callable[[Any], bool],
    *,
    timeout: float = POLL_TIMEOUT_S,
) -> Any:
    """Re-run ``probe`` until ``done`` holds or ``timeout`` passes.

    Returns the last probe value either way, so the caller's assertion (not the
    poll) owns the failure — a timeout reports the observed state, never a bare
    "timed out".
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    observed = await probe()
    while not done(observed) and loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        observed = await probe()
    return observed


async def _read_until_ok(probe: Callable[[], Awaitable[dict]], key: str) -> dict:
    """Re-run a read tool until its documented payload key is present.

    The kernel runs an instantiated document asynchronously, and while the
    notebook's own cells are finishing their console output drains through the
    same ``POST /api/kernel/execute`` scratchpad stream the read templates use:
    for well under a second a read therefore answers the tool's execution-error
    shape (``{"error": "Execution failed", "stderr": […]``) instead of its
    documented payload. Asserting on the first read after instantiate would be
    flaky, and mistaking that shape for an empty notebook would be wrong — so
    every read here polls past it on the payload's own key.
    """
    payload = await _poll(probe, lambda p: key in p)
    assert key in payload, payload
    return payload


async def _cell_map(session_id: str, server_url: str) -> dict:
    return await get_cell_map(session_id=session_id, server_url=server_url)


def _instantiated(cell_map: dict) -> bool:
    """True when a cell-map read says every cell ran AND has an output record.

    A cell leaves `stale` as soon as its execution finishes, but its output
    record can land a moment later (measured on a generated single-cell
    notebook), so the poll waits for both rather than asserting on the first
    non-stale read.
    """
    cells = cell_map.get("cells")
    if not cells or any(cell["runtime_state"] == "stale" for cell in cells):
        return False
    return all(cell["has_output"] for cell in cells)


async def _wait_for_sentinel(path: Path) -> str:
    """Poll until the sentinel file exists, then return its text."""

    async def _read() -> str:
        return path.read_text() if path.exists() else ""

    await _poll(_read, bool)
    return await _read()


@pytest.mark.live
async def test_sse_alone_does_not_execute_then_instantiate_does(
    notebook_server, tmp_path
):
    """Test A — the mechanism, on a generated notebook.

    `notebook_server` boots a headless server and creates its session with the
    `/sse` handshake (the same never-instantiated shape the shared fixture has).
    The sentinel proves, in order, that the handshake ran nothing and that
    `MarimoClient.instantiate_notebook` then ran the cell.
    """
    sentinel = tmp_path / "sentinel.txt"
    source = SENTINEL_NOTEBOOK.format(sentinel=str(sentinel), payload=SENTINEL_PAYLOAD)
    _manager, server_url, session_id, _notebook = await notebook_server(
        source, name="sentinel.py"
    )

    client = MarimoClient(server_url)
    try:
        # ── /sse alone: the session is live, the cell has NOT run.
        before = await _read_until_ok(
            lambda: _cell_map(session_id, server_url), "cells"
        )
        assert [c["runtime_state"] for c in before["cells"]] == ["stale"], before
        assert before["cells"][0]["has_output"] is False, before
        before_vars = await _read_until_ok(
            lambda: get_variables(session_id=session_id, server_url=server_url),
            "variables",
        )
        assert before_vars["variables"] == {}, before_vars
        assert not sentinel.exists(), "the /sse handshake must not execute cells"

        # ── the frontend's second step, through the tested client primitive.
        outcome = await client.instantiate_notebook(session_id)
        assert outcome.ok is True, outcome
        assert outcome.reason == "", outcome
        assert outcome.status_code == 200, outcome
        assert outcome.token_used is True, outcome
        assert outcome.token_refreshed is False, outcome

        # ── the original cell genuinely executed.
        executed = await _poll(lambda: _cell_map(session_id, server_url), _instantiated)
        assert _instantiated(executed), executed
        assert executed["cells"][0]["runtime_state"] == "idle", executed
        assert executed["cells"][0]["has_output"] is True, executed

        text = await _wait_for_sentinel(sentinel)
        assert text == SENTINEL_PAYLOAD, (sentinel, text)

        # The cell's public variable is visible to the read tools, which is
        # what proves execution and not merely a file write.
        after_vars = await _read_until_ok(
            lambda: get_variables(session_id=session_id, server_url=server_url),
            "variables",
        )
        assert after_vars["variables"]["sentinel_written"] == {
            "value": "True",
            "datatype": "bool",
        }, after_vars
    finally:
        await client.close()


@pytest.mark.live
async def test_committed_fixture_original_cells_execute_after_instantiate(
    notebook_server,
):
    """Test B — the committed fixture's own cells, on a byte-identical copy.

    `notebooks/test_marimo.py` is copied into `tmp_path` byte-for-byte, booted
    on its own disposable server, its `/sse` session instantiated, and its
    ORIGINAL cells read back through the real MCP handlers. The committed file
    is asserted unchanged at boot and at the end (the `notebook_server` factory
    re-checks it again in teardown).
    """
    committed = NOTEBOOK_PATH.read_bytes()
    _manager, server_url, session_id, notebook_copy = await notebook_server(
        committed.decode("utf-8"), name="test_marimo.py"
    )
    assert notebook_copy.read_bytes() == committed, "the copy is not byte-identical"

    client = MarimoClient(server_url)
    try:
        # ── before: /sse materialized the session but executed no cell.
        before = await _read_until_ok(
            lambda: _cell_map(session_id, server_url), "cells"
        )
        assert len(before["cells"]) == 6, before
        assert all(c["runtime_state"] == "stale" for c in before["cells"]), before
        before_vars = await _read_until_ok(
            lambda: get_variables(session_id=session_id, server_url=server_url),
            "variables",
        )
        assert before_vars["session_id"] == session_id, before_vars
        assert before_vars["tables"] == {}, before_vars
        assert before_vars["variables"] == {}, before_vars

        # ── the frontend's second step, encoded by the tested client primitive.
        outcome = await client.instantiate_notebook(session_id)
        assert outcome.ok is True, outcome
        assert outcome.reason == "", outcome
        assert outcome.status_code == 200, outcome
        assert outcome.token_used is True, outcome

        # ── after: every original cell has left `stale`.
        after = await _poll(lambda: _cell_map(session_id, server_url), _instantiated)
        assert _instantiated(after), after
        # No cell was added, dropped or re-keyed by instantiation.
        assert [c["cell_id"] for c in after["cells"]] == [
            c["cell_id"] for c in before["cells"]
        ], after

        # The clean cells ran: idle, an output record, no error.
        for needle in (SETUP, IMPORTS, COMPUTED):
            cell = _find(after, needle)
            assert cell["runtime_state"] == "idle", cell
            assert cell["has_output"] is True, cell
            assert cell["has_errors"] is False, cell

        # At least one output exists for the ORIGINAL cells — before
        # instantiation every `visual_output` was null.
        outputs = await _read_until_ok(
            lambda: get_cell_outputs(session_id=session_id, server_url=server_url),
            "cells",
        )
        assert len(outputs["cells"]) == 6, outputs
        for cell in outputs["cells"]:
            assert cell["visual_output"] is not None, cell
            assert cell["output_stale"] is False, cell

        # ... and the original computed values are live public variables.
        variables_payload = await _read_until_ok(
            lambda: get_variables(session_id=session_id, server_url=server_url),
            "variables",
        )
        variables = variables_payload["variables"]
        for name in ("value_a", "value_b", "value_c"):
            assert variables[name]["datatype"] == "int", variables
        value_a = int(variables["value_a"]["value"])
        value_b = int(variables["value_b"]["value"])
        assert (value_a, value_b) == (1, 3), variables
        # Read the fixture's own formula back instead of restating it: the
        # original cell computed `value_a + value_b**2`.
        assert int(variables["value_c"]["value"]) == value_a + value_b**2, variables

        # The intentional error cell is one STRUCTURED runtime error.
        errors = await _read_until_ok(
            lambda: get_errors(session_id=session_id, server_url=server_url),
            "has_errors",
        )
        assert errors["has_errors"] is True, errors
        assert errors["total_structured_errors"] == 1, errors
        error_row = next(
            row
            for row in errors["cells"]
            if row["cell_id"] == _find(after, ERROR_CELL)["cell_id"]
        )
        assert error_row["structured_errors"][0]["kind"] == "runtime", error_row
        assert (
            "ValueError: integration_test_error"
            in (error_row["structured_errors"][0]["msg"])
        ), error_row

        # The fixture's own defect, pinned rather than glossed over: the cell
        # reading the hidden setup helper `_double` raises NameError on the
        # CONSOLE channel only (leading-underscore names are cell temporaries,
        # never app globals), and its dependent table cell is cancelled.
        defect = _find(after, DEPENDENT_ON_SETUP)
        assert defect["runtime_state"] == "exception", defect
        assert defect["has_errors"] is False, defect
        defect_errors = next(
            row for row in errors["cells"] if row["cell_id"] == defect["cell_id"]
        )
        assert defect_errors["structured_errors"] == [], defect_errors
        assert defect_errors["has_console_exception"] is True, defect_errors
        traceback_text = " ".join(
            event["data"] for event in defect_errors["console_stderr"]
        )
        assert "NameError" in traceback_text, traceback_text
        assert "_double" in traceback_text, traceback_text

        assert _find(after, TABLE)["runtime_state"] == "cancelled", after

        # The composite read reaches the ORIGINAL cells too.
        data = await _read_until_ok(
            lambda: get_cell_data(
                session_id=session_id,
                cell_ids=[c["cell_id"] for c in after["cells"]],
                include_errors=True,
                server_url=server_url,
            ),
            "data",
        )
        assert len(data["data"]) == 6, data
        data_error = next(
            row
            for row in data["data"]
            if row["cell_id"] == _find(after, ERROR_CELL)["cell_id"]
        )
        assert data_error["structured_errors"], data_error
        data_defect = next(
            row for row in data["data"] if row["cell_id"] == defect["cell_id"]
        )
        assert data_defect["code"] == "doubled = _double(value_c)", data_defect
    finally:
        await client.close()

    # The committed fixture and the copy it was booted from are both untouched.
    assert notebook_copy.read_bytes() == committed
    assert NOTEBOOK_PATH.read_bytes() == committed
