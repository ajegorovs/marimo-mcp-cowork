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
        session_id: Optional session id (auto-bound if omitted).
        server_url: Server URL override.

    Returns:
        Dict with ``status``. On a missing/non-UI variable or a shape mismatch
        the kernel template returns a structured error explaining exactly what
        went wrong. On success the payload carries ``applied``,
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

    # A rejected update leaves the value unchanged AND prints a traceback to
    # the kernel's stderr. Reporting `ok` here is the silent-no-op bug.
    rejection = _kernel_rejection(stderr_text)
    if rejection is not None:
        return {
            "status": "error",
            "reason": "value_not_applied",
            "variable_name": variable_name,
            "session_id": sid,
            "element_type": data.get("element_type"),
            "accepted_shape": data.get("accepted_shape"),
            "submitted_value": value,
            "kernel_message": rejection,
            "value_before": data.get("value_before"),
            "value_after": data.get("value_after"),
            "message": (
                f"The kernel rejected the value {value!r} for "
                f"'{variable_name}' ({data.get('element_type')}), so the element "
                f"was NOT changed: {rejection}"
            ),
            "next_steps": [
                (
                    "Re-send the value in the shape the element accepts "
                    f"({data.get('accepted_shape') or 'see the widget docs'}) — "
                    "for a dropdown or multiselect, option keys go inside a list. "
                    "Do not assume the interaction happened."
                ),
            ],
        }

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
