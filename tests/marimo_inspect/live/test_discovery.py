"""Hermetic live coverage of server-vs-session discovery (T18).

marimo 0.24 materializes a kernel session only when a **client** connects to
``/sse`` (or the ``/ws`` websocket); launching a server — ``marimo edit`` or
``marimo run`` — never creates one, and the on-disk ``__marimo__`` session
cache stores cell outputs, never sessions. Two consequences are pinned here
against real servers booted by the ``bare_server`` factory (which deliberately
does not create a session):

- **Server discovery is not session discovery.** A fresh headless ``edit``
  server is discoverable — its census answers 200 — while reporting **zero**
  sessions, so ``discover_servers`` finds it and yet the listing it feeds has
  no notebook row. A client ``/sse`` connect is the only thing that
  materializes a session, and it materializes **exactly one**; closing that
  stream leaves the session listed as an **orphan** with no open main
  consumer (``/api/status/connections.active`` 0). A session seen before any
  client attached is therefore an earlier client's orphan (or marimo's
  auto-opened browser when not headless), never a launch artifact.
- **A ``run``-mode server is never discoverable.** ``--no-token`` registers it
  in the same registry as an ``edit`` server, but ``GET /api/sessions``
  requires ``edit`` scope and answers **401** in run mode, so the census-200
  health check drops it: ``discover_servers`` does not report it and the
  discovery path's ``servers_discovered`` does not count it. Only an explicit
  ``server_url`` pointed at a run server reaches it, as one connection-failure
  **sentinel** (``session_count`` 0, ``servers_discovered`` 1, one row).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx2 as httpx
import pytest

# A minimal deterministic document; only its (mode-independent) existence
# matters here — no cell is ever asserted on.
DISCOVERY_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    seed = 1
    return (seed,)
"""


def _registry_entries(state_dir: Path) -> list[dict]:
    """Parse the marimo server registry written under an isolated state dir."""
    return [
        json.loads(path.read_text())
        for path in sorted((state_dir / "marimo" / "servers").glob("*.json"))
    ]


async def _wait_for_registry_entries(
    state_dir: Path, expected: int, timeout: float = 10.0
) -> list[dict]:
    """Poll the isolated registry until it holds at least ``expected`` entries."""
    deadline = time.monotonic() + timeout
    entries = _registry_entries(state_dir)
    while len(entries) < expected and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        entries = _registry_entries(state_dir)
    return entries


async def _sessions(server_url: str) -> httpx.Response:
    """Raw ``GET /api/sessions`` (the census ``discover_servers`` checks)."""
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.get(f"{server_url}/api/sessions")


async def _wait_for_session_count(
    server_url: str, expected: int, timeout: float = 10.0
) -> dict:
    """Poll the census until it holds exactly ``expected`` sessions."""
    deadline = time.monotonic() + timeout
    data = (await _sessions(server_url)).json()
    while len(data) != expected and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        data = (await _sessions(server_url)).json()
    return data


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
async def test_fresh_edit_server_is_discoverable_with_zero_sessions(
    bare_server, tmp_path, monkeypatch
):
    """Launching creates a server; only a client connect creates a session.

    The freshly launched (never-sessioned) edit server is healthy and
    discovered, yet its census is empty — server discovery and session
    discovery are different things. The ``/sse`` connect then materializes
    exactly one session, and closing that stream leaves the session listed as
    an orphan with no open main consumer (it is not reaped by default).
    """
    from marimo_inspection.discovery import discover_servers
    from marimo_inspection.tools.notebooks import list_active_notebooks

    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    manager = await bare_server(DISCOVERY_NOTEBOOK, mode="edit", state_dir=state_dir)
    server_url = manager.server_url

    # --no-token registers the server at launch; the registry entry is a
    # discovery fact, not a session.
    entries = await _wait_for_registry_entries(state_dir, expected=1)
    assert len(entries) == 1, entries
    assert entries[0]["host"] == "127.0.0.1"
    assert str(entries[0]["port"]) in server_url

    census = await _sessions(server_url)
    assert census.status_code == 200, census.text
    assert census.json() == {}

    # Server discovery succeeds against the sessionless server ...
    servers = await discover_servers()
    assert [s.url for s in servers] == [server_url]
    assert servers[0].healthy is True

    # ... while the session listing it feeds reports zero sessions and zero
    # rows: a server with no session is discoverable, just empty.
    listed = await list_active_notebooks()
    assert listed["summary"]["servers_discovered"] == 1
    assert listed["summary"]["session_count"] == 0
    assert listed["summary"]["result_row_count"] == 0
    assert listed["notebooks"] == []

    # A client connect is the only thing that materializes a session — exactly
    # one, under the id the handshake asked for.
    session_id = await manager.create_session()
    data = await _wait_for_session_count(server_url, expected=1)
    assert list(data) == [session_id]

    listed_after = await list_active_notebooks()
    assert listed_after["summary"]["session_count"] == 1
    assert [row["session_id"] for row in listed_after["notebooks"]] == [session_id]

    # The fixture's stream is closed: the session survives as an orphan (it is
    # still listed) with no open main consumer.
    assert await _wait_for_active(server_url, 0) == 0
    orphaned = await _sessions(server_url)
    assert orphaned.status_code == 200
    assert list(orphaned.json()) == [session_id]


@pytest.mark.live
async def test_run_mode_server_is_registered_but_not_discoverable(
    bare_server, tmp_path, monkeypatch
):
    """A run-mode server registers yet is invisible to discovery (401 census).

    ``--no-token`` is the only registration gate, so a run server writes the
    same registry entry an edit server does — but ``GET /api/sessions``
    requires ``edit`` scope and answers 401, so the census-200 health check
    drops it: ``discover_servers`` reports it not at all and the discovery
    path's ``servers_discovered`` does not count it. Only an explicit
    ``server_url`` reaches it, as a connection-failure sentinel.
    """
    from marimo_inspection.discovery import discover_servers
    from marimo_inspection.tools.notebooks import list_active_notebooks

    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    manager = await bare_server(DISCOVERY_NOTEBOOK, mode="run", state_dir=state_dir)
    server_url = manager.server_url

    # Registration is mode-independent: the run server is in the registry.
    entries = await _wait_for_registry_entries(state_dir, expected=1)
    assert len(entries) == 1, entries
    assert str(entries[0]["port"]) in server_url

    # Its census, however, is refused for lack of edit scope.
    census = await _sessions(server_url)
    assert census.status_code == 401, census.text

    # Discovery health-checks that census, so the registered run server is
    # dropped entirely — it is never "counted but unlisted".
    assert await discover_servers() == []

    discovery_path = await list_active_notebooks()
    assert discovery_path["summary"]["servers_discovered"] == 0
    assert discovery_path["summary"]["session_count"] == 0
    assert discovery_path["summary"]["result_row_count"] == 0
    assert discovery_path["notebooks"] == []

    # An explicit server_url queries it anyway: one sentinel row, zero
    # sessions, one server queried.
    explicit = await list_active_notebooks(server_url=server_url)
    summary = explicit["summary"]
    assert summary["session_count"] == 0
    assert summary["total_notebooks"] == 0
    assert summary["servers_discovered"] == 1
    assert summary["result_row_count"] == 1
    assert len(explicit["notebooks"]) == 1

    sentinel = explicit["notebooks"][0]
    assert sentinel["name"] == "connection failed"
    assert sentinel["session_id"] == "error"
    assert sentinel["server_url"] == server_url
    assert "401" in sentinel["error"]
    # A sentinel is a row, not a session: it carries no provenance/owner.
    assert "provenance" not in sentinel
    assert "owner" not in sentinel
