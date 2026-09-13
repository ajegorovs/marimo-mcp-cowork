"""Live kernel fixtures for integration tests.

Direct kernel launch with proper session management.
No manual server start required - tests manage their own kernel lifecycle.

Architecture:
- MarimoServerManager: Manages marimo server process lifecycle (any notebook
  path; the shared session uses notebooks/test_marimo.py, the mutation suite
  boots its own servers on tmp_path copies)
- One server + one session per pytest session for the shared fixtures; the
  mutation regressions boot one additional isolated server per test, and the
  discovery regressions boot sessionless edit/run servers (the `bare_server`
  factory: launch creates no session, only a client connect does)
- Session created via the `/sse` plain-HTTP handshake (no websocket
  library needed; see docs/live-test-redesign-plan.md)
- Proper cleanup + log dumping on failure
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from collections import deque
from pathlib import Path

import httpx2 as httpx
import pytest

from marimo_inspection.client import MarimoClient

# Marimo materializes a kernel session only when a client performs the
# frontend handshake (GET /sse?session_id=<uuid>&file=<path> - or the /ws
# websocket). Polling /api/sessions can never create one. See
# docs/live-test-redesign-plan.md §0 for the verified contract.
NOTEBOOK_PATH = Path(__file__).parents[3] / "notebooks" / "test_marimo.py"
START_TIMEOUT_S = 30
HANDLE_TIMEOUT_S = 15


class MarimoServerManager:
    """Manages a marimo server process for testing.

    Provides direct kernel launch without MCP intermediary.
    Explicit lifecycle control - no auto-resurrection.
    """

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.server_url: str | None = None
        self.session_id: str | None = None
        # The notebook path this server was started with; used by the /sse
        # handshake so a manager can serve ANY notebook, not just the repo
        # fixture. Set in start(); required by create_session().
        self.notebook_path: str | None = None
        # "edit" (default) or "run" - see start(). Recorded so a failing test
        # can say which mode it booted.
        self.mode: str = "edit"
        self._state_dir: Path | None = None
        # False when the caller supplied state_dir (it owns the dir, so stop()
        # must not reap it).
        self._owns_state_dir = True
        self._log_lines: deque[str] = deque(maxlen=500)
        self._drain_task: asyncio.Task | None = None

    @staticmethod
    def _pick_port() -> int:
        """Return a free localhost port, honoring $MARIMO_TEST_PORT override."""
        env_port = os.environ.get("MARIMO_TEST_PORT")
        if env_port:
            return int(env_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def start(
        self,
        notebook_path: str | None = None,
        *,
        mode: str = "edit",
        state_dir: str | Path | None = None,
    ) -> str:
        """Start a headless marimo server on a free port.

        ``mode`` selects the subcommand: ``"edit"`` (default) or ``"run"``.
        Both write a registry entry under ``--no-token``, but a ``run``
        server's ``GET /api/sessions`` census is refused with 401 (that
        endpoint requires ``edit`` scope), so ``discover_servers()`` never
        reports a run server — see ``tests/marimo_inspect/live/test_discovery.py``.

        ``state_dir`` overrides the isolated ``XDG_STATE_HOME`` (and therefore
        the marimo server registry). Omit it for the default per-boot temp
        dir; pass one when several servers must share a registry.
        """
        if mode not in ("edit", "run"):
            raise ValueError(f"mode must be 'edit' or 'run', got {mode!r}")
        self.mode = mode
        # Remember which notebook this server owns: create_session() must
        # hand the SAME file back to the /sse handshake or the kernel opens
        # the default notebook instead.
        self.notebook_path = (
            str(notebook_path) if notebook_path is not None else str(NOTEBOOK_PATH)
        )
        notebook = self.notebook_path
        port = self._pick_port()

        # Isolate marimo's state (server registry, cli state) in a temp dir so
        # the suite neither pollutes the developer's real marimo state nor
        # clashes with it; also keeps CI hermetic. A caller-supplied dir is
        # used as-is and is NOT reaped by stop().
        if state_dir is None:
            self._state_dir = Path(tempfile.mkdtemp(prefix="marimo-test-state-"))
            self._owns_state_dir = True
        else:
            self._state_dir = Path(state_dir)
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._owns_state_dir = False
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(self._state_dir)

        cmd = [
            sys.executable,
            "-m",
            "marimo",
            mode,
            notebook,
            "--no-token",
            "--headless",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ]
        self.process = subprocess.Popen(  # noqa: ASYNC220 - test scaffolding
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Drain the pipes in the background so the server never blocks on a
        # full pipe buffer, and so we can surface logs on failure.
        self._drain_task = asyncio.create_task(self._drain_pipes())

        self.server_url = await self._wait_until_ready(port)
        return self.server_url

    async def _drain_pipes(self) -> None:
        """Continuously read server stdout/stderr into a bounded deque."""
        assert self.process is not None
        loop = asyncio.get_running_loop()
        while True:
            for reader in (self.process.stdout, self.process.stderr):
                if reader is None or reader.closed:
                    continue
                try:
                    line = await loop.run_in_executor(None, reader.readline)
                except (ValueError, OSError):
                    continue
                if not line:  # EOF on this pipe
                    continue
                self._log_lines.append(line.decode(errors="replace").rstrip("\n"))
            if self.process.poll() is not None and all(
                r is None or r.closed
                for r in (self.process.stdout, self.process.stderr)
            ):
                break
            await asyncio.sleep(0.05)

    async def _wait_until_ready(self, port: int) -> str:
        """Poll /api/version until the server responds.

        Raises with dumped server logs if it never comes up.
        """
        url = f"http://127.0.0.1:{port}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT_S
        while loop.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                break
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(f"{url}/api/version")
                if response.status_code == 200:
                    return url
            except (httpx.HTTPError, OSError):
                pass
            await asyncio.sleep(0.25)
        self.dump_logs()
        raise RuntimeError(
            f"Marimo server did not become ready within {START_TIMEOUT_S}s "
            f"at {url} (see server logs above)."
        )

    def log_tail(self, n: int = 30) -> str:
        """Return the last n captured server log lines."""
        return "\n".join(list(self._log_lines)[-n:])

    def dump_logs(self) -> None:
        """Print captured server logs (used on failure)."""
        if self._log_lines:
            print("\n--- marimo server log tail ---")
            print(self.log_tail())
            print("-------------------------------")

    async def create_session(self) -> str:
        """Create the single edit-mode session via the /sse handshake.

        marimo only materializes a session when a client connects to
        `/sse?session_id=<uuid>&file=<abs path>` (or the `/ws` websocket).
        We open the stream and wait for the `kernel-ready` event, which also
        guarantees the kernel itself is up before any test runs.
        """
        assert self.server_url is not None, "start() must be called first"
        assert self.notebook_path is not None, "start() did not set notebook_path"
        session_id = str(uuid.uuid4())
        params = {"session_id": session_id, "file": self.notebook_path}
        try:
            async with (
                httpx.AsyncClient(timeout=HANDLE_TIMEOUT_S) as client,
                client.stream(
                    "GET", f"{self.server_url}/sse", params=params
                ) as response,
            ):
                if response.status_code != 200:
                    text = await response.aread()
                    raise RuntimeError(
                        f"Session handshake failed: {response.status_code} {text}"
                    )
                async for line in response.aiter_lines():
                    if '"op": "kernel-ready"' in line or "kernel-ready" in line:
                        break
                else:
                    raise RuntimeError(
                        "Session handshake completed without kernel-ready event"
                    )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Session handshake failed: {exc}") from exc

        self.session_id = session_id
        return session_id

    async def stop(self) -> None:
        """Terminate the server process and reap it."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown best-effort
                self._log_lines.append("[stop] drain task cancelled")
            self._drain_task = None
        # Reap the isolated XDG_STATE_HOME so a long pytest session (or CI)
        # does not accumulate one temp state dir per booted server. A
        # caller-supplied dir belongs to the caller (tmp_path) — leave it.
        if self._state_dir is not None and self._owns_state_dir:
            shutil.rmtree(self._state_dir, ignore_errors=True)
        self._state_dir = None
        self._owns_state_dir = True


