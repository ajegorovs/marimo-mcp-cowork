"""Scratchpad template for setting a live marimo UI element's value.

``set_ui_value`` is a *narrow* widget-interaction template: it takes a
variable name that resolves to a live kernel global which is a marimo UI
element, plus the new value. Three properties make it safe for agent use:

1. **No source is evaluated.** The element is looked up by name in
   ``ctx.globals`` and the value is embedded as a JSON literal, so its shape
   survives exactly (scalar stays scalar, list stays list) — never coerced.
2. **Shape mismatch is refused before the update is queued.** The expected
   input shape is derived from the element's own declaration — the first
   parameter of its ``UIElement[...]`` generic base (``list[str]`` for a
   dropdown, ``int | float`` for a slider) — never hard-coded per widget
   type. The refusal names the shape and, when the submitted scalar is one
   list away from the correct form, the exact corrected payload.
3. **The update is verified, not assumed.** Queued updates flush on code-mode
   context exit, and marimo *swallows* a rejected update (the traceback goes
   to the kernel's stderr, the flush still reports success). A second context
   re-reads the element's value afterwards and reports whether it actually
   moved, so ``status: ok`` never means merely "the update was queued".

Read the element's value back in the payload, not just the flush.
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
            The template refuses a shape the element cannot accept and reports
            the corrected form instead of applying it.

    Returns:
        Python code string that runs in the scratchpad. It returns a JSON
        payload: an ``ok`` payload carrying ``applied``/``verified`` plus the
        before/after values, or a structured error when the name is missing,
        is not a UI element, or the value shape does not match the element.
    """
    name_json = json.dumps(variable_name)
    value_literal = json.dumps(json.dumps(value))
    return _TEMPLATE.replace("__UI_VARIABLE_NAME_JSON__", name_json).replace(
        "__UI_VALUE_JSON_LITERAL__", value_literal
    )


