"""Tests for the narrow `set_ui_value` tool and its code-mode template.

The tool sets the value of a *live* marimo UI element by its kernel-global
variable name. It deliberately accepts ONLY ``variable_name`` + ``value`` —
there is no source-code argument, ever. Template tests run against a fake
code-mode context (no real kernel); handler tests mock the MarimoClient.

Two contracts are locked in here:

* **No coercion.** The value shape is per widget and never re-wrapped; a shape
  the element cannot accept is refused with ``did_you_mean`` before anything is
  queued. The fake elements declare their input shape the way real marimo
  widgets do — via the first parameter of their ``UIElement[...]`` generic
  base — so the guard is exercised against the same declaration the kernel
  sees, not against a hard-coded widget list.
* **A flush is not proof.** marimo swallows a rejected update (stderr only), so
  the template re-reads the element's value in a second context and the handler
  scans stderr; a rejected update must surface as an error, never as ``ok``.
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


class _FakeCtx:
    """Fake code-mode context.

    ``apply=True`` mimics marimo's flush: the queued value lands on the
    element, so the template's read-back observes a change. ``apply=False``
    mimics a rejected-and-swallowed update (the value is recorded but never
    applied), which is what the kernel does for an invalid dropdown key.
    """

    def __init__(self, g, apply=True, raise_on_entry=None):
        self.globals = g
        self.calls = []  # (element, value) recorded set_ui_value calls
        self.apply = apply
        self.entries = 0
        self.raise_on_entry = raise_on_entry

    def set_ui_value(self, element, value):
        self.calls.append((element, value))
        if self.apply:
            element._value = value


def _run_template(
    template_code: str,
    globals_dict: dict,
    monkeypatch,
    *,
    apply=True,
    raise_on_entry=None,
):
    """Execute a template; return its namespace and the fake ctx.

    ``raise_on_entry`` makes the *n*-th ``cm.get_context()`` entry raise, which
    is how a failed read-back is simulated (the template enters a second
    context to verify).
    """
    import marimo._code_mode as cm

    fake_ctx = _FakeCtx(globals_dict, apply=apply, raise_on_entry=raise_on_entry)

    @asynccontextmanager
    async def _fake_get_context(**kwargs):
        fake_ctx.entries += 1
        if fake_ctx.raise_on_entry == fake_ctx.entries:
            raise RuntimeError("kernel read-back unavailable")
        yield fake_ctx

    monkeypatch.setattr(cm, "get_context", _fake_get_context)
    ns: dict = {}
    exec(_compile_template_body(template_code), ns)  # noqa: S102
    return ns, fake_ctx


async def _run_template_payload(
    template_code: str,
    globals_dict: dict,
    monkeypatch,
    *,
    apply=True,
    raise_on_entry=None,
) -> tuple[dict, list]:
    """Await a template's `_run` against a fake ctx; return payload + calls."""
    ns, fake_ctx = _run_template(
        template_code,
        globals_dict,
        monkeypatch,
        apply=apply,
        raise_on_entry=raise_on_entry,
    )
    payload = json.loads(await ns["_run"]())
    return payload, fake_ctx.calls


class _FakeUIElement:
    """Stand-in for a marimo UI element with an UNDECLARED input shape.

    ``__module__`` is set to marimo's UI-impl namespace so the template's
    defensive fallback (``type(v).__module__.startswith("marimo._plugins.ui")``)
    recognises it as UI even though it is not a real ``UIElement`` subclass.
    No ``__orig_bases__``: the shape guard must stay out of the way when the
    declaration is unreadable.
    """

    __module__ = "marimo._plugins.ui._impl.input"

    def __init__(self, id: str = "ui-fake-1", value=None):
        self._id = id
        self._value = value

    @property
    def value(self):
        return self._value


def _shaped_element(name: str, annotation: Any):
    """A fake element declaring its input shape the way real widgets do."""
    cls = type(name, (_FakeUIElement,), {"__module__": _FakeUIElement.__module__})
    # Declared the way real marimo widgets do, so the template's shape guard
    # reads the same declaration it reads from a real element.
    cls.__orig_bases__ = (annotation,)  # type: ignore[attr-defined]
    return cls


