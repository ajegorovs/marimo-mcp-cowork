"""MCP tool handlers for live marimo UI-element interaction.

A single, deliberately narrow write tool: ``set_ui_value``. Unlike the cell
mutation tools it takes NO source code — only a ``variable_name`` (a live
kernel global resolving to a marimo UI element) and a ``value``. Arbitrary
code execution is out of scope by construction: the tool cannot be handed a
snippet to run.

Two correctness rules shape this module:

* **Nothing is coerced.** The value is applied exactly as sent. A shape the
  element cannot accept is refused with the corrected payload, never silently
  re-wrapped.
* **A flush is not proof.** marimo catches an exception raised while applying
  a UI-element value and writes the traceback to the kernel's stderr, so the
  update silently does not happen while the execution still reports success.
  The template re-reads the element's value afterwards, and this module also
  scans stderr so a rejected update becomes an error instead of a false ``ok``.
  The traceback is read for its failure *site* too: a value rejected by the
  element's conversion and a value its ``on_change`` handler raised on are
  different failures, and marimo's stderr notice does not separate them.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.session import resolve_server_url, resolve_session_id

logger = logging.getLogger(__name__)

# Markers of a UIElement value update that marimo swallowed. The kernel prints
# the traceback (ending in `<ExcType>: <message>`) followed by this notice.
_UI_UPDATE_MARKERS = (
    "component._update(value)",
    "An exception was raised by a UIElement's on_change handler",
)
_EXCEPTION_LINE = re.compile(r"^\w*(?:Error|Exception): .+$")

# The two failure sites of a UI-element update, as marimo's own traceback quotes
# them (marimo 0.24.x, `UIElement._update`): a rejected CONVERSION never assigns
# the value (`self._value = self._convert_value(value)`), while a raising
# `on_change` handler runs *after* the assignment (`self._on_change(self._value)`).
# The notice marimo writes is the SAME for both — a plain ValueError escaping
# `_convert_value` lands in the generic `except Exception` branch of
# `runtime.py::set_ui_element_value` — so the quoted call site is the only
# available discriminator, and a traceback that lost its frames falls back to
# the read-back alone.
_ON_CHANGE_CALL_SITE = "self._on_change(self._value)"
_CONVERT_CALL_SITE = "self._convert_value(value)"


async def _get_client(
    server_url: str,
    ctx: Context | None = None,
) -> MarimoClient:
    """Create a MarimoClient, using explicit server_url or the bound one."""
    url = await resolve_server_url(server_url, ctx)
    return MarimoClient(url)


async def _execute_json(client: MarimoClient, sid: str, code: str) -> tuple[dict, str]:
    """Run a snippet; return its parsed JSON payload and the stderr text.

    marimo may interleave its own status lines with our JSON result on
    stdout, so we scan the emitted lines and return the last one that decodes
    as a JSON object. A structured ``status: error`` payload from the template
    is surfaced verbatim (it is a valid dict, not an execution failure).
    stderr is returned alongside because a UI-element update rejected by
    marimo is reported there and nowhere else.
    """
    result = await client.execute(sid, code)
    stderr_text = "\n".join(result.stderr or [])
    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": stderr_text,
        }, stderr_text
    all_lines = "\n".join(result.stdout or []).split("\n")
    for line in reversed(all_lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data, stderr_text
        except json.JSONDecodeError:
            continue
    return {
        "error": "Failed to parse set_ui_value result",
        "raw_output": "\n".join(result.stdout or []),
        "stderr": stderr_text,
    }, stderr_text


def _kernel_rejection(stderr_text: str) -> str | None:
    """The exception marimo raised while applying the value, if it did.

    Returns the kernel's own exception line (e.g. the option-name ValueError
    listing the valid keys) or ``None`` when the update was not rejected.
    """
    if not stderr_text or not any(m in stderr_text for m in _UI_UPDATE_MARKERS):
        return None
    for line in reversed(stderr_text.splitlines()):
        line = line.strip()
        if not line or line.startswith("An exception was raised"):
            continue
        if _EXCEPTION_LINE.match(line):
            return line
    return (
        "the kernel rejected the value while applying it (see the kernel "
        "stderr for the traceback)"
    )


def _rejection_site(stderr_text: str) -> str | None:
    """Where marimo raised while applying the value: ``on_change`` / ``convert``.

    ``on_change`` means the element's value was assigned (the conversion
    succeeded) and its own handler then raised — the element may still end up
    unchanged, because it already held the submitted value. ``convert`` means
    the value was never assigned. ``None`` when the stderr carries no
    recognisable UI-update traceback (an unrelated warning, or a truncated
    traceback whose frames were lost) — then the read-back alone decides.
    """
    if not stderr_text or not any(m in stderr_text for m in _UI_UPDATE_MARKERS):
        return None
    if _ON_CHANGE_CALL_SITE in stderr_text:
        return "on_change"
    if _CONVERT_CALL_SITE in stderr_text:
        return "convert"
    return None


def _rejection_payload(
    variable_name: str,
    session_id: str,
    value: Any,
    data: dict,
    kernel_message: str,
    site: str | None = None,
) -> dict:
    """Error payload for a UI update marimo raised on while applying it.

    marimo writes the same stderr notice for both failure points of a UI
    update, but they are not the same failure:

    * a rejected CONVERSION (an unknown dropdown key) raises inside
      ``_convert_value`` *before* the element's value is assigned, so the
      element is genuinely unchanged;
    * an element ``on_change`` handler raises *after* the assignment
      (``_update`` has no equality shortcut), so the value moved — or did not
      need to move, because the element already held it — and only the
      callback failed.

    Neither source alone can decide it. The read-back (``data``: ``verified`` /
    ``applied`` / ``value_before`` / ``value_after``) says whether the value
    moved, which cannot separate "the conversion was rejected" from "it already
    held the value and the handler then raised"; the kernel traceback says
    ``site`` — *where* marimo raised — but not what the element ended up
    holding. Both are used: ``site`` picks the reason code, the read-back fills
    in ``applied`` / ``no_change``, and with no usable ``site`` the read-back
    alone decides (today's rule). No message claims the element was unchanged
    when its value actually moved, and none tells the caller to re-send a value
    the kernel accepted.
    """
    before = data.get("value_before")
    after = data.get("value_after")
    element_type = data.get("element_type")
    verified = bool(data.get("verified"))
    applied = bool(data.get("applied"))
    common = {
        "status": "error",
        "variable_name": variable_name,
        "session_id": session_id,
        "element_type": element_type,
        "accepted_shape": data.get("accepted_shape"),
        "submitted_value": value,
        "kernel_message": kernel_message,
        "value_before": before,
        "value_after": after,
    }

    if site == "on_change":
        # The value was assigned (the conversion succeeded) and the element's
        # own handler then raised. `applied` may be False: the element already
        # held the value, so there was nothing to move.
        payload: dict[str, Any] = {
            **common,
            "reason": "on_change_failed",
            "applied": applied if verified else None,
            "handler_ran": True,
            "next_steps": [
                (
                    "The element ACCEPTED the value — nothing was rejected — so "
                    "re-sending it cannot help: it is the handler's own side "
                    "effects (and the dependent cells it re-ran) that failed. "
                    "Fix the handler in the widget's cell, re-run it, then "
                    "verify the downstream effects with get_variables, "
                    "get_cell_outputs, and get_errors."
                ),
            ],
        }
        if not verified:
            payload["message"] = (
                f"The kernel reported an error while applying {value!r} to "
                f"'{variable_name}' ({element_type}) and the read-back could not "
                f"confirm the element's value; the traceback shows the "
                f"element's own on_change handler raised after the value was "
                f"assigned: {kernel_message}"
            )
        elif applied:
            payload["message"] = (
                f"marimo applied {value!r} to '{variable_name}' "
                f"({element_type}) — the read-back confirms {before!r} -> "
                f"{after!r} — but the element's own on_change handler raised: "
                f"{kernel_message}"
            )
        else:
            payload["no_change"] = True
            payload["message"] = (
                f"'{variable_name}' ({element_type}) already held {value!r}, so "
                f"the value did not move ({before!r} -> {after!r}) — nothing "
                f"was rejected — but the element's own on_change handler raised "
                f"while handling it: {kernel_message}"
            )
        return payload

    if data.get("verified") and applied:
        return {
            **common,
            "reason": "on_change_failed",
            "applied": True,
            "handler_ran": True,
            "message": (
                f"marimo applied {value!r} to '{variable_name}' "
                f"({element_type}) — the read-back confirms {before!r} -> "
                f"{after!r} — but the element's own on_change handler raised: "
                f"{kernel_message}"
            ),
            "next_steps": [
                (
                    "The value change DID apply, so the widget holds its new "
                    "value; it is the handler's own side effects (and the "
                    "dependent cells it re-ran) that failed. Fix the handler "
                    "in the widget's cell, re-run it, then verify the "
                    "downstream effects with get_variables, get_cell_outputs, "
                    "and get_errors."
                ),
            ],
        }

    if verified:
        message = (
            f"The kernel reported an error while applying {value!r} to "
            f"'{variable_name}' ({element_type}), and the read-back shows the "
            f"element's value did NOT move ({before!r} -> {after!r}): "
            f"{kernel_message}"
        )
    else:
        message = (
            f"The kernel reported an error while applying {value!r} to "
            f"'{variable_name}' ({element_type}) and the read-back could not "
            f"confirm the element's value: {kernel_message}"
        )
    return {
        **common,
        "reason": "value_not_applied",
        "applied": applied,
        "message": message,
        "next_steps": [
            (
                "Re-send the value in the shape the element accepts "
                f"({data.get('accepted_shape') or 'see the widget docs'}) — "
                "for a dropdown or multiselect, option keys go inside a list. "
                "Do not assume the interaction happened."
            ),
        ],
    }


async def set_ui_value(
    variable_name: str,
    value: Any,
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Set the value of a live marimo UI element, by its variable name.

    The kernel global named by ``variable_name`` must resolve to a marimo UI
    element (e.g. ``mo.ui.slider``, ``mo.ui.dropdown``, ``mo.ui.text``). Its
    value is replaced with ``value``, triggering reactive re-execution the
    same way a user interaction would.

    Value shape is per widget and is NEVER coerced: a slider/text takes a
    scalar, a dropdown takes its option key inside a one-element list (for
    example ``["beta"]``), a multiselect takes the list of selected keys, a
    range_slider takes a two-element list, a checkbox takes a bool. The
    element's own declaration decides; a shape mismatch is refused before
    anything is applied and the response carries the corrected payload in
    ``did_you_mean``.

    This tool accepts NO source code: it exists for widget interaction only,
    not for arbitrary code execution. The update is flushed on code-mode
    context exit, the kernel then re-runs dependent cells, and the element's
    value is read back before returning — ``status: ok`` with
    ``verified: true`` means the element's own value was observed to move (or
    was already equal), not merely that the update was queued. A value marimo
    rejected is reported as an error, never as success.

    Args:
        variable_name: Name of the live kernel global holding the UI element.
        value: New value for the element, in the shape that element accepts.
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        Dict with ``status``. On a missing/non-UI variable or a shape mismatch
        the kernel template returns a structured error explaining exactly what
        went wrong. A value the kernel raised on while applying it is an error
        too, under a reason code that names the failure *site* (read from the
        kernel traceback) with the read-back filling in whether the value
        moved: ``value_not_applied`` when the element's conversion rejected the
        value before assigning it; ``on_change_failed`` when the value was
        assigned and the element's own ``on_change`` handler raised — with
        ``applied: true`` when the value moved, or ``applied: false`` +
        ``no_change: true`` when the element already held it and only the
        handler ran and failed. On success the payload carries ``applied``,
        ``verified``, and the element's value before and after; downstream
        re-runs are NOT awaited, so confirm their effects with the read tools.
    """
    if not variable_name:
        return {
            "status": "error",
            "error": "variable_name is required",
        }

    sid = await resolve_session_id(session_id, ctx)
    client = await _get_client(server_url, ctx)
    session = await client.resolve_session(session_id=sid)
    sid = session.session_id
    if ctx:
        await ctx.info(f"Setting UI element '{variable_name}' in session {sid}...")

    from marimo_inspection.templates.ui import build_set_ui_value_template

    data, stderr_text = await _execute_json(
        client, sid, build_set_ui_value_template(variable_name, value)
    )
    if "error" in data:
        # Execution failure or JSON parse failure — pass verbatim.
        return data
    if data.get("status") == "error":
        # Structured kernel rejection: unknown name, non-UI name, shape
        # mismatch. Each carries its own actionable message.
        return data

    # marimo writes a traceback to the kernel's stderr for BOTH failure points
    # of a UI update: a rejected CONVERSION (unknown dropdown key) raises
    # BEFORE the element's value is assigned, an `on_change` handler raises
    # AFTER it (with no equality shortcut, so even a value the element already
    # holds runs the handler). The traceback's own call site says which one it
    # was; the template's read-back says whether the value moved. Reporting
    # either as a plain `ok` is the silent-no-op bug, and reporting a handler
    # failure as "not applied" contradicts what actually happened.
    rejection = _kernel_rejection(stderr_text)
    if rejection is not None:
        return _rejection_payload(
            variable_name, sid, value, data, rejection, _rejection_site(stderr_text)
        )

    verified = bool(data.get("verified"))
    applied = bool(data.get("applied"))
    payload: dict[str, Any] = {
        "status": "ok",
        "variable_name": variable_name,
        "session_id": sid,
        "element_type": data.get("element_type"),
        "accepted_shape": data.get("accepted_shape"),
        "verified": verified,
        "applied": applied,
        "value_before": data.get("value_before"),
        "value_after": data.get("value_after"),
    }

    if not verified:
        payload["warning"] = (
            "The update was queued but the kernel read-back failed "
            f"({data.get('readback_error')}); this call could not confirm the "
            "element's new value."
        )
        payload["next_steps"] = [
            (
                "Confirm the element's value with get_variables (the selection "
                "is nested as variables[name][value][value]) and its dependent "
                "cells with get_cell_outputs / get_errors before declaring the "
                "interaction complete."
            ),
        ]
        return payload

    if applied:
        payload["next_steps"] = [
            (
                "Confirmed by kernel read-back: the element's value moved from "
                f"{data.get('value_before')!r} to {data.get('value_after')!r}. "
                "Dependent cells re-run asynchronously and are NOT awaited — "
                "verify their effects with get_variables, get_cell_outputs, and "
                "get_errors before declaring the interaction complete."
            ),
        ]
    else:
        payload["no_change"] = True
        payload["next_steps"] = [
            (
                "Confirmed by kernel read-back: the element already held this "
                "value, so nothing changed. If the interaction was meant to "
                "trigger a re-run of dependent cells, verify them with "
                "get_variables / get_cell_outputs."
            ),
        ]
    return payload


__all__ = ["set_ui_value"]