_TEMPLATE = """\
import json
import types
import typing

import marimo._code_mode as cm

_SCALAR_SHAPES = (str, int, float, bool)


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


def _accepted_input_shape(element):
    '''Declared frontend (input) shape of a UI element, or None.

    The FIRST type parameter of the element's ``UIElement[...]`` generic base
    is the shape the widget's frontend sends (``list[str]`` for a dropdown,
    ``int | float`` for a slider, ``dict[str, Any]`` for a table). Derived
    from the declaration, never hard-coded per widget type; None when the
    declaration is unreadable or unbound (~TypeVar) - then no shape guard is
    applied and the read-back verification alone decides the outcome.'''
    try:
        from marimo._plugins.ui._core.ui_element import UIElement
    except Exception:
        return None
    try:
        for klass in type(element).__mro__:
            bases = getattr(klass, "__orig_bases__", None)
            if not bases:
                bases = getattr(klass, "__bases__", ())
            for base in bases:
                if typing.get_origin(base) is UIElement:
                    args = typing.get_args(base)
                    if args:
                        return args[0]
    except Exception:
        return None
    return None


def _shape_text(shape):
    '''Readable name for a type declaration (None stays None).'''
    if shape is None:
        return None
    try:
        return (
            str(shape)
            .replace("typing.", "")
            .replace("<class '", "")
            .replace("'>", "")
        )
    except Exception:
        return None


def _union_args(shape):
    '''Args of a Union (typing.Union[X, Y] or X | Y), else None.'''
    if typing.get_origin(shape) in (typing.Union, types.UnionType):
        return typing.get_args(shape)
    return None


def _is_list_shape(shape):
    '''True when the element's declared input is a list/tuple.'''
    if shape is None:
        return False
    return typing.get_origin(shape) in (list, tuple)


def _is_scalar_shape(shape):
    '''True when the declared input is a concrete scalar, or a union of
    concrete scalars (None ignored). Opaque declarations (Any, ~TypeVar,
    custom classes, dicts, unions containing lists) are never guessed.'''
    if shape is None:
        return False
    if shape in _SCALAR_SHAPES:
        return True
    args = _union_args(shape)
    if args is None:
        return False
    members = [a for a in args if a is not type(None)]
    return bool(members) and all(m in _SCALAR_SHAPES for m in members)


def _jsonable(value, depth=0):
    '''Best-effort JSON-safe rendering of a widget value (never raises).'''
    try:
        json.dumps(value)
        return value
    except Exception:
        pass
    try:
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v, depth + 1) for v in list(value)[:20]]
        return str(value)[:500]
    except Exception:
        return None


def _text(value):
    '''Compact JSON text for a value, safe for unusual types.'''
    try:
        return json.dumps(_jsonable(value))
    except Exception:
        return repr(value)[:200]


def _same(a, b):
    '''Equality that survives numpy arrays / odd __eq__ (never raises).'''
    try:
        return bool(a == b)
    except Exception:
        try:
            return repr(a) == repr(b)
        except Exception:
            return False


async def _run():
    name = __UI_VARIABLE_NAME_JSON__
    value = json.loads(__UI_VALUE_JSON_LITERAL__)
    async with cm.get_context() as ctx:
        g = ctx.globals
        if name not in g:
            return json.dumps({
                "status": "error",
                "reason": "unknown_variable",
                "message": "Variable " + json.dumps(name) + " is not a live kernel global. "
                           "Current UI element globals: " + json.dumps(_ui_globals(g)),
            })
        element = g[name]
        if not _is_ui_element(element):
            return json.dumps({
                "status": "error",
                "reason": "not_a_ui_element",
                "message": "Variable " + json.dumps(name) + " resolves to a "
                           + type(element).__name__ + ", not a marimo UI element. "
                           "set_ui_value accepts only marimo UI elements (e.g. "
                           "mo.ui.slider/dropdown/text). Current UI element globals: "
                           + json.dumps(_ui_globals(g)),
                "datatype": type(element).__name__,
                "ui_element_globals": _ui_globals(g),
            })

        element_type = type(element).__name__
        shape = _accepted_input_shape(element)
        shape_text = _shape_text(shape)
        submitted_is_list = isinstance(value, (list, tuple))

        # Refuse a shape the element cannot accept BEFORE queueing anything:
        # marimo drops a rejected update silently (stderr only), so guessing
        # here would only reproduce the silent-no-op failure this guards.
        if _is_list_shape(shape) and not submitted_is_list:
            return json.dumps({
                "status": "error",
                "reason": "value_shape_mismatch",
                "variable_name": name,
                "element_type": element_type,
                "accepted_shape": shape_text,
                "submitted_value": _jsonable(value),
                "did_you_mean": [value],
                "message": "The " + element_type + " element " + json.dumps(name)
                           + " takes a list-shaped value (" + str(shape_text)
                           + "); received the scalar " + _text(value)
                           + ". Re-send it as a one-element list, e.g. "
                           + _text([value]) + " - a dropdown or multiselect takes its "
                           "option key(s) inside a list. Nothing was changed.",
            })
        if _is_scalar_shape(shape) and submitted_is_list:
            suggestion = value[0] if len(value) == 1 else None
            return json.dumps({
                "status": "error",
                "reason": "value_shape_mismatch",
                "variable_name": name,
                "element_type": element_type,
                "accepted_shape": shape_text,
                "submitted_value": _jsonable(value),
                "did_you_mean": suggestion,
                "message": "The " + element_type + " element " + json.dumps(name)
                           + " takes a scalar value (" + str(shape_text)
                           + "); received a list. Re-send the bare value"
                           + (" - here, " + _text(suggestion) if suggestion is not None else "")
                           + ". Nothing was changed.",
            })

        before = element.value
        ctx.set_ui_value(element, value)
    # Leaving the context flushes the queued update and triggers reactive
    # re-runs. A flush is NOT proof of application: marimo catches a rejected
    # update (e.g. an unknown dropdown key) and only writes it to stderr.

    verified = True
    readback_error = None
    try:
        async with cm.get_context() as verify_ctx:
            element_after = verify_ctx.globals.get(name)
            if element_after is None:
                raise LookupError(json.dumps(name) + " is gone after the update")
            after = element_after.value
    except Exception as exc:
        verified = False
        readback_error = type(exc).__name__ + ": " + str(exc)[:300]
        after = before

    return json.dumps({
        "status": "ok",
        "variable_name": name,
        "element_type": element_type,
        "accepted_shape": shape_text,
        "verified": verified,
        "readback_error": readback_error,
        "applied": (not _same(before, after)) if verified else None,
        "value_before": _jsonable(before),
        "value_after": _jsonable(after) if verified else None,
    })


print(await _run())
"""


__all__ = ["build_set_ui_value_template"]