# ─── Session-Scoped Fixtures ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def kernel_manager(request):
    """Start the marimo server and create its single edit-mode session.

    Manages the marimo server lifecycle:
    - Starts server + session before tests
    - Stops server after tests
    - No MCP intermediary
    - No auto-resurrection
    """
    manager = MarimoServerManager()
    # Accessible to pytest_sessionfinish for failure diagnostics.
    session = request.session
    try:
        await manager.start()
        await manager.create_session()
        session._marimo_kernel_manager = manager
        yield manager
    except Exception:
        manager.dump_logs()
        raise
    finally:
        await manager.stop()


@pytest.fixture(scope="session")
def live_server_url(kernel_manager):
    """Return the server URL for live tests."""
    return kernel_manager.server_url


@pytest.fixture(scope="session")
def live_session_id(kernel_manager):
    """Return the shared session id used by all live tests.

    marimo edit mode keeps one kernel session per notebook file. While its main
    consumer is open, a distinct client joins that kernel as a non-main
    read-only consumer; after the session becomes orphaned, a new connection
    may resume the same kernel and re-key its session id. Tests share the one
    fixture session rather than changing that topology.
    """
    return kernel_manager.session_id


# ─── Function-Scoped Fixtures (Per-Test Client Isolation) ─────────────────────────────────────────────────


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
async def live_session(live_client, live_session_id):
    """Resolve the test session from the live server.

    Returns the single shared session (fresh client per test; the session
    itself is server-wide because marimo edit mode is single-session).
    """
    session = await live_client.resolve_session(session_id=live_session_id)
    yield session


