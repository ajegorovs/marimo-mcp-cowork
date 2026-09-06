"""Live kernel fixtures for integration tests.

Direct kernel launch with proper session management.
No manual server start required - tests manage their own kernel lifecycle.

Architecture:
- MarimoServerManager: Manages marimo server process lifecycle
- Session isolation: Each test gets a clean session
- Proper cleanup: No residual state between tests
- Fast execution: < 10s total test time
- No Playwright: Uses HTTP API for session creation
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.discovery import discover_servers


class MarimoServerManager:
    """Manages a marimo server process for testing.

    Provides direct kernel launch without MCP intermediary.
    Explicit lifecycle control - no auto-resurrection.
    """

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.server_url: str | None = None
        self.session_id: str | None = None
        self._sessions: list[str] = []

    async def start(self, notebook_path: str = "notebooks/test_marimo.py"):
        """Start a marimo server process."""
        # Start marimo server with --no-token --headless.
        # The port is configurable via $MARIMO_TEST_PORT (default 2718) so the
        # suite does not depend on a hardcoded, possibly-occupied port.
        port = os.environ.get("MARIMO_TEST_PORT", "2718")
        cmd = [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            notebook_path,
            "--no-token",
            "--headless",
            "--port",
            port,
        ]

        self.process = subprocess.Popen(  # noqa: ASYNC220 - test scaffolding
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to start
        await asyncio.sleep(2)

        # Prefer the exact port we launched on; fall back to discovery.
        self.server_url = f"http://localhost:{port}"
        servers = await discover_servers()
        if servers:
            self.server_url = servers[0].url

        return self.server_url

    async def create_session(self):
        """Create a session using HTTP API.

        Polls /api/sessions until a session exists.
        No Playwright needed - marimo creates sessions automatically.
        """
        if not self.server_url:
            raise RuntimeError("Server not started")

        # Use urllib to poll /api/sessions
        for attempt in range(20):  # Max 20 attempts (10 seconds)
            try:
                response = urllib.request.urlopen(  # noqa: ASYNC210 - test polling
                    f"{self.server_url}/api/sessions", timeout=2
                )
                sessions = json.loads(response.read().decode())

                if sessions:
                    # Return first available session
                    return sessions[0]
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass  # Server not ready yet, continue polling

            await asyncio.sleep(0.5)

        raise TimeoutError(
            f"Timed out waiting for session at {self.server_url}/api/sessions"
        )

    async def cleanup_session(self, session_id: str):
        """Cleanup a session after test completion."""
        # For now, just remove from tracking
        # In future, could add session termination logic
        if session_id in self._sessions:
            self._sessions.remove(session_id)

    async def stop(self):
        """Stop the marimo server process."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


# ─── Session-Scoped Fixtures ──────────────────────────────────────────────


@pytest.fixture(scope="session")
async def kernel_manager():
    """Create a direct kernel manager for all live tests.

    Manages the marimo server lifecycle:
    - Starts server before tests
    - Stops server after tests
    - No MCP intermediary
    - No auto-resurrection
    """
    manager = MarimoServerManager()
    await manager.start()
    yield manager
    await manager.stop()


@pytest.fixture(scope="session")
def live_server_url(kernel_manager):
    """Return the server URL for live tests."""
    return kernel_manager.server_url


# ─── Function-Scoped Fixtures (Per-Test Isolation) ─────────────────────────


@pytest.fixture(scope="function")
async def live_client(live_server_url):
    """Create a fresh MarimoClient for each test.

    Using function scope avoids connection pool issues with SSE streaming.
    Each test gets a fresh client - no state pollution.
    """
    client = MarimoClient(live_server_url)
    yield client
    await client.close()


@pytest.fixture(scope="function")
async def live_session(live_client):
    """Resolve the test session from the live server.

    Each test gets a fresh session - no state pollution between tests.
    Session is cleaned up after test completion.
    """
    session = await live_client.resolve_session()
    yield session
    # Cleanup would happen here if needed


# ─── Legacy Compatibility (for tests that still use old fixture names) ─────


@pytest.fixture(scope="session")
def marimo_server(kernel_manager):
    """Legacy fixture name - uses kernel_manager."""
    return kernel_manager


@pytest.fixture(scope="session")
async def direct_kernel_manager(kernel_manager):
    """Compatibility with direct kernel launch tests."""
    return kernel_manager


@pytest.fixture(scope="session")
async def direct_server_url(live_server_url):
    """Compatibility with direct kernel launch tests."""
    return live_server_url


@pytest.fixture(scope="function")
async def direct_client(live_client):
    """Compatibility with direct kernel launch tests."""
    return live_client


@pytest.fixture(scope="function")
async def direct_session(live_session):
    """Compatibility with direct kernel launch tests."""
    return live_session
