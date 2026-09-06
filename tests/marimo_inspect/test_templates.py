"""Tests for scratchpad code templates.

Verifies that template functions:
- Generate valid Python code strings
- Correctly inject parameters
- Import the required modules
- Use the correct API patterns
"""

from __future__ import annotations

# -------------------------------------------------------------------
# Template generation tests
# -------------------------------------------------------------------


class TestCellMapTemplate:
    """Test build_cell_map_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert isinstance(code, str)
        assert len(code) > 0

    def test_contains_marimo_import(self):
        """Generated code imports marimo._code_mode."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "import marimo._code_mode as cm" in code

    def test_contains_json_import(self):
        """Generated code imports json."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "import json" in code

    def test_contains_get_context(self):
        """Uses cm.get_context()."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "cm.get_context()" in code

    def test_preview_lines_parameter(self):
        """preview_lines parameter is injected."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template(preview_lines=10)
        assert "10" in code
        # Verify the parameter is in the slice expression
        assert ":10]" in code or "[:10]" in code

    def test_default_preview_lines(self):
        """Default preview_lines is 3."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        # Default template should have 3 as the preview limit
        assert "[:3]" in code or ":3]" in code

    def test_uses_json_dumps(self):
        """Serializes output with json.dumps."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "json.dumps" in code

    def test_uses_top_level_await(self):
        """Uses top-level await (not asyncio.run) for execution."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "await" in code
        assert "asyncio.run" not in code


class TestCellDataTemplate:
    """Test build_cell_data_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        code = build_cell_data_template([])
        assert isinstance(code, str)
        assert len(code) > 0

    def test_empty_cell_ids(self):
        """Empty cell_ids generates all-cells query."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        code = build_cell_data_template([])
        assert "ctx.cells" in code

    def test_cell_ids_injected(self):
        """Cell IDs are JSON-serialized in the template."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        code = build_cell_data_template(["0", "1"])
        assert '"0"' in code or "0" in code
        assert '"1"' in code or "1" in code

    def test_contains_marimo_import(self):
        """Imports marimo._code_mode."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        code = build_cell_data_template([])
        assert "import marimo._code_mode as cm" in code

    def test_uses_json_dumps(self):
        """Serializes output with json.dumps."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        code = build_cell_data_template([])
        assert "json.dumps" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_CELL_DATA exists."""
        from marimo_inspection.templates.cell_data import (
            TEMPLATE_CELL_DATA,
        )

        assert isinstance(TEMPLATE_CELL_DATA, str)
        assert len(TEMPLATE_CELL_DATA) > 0


class TestCellOutputsTemplate:
    """Test build_cell_outputs_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.cell_outputs import (
            build_cell_outputs_template,
        )

        code = build_cell_outputs_template([])
        assert isinstance(code, str)
        assert len(code) > 0

    def test_cell_ids_injected(self):
        """Cell IDs are serialized in the template."""
        from marimo_inspection.templates.cell_outputs import (
            build_cell_outputs_template,
        )

        code = build_cell_outputs_template(["2"])
        assert '"2"' in code or "2" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_CELL_OUTPUTS exists."""
        from marimo_inspection.templates.cell_outputs import (
            TEMPLATE_CELL_OUTPUTS,
        )

        assert isinstance(TEMPLATE_CELL_OUTPUTS, str)
        assert len(TEMPLATE_CELL_OUTPUTS) > 0


class TestVariablesTemplate:
    """Test build_variables_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.variables import (
            build_variables_template,
        )

        code = build_variables_template([])
        assert isinstance(code, str)
        assert len(code) > 0

    def test_variable_names_injected(self):
        """Variable names are serialized in the template."""
        from marimo_inspection.templates.variables import (
            build_variables_template,
        )

        code = build_variables_template(["x", "y"])
        assert "x" in code
        assert "y" in code

    def test_empty_variables_all(self):
        """Empty list inspects all variables."""
        from marimo_inspection.templates.variables import (
            build_variables_template,
        )

        code = build_variables_template([])
        assert "globals()" in code or "globals().keys()" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_VARIABLES exists."""
        from marimo_inspection.templates.variables import (
            TEMPLATE_VARIABLES,
        )

        assert isinstance(TEMPLATE_VARIABLES, str)
        assert len(TEMPLATE_VARIABLES) > 0


class TestDependencyGraphTemplate:
    """Test build_dependency_graph_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        code = build_dependency_graph_template()
        assert isinstance(code, str)
        assert len(code) > 0

    def test_center_cell_injected(self):
        """Center cell_id is injected."""
        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        code = build_dependency_graph_template(cell_id="5")
        assert '"5"' in code or "5" in code

    def test_depth_injected(self):
        """Depth parameter is injected."""
        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        code = build_dependency_graph_template(depth=2)
        assert "2" in code

    def test_accesses_graph(self):
        """Template accesses ctx.graph."""
        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        code = build_dependency_graph_template()
        assert "ctx.graph" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_DEPENDENCY_GRAPH exists."""
        from marimo_inspection.templates.dependency import (
            TEMPLATE_DEPENDENCY_GRAPH,
        )

        assert isinstance(TEMPLATE_DEPENDENCY_GRAPH, str)
        assert len(TEMPLATE_DEPENDENCY_GRAPH) > 0


