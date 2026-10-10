"""First bounded live gate for the disposable A/B comparison fixture."""

from pathlib import Path

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.mutation import create_cell, run_cell
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

FIXTURE = Path(__file__).parents[3] / "benchmark" / "notebooks" / "mission_clean.py"


def _value(payload: dict, name: str):
    node = payload["variables"][name]["value"]
    return node["value"] if isinstance(node, dict) else node


async def _assert_bound(server_url: str, session_id: str, notebook: Path) -> None:
    client = MarimoClient(server_url)
    try:
        sessions = await client.list_sessions()
        assert len(sessions) == 1, sessions
        assert sessions[0].session_id == session_id, sessions
        assert sessions[0].file is not None, sessions
        assert Path(sessions[0].file).resolve() == notebook.resolve(), sessions
    finally:
        await client.close()


@pytest.mark.live
async def test_clean_fixture_executes_and_slider_reacts(notebook_server):
    """Prove real-kernel behaviour without claiming the mission was completed."""
    original = FIXTURE.read_bytes()
    _manager, server_url, session_id, copy = await notebook_server(
        FIXTURE.read_text(encoding="utf-8"), name=FIXTURE.name
    )
    assert copy.read_bytes() == original
    await _assert_bound(server_url, session_id, copy)
    result = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert result["status"] == "ok", result
    assert result["counts"]["requested"] == 5, result
    initial = await get_variables(session_id=session_id, server_url=server_url)
    assert str(_value(initial, "gain")) == "2", initial
    assert "4" in str(_value(initial, "processed")), initial
    moved = await set_ui_value("gain", 4, session_id=session_id, server_url=server_url)
    assert moved["status"] == "ok" and moved["verified"] and moved["applied"], moved
    after = await get_variables(session_id=session_id, server_url=server_url)
    assert str(_value(after, "gain")) == "4", after
    assert "32" in str(_value(after, "processed")), after
    errors = await get_errors(session_id=session_id, server_url=server_url)
    assert errors["has_errors"] is False, errors
    assert FIXTURE.read_bytes() == original


@pytest.mark.live
async def test_wrapper_metric_survives_a_fresh_server(notebook_server):
    """Separate runtime result from actual saved-file/fresh-kernel evidence."""
    original = FIXTURE.read_bytes()
    manager, server_url, session_id, copy = await notebook_server(
        FIXTURE.read_text(encoding="utf-8"), name="mission_saved.py"
    )
    await _assert_bound(server_url, session_id, copy)
    assert manager.process is not None
    first_pid = manager.process.pid
    boot = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert boot["status"] == "ok", boot
    created = await create_cell(
        "normalized = float(abs(processed).mean() / abs(input_vector).max())\nnormalized",
        session_id=session_id,
        server_url=server_url,
    )
    assert created["status"] == "ok", created
    executed = await run_cell(
        created["cell_id"], session_id=session_id, server_url=server_url
    )
    assert executed["status"] == "ok", executed
    low = await get_variables(session_id=session_id, server_url=server_url)
    assert float(_value(low, "normalized")) == 1.25, low
    moved = await set_ui_value("gain", 4, session_id=session_id, server_url=server_url)
    assert moved["status"] == "ok" and moved["verified"], moved
    high = await get_variables(session_id=session_id, server_url=server_url)
    assert float(_value(high, "normalized")) == 2.5, high
    assert FIXTURE.read_bytes() == original

    # The API's live read is not durability: insist the copied file contains
    # the metric before stopping this process, then start a *new* one on it.
    assert "normalized = float(" in copy.read_text(encoding="utf-8"), (
        "live edit has not been saved to the disposable notebook"
    )
    await manager.stop()
    reopened = type(manager)()
    try:
        await reopened.start(str(copy))
        fresh_id = await reopened.create_session()
        assert reopened.process is not None
        assert reopened.process.pid != first_pid
        assert fresh_id != session_id
        await _assert_bound(reopened.server_url, fresh_id, copy)
        fresh_run = await run_cell(
            mode="all", session_id=fresh_id, server_url=reopened.server_url
        )
        assert fresh_run["status"] == "ok", fresh_run
        fresh = await get_variables(session_id=fresh_id, server_url=reopened.server_url)
        assert float(_value(fresh, "normalized")) == 1.25, fresh
        errors = await get_errors(session_id=fresh_id, server_url=reopened.server_url)
        assert errors["has_errors"] is False, errors
    finally:
        await reopened.stop()
    assert FIXTURE.read_bytes() == original
