"""Tests for MCP tool handlers.

Mocks the MarimoClient to test tool handler logic in isolation.
Verifies:
- Input validation (required parameters)
- Output structure (next_steps, error handling)
- Correct client calls
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# -------------------------------------------------------------------
# list_active_notebooks tool tests
# -------------------------------------------------------------------


class TestListActiveNotebooks:
    """Test the list_active_notebooks tool handler."""

    async def test_returns_empty_when_no_servers(self, mock_no_servers):
        """Returns empty result when no servers discovered."""
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        result = await list_active_notebooks()
        assert "summary" in result
        assert "notebooks" in result
        assert result["summary"]["total_notebooks"] == 0
        assert result["notebooks"] == []
        assert "next_steps" in result
        # Honest counting: zero sessions, and an unavailable (not zero)
        # attached-client count. `active_connections` is a deprecated alias.
        assert result["summary"]["session_count"] == 0
        assert result["summary"]["result_row_count"] == 0
        assert result["summary"]["attached_client_count"] is None
        assert result["summary"]["active_connections"] == 0

    async def test_returns_notebooks_when_servers_exist(self, mock_discover):
        """Returns notebooks when servers are discovered."""
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        # Mock MarimoClient
        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.list_sessions = AsyncMock(return_value=[mock_session])
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks()

            assert result["summary"]["total_notebooks"] == 1
            assert len(result["notebooks"]) == 1
            assert result["notebooks"][0]["session_id"] == "abc123"
            assert result["notebooks"][0]["provenance"] == "unknown"
            assert result["notebooks"][0]["owner"] == "unknown"

    async def test_summary_counts_sessions_honestly(self, mock_discover):
        """The summary counts sessions, never claims attached clients.

        `session_count` (and `total_notebooks`) count the sessions
        `GET /api/sessions` reports; `result_row_count` counts the rows;
        `attached_client_count` is `None` because marimo publishes no
        per-session client count (and `/api/status/connections.active` counts
        sessions with an open main consumer, not clients); `active_connections`
        survives only as a deprecated alias for `session_count`.
        """
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.list_sessions = AsyncMock(
                return_value=[
                    MagicMock(session_id="s1", file="/a.py", basename="a.py"),
                    MagicMock(session_id="s2", file="/b.py", basename="b.py"),
                ]
            )
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks()

        summary = result["summary"]
        assert summary["session_count"] == 2
        assert summary["total_notebooks"] == 2
        assert summary["result_row_count"] == 2
        assert summary["attached_client_count"] is None
        # The alias equals the session count, not a client count.
        assert summary["active_connections"] == summary["session_count"]

    async def test_every_notebook_row_states_unknown_provenance_and_owner(
        self, mock_discover
    ):
        """Provenance and owner are unknown, not inferred from the binding.

        marimo exposes only a session's filename/path, so the tool must say
        "unknown" rather than let a caller read the auto-bind as ownership.
        """
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.list_sessions = AsyncMock(
                return_value=[
                    MagicMock(session_id="abc123", file="/t.py", basename="t.py")
                ]
            )
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks()

        row = result["notebooks"][0]
        assert row["provenance"] == "unknown"
        assert row["owner"] == "unknown"

    async def test_connection_failed_sentinel_is_not_a_session(self, mock_no_servers):
        """A failed server adds a bare sentinel row, never a fake session.

        The sentinel's keys are exactly ``name``/``path``/``session_id``/
        ``server_url``/``error``: it carries no ``provenance``/``owner`` (those
        describe a session), it is excluded from ``session_count`` and
        ``total_notebooks``, and it is visible only through ``result_row_count``.
        """
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.list_sessions = AsyncMock(
                side_effect=ConnectionError("refused")
            )
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks(server_url="http://127.0.0.1:9999")

        row = result["notebooks"][0]
        assert set(row) == {"name", "path", "session_id", "server_url", "error"}
        assert row["session_id"] == "error"
        assert "provenance" not in row
        assert "owner" not in row

        summary = result["summary"]
        assert summary["session_count"] == 0
        assert summary["total_notebooks"] == 0
        assert summary["result_row_count"] == 1
        assert summary["active_connections"] == 0
        assert summary["attached_client_count"] is None

    async def test_total_notebooks_excludes_the_failure_sentinel(self):
        """`total_notebooks` counts real sessions; `result_row_count` counts rows.

        One server answers and one refuses: the summary reports one notebook
        but two rows — the deliberate failure-path correction.
        """
        from marimo_inspection.discovery import DiscoveredServer
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        servers = [
            DiscoveredServer(url="http://127.0.0.1:8090", pid=1, healthy=True),
            DiscoveredServer(url="http://127.0.0.1:8091", pid=2, healthy=True),
        ]

        def _client(url: str):
            inst = MagicMock()
            if url.endswith("8091"):
                inst.list_sessions = AsyncMock(
                    side_effect=ConnectionError("refused")
                )
            else:
                inst.list_sessions = AsyncMock(
                    return_value=[
                        MagicMock(session_id="s1", file="/a.py", basename="a.py")
                    ]
                )
            return inst

        with (
            patch(
                "marimo_inspection.discovery.discover_servers",
                new_callable=lambda: AsyncMock(return_value=servers),
            ),
            patch(
                "marimo_inspection.tools.notebooks.MarimoClient",
                side_effect=_client,
            ),
        ):
            result = await list_active_notebooks()

        summary = result["summary"]
        assert summary["total_notebooks"] == 1
        assert summary["session_count"] == 1
        assert summary["result_row_count"] == 2
        assert summary["active_connections"] == 1
        assert len(result["notebooks"]) == 2

    async def test_with_explicit_server_url(self, mock_no_servers):
        """Accepts explicit server_url parameter."""
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="xyz",
                file="/explicit.py",
                basename="explicit.py",
            )
            mock_instance.list_sessions = AsyncMock(return_value=[mock_session])
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks(server_url="http://127.0.0.1:9000")
            assert result["summary"]["total_notebooks"] == 1

    async def test_error_on_failed_server(self):
        """Captures server connection errors."""
        from marimo_inspection.tools.notebooks import (
            list_active_notebooks,
        )

        with patch("marimo_inspection.tools.notebooks.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.list_sessions = AsyncMock(
                side_effect=ConnectionError("refused")
            )
            mock_client_cls.return_value = mock_instance

            result = await list_active_notebooks(server_url="http://127.0.0.1:9999")
            # Should still return structure even on error
            assert "summary" in result
            assert "notebooks" in result


# -------------------------------------------------------------------
# get_cell_map tool tests
# -------------------------------------------------------------------


class TestGetCellMap:
    """Test the get_cell_map tool handler."""

    async def test_requires_session_id(self):
        """session_id is required."""
        from marimo_inspection.tools.cells import get_cell_map

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.resolve_session = AsyncMock(
                side_effect=ValueError("server_url required")
            )
            mock_client_cls.return_value = mock_instance

            # Should raise because server_url is missing
            with pytest.raises(ValueError):
                await get_cell_map(session_id="abc123")

    async def test_calls_client_with_session(self):
        """Calls client.resolve_session with correct ID."""
        from marimo_inspection.tools.cells import get_cell_map

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [{"cell_id": "0", "name": "imports"}], "total_cells": 1}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_map(
                session_id="abc123", server_url="http://127.0.0.1:8090"
            )
            assert "cells" in result
            assert result["cells"] == [{"cell_id": "0", "name": "imports"}]
            assert result["total_cells"] == 1

    async def test_preview_lines_default(self):
        """Default preview_lines is 3."""
        from marimo_inspection.tools.cells import get_cell_map

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = ['{"cells": [], "total_cells": 0}']
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_map(
                session_id="abc123",
                preview_lines=5,
                server_url="http://127.0.0.1:8090",
            )
            assert "preview_lines" in result or result["cells"] == []

    async def test_execution_error(self):
        """Returns error dict on execution failure."""
        from marimo_inspection.tools.cells import get_cell_map

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "error"
            mock_execute_result.stderr = ["NameError"]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_map(
                session_id="abc123", server_url="http://127.0.0.1:8090"
            )
            assert "error" in result
            assert result["stderr"] == ["NameError"]


# -------------------------------------------------------------------
# get_cell_data tool tests
# -------------------------------------------------------------------


class TestGetCellData:
    """Test the get_cell_data tool handler."""

    async def test_requires_session_id(self):
        """session_id is required."""
        from marimo_inspection.tools.cells import get_cell_data

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.resolve_session = AsyncMock(
                side_effect=ValueError("server_url required")
            )
            mock_client_cls.return_value = mock_instance

            with pytest.raises(ValueError):
                await get_cell_data(session_id="abc123")

    async def test_returns_cell_data(self):
        """Returns cell runtime data."""
        from marimo_inspection.tools.cells import get_cell_data

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"data": [{"cell_id": "0", "code": "import numpy"}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_data(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "data" in result
            assert len(result["data"]) == 1
            assert result["data"][0]["cell_id"] == "0"

    async def test_cell_ids_parameter(self):
        """Passes cell_ids to template."""
        from marimo_inspection.tools.cells import get_cell_data

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = ['{"data": [{"cell_id": "5"}]}']
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_data(
                session_id="abc123",
                cell_ids=["5"],
                server_url="http://127.0.0.1:8090",
            )
            assert "data" in result


class TestGetCellDataChangeTracking:
    """get_cell_data records selective reads into the change tracker.

    The read must refresh the agent's baseline for exactly the returned
    cells (merge-only), and must leave the tracker untouched on any failure.
    """

    SID = "abc123"

    def _code_hash(self, code: str) -> str:
        import hashlib

        return hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]

    def _patch_client(self, stdout_lines: list[str], status: str = "ok"):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_instance = MagicMock()
        mock_session = MagicMock(
            session_id=self.SID, file="/test.py", basename="test.py"
        )
        mock_instance.resolve_session = AsyncMock(return_value=mock_session)
        mock_execute_result = MagicMock()
        mock_execute_result.status = status
        mock_execute_result.stdout = stdout_lines
        mock_execute_result.stderr = ["boom"]
        mock_instance.execute = AsyncMock(return_value=mock_execute_result)
        return patch(
            "marimo_inspection.tools.cells.MarimoClient", return_value=mock_instance
        )

    async def test_records_hash_of_exact_returned_source_and_state(self):
        """Fingerprints the exact source string and runtime state returned."""
        import json

        from marimo_inspection.tools.cells import get_cell_data
        from marimo_inspection.tools.change_tracking import get_tracker

        code = "import numpy as np\nx = np.arange(5)\n"
        tracker = get_tracker()
        tracker.clear_session(self.SID)
        try:
            with self._patch_client(
                [
                    json.dumps(
                        {
                            "data": [
                                {
                                    "cell_id": "0",
                                    "code": code,
                                    "runtime_state": "idle",
                                    "variables": None,
                                }
                            ]
                        }
                    )
                ]
            ):
                result = await get_cell_data(
                    session_id=self.SID, server_url="http://127.0.0.1:8090"
                )
            assert "error" not in result
            fp = tracker.get_cell_fingerprint(self.SID, "0")
            assert fp is not None
            assert fp.code_hash == self._code_hash(code)
            assert fp.state == "idle"
        finally:
            tracker.clear_session(self.SID)

    async def test_selective_read_preserves_other_cell_fingerprint(self):
        """get_cell_data(cell_ids=[...]) must not erase unread cells."""
        import json

        from marimo_inspection.tools.cells import get_cell_data
        from marimo_inspection.tools.change_tracking import (
            CellFingerprint,
            get_tracker,
        )

        code_a = "a = 1"
        tracker = get_tracker()
        tracker.clear_session(self.SID)
        try:
            tracker.record_cells(
                self.SID, {"B": CellFingerprint(code_hash="bhash", state="idle")}
            )
            with self._patch_client(
                [
                    json.dumps(
                        {
                            "data": [
                                {
                                    "cell_id": "A",
                                    "code": code_a,
                                    "runtime_state": "stale",
                                    "variables": None,
                                }
                            ]
                        }
                    )
                ]
            ):
                result = await get_cell_data(
                    session_id=self.SID,
                    cell_ids=["A"],
                    server_url="http://127.0.0.1:8090",
                )
            assert "error" not in result
            # Unread B survives the selective read.
            assert tracker.get_cell_fingerprint(self.SID, "B").code_hash == "bhash"
            # Read A is recorded with the hash of the exact returned source.
            fp = tracker.get_cell_fingerprint(self.SID, "A")
            assert fp is not None
            assert fp.code_hash == self._code_hash(code_a)
            assert fp.state == "stale"
        finally:
            tracker.clear_session(self.SID)

    async def test_get_cell_data_creates_snapshot_when_none_exists(self):
        """A first get_cell_data establishes the session baseline."""
        from marimo_inspection.tools.cells import get_cell_data
        from marimo_inspection.tools.change_tracking import get_tracker

        tracker = get_tracker()
        tracker.clear_session(self.SID)
        try:
            with self._patch_client(['{"data": [{"cell_id": "0", "code": "x = 1"}]}']):
                result = await get_cell_data(
                    session_id=self.SID, server_url="http://127.0.0.1:8090"
                )
            assert "error" not in result
            assert tracker.has_snapshot(self.SID)
        finally:
            tracker.clear_session(self.SID)

    async def test_json_parse_failure_leaves_tracker_untouched(self):
        """A failed JSON parse must not record anything."""
        from marimo_inspection.tools.cells import get_cell_data
        from marimo_inspection.tools.change_tracking import (
            CellFingerprint,
            get_tracker,
        )

        tracker = get_tracker()
        tracker.clear_session(self.SID)
        try:
            tracker.record_cells(self.SID, {"B": CellFingerprint(code_hash="bhash")})
            with self._patch_client(["not valid json"]):
                result = await get_cell_data(
                    session_id=self.SID, server_url="http://127.0.0.1:8090"
                )
            assert "error" in result
            assert tracker.get_cell_fingerprint(self.SID, "B").code_hash == "bhash"
            assert tracker.get_cell_fingerprint(self.SID, "0") is None
        finally:
            tracker.clear_session(self.SID)

    async def test_execution_error_leaves_tracker_untouched(self):
        """An execution failure must not record anything."""
        from marimo_inspection.tools.cells import get_cell_data
        from marimo_inspection.tools.change_tracking import (
            CellFingerprint,
            get_tracker,
        )

        tracker = get_tracker()
        tracker.clear_session(self.SID)
        try:
            tracker.record_cells(self.SID, {"B": CellFingerprint(code_hash="bhash")})
            with self._patch_client([], status="error"):
                result = await get_cell_data(
                    session_id=self.SID, server_url="http://127.0.0.1:8090"
                )
            assert "error" in result
            assert tracker.get_cell_fingerprint(self.SID, "B").code_hash == "bhash"
            assert tracker.get_cell_fingerprint(self.SID, "0") is None
        finally:
            tracker.clear_session(self.SID)


# -------------------------------------------------------------------
# get_cell_outputs tool tests
# -------------------------------------------------------------------


class TestGetCellOutputs:
    """Test the get_cell_outputs tool handler."""

    async def test_returns_cell_outputs(self):
        """Returns cell outputs."""
        from marimo_inspection.tools.cells import get_cell_outputs

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [{"cell_id": "0", "visual_output": "image"}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_outputs(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "cells" in result
            assert len(result["cells"]) == 1

    async def test_description_warns_that_stale_output_may_be_restored(self):
        """The public description names the stale signal and the remedy (T21).

        A caller that never learns an output can be a RESTORED rendering from
        an earlier run reads it as current — the reporting gap T21 records.
        """
        import inspect as _inspect

        from marimo_inspection.tools.cells import get_cell_outputs

        description = " ".join(
            (_inspect.getdoc(get_cell_outputs) or "").lower().split()
        )
        assert "cells[]" in description
        assert "runtime_state" in description
        assert "output_stale" in description
        # The remedy must be stated: run the cell before trusting it.
        assert "stale" in description
        assert "run the cell" in description

    def test_resource_teaches_the_stale_output_rule(self):
        """The packaged co-work loop carries the same stale-output rule.

        The MCP resources are the runtime authority for the co-work loop; a
        caller that only reads the resource must still learn that a reported
        output can be restored and stale.
        """
        from marimo_inspection.resources import load_resource_text

        text = " ".join(load_resource_text("co-work-loop.md").lower().split())
        assert "runtime_state" in text
        assert "output_stale" in text
        assert "run the cell before trusting it as current" in text

    async def test_stale_cells_are_flagged_in_next_steps(self):
        """A stale row survives untouched and is called out in next_steps."""
        import json

        from marimo_inspection.tools.cells import get_cell_outputs

        stale_row = {
            "cell_id": "Xref",
            "visual_output": {"channel": "output", "data": "restored"},
            "visual_mimetype": "text/html",
            "stdout": [],
            "stderr": [],
            "console_events": [],
            "runtime_state": "stale",
            "output_stale": True,
        }
        current_row = {
            "cell_id": "BYtC",
            "visual_output": None,
            "visual_mimetype": None,
            "stdout": [],
            "stderr": [],
            "console_events": [],
            "runtime_state": "idle",
            "output_stale": False,
        }

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.resolve_session = AsyncMock(
                return_value=MagicMock(session_id="abc123")
            )
            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                json.dumps(
                    {
                        "cells": [stale_row, current_row],
                        "missing_cell_ids": ["gone"],
                    }
                )
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_outputs(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )

        # Row-level signals pass through unmodified...
        by_id = {row["cell_id"]: row for row in result["cells"]}
        assert by_id["Xref"]["output_stale"] is True
        assert by_id["Xref"]["runtime_state"] == "stale"
        assert by_id["Xref"]["visual_output"]["data"] == "restored"
        assert by_id["BYtC"]["output_stale"] is False
        # ...and the caller is told to run the stale cell before trusting it.
        stale_hint = next(
            (step for step in result["next_steps"] if "Xref" in step), None
        )
        assert stale_hint is not None, result["next_steps"]
        assert "run the cell" in stale_hint.lower()
        assert "restored" in stale_hint.lower()
        # The current cell is never called out as stale.
        assert all("BYtC" not in step for step in result["next_steps"])
        # The existing missing-id contract is untouched.
        assert result["missing_cell_ids"] == ["gone"]
        assert result["next_steps"][0].startswith("These requested cell ids")


# -------------------------------------------------------------------
# get_variables tool tests
# -------------------------------------------------------------------


class TestGetVariables:
    """Test the get_variables tool handler."""

    async def test_returns_variables_and_tables(self):
        """Returns both variables and tables."""
        from marimo_inspection.tools.variables import get_variables

        with patch("marimo_inspection.tools.variables.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"variables": {"x": 1}, "tables": {"df": {"rows": 10}}}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_variables(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "variables" in result
            assert "tables" in result
            assert result["variables"] == {"x": 1}
            assert result["tables"] == {"df": {"rows": 10}}

    async def test_variable_names_parameter(self):
        """Passes variable_names to template."""
        from marimo_inspection.tools.variables import get_variables

        with patch("marimo_inspection.tools.variables.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = ['{"variables": {"x": 1}, "tables": {}}']
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_variables(
                session_id="abc123",
                variable_names=["x"],
                server_url="http://127.0.0.1:8090",
            )
            assert "variables" in result


# -------------------------------------------------------------------
# get_dependency_graph tool tests
# -------------------------------------------------------------------


class TestGetDependencyGraph:
    """Test the get_dependency_graph tool handler."""

    async def test_returns_dependency_graph(self):
        """Returns full dependency graph."""
        from marimo_inspection.tools.dependency import (
            get_dependency_graph,
        )

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [], "variable_owners": {}, "multiply_defined": [], "cycles": []}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_dependency_graph(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "cells" in result
            assert "variable_owners" in result
            assert "multiply_defined" in result
            assert "cycles" in result

    async def test_cell_id_is_refused_not_ignored(self):
        """cell_id is refused loudly, and nothing is read from the notebook.

        The old behaviour accepted cell_id and returned the full graph anyway —
        the argument was documented and dead at the same time.
        """
        from marimo_inspection.tools.dependency import get_dependency_graph

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            result = await get_dependency_graph(
                session_id="abc123",
                cell_id="5",
                server_url="http://127.0.0.1:8090",
            )

        assert result["status"] == "error"
        assert result["reason"] == "unsupported_argument"
        assert result["unsupported_arguments"] == ["cell_id"]
        assert "cells" not in result
        mock_client_cls.assert_not_called()

    async def test_depth_is_refused_not_ignored(self):
        """A non-zero depth is refused; depth=0 (the default) stays valid."""
        from marimo_inspection.tools.dependency import get_dependency_graph

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            refused = await get_dependency_graph(
                session_id="abc123",
                depth=2,
                server_url="http://127.0.0.1:8090",
            )

            assert refused["reason"] == "unsupported_argument"
            assert refused["unsupported_arguments"] == ["depth"]
            mock_client_cls.assert_not_called()

            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)
            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [], "variable_owners": {}, "multiply_defined": [], "cycles": []}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            ok = await get_dependency_graph(
                session_id="abc123",
                depth=0,
                server_url="http://127.0.0.1:8090",
            )

        assert "cells" in ok

    async def test_both_arguments_reported_together(self):
        """Both ignored arguments are named in one refusal."""
        from marimo_inspection.tools.dependency import get_dependency_graph

        result = await get_dependency_graph(
            session_id="abc123",
            cell_id="5",
            depth=1,
            server_url="http://127.0.0.1:8090",
        )

        assert result["unsupported_arguments"] == ["cell_id", "depth"]

    async def test_cell_names_pass_through(self):
        """cell_name from the template is carried into the payload (H3)."""
        from marimo_inspection.tools.dependency import get_dependency_graph

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)
            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                (
                    '{"cells": [{"cell_id": "0", "cell_name": "load_data", "defs": [],'
                    ' "refs": [], "parent_cell_ids": [], "child_cell_ids": []}],'
                    ' "variable_owners": {}, "multiply_defined": [], "cycles": []}'
                )
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_dependency_graph(
                session_id="abc123", server_url="http://127.0.0.1:8090"
            )

        assert result["cells"][0]["cell_name"] == "load_data"

    async def test_multiply_defined_next_steps(self):
        """Includes next_steps for multiply-defined variables."""
        from marimo_inspection.tools.dependency import (
            get_dependency_graph,
        )

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [], "variable_owners": {}, "multiply_defined": ["x"], "cycles": []}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_dependency_graph(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "next_steps" in result
            assert any("multiply-defined" in step for step in result["next_steps"])

    async def test_cycles_next_steps(self):
        """Includes next_steps for cycles."""
        from marimo_inspection.tools.dependency import (
            get_dependency_graph,
        )

        with patch(
            "marimo_inspection.tools.dependency.MarimoClient"
        ) as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"cells": [], "variable_owners": {}, "multiply_defined": [], "cycles": [{"cell_ids": ["0", "1"], "edges": [["0", "1"], ["1", "0"]]}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_dependency_graph(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "next_steps" in result
            assert any("cycle" in step.lower() for step in result["next_steps"])


# -------------------------------------------------------------------
# get_errors tool tests
# -------------------------------------------------------------------


class TestGetErrors:
    """Test the get_errors tool handler."""

    async def test_returns_no_errors(self):
        """Returns clean result when no errors."""
        from marimo_inspection.tools.errors import get_errors

        with patch("marimo_inspection.tools.errors.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"has_errors": false, "total_errors": 0, "total_structured_errors": 0, "total_cells_with_errors": 0, "has_console_exception": false, "total_console_exception_cells": 0, "cells": []}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_errors(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert result["has_errors"] is False
            assert result["total_errors"] == 0
            assert result["total_structured_errors"] == 0
            assert result["has_console_exception"] is False
            assert result["total_console_exception_cells"] == 0
            assert any(step == "No errors detected" for step in result["next_steps"])

    async def test_returns_errors(self):
        """Returns error details when errors exist."""
        from marimo_inspection.tools.errors import get_errors

        with patch("marimo_inspection.tools.errors.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"has_errors": true, "total_errors": 1, "total_structured_errors": 1, "total_cells_with_errors": 1, "has_console_exception": false, "total_console_exception_cells": 0, "cells": [{"cell_id": "5", "structured_errors": [{"kind": "runtime", "cell": "5", "msg": "name \'x\' is not defined", "exception": "NameError(\'x\')"}], "console_stderr": [], "has_console_exception": false}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_errors(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert result["has_errors"] is True
            assert result["total_errors"] == 1
            assert result["total_structured_errors"] == 1
            assert len(result["cells"]) == 1
            assert result["cells"][0]["structured_errors"][0]["kind"] == "runtime"

    async def test_console_exception_visible_without_structured(self):
        """Console-only exception evidence is reported and steered to."""
        from marimo_inspection.tools.errors import get_errors

        with patch("marimo_inspection.tools.errors.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = [
                '{"has_errors": false, "total_errors": 0, "total_structured_errors": 0, "total_cells_with_errors": 0, "has_console_exception": true, "total_console_exception_cells": 1, "cells": [{"cell_id": "9", "structured_errors": [], "console_stderr": [{"channel": "stderr", "data": "Traceback (most recent call last):\\nNameError: boom"}], "has_console_exception": true}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_errors(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert result["has_errors"] is False
            assert result["total_errors"] == 0
            assert result["total_structured_errors"] == 0
            assert result["has_console_exception"] is True
            assert result["total_console_exception_cells"] == 1
            assert len(result["cells"]) == 1
            assert result["cells"][0]["structured_errors"] == []
            assert result["cells"][0]["has_console_exception"] is True
            # next_steps must not claim a clean session.
            assert not any(
                step == "No errors detected" for step in result["next_steps"]
            )
            assert any(
                "exception" in step.lower() or "console" in step.lower()
                for step in result["next_steps"]
            )


# -------------------------------------------------------------------
# lint_notebook tool tests
# -------------------------------------------------------------------


class TestLintNotebook:
    """Test the lint_notebook tool handler (server-side file linting)."""

    def _make_client_mock(self, file="/test.py"):
        mock_instance = MagicMock()
        mock_session = MagicMock(
            session_id="abc123",
            file=file,
            basename="test.py",
        )
        mock_instance.resolve_session = AsyncMock(return_value=mock_session)
        mock_client_cls = patch("marimo_inspection.tools.lint.MarimoClient")
        patched_cls = mock_client_cls.start()
        patched_cls.return_value = mock_instance
        return mock_client_cls

    async def test_returns_clean_lint(self):
        """Returns clean result when no issues."""
        from marimo_inspection.tools.lint import lint_notebook

        clean = {
            "summary": {
                "total_issues": 0,
                "breaking_issues": 0,
                "runtime_issues": 0,
                "formatting_issues": 0,
                "wasm_issues": 0,
            },
            "diagnostics": [],
        }
        with (
            patch("marimo_inspection.tools.lint.Path.is_file") as mock_is_file,
            patch("marimo_inspection.tools.lint.Path.read_text") as mock_read,
            patch("marimo_inspection.tools.lint._lint_source") as mock_lint,
        ):
            mock_is_file.return_value = True
            mock_read.return_value = (
                "import marimo as mo\n@mo.cell\ndef _():\n    pass\n"
            )
            mock_lint.return_value = clean
            mock_client_cls = self._make_client_mock()
            try:
                result = await lint_notebook(
                    session_id="abc123",
                    server_url="http://127.0.0.1:8090",
                )
            finally:
                mock_client_cls.stop()

        assert "summary" in result
        assert result["summary"]["total_issues"] == 0
        assert "next_steps" in result

    async def test_returns_lint_issues(self):
        """Returns diagnostics when issues found."""
        from marimo_inspection.tools.lint import lint_notebook

        issues = {
            "summary": {
                "total_issues": 2,
                "breaking_issues": 1,
                "runtime_issues": 0,
                "formatting_issues": 1,
                "wasm_issues": 0,
            },
            "diagnostics": [
                {"rule": "unused-import", "severity": "breaking", "message": "unused"}
            ],
        }
        with (
            patch("marimo_inspection.tools.lint.Path.is_file") as mock_is_file,
            patch("marimo_inspection.tools.lint.Path.read_text") as mock_read,
            patch("marimo_inspection.tools.lint._lint_source") as mock_lint,
        ):
            mock_is_file.return_value = True
            mock_read.return_value = "import mo\n"
            mock_lint.return_value = issues
            mock_client_cls = self._make_client_mock()
            try:
                result = await lint_notebook(
                    session_id="abc123",
                    server_url="http://127.0.0.1:8090",
                )
            finally:
                mock_client_cls.stop()

        assert result["summary"]["breaking_issues"] == 1
        assert result["summary"]["formatting_issues"] == 1
        assert len(result["diagnostics"]) == 1

    async def test_next_steps_for_breaking_issues(self):
        """Includes next_steps for breaking issues."""
        from marimo_inspection.tools.lint import lint_notebook

        issues = {
            "summary": {
                "total_issues": 1,
                "breaking_issues": 1,
                "runtime_issues": 0,
                "formatting_issues": 0,
                "wasm_issues": 0,
            },
            "diagnostics": [
                {"rule": "test", "severity": "breaking", "message": "test"}
            ],
        }
        with (
            patch("marimo_inspection.tools.lint.Path.is_file") as mock_is_file,
            patch("marimo_inspection.tools.lint.Path.read_text") as mock_read,
            patch("marimo_inspection.tools.lint._lint_source") as mock_lint,
        ):
            mock_is_file.return_value = True
            mock_read.return_value = "import mo\n"
            mock_lint.return_value = issues
            mock_client_cls = self._make_client_mock()
            try:
                result = await lint_notebook(
                    session_id="abc123",
                    server_url="http://127.0.0.1:8090",
                )
            finally:
                mock_client_cls.stop()

        assert any("breaking" in step.lower() for step in result["next_steps"])


# -------------------------------------------------------------------
# Error handling tests
# -------------------------------------------------------------------


class TestToolErrorHandling:
    """Test error handling across tools."""

    async def test_cell_map_json_parse_error(self):
        """Returns error dict on JSON parse failure."""
        from marimo_inspection.tools.cells import get_cell_map

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = ["not valid json"]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_map(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "error" in result
            assert "raw_output" in result

    async def test_cell_data_json_parse_error(self):
        """Returns error dict on JSON parse failure."""
        from marimo_inspection.tools.cells import get_cell_data

        with patch("marimo_inspection.tools.cells.MarimoClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_session = MagicMock(
                session_id="abc123",
                file="/test.py",
                basename="test.py",
            )
            mock_instance.resolve_session = AsyncMock(return_value=mock_session)

            mock_execute_result = MagicMock()
            mock_execute_result.status = "ok"
            mock_execute_result.stdout = ["not valid json"]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_cell_data(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert "error" in result


# -------------------------------------------------------------------
# set_active_session tool tests
# -------------------------------------------------------------------


class TestSetActiveSession:
    """Test the set_active_session tool handler.

    T17 validation makes the handler query the target server's live sessions
    before it binds anything, so these cases stub that query (the real-socket
    validation regression lives in test_session_binding.py). Each case asserts
    on what was *written*, since "no state on refusal" is the contract.
    """

    async def test_binds_session(self):
        """Binds a session_id the server reports and returns confirmation."""
        from marimo_inspection.tools.session import (
            _SERVER_URL_KEY,
            _SESSION_KEY,
            set_active_session,
        )

        mock_ctx = MagicMock()
        mock_ctx.set_state = AsyncMock()
        mock_ctx.get_state = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch("marimo_inspection.tools.session.MarimoClient") as client_cls:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.list_sessions = AsyncMock(
                return_value=[
                    MagicMock(session_id="abc123", file="/t.py", basename="t.py")
                ]
            )
            client_cls.return_value = instance

            result = await set_active_session(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
                ctx=mock_ctx,
            )

        assert result["status"] == "OK"
        assert result["active_session_id"] == "abc123"
        assert result["active_server_url"] == "http://127.0.0.1:8090"
        assert result["validated"] is True
        written = {
            call.args[0]: call.args[1] for call in mock_ctx.set_state.call_args_list
        }
        assert written == {
            _SESSION_KEY: "abc123",
            _SERVER_URL_KEY: "http://127.0.0.1:8090",
        }
        instance.close.assert_awaited_once()

    async def test_refuses_a_session_id_the_server_does_not_report(self):
        """An unreported id is refused and no binding state is written."""
        from marimo_inspection.tools.session import set_active_session

        mock_ctx = MagicMock()
        mock_ctx.set_state = AsyncMock()
        mock_ctx.get_state = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch("marimo_inspection.tools.session.MarimoClient") as client_cls:
            instance = MagicMock()
            instance.close = AsyncMock()
            instance.list_sessions = AsyncMock(return_value=[])
            client_cls.return_value = instance

            result = await set_active_session(
                session_id="invented",
                server_url="http://127.0.0.1:8090",
                ctx=mock_ctx,
            )

        assert result["status"] == "error"
        assert result["reason"] == "session_not_found"
        assert result["bound"] is False
        mock_ctx.set_state.assert_not_called()

    async def test_requires_session_id(self):
        """Returns the structured invalid_session_id refusal when empty."""
        from marimo_inspection.tools.session import set_active_session

        result = await set_active_session(session_id="")
        assert result["status"] == "error"
        assert result["reason"] == "invalid_session_id"
        assert result["bound"] is False
        assert result["state_changed"] is False
        assert "error" in result
        assert "help" in result
