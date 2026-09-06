"""HTTP client for marimo's API.

Handles communication with a running marimo server via its HTTP API:
- GET /api/sessions (list active sessions)
- POST /api/kernel/execute (scratchpad execution)
- SSE stream parsing (execution output)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Self

import httpx2 as httpx

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about an active marimo session."""

    session_id: str
    file: str | None = None
    basename: str | None = None
    running_notebooks: int = 0


@dataclass
class ExecuteResult:
    """Result from scratchpad execution."""

    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    output: Any = None
    execution_count: int | None = None
    status: str = ""  # "ok" | "error"


class MarimoClient:
    """HTTP client for marimo's API."""

    def __init__(self, server_url: str) -> None:
        """Create a marimo client.

        Args:
            server_url: Base URL of the marimo server (without trailing slash).
        """
        self._url = server_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def list_sessions(self) -> list[SessionInfo]:
        """List active sessions on the server."""
        client = await self._get_client()
        response = await client.get(f"{self._url}/api/sessions")
        response.raise_for_status()
        data = response.json()

        sessions: list[SessionInfo] = []
        # marimo /api/sessions returns {session_id: {filename, path, ...}, ...}
        for session_id, session_info in data.items():
            file_path = session_info.get("path", session_info.get("filename"))
            basename = None
            if file_path:
                from pathlib import Path

                basename = Path(file_path).name

            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    file=file_path,
                    basename=basename,
                )
            )
        return sessions

    async def resolve_session(
        self, file_path: str | None = None, session_id: str | None = None
    ) -> SessionInfo:
        """Resolve a session by file path or session ID.

        Args:
            file_path: Absolute path to the notebook file.
            session_id: Explicit session ID.

        Returns:
            SessionInfo for the matched session.

        Raises:
            ValueError: If no matching session is found.
        """
        sessions = await self.list_sessions()

        if session_id:
            for s in sessions:
                if s.session_id == session_id:
                    return s
            raise ValueError(f"Session {session_id} not found")

        if file_path:
            for s in sessions:
                if s.file == file_path:
                    return s
            # Try basename match
            from pathlib import Path

            basename = Path(file_path).name
            for s in sessions:
                if s.basename == basename:
                    return s
            raise ValueError(
                f"No session found for file {file_path}. "
                f"Sessions: {[s.file for s in sessions]}"
            )

        if len(sessions) == 1:
            return sessions[0]

        if not sessions:
            raise ValueError("No active sessions on server")

        raise ValueError(
            f"Multiple sessions found; specify file_path or session_id. "
            f"Sessions: {[(s.session_id, s.file) for s in sessions]}"
        )

    async def execute(
        self,
        session_id: str,
        code: str,
        timeout: float = 30.0,
    ) -> ExecuteResult:
        """Execute code in the marimo scratchpad.

        The scratchpad namespace is a shallow copy of kernel globals.
        New top-level bindings created by the code are discarded after execution.

        Args:
            session_id: The session ID to execute in.
            code: Python code to execute.
            timeout: Timeout in seconds for the execution.

        Returns:
            ExecuteResult with stdout, stderr, and status.
        """
        client = await self._get_client()

        body = {
            "code": code,
            "id": "scratch",
            "session_id": session_id,
        }

        headers = {"Marimo-Session-Id": session_id}

        # Use SSE streaming for the response
        async with client.stream(
            "POST",
            f"{self._url}/api/kernel/execute",
            json=body,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                text = await response.aread()
                raise RuntimeError(f"Execution failed: {response.status_code} {text}")

            result = ExecuteResult()
            current_event = ""
            async for line in response.aiter_lines():
                line = line.rstrip("\r\n")
                if not line:
                    continue

                # Parse SSE format: "event: <name>" and "data: <json>"
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("event:"):
                    current_event = line[6:]
                elif line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Marimo's execute endpoint uses event names, not type field
                    if current_event == "stdout":
                        result.stdout.append(data.get("data", ""))
                    elif current_event == "stderr":
                        result.stderr.append(data.get("data", ""))
                    elif current_event == "done":
                        result.status = "ok" if data.get("success") else "error"
                        result.output = data.get("output")
                    elif current_event == "output":
                        result.output = data.get("output")
                elif line.startswith("data:"):
                    data_str = line[5:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if current_event == "stdout":
                        result.stdout.append(data.get("data", ""))
                    elif current_event == "stderr":
                        result.stderr.append(data.get("data", ""))
                    elif current_event == "done":
                        result.status = "ok" if data.get("success") else "error"
                        result.output = data.get("output")
                    elif current_event == "output":
                        result.output = data.get("output")

            return result

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
