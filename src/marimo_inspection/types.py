"""Shared dataclasses for marimo inspection MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarimoNotebookInfo:
    """Information about an active marimo notebook."""

    name: str
    path: str
    session_id: str


@dataclass
class LightweightCellInfo:
    """Lightweight cell information for the cell map."""

    cell_id: str
    name: str
    preview: str
    line_count: int
    runtime_state: str | None = None
    has_output: bool = False
    has_console_output: bool = False
    has_errors: bool = False


@dataclass
class CellRuntimeData:
    """Full runtime data for a single cell."""

    cell_id: str
    code: str | None = None
    errors: list[dict[str, Any]] | None = None
    runtime_state: str | None = None
    execution_time_ms: float | None = None
    variables: dict[str, Any] | None = None


@dataclass
class CellVisualOutput:
    """Visual output from a cell execution."""

    visual_output: str | None = None
    visual_mimetype: str | None = None


@dataclass
class CellOutputData:
    """Output data for a cell."""

    cell_id: str
    visual_output: CellVisualOutput | None = None
    stdout: list[str] | None = None
    stderr: list[str] | None = None


@dataclass
class VariableValue:
    """Information about a kernel variable."""

    name: str
    value: str
    datatype: str | None = None


@dataclass
class DataTableMetadata:
    """Metadata about a data table."""

    source: str
    num_rows: int | None
    num_columns: int | None
    columns: list[dict[str, Any]]
    engine: str | None = None
    primary_keys: list[str] | None = None
    indexes: list[str] | None = None


@dataclass
class VariableInfo:
    """Variable information in the dependency graph."""

    name: str
    kind: str
    datatype: str | None = None


@dataclass
class CellDependencyInfo:
    """Dependency information for a single cell."""

    cell_id: str
    cell_name: str
    defs: list[VariableInfo]
    refs: list[str]
    parent_cell_ids: list[str]
    child_cell_ids: list[str]


@dataclass
class CycleInfo:
    """Cycle information in the dependency graph."""

    cell_ids: list[str]
    edges: list[list[str]]


@dataclass
class CellError:
    """Error detail for a single error."""

    type: str
    message: str
    traceback: list[str] = field(default_factory=list)


@dataclass
class CellErrors:
    """All errors for a cell."""

    cell_id: str
    errors: list[CellError]
    stderr: list[str] = field(default_factory=list)


@dataclass
class Diagnostic:
    """Linting diagnostic."""

    rule: str
    severity: str  # "breaking" | "runtime" | "formatting"
    message: str
    cell_id: str | None = None
    line: int | None = None
    column: int | None = None
