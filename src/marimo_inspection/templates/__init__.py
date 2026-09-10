"""Scratchpad code templates for marimo inspection.

Each template is a Python code string that runs inside marimo's kernel
via the scratchpad (POST /api/kernel/execute). Templates use
`cm.get_context()` to access marimo internals.
"""

from marimo_inspection.templates.cell_data import (
    TEMPLATE_CELL_DATA,
)
from marimo_inspection.templates.cell_map import (
    TEMPLATE_CELL_MAP,
)
from marimo_inspection.templates.cell_outputs import (
    TEMPLATE_CELL_OUTPUTS,
)
from marimo_inspection.templates.dependency import (
    TEMPLATE_DEPENDENCY_GRAPH,
)
from marimo_inspection.templates.errors import (
    TEMPLATE_ERRORS,
)
from marimo_inspection.templates.lint import (
    TEMPLATE_LINT,
)
from marimo_inspection.templates.ui import build_set_ui_value_template
from marimo_inspection.templates.variables import (
    TEMPLATE_VARIABLES,
)

__all__ = [
    "TEMPLATE_CELL_DATA",
    "TEMPLATE_CELL_MAP",
    "TEMPLATE_CELL_OUTPUTS",
    "TEMPLATE_DEPENDENCY_GRAPH",
    "TEMPLATE_ERRORS",
    "TEMPLATE_LINT",
    "TEMPLATE_VARIABLES",
    "build_set_ui_value_template",
]
