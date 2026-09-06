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

    async def test_cell_id_parameter(self):
        """Passes cell_id to center graph."""
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
                cell_id="5",
                server_url="http://127.0.0.1:8090",
            )
            assert "cells" in result

    async def test_depth_parameter(self):
        """Passes depth parameter."""
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
                depth=2,
                server_url="http://127.0.0.1:8090",
            )
            assert "cells" in result

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
                '{"has_errors": false, "total_errors": 0, "total_cells_with_errors": 0, "cells": []}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_errors(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert result["has_errors"] is False
            assert result["total_errors"] == 0

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
                '{"has_errors": true, "total_errors": 1, "total_cells_with_errors": 1, "cells": [{"cell_id": "5", "errors": [{"type": "NameError", "message": "x"}]}]}'
            ]
            mock_instance.execute = AsyncMock(return_value=mock_execute_result)
            mock_client_cls.return_value = mock_instance

            result = await get_errors(
                session_id="abc123",
                server_url="http://127.0.0.1:8090",
            )
            assert result["has_errors"] is True
            assert result["total_errors"] == 1
            assert len(result["cells"]) == 1


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
    """Test the set_active_session tool handler."""

    async def test_binds_session(self):
        """Binds a session_id and returns confirmation."""
        from unittest.mock import AsyncMock, MagicMock

        from marimo_inspection.tools.session import set_active_session

        mock_ctx = MagicMock()
        mock_ctx.set_state = AsyncMock()
        mock_ctx.get_state = AsyncMock()
        mock_ctx.info = AsyncMock()

        result = await set_active_session(session_id="abc123", ctx=mock_ctx)

        assert result["status"] == "OK"
        assert result["active_session_id"] == "abc123"
        mock_ctx.set_state.assert_called_once()

    async def test_requires_session_id(self):
        """Returns error when session_id is empty."""
        from marimo_inspection.tools.session import set_active_session

        result = await set_active_session(session_id="")
        assert "error" in result
        assert "help" in result