@pytest.fixture(scope="function")
async def mutation_server(tmp_path):
    """Start an ISOLATED marimo server on a tmp_path copy of the fixture notebook.

    The mutation regressions create/edit/run/delete cells, which changes
    kernel state that must never leak into the shared session — so each test
    gets its own server + session on a COPY of notebooks/test_marimo.py (a
    fresh uuid session id also keeps the process-wide change tracker's keys
    separate from the shared session's). marimo may re-serialize the notebook
    file, so only a disposable copy is ever mounted.

    Yields (manager, server_url, session_id, notebook_copy). After a PASSING
    test, teardown re-checks that the repo fixture notebook is byte-identical
    to what it was at boot (the hermetic binding); a failed body already
    raised out through the fixture, so that check can never mask a real error.
    """
    notebook_copy = tmp_path / "test_marimo.py"
    original_bytes = NOTEBOOK_PATH.read_bytes()
    shutil.copyfile(NOTEBOOK_PATH, notebook_copy)
    manager = MarimoServerManager()
    try:
        await manager.start(str(notebook_copy))
        await manager.create_session()
        yield manager, manager.server_url, manager.session_id, notebook_copy
    except Exception:
        manager.dump_logs()
        raise
    finally:
        await manager.stop()

    # Only reached when setup + the test body succeeded (see docstring).
    assert NOTEBOOK_PATH.read_bytes() == original_bytes, (
        "Hermeticity violation: notebooks/test_marimo.py changed under the "
        "mutation suite. Run `git status` and restore it."
    )


