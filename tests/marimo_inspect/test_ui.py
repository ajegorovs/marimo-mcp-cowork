"""Tests for the narrow `set_ui_value` tool and its code-mode template.

The tool sets the value of a *live* marimo UI element by its kernel-global
variable name. It deliberately accepts ONLY ``variable_name`` + ``value`` —
there is no source-code argument, ever. Template tests run against a fake
code-mode context (no real kernel); handler tests mock the MarimoClient.

The value shape is preserved exactly (a scalar stays scalar, a list stays a
list) — no coercion, no list-wrapping. Nothing here bootstraps a real widget,
so true reactive behavior is NOT claimed; that is a release-gate (live,
browser-instantiated) concern explicitly out of scope for these hermetic tests.
"""

from __future__ import annotations

import ast
import json
from contextlib import asynccontextmanager
from typing import Any

# -------------------------------------------------------------------
# Helpers: execute a scratchpad template against a fake CodeMode context
# -------------------------------------------------------------------


def _compile_template_body(template_code: str):
    """Compile a scratchpad template without its trailing ``print(await _)``."""
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
    return compile(tree, "<set_ui_value_template>", "exec")


def _run_template(
    template_code: str, globals_dict: dict, monkeypatch
) -> tuple[dict[str, Any], Any]:
    """Execute a template; return its namespace and the fake ctx."""
    import marimo._code_mode as cm

    class _FakeCtx:
        def __init__(self, g):
            self.globals = g
            self.calls = []  # (element, value) recorded set_ui_value calls

        def set_ui_value(self, element, value):
            self.calls.append((element, value))

    fake_ctx = _FakeCtx(globals_dict)

    @asynccontextmanager
    async def _fake_get_context(**kwargs):
        yield fake_ctx

    monkeypatch.setattr(cm, "get_context", _fake_get_context)
    ns: dict = {}
    exec(_compile_template_body(template_code), ns)  # noqa: S102
    return ns, fake_ctx


async def _run_template_payload(
    template_code: str, globals_dict: dict, monkeypatch
) -> tuple[dict, list]:
    """Await a template's `_run` against a fake ctx; return payload + calls."""
    ns, fake_ctx = _run_template(template_code, globals_dict, monkeypatch)
    payload = json.loads(await ns["_run"]())
    return payload, fake_ctx.calls


class _FakeUIElement:
    """Stand-in for a marimo UI element.

    ``__module__`` is set to marimo's UI-impl namespace so the template's
    defensive fallback (``type(v).__module__.startswith("marimo._plugins.ui")``)
    recognises it as UI even though it is not a real ``UIElement`` subclass.
    """

    __module__ = "marimo._plugins.ui._impl.input"

    def __init__(self, id: str = "ui-fake-1", value=None):
        self._id = id
        self.value = value


# -------------------------------------------------------------------
# build_set_ui_value_template — generation + behavior
# -------------------------------------------------------------------


