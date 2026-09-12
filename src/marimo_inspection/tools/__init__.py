"""MCP tool handlers for marimo inspection."""

from marimo_inspection.tools.cells import (
    get_cell_data,
    get_cell_map,
    get_cell_outputs,
)
from marimo_inspection.tools.dependency import get_dependency_graph
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.lifecycle import restart_kernel
from marimo_inspection.tools.lint import lint_notebook
from marimo_inspection.tools.mutation import (
    create_cell,
    delete_cell,
    edit_cell,
    run_cell,
)
from marimo_inspection.tools.notebooks import list_active_notebooks
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

__all__ = [
    "create_cell",
    "delete_cell",
    "edit_cell",
    "get_cell_data",
    "get_cell_map",
    "get_cell_outputs",
    "get_dependency_graph",
    "get_errors",
    "get_variables",
    "lint_notebook",
    "list_active_notebooks",
    "restart_kernel",
    "run_cell",
    "set_ui_value",
]
