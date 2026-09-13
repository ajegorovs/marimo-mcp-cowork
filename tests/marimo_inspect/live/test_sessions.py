"""Hermetic live assertions of the session-census field limit (T22).

marimo 0.24's ``GET /api/sessions`` publishes only each session's
``filename``/``path``. Provenance (how a session came to exist) and owner
(which client holds it) are therefore **not knowable** from the census, which
is why ``list_active_notebooks`` reports them as ``"unknown"`` placeholders
rather than inferring them from the MCP binding, and why
``attached_client_count`` is ``null`` rather than a fabricated number.

The raw shape is pinned through the ``notebook_server`` factory (a disposable
server on a purpose-built notebook) so the honest placeholders cannot be
"fixed" into fabricated values without this gate failing first. The real
``list_active_notebooks`` handler is then called against that server to pin the
summary counts, and ``/api/status/connections.active`` is measured to show it
counts sessions with an **open main consumer** — never attached clients.
"""

from __future__ import annotations

import asyncio
import time

import httpx2 as httpx
import pytest

# A deterministic document; only its existence matters (the session census is
# what is asserted, not any cell).
SESSION_CENSUS_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    seed = 1
    return (seed,)
"""

#: The only per-session fields marimo 0.24 publishes. If marimo starts
#: publishing provenance/owner, or a per-session client count, this set (and
#: the ``"unknown"`` placeholders the tool reports) must be revisited
#: deliberately — never inferred.
_PUBLISHED_SESSION_FIELDS = {"filename", "path"}


async def _active_connections(server_url: str) -> int:
    """``/api/status/connections.active`` — sessions with an OPEN main consumer."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{server_url}/api/status/connections")
    assert response.status_code == 200, response.text
    return response.json()["active"]


async def _wait_for_active(
    server_url: str, expected: int, timeout: float = 10.0
) -> int:
    """Poll the connection counter until it settles (or the deadline passes)."""
    deadline = time.monotonic() + timeout
    observed = await _active_connections(server_url)
    while observed != expected and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        observed = await _active_connections(server_url)
    return observed


@pytest.mark.live
async def test_sessions_census_publishes_only_filename_and_path(notebook_server):
    """The raw census carries no creator, owner, or per-session client count."""
    _manager, server_url, session_id, _notebook = await notebook_server(
        SESSION_CENSUS_NOTEBOOK
    )

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{server_url}/api/sessions")
    assert response.status_code == 200, response.text
    data = response.json()

    assert list(data) == [session_id], data
    published = set(data[session_id])
    unexpected = published - _PUBLISHED_SESSION_FIELDS
    assert not unexpected, (
        f"marimo now publishes {sorted(unexpected)} per session; revisit the "
        "provenance/owner placeholders and `attached_client_count`"
    )


@pytest.mark.live
async def test_list_active_notebooks_reports_truthful_scoped_counts(
    notebook_server,
):
    """The real handler reports honest, scoped counts against a live server.

    One hermetic session means ``session_count`` / ``total_notebooks`` / the
    deprecated ``active_connections`` alias all read 1 and ``result_row_count``
    reads 1; ``attached_client_count`` stays ``None`` because marimo publishes
    no per-session client count at all. Provenance/owner stay ``"unknown"``.
    """
    from marimo_inspection.tools.notebooks import list_active_notebooks

    _manager, server_url, _session_id, _notebook = await notebook_server(
        SESSION_CENSUS_NOTEBOOK
    )

    result = await list_active_notebooks(server_url=server_url)

    summary = result["summary"]
    assert summary["session_count"] == 1
    assert summary["total_notebooks"] == 1
    # Deprecated alias, equal to the session count — never a client count.
    assert summary["active_connections"] == 1
    assert summary["attached_client_count"] is None
    assert summary["result_row_count"] == 1
    assert summary["servers_discovered"] == 1

    assert len(result["notebooks"]) == 1
    row = result["notebooks"][0]
    assert row["provenance"] == "unknown"
    assert row["owner"] == "unknown"


@pytest.mark.live
async def test_connections_active_tracks_the_main_consumer_not_clients(
    notebook_server,
):
    """`/api/status/connections.active` counts sessions with an open MAIN consumer.

    The ``notebook_server`` fixture creates its session over ``/sse`` and closes
    that stream, leaving the session an **orphan**: it is still listed by
    ``/api/sessions`` (so ``list_active_notebooks`` counts it) while the
    connection counter reads 0. Reconnecting the *same* session id over ``/sse``
    makes this stream that session's main consumer, so the counter reads 1;
    closing it orphans the session again and the counter returns to 0.

    At no point is this counter an attached-client count — the tool never
    reports it as one, and ``attached_client_count`` stays ``None`` throughout.
    """
    from marimo_inspection.tools.notebooks import list_active_notebooks

    _manager, server_url, session_id, notebook = await notebook_server(
        SESSION_CENSUS_NOTEBOOK
    )

    # Fixture stream already closed: session listed, but no open main consumer.
    assert await _wait_for_active(server_url, 0) == 0
    orphaned = await list_active_notebooks(server_url=server_url)
    assert orphaned["summary"]["session_count"] == 1
    assert orphaned["summary"]["attached_client_count"] is None

    params = {"session_id": session_id, "file": str(notebook)}
    async with (
        httpx.AsyncClient(timeout=15) as client,
        client.stream("GET", f"{server_url}/sse", params=params) as response,
    ):
        assert response.status_code == 200
        assert await _wait_for_active(server_url, 1) == 1

        # Still one session; the counter is measuring the main consumer, not a
        # client, and the tool still refuses to call it an attached-client count.
        while_open = await list_active_notebooks(server_url=server_url)
        assert while_open["summary"]["session_count"] == 1
        assert while_open["summary"]["attached_client_count"] is None

    # Main stream closed → orphan again → no open main consumer.
    assert await _wait_for_active(server_url, 0) == 0
    after = await list_active_notebooks(server_url=server_url)
    assert after["summary"]["session_count"] == 1
    assert after["summary"]["attached_client_count"] is None
