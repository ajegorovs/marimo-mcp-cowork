"""Tests for scratchpad code templates.

Verifies that template functions:
- Generate valid Python code strings
- Correctly inject parameters
- Import the required modules
- Use the correct API patterns
"""

from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

# Private, but pinned to the same marimo 0.24.x range as the templates under
# test; the fake needs the REAL enum (a plain "stderr" string hides the bug).
from marimo._messaging.cell_output import CellChannel

# -------------------------------------------------------------------
# Helpers: execute a scratchpad template against a fake CodeMode context
# -------------------------------------------------------------------


def _compile_template_body(template_code: str):
    """Compile a scratchpad template without its trailing ``print(await _)``.

    Top-level await cannot run synchronously via exec(), so the final
    print-expression is dropped; the test invokes the defined async entry
    function directly.
    """
    tree = ast.parse(template_code)
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "print"
        )
    ]
    ast.fix_missing_locations(tree)
    return compile(tree, "<scratchpad_template>", "exec")


async def _exec_template(
    template_code: str,
    fake_ctx,
    monkeypatch,
    fn_name: str,
) -> dict:
    """Execute a scratchpad template against a fake CodeMode context.

    ``marimo._code_mode.get_context`` is monkeypatched to yield ``fake_ctx``;
    the template body runs in a fresh namespace, then its async entry
    function is invoked. Returns the JSON payload the template prints.
    """
    import marimo._code_mode as cm

    @asynccontextmanager
    async def _fake_get_context(**kwargs):
        yield fake_ctx

    monkeypatch.setattr(cm, "get_context", _fake_get_context)
    ns: dict = {}
    # Executing the generated scratchpad template is the point of this
    # harness; the code is repo-authored, not user input.
    exec(_compile_template_body(template_code), ns)  # noqa: S102
    return json.loads(await ns[fn_name]())


class _FakeCell:
    """Minimal stand-in for marimo._code_mode.NotebookCell."""

    def __init__(
        self,
        cell_id: str,
        code: str = "x = 1",
        name: str = "",
        status: str = "idle",
        output=None,
        console_outputs: list | None = None,
        errors: list | None = None,
    ):
        self.id = cell_id
        self.code = code
        self.name = name
        self.status = status
        self.output = output
        self.console_outputs = console_outputs or []
        self.errors = errors or []


class _UnreadableCell:
    """Cell whose runtime fields raise — simulates private-API drift."""

    id = "BROKEN"
    code = "boom()"
    name = ""
    status = "stale"

    @property
    def output(self):
        raise RuntimeError("private API drift")

    @property
    def console_outputs(self):
        raise RuntimeError("private API drift")

    @property
    def errors(self):
        raise RuntimeError("private API drift")


class _FakeCellError:
    """Minimal stand-in for marimo's CellError record."""

    def __init__(self, kind: str = "runtime", msg: str = "boom", exception=None):
        self.kind = kind
        self.msg = msg
        self.exception = exception


class _FakeConsoleEvent:
    channel = "stdout"
    data = "hello"


class _FakeStderrTracebackEvent:
    channel = "stderr"
    data = "Traceback (most recent call last):\nNameError: boom"


class _FakePlainStderrEvent:
    channel = "stderr"
    data = "some warning text without exception markers"


class _FakeErrorLabelEvent:
    """stderr that *labels* an error without naming an exception type.

    The exact shape the hunt found: a healthy cell printing a message like this
    was reported as a console exception because the scan matched the bare
    ``Error:`` marker.
    """

    channel = "stderr"
    data = "Error: 3 rows skipped (not an exception)"


class _FakeExceptionLineEvent:
    """stderr carrying an exception line but no traceback header."""

    channel = "stderr"
    data = "ValueError: could not convert string to float"