class TestErrorsTemplate:
    """Test build_errors_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        code = build_errors_template()
        assert isinstance(code, str)
        assert len(code) > 0

    def test_contains_marimo_import(self):
        """Imports marimo._code_mode."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        code = build_errors_template()
        assert "import marimo._code_mode as cm" in code

    def test_checks_cells(self):
        """Iterates over ctx.cells."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        code = build_errors_template()
        assert "ctx.cells" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_ERRORS exists."""
        from marimo_inspection.templates.errors import (
            TEMPLATE_ERRORS,
        )

        assert isinstance(TEMPLATE_ERRORS, str)
        assert len(TEMPLATE_ERRORS) > 0


class TestLintTemplate:
    """Test build_lint_template()."""

    def test_returns_string(self):
        """Returns a non-empty string."""
        from marimo_inspection.templates.lint import (
            build_lint_template,
        )

        code = build_lint_template()
        assert isinstance(code, str)
        assert len(code) > 0

    def test_imports_rule_engine(self):
        """Imports RuleEngine."""
        from marimo_inspection.templates.lint import (
            build_lint_template,
        )

        code = build_lint_template()
        assert "RuleEngine" in code

    def test_imports_severity(self):
        """Imports Severity enum."""
        from marimo_inspection.templates.lint import (
            build_lint_template,
        )

        code = build_lint_template()
        assert "Severity" in code

    def test_checks_notebook(self):
        """Calls check_notebook."""
        from marimo_inspection.templates.lint import (
            build_lint_template,
        )

        code = build_lint_template()
        assert "check_notebook" in code

    def test_prebuilt_template(self):
        """Pre-built TEMPLATE_LINT exists."""
        from marimo_inspection.templates.lint import (
            TEMPLATE_LINT,
        )

        assert isinstance(TEMPLATE_LINT, str)
        assert len(TEMPLATE_LINT) > 0


# -------------------------------------------------------------------
# Shared template properties
# -------------------------------------------------------------------


class TestAllTemplates:
    """Test common properties across all templates."""

    def test_all_templates_are_valid_python_structure(self):
        """All templates import and use json.dumps."""
        from marimo_inspection.templates import (
            TEMPLATE_CELL_DATA,
            TEMPLATE_CELL_MAP,
            TEMPLATE_CELL_OUTPUTS,
            TEMPLATE_DEPENDENCY_GRAPH,
            TEMPLATE_ERRORS,
            TEMPLATE_LINT,
            TEMPLATE_VARIABLES,
        )

        templates = {
            "cell_map": TEMPLATE_CELL_MAP,
            "cell_data": TEMPLATE_CELL_DATA,
            "cell_outputs": TEMPLATE_CELL_OUTPUTS,
            "variables": TEMPLATE_VARIABLES,
            "dependency": TEMPLATE_DEPENDENCY_GRAPH,
            "errors": TEMPLATE_ERRORS,
            "lint": TEMPLATE_LINT,
        }

        for name, code in templates.items():
            assert "json.dumps" in code, f"{name} template missing json.dumps"
            assert "import json" in code, f"{name} template missing json import"

    def test_all_templates_use_code_mode(self):
        """All templates import marimo._code_mode."""
        from marimo_inspection.templates import (
            TEMPLATE_CELL_DATA,
            TEMPLATE_CELL_MAP,
            TEMPLATE_CELL_OUTPUTS,
            TEMPLATE_DEPENDENCY_GRAPH,
            TEMPLATE_ERRORS,
            TEMPLATE_LINT,
            TEMPLATE_VARIABLES,
        )

        templates = {
            "cell_map": TEMPLATE_CELL_MAP,
            "cell_data": TEMPLATE_CELL_DATA,
            "cell_outputs": TEMPLATE_CELL_OUTPUTS,
            "variables": TEMPLATE_VARIABLES,
            "dependency": TEMPLATE_DEPENDENCY_GRAPH,
            "errors": TEMPLATE_ERRORS,
            "lint": TEMPLATE_LINT,
        }

        for name, code in templates.items():
            assert "import marimo._code_mode" in code, (
                f"{name} template missing code_mode import"
            )

    def test_all_templates_use_asyncio_run(self):
        """All templates use top-level await (not asyncio.run) for execution."""
        from marimo_inspection.templates import (
            TEMPLATE_CELL_DATA,
            TEMPLATE_CELL_MAP,
            TEMPLATE_CELL_OUTPUTS,
            TEMPLATE_DEPENDENCY_GRAPH,
            TEMPLATE_ERRORS,
            TEMPLATE_LINT,
            TEMPLATE_VARIABLES,
        )

        templates = {
            "cell_map": TEMPLATE_CELL_MAP,
            "cell_data": TEMPLATE_CELL_DATA,
            "cell_outputs": TEMPLATE_CELL_OUTPUTS,
            "variables": TEMPLATE_VARIABLES,
            "dependency": TEMPLATE_DEPENDENCY_GRAPH,
            "errors": TEMPLATE_ERRORS,
            "lint": TEMPLATE_LINT,
        }

        for name, code in templates.items():
            assert "await" in code, f"{name} template missing await"
            assert "asyncio.run" not in code, (
                f"{name} template should not use asyncio.run"
            )
