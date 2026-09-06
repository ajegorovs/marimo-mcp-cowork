"""Shared fixtures for marimo-inspect tests.

Provides:
- mcp_server: In-process FastMCP server for tool testing
- mock_marimo_client: Async mock of MarimoClient with canned responses
- mock_discover: Patched discover_servers for discovery tests
- fixtures/: Test data files for mocked HTTP responses
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures for FastMCP in-process testing
# ---------------------------------------------------------------------------
from fastmcp.client import Client


@pytest.fixture
def mcp_server():
    """Create the marimo-inspect FastMCP server (no network)."""
    from marimo_inspection.server import create_server

    return create_server()


@pytest.fixture
async def mcp_client(mcp_server):
    """In-process FastMCP client connected to the server."""
    async with Client(transport=mcp_server) as client:
        yield client


# ---------------------------------------------------------------------------
# Mock MarimoClient for tool-level tests
# ---------------------------------------------------------------------------


class MockExecuteResult:
    """Mimics MarimoClient.ExecuteResult."""

    def __init__(
        self,
        output: Any = None,
        status: str = "ok",
        stdout: list[str] | None = None,
        stderr: list[str] | None = None,
    ):
        self.output = output
        self.status = status
        self.stdout = stdout or ["{}"]
        self.stderr = stderr or []


class MockMarimoClient:
    """Mimics MarimoClient for tool handler testing."""

    def __init__(self, sessions: list | None = None, execute_output: Any = None):
        # Note: sessions are now dicts with session_id, file, basename
        # matching the SessionInfo dataclass
        self.sessions = sessions or [
            {
                "session_id": "test-session-1",
                "file": "/repo/notebooks/test.py",
                "basename": "test.py",
            }
        ]
        self._execute_output = execute_output
        self._call_count = 0

    async def list_sessions(self) -> list[Any]:
        return self.sessions

    async def resolve_session(self, session_id: str | None = None):
        if session_id:
            for s in self.sessions:
                if s.get("session_id") == session_id or s.get("id") == session_id:
                    return MagicMock(
                        session_id=s.get("session_id", s["id"]),
                        file=s.get("file"),
                        basename=s.get("basename"),
                    )
            raise ValueError(f"Session {session_id} not found")
        s = self.sessions[0]
        return MagicMock(
            session_id=s.get("session_id", s["id"]),
            file=s.get("file"),
            basename=s.get("basename"),
        )

    async def execute(self, session_id: str, code: str) -> MockExecuteResult:
        self._call_count += 1
        return MockExecuteResult(
            output=self._execute_output,
            status="ok",
            stdout=[json.dumps(self._execute_output)],
            stderr=[],
        )

    async def close(self):
        pass


def _make_mock_client_factory(output: Any, sessions: list | None = None) -> MagicMock:
    """Create a MagicMock that returns MockMarimoClient instances."""
    mock = MagicMock()

    async def factory(*args, **kwargs):
        return MockMarimoClient(
            sessions=sessions,
            execute_output=output,
        )

    mock.return_value = MagicMock(
        list_sessions=AsyncMock(return_value=sessions or []),
        resolve_session=AsyncMock(
            return_value=MagicMock(
                session_id="test-session-1",
                file="/repo/notebooks/test.py",
                basename="test.py",
            )
        ),
        execute=AsyncMock(
            return_value=MockExecuteResult(
                output=output,
                status="ok",
                stdout=[json.dumps(output)],
                stderr=[],
            )
        ),
    )
    return mock


@pytest.fixture
def mock_client_factory():
    """Factory fixture: returns a patched MarimoClient class."""

    def _make(output: Any, sessions: list | None = None):
        return _make_mock_client_factory(output, sessions)

    return _make


# ---------------------------------------------------------------------------
# Mock discovery
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_discover():
    """Patch marimo_inspection.discovery.discover_servers."""
    from marimo_inspection.discovery import DiscoveredServer

    servers = [
        DiscoveredServer(url="http://127.0.0.1:8090", pid=1234, healthy=True),
    ]

    with patch(
        "marimo_inspection.discovery.discover_servers",
        new_callable=lambda: AsyncMock(return_value=servers),
    ):
        yield servers


@pytest.fixture
def mock_no_servers():
    """Patch discover_servers to return no servers."""

    with patch(
        "marimo_inspection.discovery.discover_servers",
        new_callable=lambda: AsyncMock(return_value=[]),
    ):
        yield []


@pytest.fixture
def mock_http_check():
    """Patch the HTTP health check in discovery."""
    with patch(
        "marimo_inspection.discovery.httpx.AsyncClient",
        new_callable=AsyncMock,
    ) as mock_client_cls:
        instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        instance.get = AsyncMock(return_value=mock_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = instance
        yield mock_client_cls


# ---------------------------------------------------------------------------
# Mock MarimoClient at module level (for tool tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_marimo_client():
    """Patch MarimoClient in the tools modules."""
    with patch("marimo_inspection.tools.cells.MarimoClient") as mock:
        mock_instance = MagicMock(
            resolve_session=AsyncMock(
                return_value=MagicMock(
                    session_id="test-session-1",
                    file="/repo/notebooks/test.py",
                    basename="test.py",
                )
            ),
            execute=AsyncMock(
                return_value=MockExecuteResult(
                    output={"cells": [], "total_cells": 0},
                    status="ok",
                    stdout=["{}"],
                    stderr=[],
                )
            ),
        )
        mock.return_value = mock_instance

        # Also patch in other modules
        with (
            patch("marimo_inspection.tools.variables.MarimoClient") as mock_v,
            patch("marimo_inspection.tools.dependency.MarimoClient") as mock_d,
            patch("marimo_inspection.tools.errors.MarimoClient") as mock_e,
            patch("marimo_inspection.tools.lint.MarimoClient") as mock_l,
        ):
            mock_v.return_value = mock_instance
            mock_d.return_value = mock_instance
            mock_e.return_value = mock_instance
            mock_l.return_value = mock_instance
            yield mock


# ---------------------------------------------------------------------------
# Helper: load test fixtures from disk
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture from the fixtures directory."""
    path = FIXTURES_DIR / name
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Parametrized session data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_data():
    """Sample session data from marimo /api/sessions.

    The actual marimo API returns sessions as top-level keys:
    {session_id: {path, filename, ...}, ...}
    """
    return {
        "abc123": {
            "path": "/repo/notebooks/test.py",
            "filename": "test.py",
        },
        "def456": {
            "path": "/repo/notebooks/analysis.py",
            "filename": "analysis.py",
        },
    }