class TestSetUiValueTemplate:
    def test_returns_string(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert isinstance(code, str)
        assert len(code) > 0

    def test_is_valid_python(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        # Templates end in a top-level `await`, so compile with the
        # allow-top-level-await flag (matching how the scratchpad runs them).
        compile(code, "<template>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_uses_code_mode(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert "import marimo._code_mode as cm" in code
        assert "cm.get_context()" in code

    def test_uses_single_context(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert code.count("ctx = cm.get_context()") == 0  # uses `async with`
        assert code.count("async with cm.get_context()") == 1

    def test_calls_set_ui_value_on_context(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert "set_ui_value(" in code

    def test_uses_globals_by_name(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert "ctx.globals" in code

    def test_resolves_string_globals(self):
        """The name must be resolved via ctx.globals[name], not bare eval."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("widget", "a")
        assert "ctx.globals" in code
        assert "g[name]" in code  # dict lookup by the injected name variable
        assert "eval(" not in code  # never evaluate arbitrary source

    # --- value shape preservation ------------------------------------

    def test_scalar_value_embedded_via_json_roundtrip(self):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        # A dropdown key "a" must be serialized as the scalar, never ["a"].
        code = build_set_ui_value_template("dd", "a")
        assert "value = json.loads(" in code  # reconstruct exact JSON shape
        # json.dumps("a") == '"a"' — the scalar JSON is a bare JSON string,
        # not a 1-element list, so the value JSON text contains no brackets.
        serialized = json.dumps(json.dumps("a"))
        assert serialized in code

    async def test_scalar_value_roundtrips_via_harness(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement()
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("widget", "a"),
            {"widget": element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert payload["variable_name"] == "widget"
        assert calls == [(element, "a")]  # scalar preserved exactly

    async def test_list_value_roundtrips_via_harness(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement()
        value = ["a", "b", "c"]
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("widget", value),
            {"widget": element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert calls == [(element, value)]  # list shape preserved exactly

    async def test_value_not_coerced_into_list(self, monkeypatch):
        """A scalar must reach set_ui_value as the scalar, not a 1-list."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement()
        _, calls = await _run_template_payload(
            build_set_ui_value_template("dd", "red"),
            {"dd": element},
            monkeypatch,
        )
        recorded = calls[0][1]
        assert recorded == "red"
        assert isinstance(recorded, str)

    # --- safe quoting / weird inputs ---------------------------------

    async def test_weird_name_and_value_roundtrip(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        name = 'weird "name"\nwith ûnicode'
        value = {"k": 'v"quote', "emoji": "🙂\nline2", "n": None}
        element = _FakeUIElement()
        payload, calls = await _run_template_payload(
            build_set_ui_value_template(name, value),
            {name: element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert calls == [(element, value)]

    # --- rejection paths ---------------------------------------------

    async def test_missing_variable_error(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        payload, calls = await _run_template_payload(
            build_set_ui_value_template("nope", "a"),
            {"widget": _FakeUIElement()},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert "not a live kernel global" in payload["message"]
        assert calls == []

    async def test_non_ui_variable_error(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        # A plain int global is NOT a UI element.
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("plain", "a"),
            {"plain": 42},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert "not a marimo UI element" in payload["message"]
        assert payload["datatype"] == "int"
        assert payload["ui_element_globals"] == []  # no UI names found
        assert calls == []

    async def test_non_ui_error_lists_available_ui_names(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        payload, _ = await _run_template_payload(
            build_set_ui_value_template("plain", "a"),
            {"plain": 42, "slider": _FakeUIElement()},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert "slider" in payload["ui_element_globals"]

    async def test_error_paths_do_not_update(self, monkeypatch):
        """A rejected name must never emit a value-update call."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        _, calls = await _run_template_payload(
            build_set_ui_value_template("plain", "a"),
            {"plain": 42},
            monkeypatch,
        )
        assert calls == []


# -------------------------------------------------------------------
# set_ui_value tool handler
# -------------------------------------------------------------------


class TestSetUiValueTool:
    SID = "abc123"
    URL = "http://127.0.0.1:8090"

    def _patch_client(self, stdout_lines, status="ok"):
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
            "marimo_inspection.tools.ui.MarimoClient", return_value=mock_instance
        )

    async def test_requires_variable_name(self):
        from marimo_inspection.tools.ui import set_ui_value

        result = await set_ui_value("", value="a")
        assert result["status"] == "error"
        assert "variable_name" in result["error"]

    async def test_ok_status_and_next_steps(self):
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(['{"status": "ok", "variable_name": "slider"}']):
            result = await set_ui_value(
                "slider",
                value=5,
                session_id=self.SID,
                server_url=self.URL,
            )
        assert result["status"] == "ok"
        assert result["variable_name"] == "slider"
        assert result["session_id"] == self.SID
        assert len(result["next_steps"]) >= 1
        assert any("get_variables" in s for s in result["next_steps"])
        assert any("get_cell_outputs" in s for s in result["next_steps"])

    async def test_preserves_value_shape_in_sent_code(self):
        """The handler passes the exact value to the template builder."""
        import marimo_inspection.templates.ui as tpl
        from marimo_inspection.tools.ui import set_ui_value

        captured = {}

        orig = tpl.build_set_ui_value_template

        def spy(variable_name, value):
            captured["value"] = value
            return orig(variable_name, value)

        tpl.build_set_ui_value_template = spy
        try:
            with self._patch_client(['{"status": "ok", "variable_name": "dd"}']):
                await set_ui_value(
                    "dd",
                    value="red",
                    session_id=self.SID,
                    server_url=self.URL,
                )
        finally:
            tpl.build_set_ui_value_template = orig
        assert captured["value"] == "red"

    async def test_in_kernel_error_passthrough(self):
        """A status-error from the kernel surfaces as an error dict."""
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client([], status="error"):
            result = await set_ui_value(
                "plain",
                value="a",
                session_id=self.SID,
                server_url=self.URL,
            )
        assert "error" in result
        assert result["stderr"] == "boom"


# -------------------------------------------------------------------
# MCP schema — narrow contract, no arbitrary code
# -------------------------------------------------------------------


class TestSetUiValueSchema:
    async def test_registered_tool_has_no_code_parameter(self, mcp_server):
        """set_ui_value must NOT expose source/code/expression parameters."""
        from fastmcp.client import Client

        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "set_ui_value")
        props = set(tool.input_schema.get("properties", {}))
        assert "variable_name" in props
        assert "value" in props
        assert "source" not in props
        assert "code" not in props
        assert "expression" not in props
        assert "statement" not in props

    async def test_annotations_say_mutating_not_idempotent(self, mcp_server):
        """Registered with readOnlyHint=False, destructiveHint=True,
        idempotentHint=False."""
        from fastmcp.client import Client

        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "set_ui_value")
        ann = tool.annotations
        assert ann is not None
        assert ann.read_only_hint is False
        assert ann.destructive_hint is True
        assert ann.idempotent_hint is False