@pytest.fixture(scope="function")
async def notebook_server(tmp_path):
    """Factory: boot an isolated headless server on a PURPOSE-BUILT notebook.

    Returns an async ``_boot(source, name="notebook.py")`` that writes ``source``
    into ``tmp_path``, starts a fresh ``MarimoServerManager`` on it and creates
    its session via the ``/sse`` handshake — the same never-instantiated session
    the shared and mutation fixtures use, but on a cell set the caller controls.
    Use it when a test needs a deterministic document (e.g. a bulk-run notebook
    without the repo fixture's deliberate error cell) instead of mutating a copy
    of ``notebooks/test_marimo.py``.

    Teardown stops every server it started and re-checks that the repo fixture
    ``notebooks/test_marimo.py`` is byte-identical to what it was at boot (the
    same hermeticity gate ``mutation_server`` applies). The check runs only when
    setup + the test body succeeded, so it can never mask a real failure.
    """
    managers: list[MarimoServerManager] = []
    original_bytes = NOTEBOOK_PATH.read_bytes()

    async def _boot(source: str, name: str = "notebook.py"):
        notebook = tmp_path / name
        notebook.write_text(source)
        manager = MarimoServerManager()
        await manager.start(str(notebook))
        await manager.create_session()
        managers.append(manager)
        return manager, manager.server_url, manager.session_id, notebook

    try:
        yield _boot
    except Exception:
        for manager in managers:
            manager.dump_logs()
        raise
    finally:
        for manager in managers:
            await manager.stop()

    # Only reached when setup + the test body succeeded (see docstring).
    assert NOTEBOOK_PATH.read_bytes() == original_bytes, (
        "Hermeticity violation: notebooks/test_marimo.py changed under the "
        "notebook_server factory. Run `git status` and restore it."
    )


@pytest.fixture(scope="function")
async def bare_server(tmp_path):
    """Factory: boot an isolated headless server that stays **sessionless**.

    Returns an async ``_boot(source, *, name="notebook.py", mode="edit",
    state_dir=None) -> MarimoServerManager`` that writes ``source`` into
    ``tmp_path``, starts a ``MarimoServerManager`` on it and does **not**
    create a session — the caller decides whether and when a client
    materializes one (launch never does; see
    ``tests/marimo_inspect/live/test_discovery.py``).

    ``mode`` selects ``marimo edit`` (default) or ``marimo run``.
    ``state_dir`` pins the isolated ``XDG_STATE_HOME`` — and with it the marimo
    server registry — so a test can point ``discover_servers()`` (and
    ``list_active_notebooks()``' discovery path) at exactly the servers it
    booted.

    Teardown stops every server it started and re-checks that the repo fixture
    ``notebooks/test_marimo.py`` is byte-identical to what it was at boot (the
    same hermeticity gate the other factories apply).
    """
    managers: list[MarimoServerManager] = []
    original_bytes = NOTEBOOK_PATH.read_bytes()

    async def _boot(
        source: str,
        *,
        name: str = "notebook.py",
        mode: str = "edit",
        state_dir: str | Path | None = None,
    ) -> MarimoServerManager:
        notebook = tmp_path / name
        notebook.write_text(source)
        manager = MarimoServerManager()
        await manager.start(str(notebook), mode=mode, state_dir=state_dir)
        managers.append(manager)
        return manager

    try:
        yield _boot
    except Exception:
        for manager in managers:
            manager.dump_logs()
        raise
    finally:
        for manager in managers:
            await manager.stop()

    # Only reached when setup + the test body succeeded (see docstring).
    assert NOTEBOOK_PATH.read_bytes() == original_bytes, (
        "Hermeticity violation: notebooks/test_marimo.py changed under the "
        "bare_server factory. Run `git status` and restore it."
    )


# ─── Failure diagnostics ──────────────────────────────────────────────────────────────────────────


def pytest_sessionfinish(session, exitstatus):
    """Dump shared server log tail when a live run fails.

    The kernel_manager fixture also dumps logs on setup errors; this covers
    test-level failures so a failing suite says *where* the server failed,
    not just "assertion failed".
    """
    if exitstatus != 0:
        manager = getattr(session, "_marimo_kernel_manager", None)
        if manager is not None:
            manager.dump_logs()