@pytest.fixture
def cell_map_response():
    """Sample cell map response."""
    return {
        "cells": [
            {
                "cell_id": "0",
                "name": "imports",
                "preview": "import marimo as mo\nimport numpy",
                "line_count": 15,
                "runtime_state": "idle",
                "has_output": False,
            },
            {
                "cell_id": "1",
                "name": "data loading",
                "preview": "from PIL import Image\nimg = Image.open",
                "line_count": 8,
                "runtime_state": "idle",
                "has_output": False,
            },
        ],
        "total_cells": 2,
    }


@pytest.fixture
def cell_data_response():
    """Sample cell data response."""
    return {
        "data": [
            {
                "cell_id": "0",
                "code": "import marimo as mo\nimport numpy as np",
                "runtime_state": "idle",
                "variables": {"np": "module", "mo": "module"},
            },
        ],
    }


@pytest.fixture
def variables_response():
    """Sample variables response."""
    return {
        "variables": {
            "name": "image_data",
            "value": "array with shape (1920, 1080, 3)",
            "datatype": "numpy.ndarray",
            "shape": [1920, 1080, 3],
            "dtype": "uint8",
        },
        "tables": {
            "stats": {
                "source": "DataFrame",
                "num_rows": 100,
                "num_columns": 5,
                "columns": ["id", "name", "score", "group", "tags"],
            },
        },
    }


@pytest.fixture
def dependency_response():
    """Sample dependency graph response."""
    return {
        "cells": [
            {
                "cell_id": "0",
                "cell_name": "imports",
                "defs": [{"name": "np", "kind": "variable"}],
                "refs": [],
                "parent_cell_ids": [],
                "child_cell_ids": ["1"],
            },
            {
                "cell_id": "1",
                "cell_name": "data loading",
                "defs": [{"name": "df", "kind": "variable"}],
                "refs": ["np"],
                "parent_cell_ids": ["0"],
                "child_cell_ids": [],
            },
        ],
        "variable_owners": {
            "np": ["0"],
            "df": ["1"],
        },
        "multiply_defined": [],
        "cycles": [],
    }


@pytest.fixture
def errors_response():
    """Sample errors response."""
    return {
        "has_errors": True,
        "total_errors": 1,
        "total_cells_with_errors": 1,
        "cells": [
            {
                "cell_id": "2",
                "errors": [
                    {
                        "type": "NameError",
                        "message": "name 'undefined_var' is not defined",
                        "traceback": ["Cell 2", "  undefined_var"],
                    }
                ],
                "stderr": ["NameError: name 'undefined_var' is not defined\n"],
            }
        ],
    }


@pytest.fixture
def lint_response():
    """Sample lint response."""
    return {
        "summary": {
            "total_issues": 2,
            "breaking_issues": 0,
            "runtime_issues": 1,
            "formatting_issues": 1,
        },
        "diagnostics": [
            {
                "rule": "unused-import",
                "severity": "RUNTIME",
                "message": "unused import 'os'",
                "cell_id": "0",
                "line": 5,
                "column": 1,
            },
            {
                "rule": "unused-variable",
                "severity": "FORMATTING",
                "message": "unused variable 'unused'",
                "cell_id": "1",
                "line": 3,
                "column": 1,
            },
        ],
    }
