"""Live integration tests for the cell_data template.

Behavioral assertions: code retrieved from the live kernel round-trips the
fixture notebook's actual cell source.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.cell_data import TEMPLATE_CELL_DATA
from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell

EXPECTED_TOTAL_CELLS = 6

# The legacy row shape (backward compatibility) and the exact opt-in additions.
_LEGACY_ROW_KEYS = {"cell_id", "code", "runtime_state", "variables"}
_OPT_IN_ROW_KEYS = {
    "structured_errors",
    "console_stderr",
    "has_console_exception",
    "console_exception_evidence",
}


@pytest.mark.live
async def test_cell_data_returns_all_cells(live_client, live_session):
    """cell_data (empty list = all cells) returns every fixture cell."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])
    assert len(data["data"]) == EXPECTED_TOTAL_CELLS


@pytest.mark.live
async def test_cell_data_roundtrips_known_code(live_client, live_session):
    """The computed-value cell's source round-trips exactly."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])

    code_by_id = {c["cell_id"]: c["code"] for c in data["data"]}
    # The fixtures' value_a/value_b/value_c cell.
    matching = [
        code
        for code in code_by_id.values()
        if "value_a = 1" in code and "value_c = value_a + value_b**2" in code
    ]
    assert matching, (
        f"No cell with computed-value source; got: {list(code_by_id.values())}"
    )


@pytest.mark.live
async def test_cell_data_roundtrips_error_cell(live_client, live_session):
    """The intentional error cell's source is retrievable."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])

    error_codes = [
        c["code"] for c in data["data"] if "integration_test_error" in c["code"]
    ]
    assert error_codes, "Error cell source should be present via cell_data"


@pytest.mark.live
async def test_cell_data_multiple_cells_matches_cell_map(live_client, live_session):
    """cell_data count agrees with the cell map total."""
    from marimo_inspection.templates.cell_map import TEMPLATE_CELL_MAP

    map_result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_MAP)
    map_data = json.loads(map_result.stdout[0])

    result = await live_client.execute(live_session.session_id, TEMPLATE_CELL_DATA)
    data = json.loads(result.stdout[0])
    assert len(data["data"]) == map_data["total_cells"]


@pytest.mark.live
async def test_include_errors_is_opt_in_and_reports_a_raising_cell(mutation_server):
    """Composite read: default rows unchanged; opt-in rows carry both channels.

    A cell that raises reports its structured runtime error inline AND marimo's
    console-channel traceback, while a clean cell reports explicit
    empty/false/null error fields — and the default call adds none of the
    optional keys, not even for the erroring cell.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server

    created: list[str] = []
    try:
        raiser = await create_cell(
            'raise ValueError("composite_read_probe")',
            session_id=session_id,
            server_url=server_url,
        )
        assert raiser["status"] == "ok", raiser
        created.append(raiser["cell_id"])
        clean = await create_cell(
            "composite_read_clean = 42",
            session_id=session_id,
            server_url=server_url,
        )
        assert clean["status"] == "ok", clean
        created.append(clean["cell_id"])

        # Both cells run; the raiser legitimately ends in exception state.
        clean_run = await run_cell(
            clean["cell_id"], session_id=session_id, server_url=server_url
        )
        assert clean_run["status"] == "ok", clean_run
        await run_cell(raiser["cell_id"], session_id=session_id, server_url=server_url)

        selected = [raiser["cell_id"], clean["cell_id"]]

        default = await get_cell_data(
            cell_ids=selected, session_id=session_id, server_url=server_url
        )
        assert "error" not in default, default
        default_rows = {row["cell_id"]: row for row in default["data"]}
        assert set(default_rows) == set(selected)
        for row in default["data"]:
            assert _OPT_IN_ROW_KEYS.isdisjoint(row), row
            assert set(row) == _LEGACY_ROW_KEYS, row
            assert row["variables"] is None, row

        opt_in = await get_cell_data(
            cell_ids=selected,
            include_errors=True,
            session_id=session_id,
            server_url=server_url,
        )
        assert "error" not in opt_in, opt_in
        opt_rows = {row["cell_id"]: row for row in opt_in["data"]}
        assert set(opt_rows) == set(selected)
        for row in opt_in["data"]:
            assert set(row) == _LEGACY_ROW_KEYS | _OPT_IN_ROW_KEYS, row

        boom = opt_rows[raiser["cell_id"]]
        assert [e["kind"] for e in boom["structured_errors"]] == ["runtime"], boom
        evidence = (
            boom["structured_errors"][0]["msg"]
            + " "
            + (boom["structured_errors"][0]["exception"] or "")
        )
        assert "composite_read_probe" in evidence, boom
        # The console channel is NOT empty for a normal raising cell: marimo
        # records the traceback as a stderr console event, so the opt-in row
        # carries real exception evidence next to the structured error
        # (observed live on 0.24: one stderr entry whose data is the traceback
        # text, mimetype application/vnd.marimo+traceback, evidence
        # "traceback"). Assert that observed shape, not just "it is a list".
        assert boom["console_stderr"], boom
        assert all(e["channel"] == "stderr" for e in boom["console_stderr"]), boom
        stderr_text = "\n".join(str(e["data"]) for e in boom["console_stderr"])
        assert "Traceback (most recent call last)" in stderr_text, boom
        assert "ValueError: composite_read_probe" in stderr_text, boom
        assert boom["has_console_exception"] is True, boom
        assert boom["console_exception_evidence"] == "traceback", boom

        fresh = opt_rows[clean["cell_id"]]
        assert fresh["structured_errors"] == [], fresh
        assert fresh["console_stderr"] == [], fresh
        assert fresh["has_console_exception"] is False, fresh
        assert fresh["console_exception_evidence"] is None, fresh
    finally:
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            assert deleted.get("status") == "ok", deleted
