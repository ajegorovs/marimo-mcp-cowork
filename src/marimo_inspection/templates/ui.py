"""Scratchpad template for setting a live marimo UI element's value.

``set_ui_value`` is a *narrow* widget-interaction template: it takes a
variable name that resolves to a live kernel global which is a marimo UI
element, plus the new value. The value is embedded as a JSON literal so its
shape is preserved exactly (a scalar stays a scalar; a list of keys stays a
list — no coercion), and the element lookup is by name from ``ctx.globals``
— never by evaluating arbitrary source. Missing or non-UI names fail with a
clear, actionable payload rather than a traceback dump.
"""

from __future__ import annotations

import json
from typing import Any


def build_set_ui_value_template(variable_name: str, value: Any) -> str:
    """Build a scratchpad snippet that sets a marimo UI element's value.

    Args:
        variable_name: Name of a live kernel global resolving to a marimo UI
            element (e.g. ``mo.ui.slider`` / ``mo.ui.dropdown`` / ``mo.ui.text``).
        value: The new value. Serialized with ``json.dumps`` at build time and
            reconstructed in-kernel via ``json.loads``, so its shape is
            preserved exactly (a scalar stays a scalar; a list of keys stays a
            list; ``None``/bool round-trip) — no coercion, no list-wrapping.

    Returns:
        Python code string that runs in the scratchpad, returning a JSON
        payload or a clear error when the name is missing or not a UI element.
    """
    name_json = json.dumps(variable_name)
    value_literal = json.dumps(json.dumps(value))
    return _TEMPLATE.replace("__UI_VARIABLE_NAME_JSON__", name_json).replace(
        "__UI_VALUE_JSON_LITERAL__", value_literal
    )


_TEMPLATE = """\
import json
import marimo._code_mode as cm


def _is_ui_element(obj):
    '''True for marimo UI elements. Defensive against private-API drift:
    prefer the UIElement isinstance check, fall back to module+value probe.'''
    try:
        from marimo._plugins.ui._core.ui_element import UIElement
        if obj is not None and isinstance(obj, UIElement):
            return True
    except Exception:
        pass
    try:
        if obj is None:
            return False
        mod = type(obj).__module__
        return hasattr(obj, "value") and mod.startswith("marimo._plugins.ui")
    except Exception:
        return False


def _ui_globals(g):
    '''Names in g that resolve to a marimo UI element.'''
    out = []
    for n, v in g.items():
        if not n.startswith("_") and _is_ui_element(v):
            out.append(n)
    return sorted(out)


async def _run():
    name = __UI_VARIABLE_NAME_JSON__
    value = json.loads(__UI_VALUE_JSON_LITERAL__)
    async with cm.get_context() as ctx:
        g = ctx.globals
        if name not in g:
            return json.dumps({
                "status": "error",
                "message": "Variable " + json.dumps(name) + " is not a live kernel global. "
                           "Current UI element globals: " + json.dumps(_ui_globals(g)),
            })
        element = g[name]
        if not _is_ui_element(element):
            return json.dumps({
                "status": "error",
                "message": "Variable " + json.dumps(name) + " resolves to a "
                           + type(element).__name__ + ", not a marimo UI element. "
                           "set_ui_value accepts only marimo UI elements (e.g. "
                           "mo.ui.slider/dropdown/text). Current UI element globals: "
                           + json.dumps(_ui_globals(g)),
                "datatype": type(element).__name__,
                "ui_element_globals": _ui_globals(g),
            })
        ctx.set_ui_value(element, value)
        return json.dumps({"status": "ok", "variable_name": name})


print(await _run())
"""


__all__ = ["build_set_ui_value_template"]
