"""Server discovery from marimo's registry files.

Marimo registers a running server in its server registry when it is started
with ``--no-token`` — in **both** modes, edit and run (the registration gate in
``_server/api/lifespans.py`` is auth-disabled, not the session mode). POSIX:
``$XDG_STATE_HOME/marimo/servers/<host>_<port>.json``; Windows:
``%USERPROFILE%/.marimo/servers``.

This module reads those entries and health-checks each with
``GET /api/sessions``; only a ``200`` counts. That check is also what makes a
``marimo run`` server undiscoverable: ``GET /api/sessions`` requires ``edit``
scope and answers ``401`` in run mode, so a run server registers yet is never
returned here. Registering, being discoverable, and having a live session are
three different things: in marimo 0.24 a launch creates no session at all —
only a client connect (``/ws`` or ``/sse``) does.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredServer:
    """A discovered and health-checked marimo server."""

    url: str
    pid: int | None = None
    healthy: bool = False


async def discover_servers(
    base_url: str | None = None,
) -> list[DiscoveredServer]:
    """Discover running marimo servers from the registry.

    Scans the registry for server entries, validates each via HTTP health
    check (``GET /api/sessions`` must answer ``200``), and returns the healthy
    ones. A ``marimo run`` server registers in the same registry but answers
    ``401`` to that endpoint (it requires ``edit`` scope), so it is dropped
    here and never counted by a discovery-based ``servers_discovered``.

    Args:
        base_url: Optional override for the registry base URL.
            Defaults to scanning the local registry.

    Returns:
        List of discovered servers, healthy ones first.
    """
    servers = []

    if base_url:
        server = await _check_server(base_url)
        if server:
            servers.append(server)
        return servers

    registry_dir = _get_registry_dir()
    if not registry_dir.exists():
        logger.warning("Registry directory not found: %s", registry_dir)
        return servers

    for json_file in sorted(registry_dir.glob("*.json")):
        try:
            server = await _check_server_file(json_file)
            if server:
                servers.append(server)
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Failed to parse registry file %s: %s", json_file, exc)

    # Healthy servers first
    servers.sort(key=lambda s: not s.healthy)
    return servers


async def _check_server(url: str) -> DiscoveredServer | None:
    """Health-check a single server URL."""
    server = DiscoveredServer(url=url)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{url}/api/sessions")
            if response.status_code == 200:
                server.healthy = True
                return server
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("Server %s health check failed: %s", url, exc)

    return None


async def _check_server_file(json_file: Path) -> DiscoveredServer | None:
    """Parse a registry JSON file and health-check the server."""
    try:
        data = json.loads(json_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Support both old format (url/baseUrl) and new format (host+port)
    url = data.get("url") or data.get("baseUrl")
    if not url:
        host = data.get("host")
        port = data.get("port")
        if host and port:
            url = f"http://{host}:{port}"
        else:
            return None

    # Remove trailing slashes
    url = url.rstrip("/")

    pid = data.get("pid")
    server = DiscoveredServer(url=url, pid=pid)

    # Check if the server process is still running (Windows)
    if pid and os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # OpenProcess with PROCESS_QUERY_INFORMATION
            handle = kernel32.OpenProcess(0x00000400, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
            else:
                # Process not running - stale registry entry
                logger.debug("Stale registry: PID %d not running", pid)
                return None
        except OSError as exc:
            # Fall through to HTTP check
            logger.debug("Failed to check PID %d: %s", pid, exc)

    # HTTP health check
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{url}/api/sessions")
            if response.status_code == 200:
                server.healthy = True
                return server
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("Server %s health check failed: %s", url, exc)

    return None


def _get_registry_dir() -> Path:
    """Get the marimo server registry directory.

    Marimo registers server instances in a platform-specific directory:

    - Windows: ``%USERPROFILE%\\.marimo\\servers``
    - POSIX: ``$XDG_STATE_HOME/marimo/servers`` (defaults to
      ``~/.local/state/marimo/servers``)

    Servers started with ``--no-token`` register there and deregister on
    clean shutdown. This mirrors ``discover-servers.sh``.
    """
    if os.name == "nt":
        return Path.home() / ".marimo" / "servers"

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "marimo" / "servers"
    return Path.home() / ".local" / "state" / "marimo" / "servers"


async def list_sessions(
    server_url: str,
) -> dict[str, Any]:
    """List active sessions on a specific server.

    Args:
        server_url: Base URL of the marimo server (without trailing slash).

    Returns:
        Raw session data from GET /api/sessions.
    """
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{server_url}/api/sessions")
        response.raise_for_status()
        return response.json()