class _FakeEnumChannelEvent:
    """Console event carrying marimo's REAL str-mixin ``CellChannel`` enum.

    ``str(CellChannel.STDERR)`` is ``"CellChannel.STDERR"`` while ``repr``
    reads ``stderr`` — the exact shape a real kernel hands the templates, and
    the exact shape a raw ``str(channel)`` comparison never matched.
    """

    def __init__(
        self,
        channel=CellChannel.STDERR,
        data="Traceback (most recent call last):\nValueError: boom",
    ):
        self.channel = channel
        self.data = data


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

    def test_flags_are_computed_not_hardcoded(self):
        """Flag fields must be computed per cell, never hardcoded False."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert '"has_output": _flag(' in code
        assert '"has_console_output": _flag(' in code
        assert '"has_errors": _flag(' in code
        assert '"has_output": False' not in code
        assert '"has_console_output": False' not in code
        assert '"has_errors": False' not in code

    def test_flag_reader_has_defensive_none_fallback(self):
        """Unreadable fields must yield None, never a false assertion."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        code = build_cell_map_template()
        assert "def _flag(reader):" in code
        assert "try:" in code
        assert "except Exception:" in code
        assert "return None" in code
        assert "c.output is not None" in code

    async def test_flags_reflect_real_fields(self, monkeypatch):
        """has_output/has_console_output/has_errors mirror CodeMode fields."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        data = await _exec_template(
            build_cell_map_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell("0", output=object()),
                    _FakeCell("1", console_outputs=[_FakeConsoleEvent()]),
                    _FakeCell("2", errors=[_FakeCellError()]),
                    _FakeCell("3"),
                ]
            ),
            monkeypatch,
            "get_cell_map",
        )
        by_id = {c["cell_id"]: c for c in data["cells"]}
        assert by_id["0"]["has_output"] is True
        assert by_id["0"]["has_console_output"] is False
        assert by_id["0"]["has_errors"] is False
        assert by_id["1"]["has_output"] is False
        assert by_id["1"]["has_console_output"] is True
        assert by_id["1"]["has_errors"] is False
        assert by_id["2"]["has_output"] is False
        assert by_id["2"]["has_console_output"] is False
        assert by_id["2"]["has_errors"] is True
        assert by_id["3"]["has_output"] is False
        assert by_id["3"]["has_console_output"] is False
        assert by_id["3"]["has_errors"] is False

    async def test_flags_are_none_when_field_unreadable(self, monkeypatch):
        """A cell whose runtime fields raise reports None, not False."""
        from marimo_inspection.templates.cell_map import (
            build_cell_map_template,
        )

        data = await _exec_template(
            build_cell_map_template(),
            SimpleNamespace(cells=[_UnreadableCell()]),
            monkeypatch,
            "get_cell_map",
        )
        assert len(data["cells"]) == 1
        cell = data["cells"][0]
        assert cell["cell_id"] == "BROKEN"
        assert cell["has_output"] is None
        assert cell["has_console_output"] is None
        assert cell["has_errors"] is None
        # The readable fields are still reported.
        assert cell["line_count"] == 1


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

    async def test_missing_ids_are_reported(self, monkeypatch):
        """A requested id that resolves to nothing is reported (H4).

        Pre-fix it was dropped silently, so a deleted or mistyped id looked
        like "nothing matched" while the write tools refuse the same id.
        """
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        data = await _exec_template(
            build_cell_data_template(["0", "gone"]),
            SimpleNamespace(cells={"0": _FakeCell("0")}),
            monkeypatch,
            "get_cell_data",
        )

        assert [row["cell_id"] for row in data["data"]] == ["0"]
        assert data["missing_cell_ids"] == ["gone"]

    async def test_all_cells_reports_nothing_missing(self, monkeypatch):
        """The all-cells path (empty ids) has no requested-but-missing id."""
        from marimo_inspection.templates.cell_data import (
            build_cell_data_template,
        )

        data = await _exec_template(
            build_cell_data_template([]),
            SimpleNamespace(cells={"0": _FakeCell("0")}),
            monkeypatch,
            "get_cell_data",
        )

        assert data["missing_cell_ids"] == []
        assert [row["cell_id"] for row in data["data"]] == ["0"]


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

    def test_shares_the_console_channel_normalizer(self):
        """stdout/stderr filters use the shared channel normalizer."""
        from marimo_inspection.templates.cell_outputs import (
            build_cell_outputs_template,
        )

        code = build_cell_outputs_template([])
        assert "def _channel_name" in code
        assert '_channel_name(o) == "stdout"' in code
        assert '_channel_name(o) == "stderr"' in code

    async def test_enum_channel_events_land_in_the_stdout_stderr_lists(
        self, monkeypatch
    ):
        """A real CellChannel enum lands in stdout/stderr, not just events."""
        from marimo_inspection.templates.cell_outputs import (
            build_cell_outputs_template,
        )

        data = await _exec_template(
            build_cell_outputs_template([]),
            SimpleNamespace(
                cells={
                    "7": _FakeCell(
                        "7",
                        console_outputs=[
                            _FakeEnumChannelEvent(CellChannel.STDOUT, "hello"),
                            _FakeEnumChannelEvent(),
                        ],
                    )
                }
            ),
            monkeypatch,
            "get_cell_outputs",
        )
        cell = data["cells"][0]
        assert [e["channel"] for e in cell["stdout"]] == ["stdout"]
        assert [e["channel"] for e in cell["stderr"]] == ["stderr"]
        assert [e["channel"] for e in cell["console_events"]] == [
            "stdout",
            "stderr",
        ]

    async def test_missing_ids_are_reported(self, monkeypatch):
        """An id that resolves to nothing is reported, not dropped (H4).

        Without this, a cell that does not exist and a cell with no output
        produce the same payload.
        """
        from marimo_inspection.templates.cell_outputs import (
            build_cell_outputs_template,
        )

        data = await _exec_template(
            build_cell_outputs_template(["7", "gone"]),
            SimpleNamespace(cells={"7": _FakeCell("7")}),
            monkeypatch,
            "get_cell_outputs",
        )

        assert [cell["cell_id"] for cell in data["cells"]] == ["7"]
        assert data["missing_cell_ids"] == ["gone"]


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

    def test_takes_no_centring_arguments(self):
        """cell_id/depth are refused by the tool, so the template has no args.

        The old template accepted (and embedded) both while ignoring them.
        """
        import inspect

        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        params = inspect.signature(build_dependency_graph_template).parameters
        assert list(params) == []

    def test_names_cells_from_the_notebook_cells(self):
        """cell_name is read from ctx.cells — the same source get_cell_map uses."""
        from marimo_inspection.templates.dependency import (
            build_dependency_graph_template,
        )

        code = build_dependency_graph_template()
        assert "name_by_id" in code
        assert 'getattr(cell, "name", "")' in code

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

    def test_two_distinct_channels(self):
        """The payload must split structured errors and console stderr."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        code = build_errors_template()
        assert '"structured_errors"' in code
        assert '"console_stderr"' in code
        assert '"has_console_exception"' in code
        assert '"total_structured_errors"' in code
        assert '"total_console_exception_cells"' in code
        # Never a raw stderr list conflated into the cell payload.
        assert '"stderr": []' not in code

    def test_reuses_cell_outputs_serializer(self):
        """console_stderr uses the shared cell_outputs console serializer."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        code = build_errors_template()
        assert "def _cell_output_to_dict" in code
        # The channel normalization is shared with the serializer (one helper),
        # never re-implemented per call site.
        assert "def _channel_name" in code
        assert '"channel": channel' in code
        assert '_channel_name(o) == "stderr"' in code

    async def test_fresh_session_zero_summary(self, monkeypatch):
        """An empty session reports a self-consistent zero summary."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(cells=[]),
            monkeypatch,
            "get_errors",
        )
        assert data == {
            "has_errors": False,
            "total_errors": 0,
            "total_structured_errors": 0,
            "total_cells_with_errors": 0,
            "has_console_exception": False,
            "total_console_exception_cells": 0,
            "cells": [],
        }

    async def test_structured_only_cell(self, monkeypatch):
        """Structured errors land in structured_errors, not the stderr channel."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "5",
                        errors=[_FakeCellError(kind="runtime", msg="boom")],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )
        assert data["has_errors"] is True
        assert data["total_errors"] == 1
        assert data["total_structured_errors"] == 1
        assert data["total_cells_with_errors"] == 1
        assert data["has_console_exception"] is False
        assert data["total_console_exception_cells"] == 0
        cell = data["cells"][0]
        assert cell["cell_id"] == "5"
        assert cell["structured_errors"] == [
            {"kind": "runtime", "cell": "5", "msg": "boom", "exception": "None"}
        ]
        assert cell["console_stderr"] == []
        assert cell["has_console_exception"] is False

    async def test_console_exception_visible_when_structured_empty(self, monkeypatch):
        """UI-handler tracebacks on stderr surface even with c.errors empty."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "9",
                        console_outputs=[
                            _FakeStderrTracebackEvent(),
                            _FakeConsoleEvent(),
                        ],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )
        assert data["has_errors"] is False
        assert data["total_errors"] == 0
        assert data["total_structured_errors"] == 0
        assert data["total_cells_with_errors"] == 0
        assert data["has_console_exception"] is True
        assert data["total_console_exception_cells"] == 1
        cell = data["cells"][0]
        assert cell["cell_id"] == "9"
        assert cell["structured_errors"] == []
        assert cell["has_console_exception"] is True
        assert cell["console_exception_evidence"] == "traceback"
        # Only stderr-channel events are serialized into console_stderr.
        assert [e["channel"] for e in cell["console_stderr"]] == ["stderr"]
        assert "Traceback" in cell["console_stderr"][0]["data"]

    async def test_error_label_without_a_type_is_not_an_exception(self, monkeypatch):
        """`Error: 3 rows skipped` is a message, not an exception (H6).

        The pre-fix scan matched the bare ``Error:`` marker, so a cell that
        only logged this line came back with ``has_console_exception: true``,
        counted into ``total_console_exception_cells``, and carried a
        next_step asserting a traceback/UI-handler cause.
        """
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "3",
                        console_outputs=[_FakeErrorLabelEvent()],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )

        assert data["has_console_exception"] is False
        assert data["total_console_exception_cells"] == 0
        assert data["cells"] == []

    async def test_exception_line_is_evidence_without_a_header(self, monkeypatch):
        """An unindented `ValueError: ...` line is real evidence (H6)."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "4",
                        console_outputs=[_FakeExceptionLineEvent()],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )

        assert data["has_console_exception"] is True
        assert data["total_console_exception_cells"] == 1
        cell = data["cells"][0]
        assert cell["has_console_exception"] is True
        assert cell["console_exception_evidence"] == "exception_line"

    async def test_enum_channel_stderr_is_not_filtered_out(self, monkeypatch):
        """A real CellChannel enum must match the stderr channel filter.

        The kernel hands the template marimo's str-mixin enum, whose ``str()``
        is ``"CellChannel.STDERR"`` — comparing that raw never matched, so the
        console channel reported nothing.
        """
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "11",
                        console_outputs=[_FakeEnumChannelEvent()],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )
        assert data["has_console_exception"] is True
        assert data["total_console_exception_cells"] == 1
        cell = data["cells"][0]
        assert [e["channel"] for e in cell["console_stderr"]] == ["stderr"]
        assert "Traceback" in cell["console_stderr"][0]["data"]

    async def test_non_exception_stderr_is_not_flagged(self, monkeypatch):
        """Plain stderr without exception markers is not exception evidence."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell("7", console_outputs=[_FakePlainStderrEvent()]),
                ]
            ),
            monkeypatch,
            "get_errors",
        )
        # Conservative: no clear exception evidence, no structured errors,
        # so the cell is not reported as an error at all.
        assert data == {
            "has_errors": False,
            "total_errors": 0,
            "total_structured_errors": 0,
            "total_cells_with_errors": 0,
            "has_console_exception": False,
            "total_console_exception_cells": 0,
            "cells": [],
        }

    async def test_both_channels_in_one_cell(self, monkeypatch):
        """A cell can carry structured errors AND console exception evidence."""
        from marimo_inspection.templates.errors import (
            build_errors_template,
        )

        data = await _exec_template(
            build_errors_template(),
            SimpleNamespace(
                cells=[
                    _FakeCell(
                        "6",
                        errors=[_FakeCellError(kind="graph", msg="cycle")],
                        console_outputs=[_FakeStderrTracebackEvent()],
                    )
                ]
            ),
            monkeypatch,
            "get_errors",
        )
        assert data["has_errors"] is True
        assert data["total_structured_errors"] == 1
        assert data["total_cells_with_errors"] == 1
        assert data["has_console_exception"] is True
        assert data["total_console_exception_cells"] == 1
        cell = data["cells"][0]
        assert cell["structured_errors"][0]["kind"] == "graph"
        assert cell["has_console_exception"] is True
        assert len(cell["console_stderr"]) == 1


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


class TestCreateCellTemplate:
    """Test build_create_cell_template() hide_code default schema.

    T6: created cells are visible by default (hide_code=False); hiding stays
    available as an explicit opt-in for setup/implementation cells.
    """

    def test_default_renders_hide_code_false(self):
        """Without hide_code, the generated call renders hide_code=False."""
        from marimo_inspection.templates.mutation import (
            build_create_cell_template,
        )

        code = build_create_cell_template("x = 1")
        assert "hide_code=False" in code
        assert "hide_code=True" not in code

    def test_explicit_hide_code_true_still_renders(self):
        """hide_code=True remains an explicit opt-in."""
        from marimo_inspection.templates.mutation import (
            build_create_cell_template,
        )

        code = build_create_cell_template("x = 1", hide_code=True)
        assert "hide_code=True" in code


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
