"""Tests for shared dataclasses (types.py).

Verifies dataclass structure and default values.
"""

from __future__ import annotations


class TestMarimoNotebookInfo:
    """Test MarimoNotebookInfo dataclass."""

    def test_create(self):
        """Create notebook info."""
        from marimo_inspection.types import MarimoNotebookInfo

        info = MarimoNotebookInfo(
            name="test.py",
            path="/home/user/test.py",
            session_id="abc123",
        )
        assert info.name == "test.py"
        assert info.path == "/home/user/test.py"
        assert info.session_id == "abc123"


class TestLightweightCellInfo:
    """Test LightweightCellInfo dataclass."""

    def test_create_minimal(self):
        """Create with required fields."""
        from marimo_inspection.types import LightweightCellInfo

        cell = LightweightCellInfo(
            cell_id="0",
            name="imports",
            preview="import numpy",
            line_count=5,
        )
        assert cell.cell_id == "0"
        assert cell.name == "imports"
        assert cell.line_count == 5
        assert cell.runtime_state is None
        assert cell.has_output is False

    def test_create_full(self):
        """Create with all fields."""
        from marimo_inspection.types import LightweightCellInfo

        cell = LightweightCellInfo(
            cell_id="5",
            name="plot",
            preview="plt.show()",
            line_count=3,
            runtime_state="idle",
            has_output=True,
            has_console_output=True,
            has_errors=False,
        )
        assert cell.has_output is True
        assert cell.has_console_output is True
        assert cell.has_errors is False


class TestCellRuntimeData:
    """Test CellRuntimeData dataclass."""

    def test_create(self):
        """Create cell runtime data."""
        from marimo_inspection.types import CellRuntimeData

        data = CellRuntimeData(cell_id="0")
        assert data.cell_id == "0"
        assert data.code is None
        assert data.errors is None
        assert data.execution_time_ms is None

    def test_with_code(self):
        """Create with code and variables."""
        from marimo_inspection.types import CellRuntimeData

        data = CellRuntimeData(
            cell_id="1",
            code="x = 1",
            runtime_state="idle",
            execution_time_ms=42.5,
            variables={"x": 1},
        )
        assert data.code == "x = 1"
        assert data.execution_time_ms == 42.5


class TestCellOutputData:
    """Test CellOutputData dataclass."""

    def test_create(self):
        """Create cell output data."""
        from marimo_inspection.types import CellOutputData

        output = CellOutputData(cell_id="0")
        assert output.cell_id == "0"
        assert output.visual_output is None
        assert output.stdout is None

    def test_with_visual(self):
        """Create with visual output."""
        from marimo_inspection.types import CellOutputData, CellVisualOutput

        output = CellOutputData(
            cell_id="1",
            visual_output=CellVisualOutput(
                visual_output="base64data",
                visual_mimetype="image/png",
            ),
            stdout=["hello"],
            stderr=["warning"],
        )
        assert output.stdout == ["hello"]
        assert output.stderr == ["warning"]


class TestVariableValue:
    """Test VariableValue dataclass."""

    def test_create(self):
        """Create variable value."""
        from marimo_inspection.types import VariableValue

        var = VariableValue(
            name="x",
            value="42",
            datatype="int",
        )
        assert var.name == "x"
        assert var.value == "42"


class TestDataTableMetadata:
    """Test DataTableMetadata dataclass."""

    def test_create(self):
        """Create table metadata."""
        from marimo_inspection.types import DataTableMetadata

        table = DataTableMetadata(
            source="DataFrame",
            num_rows=100,
            num_columns=5,
            columns=["id", "name", "score", "group", "tags"],
        )
        assert table.source == "DataFrame"
        assert table.num_rows == 100
        assert table.num_columns == 5


class TestVariableInfo:
    """Test VariableInfo dataclass."""

    def test_create(self):
        """Create variable info."""
        from marimo_inspection.types import VariableInfo

        var = VariableInfo(
            name="x",
            kind="variable",
            datatype="int",
        )
        assert var.name == "x"
        assert var.kind == "variable"


class TestCellDependencyInfo:
    """Test CellDependencyInfo dataclass."""

    def test_create(self):
        """Create dependency info."""
        from marimo_inspection.types import CellDependencyInfo, VariableInfo

        dep = CellDependencyInfo(
            cell_id="1",
            cell_name="data loading",
            defs=[VariableInfo(name="df", kind="variable")],
            refs=["pd"],
            parent_cell_ids=["0"],
            child_cell_ids=["2"],
        )
        assert dep.cell_id == "1"
        assert len(dep.defs) == 1
        assert dep.defs[0].name == "df"
        assert "pd" in dep.refs
        assert dep.parent_cell_ids == ["0"]
        assert dep.child_cell_ids == ["2"]


class TestCycleInfo:
    """Test CycleInfo dataclass."""

    def test_create(self):
        """Create cycle info."""
        from marimo_inspection.types import CycleInfo

        cycle = CycleInfo(
            cell_ids=["0", "1"],
            edges=[["0", "1"], ["1", "0"]],
        )
        assert cycle.cell_ids == ["0", "1"]
        assert len(cycle.edges) == 2


class TestCellError:
    """Test CellError dataclass."""

    def test_create(self):
        """Create cell error."""
        from marimo_inspection.types import CellError

        error = CellError(
            type="NameError",
            message="name 'x' is not defined",
            traceback=["Cell 0", "  x = 1"],
        )
        assert error.type == "NameError"
        assert len(error.traceback) == 2

    def test_default_traceback(self):
        """Default traceback is empty list."""
        from marimo_inspection.types import CellError

        error = CellError(
            type="ValueError",
            message="invalid value",
        )
        assert error.traceback == []


class TestCellErrors:
    """Test CellErrors dataclass."""

    def test_create(self):
        """Create cell errors."""
        from marimo_inspection.types import CellError, CellErrors

        errors = CellErrors(
            cell_id="0",
            errors=[
                CellError(
                    type="NameError",
                    message="x",
                )
            ],
            stderr=["NameError: x"],
        )
        assert errors.cell_id == "0"
        assert len(errors.errors) == 1


class TestDiagnostic:
    """Test Diagnostic dataclass."""

    def test_create(self):
        """Create diagnostic."""
        from marimo_inspection.types import Diagnostic

        diag = Diagnostic(
            rule="unused-import",
            severity="runtime",
            message="unused import 'os'",
            cell_id="0",
            line=5,
            column=1,
        )
        assert diag.rule == "unused-import"
        assert diag.severity == "runtime"
        assert diag.cell_id == "0"
        assert diag.line == 5

    def test_severities(self):
        """Accepts all severity levels."""
        from marimo_inspection.types import Diagnostic

        # Breaking
        Diagnostic(
            rule="test",
            severity="breaking",
            message="test",
        )

        # Runtime
        Diagnostic(
            rule="test",
            severity="runtime",
            message="test",
        )

        # Formatting
        Diagnostic(
            rule="test",
            severity="formatting",
            message="test",
        )