def _list_shaped():
    from marimo._plugins.ui._core.ui_element import UIElement

    return _shaped_element("_FakeListShaped", UIElement[list[str], Any])


def _scalar_shaped():
    from marimo._plugins.ui._core.ui_element import UIElement

    return _shaped_element("_FakeScalarShaped", UIElement[int | float, int | float])


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

    def test_uses_two_contexts_set_then_verify(self):
        """One context queues the update; a second re-reads the value.

        The flush happens on the FIRST context exit and marimo swallows a
        rejection there, so a template that never re-enters cannot tell an
        applied update from a dropped one.
        """
        from marimo_inspection.templates.ui import build_set_ui_value_template

        code = build_set_ui_value_template("x", "a")
        assert code.count("async with cm.get_context()") == 2

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

        # A scalar must be embedded as a bare JSON scalar, never ["x"].
        code = build_set_ui_value_template("dd", "a")
        assert "value = json.loads(" in code  # reconstruct exact JSON shape
        serialized = json.dumps(json.dumps("a"))
        assert serialized in code
        assert json.dumps(json.dumps(["a"])) not in code

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
        assert payload["verified"] is True
        assert payload["applied"] is True
        assert payload["value_after"] == "a"
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

    # --- declared-shape guard (no coercion, corrective error) --------

    async def test_list_shaped_element_refuses_scalar(self, monkeypatch):
        """The dropdown case from the consumer report: refuse, don't guess."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _list_shaped()()
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("dd", "beta"),
            {"dd": element},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert payload["reason"] == "value_shape_mismatch"
        assert payload["element_type"] == "_FakeListShaped"
        assert payload["accepted_shape"] == "list[str]"
        assert payload["did_you_mean"] == ["beta"]
        assert payload["submitted_value"] == "beta"
        assert "Nothing was changed" in payload["message"]
        assert calls == []  # never queued
        assert element.value is None  # untouched

    async def test_scalar_refusal_suggests_the_elements_own_key(self, monkeypatch):
        """H5: the correction carries the option KEY, not the submitted type.

        A multiselect whose keys are the strings "4"/"5" does not accept the
        suggested ``[4]`` — following the old ``did_you_mean`` verbatim produced
        a second error, and the working form was never suggested.
        """
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _list_shaped()()
        element._options = {"4": "four", "5": "five"}
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("ms", 4),
            {"ms": element},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert payload["reason"] == "value_shape_mismatch"
        assert payload["submitted_value"] == 4
        assert payload["did_you_mean"] == ["4"]
        assert '"4"' in payload["message"]
        assert calls == []

    async def test_scalar_refusal_falls_back_without_options(self, monkeypatch):
        """No options to match against keeps the plain one-element form."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _list_shaped()()
        payload, _ = await _run_template_payload(
            build_set_ui_value_template("rs", 7),
            {"rs": element},
            monkeypatch,
        )
        assert payload["did_you_mean"] == [7]

    async def test_list_shaped_element_accepts_one_element_list(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _list_shaped()(value=["alpha"])
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("dd", ["beta"]),
            {"dd": element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert payload["accepted_shape"] == "list[str]"
        assert payload["applied"] is True
        assert payload["value_before"] == ["alpha"]
        assert payload["value_after"] == ["beta"]
        assert calls == [(element, ["beta"])]

    async def test_scalar_shaped_element_refuses_list(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _scalar_shaped()(value=3)
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("sld", [7]),
            {"sld": element},
            monkeypatch,
        )
        assert payload["status"] == "error"
        assert payload["reason"] == "value_shape_mismatch"
        assert payload["accepted_shape"] == "int | float"
        assert payload["did_you_mean"] == 7
        assert calls == []
        assert element.value == 3

    async def test_undeclared_shape_is_not_guessed(self, monkeypatch):
        """No readable declaration -> apply as sent, decide by read-back."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement()
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("w", ["x"]),
            {"w": element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert payload["accepted_shape"] is None
        assert calls == [(element, ["x"])]

    # --- read-back semantics -----------------------------------------

    async def test_unchanged_value_reports_not_applied(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement(value="same")
        payload, _ = await _run_template_payload(
            build_set_ui_value_template("w", "same"),
            {"w": element},
            monkeypatch,
        )
        assert payload["status"] == "ok"
        assert payload["verified"] is True
        assert payload["applied"] is False
        assert payload["value_before"] == "same"
        assert payload["value_after"] == "same"

    async def test_swallowed_update_is_visible_to_readback(self, monkeypatch):
        """apply=False mimics marimo dropping a rejected update."""
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _list_shaped()(value=["alpha"])
        payload, calls = await _run_template_payload(
            build_set_ui_value_template("dd", ["nope"]),
            {"dd": element},
            monkeypatch,
            apply=False,
        )
        assert payload["status"] == "ok"  # the template alone cannot say why
        assert payload["applied"] is False  # but it never claims success
        assert payload["value_after"] == ["alpha"]
        assert calls == [(element, ["nope"])]

    async def test_failed_readback_is_not_verified(self, monkeypatch):
        from marimo_inspection.templates.ui import build_set_ui_value_template

        element = _FakeUIElement()
        payload, _ = await _run_template_payload(
            build_set_ui_value_template("w", "x"),
            {"w": element},
            monkeypatch,
            raise_on_entry=2,
        )
        assert payload["status"] == "ok"
        assert payload["verified"] is False
        assert payload["applied"] is None
        assert payload["value_after"] is None
        assert "RuntimeError" in payload["readback_error"]

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
        assert payload["reason"] == "unknown_variable"
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
        assert payload["reason"] == "not_a_ui_element"
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

# The real kernel stderr for an invalid dropdown key: marimo catches the
# exception while applying the value and writes the traceback to stderr, so
# the execution still reports success. Only the "option name 'nope'" line is
# useful to the caller. The `_update` frame quotes the CONVERSION call site
# (`self._value = self._convert_value(value)`, ui_element.py:468 in 0.24.x) —
# that is what identifies this failure point (see `_HANDLER_STDERR_NO_MOVE`).
_REJECTION_STDERR = """Traceback (most recent call last):
  File ".../marimo/_runtime/runtime.py", line 2030, in set_ui_element_value
    component._update(value)
  File ".../marimo/_plugins/ui/_core/ui_element.py", line 468, in _update
    self._value = self._convert_value(value)
  File ".../marimo/_plugins/ui/_impl/input.py", line 1116, in _convert_value
    _validate_option_name(self._selected_key, self.options)
  File ".../marimo/_plugins/ui/_impl/input.py", line 957, in _validate_option_name
    raise ValueError(
ValueError: The option name 'nope' is not a valid option. Please use one of the following options: ['alpha', 'beta']

An exception was raised by a UIElement's on_change handler:"""

# Real kernel stderr for the T13 residual, captured from a marimo 0.24.0 kernel
# (a slider created with `value=1`, submitted 1 again — `_update` assigns the
# same value with no equality shortcut and still calls the handler, which
# raises). The notice text is IDENTICAL to the conversion rejection above: a
# plain ValueError from `_convert_value` lands in runtime.py's generic
# `except Exception` branch. Only the quoted call site separates them — here
# `self._on_change(self._value)` (ui_element.py:473).
_HANDLER_STDERR_NO_MOVE = """Traceback (most recent call last):
  File ".../marimo/_runtime/runtime.py", line 2030, in set_ui_element_value
    component._update(value)
  File ".../marimo/_plugins/ui/_core/ui_element.py", line 473, in _update
    self._on_change(self._value)
  File ".../test_notebook.py", line 3, in _boom
    raise ValueError('boom from on_change')
ValueError: boom from on_change

An exception was raised by a UIElement's on_change handler:"""


class TestRejectionSite:
    """Which call site marimo raised at, read from its own traceback frames."""

    def test_handler_site_is_recognised(self):
        from marimo_inspection.tools.ui import _rejection_site

        assert _rejection_site(_HANDLER_STDERR_NO_MOVE) == "on_change"

    def test_conversion_site_is_recognised(self):
        from marimo_inspection.tools.ui import _rejection_site

        assert _rejection_site(_REJECTION_STDERR) == "convert"

    def test_unrelated_stderr_has_no_site(self):
        from marimo_inspection.tools.ui import _rejection_site

        assert _rejection_site("some warning: deprecation") is None

    def test_empty_stderr_has_no_site(self):
        from marimo_inspection.tools.ui import _rejection_site

        assert _rejection_site("") is None


class TestSetUiValueTool:
    SID = "abc123"
    URL = "http://127.0.0.1:8090"

    def _patch_client(self, stdout_lines, status="ok", stderr=None):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_instance = MagicMock()
        mock_session = MagicMock(
            session_id=self.SID, file="/test.py", basename="test.py"
        )
        mock_instance.resolve_session = AsyncMock(return_value=mock_session)
        mock_execute_result = MagicMock()
        mock_execute_result.status = status
        mock_execute_result.stdout = stdout_lines
        mock_execute_result.stderr = ["boom"] if stderr is None else stderr
        mock_instance.execute = AsyncMock(return_value=mock_execute_result)
        return patch(
            "marimo_inspection.tools.ui.MarimoClient", return_value=mock_instance
        )

    def _ok_payload(self, **overrides):
        payload = {
            "status": "ok",
            "variable_name": "slider",
            "element_type": "slider",
            "accepted_shape": "int | float",
            "verified": True,
            "readback_error": None,
            "applied": True,
            "value_before": 3,
            "value_after": 7,
        }
        payload.update(overrides)
        return [json.dumps(payload)]

    async def test_requires_variable_name(self):
        from marimo_inspection.tools.ui import set_ui_value

        result = await set_ui_value("", value="a")
        assert result["status"] == "error"
        assert "variable_name" in result["error"]

    async def test_ok_status_and_next_steps(self):
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(self._ok_payload(), stderr=[]):
            result = await set_ui_value(
                "slider",
                value=7,
                session_id=self.SID,
                server_url=self.URL,
            )
        assert result["status"] == "ok"
        assert result["variable_name"] == "slider"
        assert result["session_id"] == self.SID
        assert result["verified"] is True
        assert result["applied"] is True
        assert result["value_before"] == 3
        assert result["value_after"] == 7
        assert result.get("no_change") is None
        assert len(result["next_steps"]) >= 1
        assert any("get_variables" in s for s in result["next_steps"])
        assert any("get_cell_outputs" in s for s in result["next_steps"])

    async def test_no_change_is_reported_as_unchanged(self):
        """Same value twice: ok, but explicitly a no-op — not a new mutation."""
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(applied=False, value_before=7, value_after=7),
            stderr=[],
        ):
            result = await set_ui_value(
                "slider", value=7, session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "ok"
        assert result["verified"] is True
        assert result["applied"] is False
        assert result["no_change"] is True

    async def test_unverified_update_carries_a_warning(self):
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(
                verified=False,
                applied=None,
                value_after=None,
                readback_error="RuntimeError: kernel read-back unavailable",
            ),
            stderr=[],
        ):
            result = await set_ui_value(
                "slider", value=7, session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "ok"
        assert result["verified"] is False
        assert "read-back failed" in result["warning"]
        assert any("get_variables" in s for s in result["next_steps"])

    async def test_swallowed_kernel_rejection_becomes_an_error(self):
        """The consumer bug: ok + no change must become a real error."""
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(
                variable_name="dd",
                element_type="dropdown",
                accepted_shape="list[str]",
                applied=False,
                value_before="alpha",
                value_after="alpha",
            ),
            stderr=_REJECTION_STDERR.splitlines(),
        ):
            result = await set_ui_value(
                "dd", value=["nope"], session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "error"
        assert result["reason"] == "value_not_applied"
        assert result["element_type"] == "dropdown"
        assert result["accepted_shape"] == "list[str]"
        assert result["submitted_value"] == ["nope"]
        assert result["kernel_message"].startswith("ValueError: The option name")
        assert "['alpha', 'beta']" in result["kernel_message"]
        assert result["value_after"] == "alpha"  # unchanged, stated plainly
        assert result["next_steps"]

    async def test_on_change_failure_on_an_unchanged_value_is_not_value_not_applied(
        self,
    ):
        """T13 residual: the handler ran on a value the element already held.

        Nothing was rejected — the element accepted the value (it already held
        it) and then its own ``on_change`` handler raised. Reporting
        ``value_not_applied`` tells the caller to re-send the value, which
        cannot help; the handler is what failed.
        """
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(
                variable_name="slider",
                element_type="slider",
                accepted_shape="int | float",
                applied=False,
                value_before=1,
                value_after=1,
            ),
            stderr=_HANDLER_STDERR_NO_MOVE.splitlines(),
        ):
            result = await set_ui_value(
                "slider", value=1, session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "error"
        assert result["reason"] == "on_change_failed"
        assert result["applied"] is False
        assert result["no_change"] is True
        assert result["handler_ran"] is True
        assert result["kernel_message"] == "ValueError: boom from on_change"
        assert result["value_before"] == 1 and result["value_after"] == 1
        # The message must carry the handler's own failure, and no next step
        # may tell the caller to re-send the value.
        assert "boom from on_change" in result["message"]
        assert "Re-send" not in " ".join(result["next_steps"])

    async def test_unverified_readback_with_a_handler_failure_stays_truthful(self):
        """No read-back: say so, and still name the handler as the failure site."""
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(
                verified=False,
                applied=None,
                value_after=None,
                readback_error="RuntimeError: kernel read-back unavailable",
            ),
            stderr=_HANDLER_STDERR_NO_MOVE.splitlines(),
        ):
            result = await set_ui_value(
                "slider", value=1, session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "error"
        assert result["reason"] == "on_change_failed"
        assert result["applied"] is None  # unknown, not False
        assert result["handler_ran"] is True
        # The message must not claim a read-back confirmation that never happened.
        assert "could not confirm" in result["message"]
        assert "read-back confirms" not in result["message"]

    async def test_truncated_stderr_falls_back_to_the_readback_rule(self):
        """A traceback whose frames were lost cannot classify the failure site.

        With no quoted call site the read-back alone decides — today's rule: a
        moved value plus a rejection is still a handler failure, an unmoved one
        stays ``value_not_applied``. Whatever happens, the fallback must never
        report a wrong success.
        """
        from marimo_inspection.tools.ui import set_ui_value

        truncated = (
            "component._update(value)\n"
            "ValueError: boom from on_change\n\n"
            "An exception was raised by a UIElement's on_change handler:"
        ).splitlines()

        with self._patch_client(
            self._ok_payload(
                applied=True,
                value_before=1,
                value_after=5,
            ),
            stderr=truncated,
        ):
            moved = await set_ui_value(
                "slider", value=5, session_id=self.SID, server_url=self.URL
            )
        assert moved["reason"] == "on_change_failed"
        assert moved["applied"] is True

        with self._patch_client(
            self._ok_payload(
                applied=False,
                value_before=1,
                value_after=1,
            ),
            stderr=truncated,
        ):
            unmoved = await set_ui_value(
                "slider", value=1, session_id=self.SID, server_url=self.URL
            )
        assert unmoved["reason"] == "value_not_applied"
        assert unmoved["applied"] is False

    async def test_unrelated_stderr_is_not_a_rejection(self):
        from marimo_inspection.tools.ui import set_ui_value

        with self._patch_client(
            self._ok_payload(), stderr=["some warning: deprecation"]
        ):
            result = await set_ui_value(
                "slider", value=7, session_id=self.SID, server_url=self.URL
            )
        assert result["status"] == "ok"

    async def test_shape_mismatch_passthrough(self):
        """An in-kernel shape refusal is returned verbatim, not re-wrapped."""
        from marimo_inspection.tools.ui import set_ui_value

        mismatch = {
            "status": "error",
            "reason": "value_shape_mismatch",
            "variable_name": "dd",
            "element_type": "dropdown",
            "accepted_shape": "list[str]",
            "submitted_value": "beta",
            "did_you_mean": ["beta"],
            "message": "…Nothing was changed.",
        }
        with self._patch_client([json.dumps(mismatch)], stderr=[]):
            result = await set_ui_value(
                "dd", value="beta", session_id=self.SID, server_url=self.URL
            )
        assert result == mismatch

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
            with self._patch_client(self._ok_payload(), stderr=[]):
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
