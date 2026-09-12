"""MCP tool handlers for live marimo UI-element interaction.

A single, deliberately narrow write tool: ``set_ui_value``. Unlike the cell
mutation tools it takes NO source code — only a ``variable_name`` (a live
kernel global resolving to a marimo UI element) and a ``value``. Arbitrary
code execution is out of scope by construction: the tool cannot be handed a
snippet to run.

Three correctness rules shape this module:

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
* **An element value is not the interaction.** A ``mo.ui.button``'s element
  value is its ``on_click`` return, so a handler that only sets state leaves
  ``.value`` unchanged and a naive no-change report says "nothing happened"
  about a click that landed. ``mo.ui.run_button`` reuses the same component
  but has no ``on_click``: a nonzero counter sets its value to ``True`` and
  the runtime resets it to ``False`` once the dependent cells finish, so its
  read-back may also show an unchanged (``False``) value for a click that
  landed. marimo's *frontend* value is the element's click counter and is
  assigned before the conversion runs, so the template reports it before and
  after; a nonzero counter that moved to the submitted value is delivery
  evidence (the T20 fix). ``0`` is the initialization sentinel — marimo's
  button conversion processes no click for it — and a counter that already
  held the submitted value makes the invocation *unknown*, not true and not
  false, because the read-back cannot see a repeated click. The handler's
  arbitrary side effects are never verified by the read-back: button payloads
  report ``side_effects_verified: false`` and say so.
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
# `on_click handler for button` is a THIRD marker: marimo 0.24's
# `button._convert_value` catches its handler's exception itself and writes it
# before the traceback, so `_update` never raises and neither generic notice
# above appears — the failure is invisible without this marker.
_ON_CLICK_MARKER = "on_click handler for button"
_UI_UPDATE_MARKERS = (
    "component._update(value)",
    "An exception was raised by a UIElement's on_change handler",
    _ON_CLICK_MARKER,
)
_EXCEPTION_LINE = re.compile(r"^\w*(?:Error|Exception): .+$")

# The failure sites of a UI-element update, as marimo's own traceback quotes
# them (marimo 0.24.x, `UIElement._update`): a rejected CONVERSION never assigns
# the value (`self._value = self._convert_value(value)`), while a raising
# `on_change` handler runs *after* the assignment (`self._on_change(self._value)`).
# The notice marimo writes is the SAME for both — a plain ValueError escaping
# `_convert_value` lands in the generic `except Exception` branch of
# `runtime.py::set_ui_element_value` — so the quoted call site is the only
# available discriminator, and a traceback that lost its frames falls back to
# the read-back alone.
#
# A button's `on_click` failure is different again: the exception is caught
# INSIDE `button._convert_value`, which writes `_ON_CLICK_MARKER` before the
# (frame-truncated) traceback and then returns None. The marker is unique, so
# it is the site discriminator.
_ON_CHANGE_CALL_SITE = "self._on_change(self._value)"
_CONVERT_CALL_SITE = "self._convert_value(value)"

# Element types whose frontend value is a click counter rather than the element
# value. Their element-value semantics differ: `button`'s value is its
# `on_click` return, while `run_button` has no `on_click` — a nonzero counter
# sets its value True and the runtime resets it False once its dependents
# finish. The counter read-back is the shared delivery evidence.
_BUTTON_TYPES = ("button", "run_button")
# Only `button` has an `on_click` handler, so only it can produce marimo's
# `on_click handler for button` marker.
_ON_CLICK_ELEMENT_TYPE = "button"


def _is_button(data: dict) -> bool:
    """Whether the updated element is a ``button`` or ``run_button``."""
    return data.get("element_type") in _BUTTON_TYPES


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
    """Where marimo raised while applying the value: ``on_click`` / ``on_change`` / ``convert``.

    ``on_click`` means a button's own handler raised — marimo 0.24's
    ``button._convert_value`` catches it and writes the unique
    ``on_click handler for button`` marker, so no call-site inspection is
    needed (and the generic ``on_change`` marker is never written for it).
    The marker is only *recognised* here; whether it can be attributed to the
    target is decided by :func:`_attributable_on_click` (a ``run_button`` or
    any other element, or a ``0`` counter, can never own it).
    ``on_change`` means the element's value was assigned (the conversion
    succeeded) and its own handler then raised — the element may still end up
    unchanged, because it already held the submitted value. ``convert`` means
    the value was never assigned. ``None`` when the stderr carries no
    recognisable UI-update traceback (an unrelated warning, or a truncated
    traceback whose frames were lost) — then the read-back alone decides.
    """
    if not stderr_text:
        return None
    if _ON_CLICK_MARKER in stderr_text:
        return "on_click"
    if not any(m in stderr_text for m in _UI_UPDATE_MARKERS):
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

    if site == "on_click":
        # A BUTTON's on_click handler raised. marimo's own `_convert_value`
        # caught it (marker + traceback to stderr) and returned None, so the
        # update was otherwise treated as successful: the click WAS delivered
        # and the handler WAS entered. Side effects it applied before raising
        # are partially applied at best, and the read-back cannot verify them.
        payload: dict[str, Any] = {
            **common,
            "reason": "on_click_failed",
            "applied": applied if verified else None,
            "handler_ran": True,
            "handler_invoked": True,
            "side_effects_verified": False,
            "next_steps": [
                (
                    "The button's on_click handler ran and raised, so any side "
                    "effects it applies are at best partially applied — "
                    "marimo caught the exception inside the button's own "
                    "conversion and carried on. Submitting the same counter "
                    "again cannot repair a handler that raises. Fix the "
                    "handler in the widget's cell, re-run it, then verify the "
                    "state it was meant to change with get_variables, "
                    "get_cell_outputs, and get_errors."
                ),
            ],
        }
        if not verified:
            payload["message"] = (
                f"The kernel reported an error from the on_click handler of "
                f"'{variable_name}' ({element_type}) while applying the click "
                f"counter {value!r}; the handler ran and raised, and may have "
                f"applied partial side effects before raising. The read-back "
                f"could not confirm the element's state: {kernel_message}"
            )
        elif applied:
            payload["message"] = (
                f"marimo delivered the click to '{variable_name}' "
                f"({element_type}) — the read-back shows {before!r} -> "
                f"{after!r} — but its on_click handler ran and raised: "
                f"{kernel_message}. The handler may have applied partial side "
                f"effects before raising; this call does not verify them."
            )
        else:
            payload["message"] = (
                f"marimo delivered the click to '{variable_name}' "
                f"({element_type}) and its on_click handler ran and raised: "
                f"{kernel_message}. The element's own value did not move "
                f"({before!r} -> {after!r}) — a handler that returns nothing "
                f"leaves it unchanged, so that is the element's normal shape, "
                f"not evidence the handler was skipped — and the handler may "
                f"have applied partial side effects before raising; this call "
                f"does not verify them."
            )
        return payload

    if site == "unattributed":
        # stderr carried the button marker but it cannot belong to THIS update:
        # a `run_button` (which has no on_click), any other element (an
        # on_change handler can print the literal marker text), or a `0`
        # submitted counter, for which marimo never calls on_click. Fail
        # conservatively as a generic UI-update error — never a false on_click
        # attribution, never a `handler_invoked` claim.
        payload: dict[str, Any] = {
            **common,
            "reason": "ui_update_failed",
            "applied": applied if verified else None,
            "message": (
                f"The kernel reported an error while applying {value!r} to "
                f"'{variable_name}' ({element_type}), but the failure could not "
                f"be attributed to that element's own handler: marimo's button "
                f"on_click marker only belongs to a 'button' clicked with a "
                f"nonzero counter, and this update is not that. The "
                f"interaction's outcome is unconfirmed — do not assume it "
                f"happened. Kernel message: {kernel_message}"
            ),
            "next_steps": [
                (
                    "Inspect get_errors and the cell's console_stderr for the "
                    "traceback, and read the element with get_variables before "
                    "deciding what happened — this call could not attribute "
                    "the failure to the target element's handler."
                ),
            ],
        }
        if _is_button(data):
            payload["side_effects_verified"] = False
        return payload

    if site == "on_change":
        # The value was assigned (the conversion succeeded) and the element's
        # own handler then raised. `applied` may be False: the element already
        # held the value, so there was nothing to move — or, for a button, the
        # element's own value is not the interaction at all (a `button`'s is the
        # on_click return, a `run_button`'s is reset to False), so an unchanged
        # value must NOT be reported as "already held / nothing changed".
        is_button = _is_button(data)
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
        if is_button:
            # A button payload never claims its arbitrary side effects were
            # verified.
            payload["side_effects_verified"] = False
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
        elif is_button:
            payload["message"] = (
                f"The element's own on_change handler raised for "
                f"'{variable_name}' ({element_type}): {kernel_message}. The "
                f"element's own value did not move ({before!r} -> {after!r}), "
                f"but a button's value is not the interaction — a button's is "
                f"the on_click return and a run_button's is reset to False "
                f"after its dependents run — so that is its normal shape here, "
                f"not a 'nothing happened' result. The handler's side effects "
                f"are not verified by this call."
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
        payload = {
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
        if _is_button(data):
            payload["side_effects_verified"] = False
        return payload

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
    payload = {
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
    if _is_button(data):
        payload["side_effects_verified"] = False
    return payload


def _same_json(a: Any, b: Any) -> bool:
    """Equality for JSON-safe values (the template guarantees JSON-safety)."""
    return bool(a == b)


def _attributable_on_click(data: dict, value: Any) -> bool:
    """Whether stderr's button marker can belong to THIS update (T20 guard).

    marimo writes ``on_click handler for button`` from ``button._convert_value``
    only when the target is a ``button`` (never a ``run_button``, which has no
    ``on_click``) and only for a nonzero counter (``0`` returns the initial
    value without calling ``on_click``). Any other element's handler — or a
    literal string one printed — can put the marker text in stderr, so the
    marker alone never earns an ``on_click`` attribution. Only the target's own
    element type plus a nonzero submitted counter make it attributable. (A
    literal copy of the marker text written by a real ``button``'s other
    handler, with a nonzero counter, is indistinguishable from a genuine
    ``on_click`` failure without parsing the traceback; that residue is the
    narrowest case the marker can still cover, and it is never claimed for any
    other element or for the ``0`` sentinel.)
    """
    return (
        data.get("element_type") == _ON_CLICK_ELEMENT_TYPE
        and not _same_json(value, 0)
    )


def _button_interaction_payload(value: Any, data: dict, verified: bool) -> dict:
    """Truthful interaction evidence for a ``button`` / ``run_button`` (T20).

    Both element types carry a frontend **click counter** — 0 during
    initialization, then 1, 2, 3, ... — which ``UIElement._update`` assigns
    *before* the conversion runs, so the counter read-back is the shared
    delivery evidence. Their element ``value`` differs:

    * ``button`` — ``value`` is the ``on_click`` return (``None`` for a handler
      that only sets state), so a click may leave it unchanged;
    * ``run_button`` — there is no ``on_click``: a nonzero counter makes the
      conversion set ``value`` to ``True``, and the runtime resets it to
      ``False`` after the dependent cells run, so the read-back may also show
      an unchanged (``False``) value for a click that landed.

    The counter tri-state is the same for both:

    * submitted ``0`` — the initialization sentinel: marimo's conversion
      returns the initial value / ``False`` and processes no click, so
      ``handler_invoked`` is ``False`` and no click was delivered;
    * nonzero, counter moved to the submitted value — the update reached the
      element and marimo ran its conversion (``handler_invoked: True``);
    * nonzero, counter already at the submitted value — this call cannot tell
      whether the click was processed again (``handler_invoked: None``), even
      though marimo's runtime does process a repeated nonzero counter. Neither
      "delivered" nor "skipped" may be claimed.

    Arbitrary side effects are never verified here — the read-back only sees
    the element — so ``side_effects_verified`` is always ``False`` and every
    path carries verification guidance. The ``no_change`` / "already held the
    value" framing that is correct for a slider is deliberately not used for a
    button: an unchanged element value there is the normal shape, not evidence
    that nothing happened.
    """
    submitted = value
    element_type = data.get("element_type")
    variable_name = data.get("variable_name")
    is_run_button = element_type == "run_button"
    before = data.get("value_before")
    after = data.get("value_after")
    f_before = data.get("frontend_value_before")
    f_after = data.get("frontend_value_after")

    # Type-aware wording: never claim a run_button has an on_click handler or
    # gets its value from one.
    type_label = "run_button" if is_run_button else "button"
    if is_run_button:
        value_shape = (
            "a run_button's value is True only while its dependent cells run "
            "and is reset to False once they finish, so an unchanged (False) "
            "value is its normal post-run shape"
        )
        invoked_clause = (
            "marimo ran the run_button's update (its value set True for the "
            "dependent re-run, then reset to False once they finished)"
        )
        sentinel_clause = (
            "marimo's run_button conversion returns False and processes no "
            "click"
        )
        unknown_question = "this click was processed"
        repeated_clause = (
            "marimo's runtime does process a repeated nonzero counter for a "
            "run_button"
        )
    else:
        value_shape = (
            "a button's value is its on_click return, so a handler that "
            "returns nothing leaves it unchanged"
        )
        invoked_clause = "marimo's button conversion invoked its on_click handler"
        sentinel_clause = (
            "marimo's button conversion returns the initial value and never "
            "calls on_click"
        )
        unknown_question = "its on_click handler was invoked"
        repeated_clause = (
            "marimo's runtime does invoke the on_click handler for a repeated "
            "nonzero counter"
        )

    fields: dict[str, Any] = {
        "frontend_value_before": f_before,
        "frontend_value_after": f_after,
        "side_effects_verified": False,
    }

    if _same_json(submitted, 0):
        # The initialization sentinel: marimo's conversion returns the initial
        # value (button) / False (run_button) for 0 and processes no click.
        fields.update(
            handler_invoked=False,
            click_delivered=False,
            warning=(
                f"The {type_label} was not clicked: a {type_label}'s frontend "
                f"value is a click counter and 0 is its initialization "
                f"sentinel, for which {sentinel_clause}."
            ),
            message=(
                f"'{variable_name}' ({element_type}) was not clicked: "
                f"{submitted!r} is the {type_label} counter's initialization "
                f"sentinel, for which {sentinel_clause}. No click was "
                f"delivered. Submit a nonzero, advancing counter (the frontend "
                f"sends 1 on the first click, then 2, 3, ...) to drive the "
                f"{type_label}."
            ),
            next_steps=[
                (
                    f"Submit a nonzero, advancing counter to click the "
                    f"{type_label} (the frontend sends 1 on the first click, "
                    f"then 2, 3, ...), then verify the handler's effects with "
                    f"get_variables, get_cell_outputs, and get_errors."
                ),
            ],
        )
        return fields

    if not verified:
        # The element read-back itself failed, so neither the element value nor
        # the frontend counter is known.
        readback = data.get("readback_error")
        detail = f" ({readback})" if readback else ""
        fields.update(
            handler_invoked=None,
            click_delivered=None,
            warning=(
                "The update was queued but the kernel read-back failed"
                f"{detail}; this call cannot say whether {unknown_question}."
            ),
            message=(
                f"The counter update for '{variable_name}' ({element_type}) was "
                f"queued but the kernel read-back failed{detail}; whether "
                f"{unknown_question} is unknown, and the handler's side effects "
                f"are not verified."
            ),
            next_steps=[
                (
                    "Confirm the handler's effects with get_variables and "
                    "get_cell_outputs, and check get_errors, before declaring "
                    "the interaction complete — this call could not verify it."
                ),
            ],
        )
        return fields

    if f_after is None:
        # The element read-back succeeded, but the frontend click counter is
        # unreadable. The failure is the counter, not the element read-back,
        # so no readback_error is interpolated and nothing blames the element.
        fields.update(
            handler_invoked=None,
            click_delivered=None,
            warning=(
                f"The element read-back succeeded, but the {type_label}'s "
                "frontend click counter could not be read after the update, so "
                f"this call cannot say whether {unknown_question}."
            ),
            message=(
                f"The element read-back for '{variable_name}' ({element_type}) "
                f"succeeded, but its frontend click counter was not readable "
                f"afterwards, so whether {unknown_question} is unknown — the "
                f"counter is simply unavailable to this call. The handler's "
                f"side effects are not verified."
            ),
            next_steps=[
                (
                    "Confirm the handler's effects with get_variables and "
                    "get_cell_outputs, and check get_errors, before declaring "
                    "the interaction complete — this call could not verify it."
                ),
            ],
        )
        return fields

    if f_before is None:
        # The counter is only half-observed: the submitted value is what the
        # element holds now, but there is no "before" to compare it against.
        fields.update(
            handler_invoked=None,
            click_delivered=None,
            warning=(
                f"The {type_label}'s click counter could not be read before the "
                f"update, so this call cannot say whether {unknown_question}."
            ),
            message=(
                f"The submitted counter {submitted!r} is what '{variable_name}' "
                f"({element_type}) now holds, but its previous counter value "
                f"was unreadable, so whether {unknown_question} is unknown; the "
                f"handler's side effects are not verified."
            ),
            next_steps=[
                (
                    "Confirm the handler's effects with get_variables and "
                    "get_cell_outputs, and check get_errors, before declaring "
                    "the interaction complete — this call could not verify it."
                ),
            ],
        )
        return fields

    if not _same_json(f_after, submitted):
        # The counter is readable but does not hold what was submitted, so the
        # update's delivery cannot be confirmed from it.
        fields.update(
            handler_invoked=None,
            click_delivered=None,
            warning=(
                f"The element read-back succeeded, but the {type_label}'s "
                f"frontend click counter holds {f_after!r}, not the submitted "
                f"{submitted!r}, so this call cannot say whether "
                f"{unknown_question}."
            ),
            message=(
                f"The click counter for '{variable_name}' ({element_type}) "
                f"reads {f_after!r} after the update, not the submitted "
                f"{submitted!r}, so whether {unknown_question} is unknown; the "
                f"handler's side effects are not verified."
            ),
            next_steps=[
                (
                    "Read the element and its dependents with get_variables / "
                    "get_cell_outputs and check get_errors before declaring the "
                    "interaction complete — this call could not verify it."
                ),
            ],
        )
        return fields

    if not _same_json(f_before, f_after):
        fields.update(
            handler_invoked=True,
            click_delivered=True,
            message=(
                f"The click counter for '{variable_name}' ({element_type}) "
                f"moved from {f_before!r} to {f_after!r}, so the update was "
                f"delivered and {invoked_clause}. The element's own value reads "
                f"{before!r} -> {after!r} ({'moved' if after != before else 'unchanged'}) — "
                f"{value_shape}, so an unchanged element value is not evidence "
                f"the click was skipped. The handler's side effects are NOT "
                f"verified by this read-back."
            ),
            next_steps=[
                (
                    "The click was delivered, but the handler's side effects are "
                    "not verified here. Confirm the state it was meant to change "
                    "(and the dependent cells it re-ran) with get_variables, "
                    "get_cell_outputs, and get_errors before declaring the "
                    "interaction complete."
                ),
            ],
        )
        return fields

    # Nonzero counter already at the submitted value: the counter did not move,
    # so the read-back cannot distinguish a repeated invocation from a no-op.
    fields.update(
        handler_invoked=None,
        click_delivered=None,
        warning=(
            f"The {type_label}'s frontend counter was already at the submitted "
            f"value {submitted!r} and did not change, so this call cannot tell "
            f"whether {unknown_question} again. {repeated_clause}, but this "
            f"read-back cannot verify that it did — do not report the click as "
            f"delivered, and do not report it as skipped."
        ),
        message=(
            f"The element '{variable_name}' ({element_type}) was already at "
            f"click counter {submitted!r} and it did not change, so whether "
            f"{unknown_question} is unknown. {repeated_clause}, but the element "
            f"read-back cannot verify that this call did; the handler's side "
            f"effects are not verified either way."
        ),
        next_steps=[
            (
                "Deliver a NEW, higher counter value (the frontend sends an "
                "advancing counter per click) so the read-back can confirm the "
                "interaction, then verify the handler's effects with "
                "get_variables, get_cell_outputs, and get_errors."
            ),
        ],
    )
    return fields


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
    context exit, and the element's own value is read back before returning —
    ``status: ok`` with ``verified: true`` means the element's value was
    observed to move (or was already equal), not merely that the update was
    queued. A value marimo rejected is reported as an error, never as success.

    **A button's element value is not the interaction.** ``button`` and
    ``run_button`` both expose a *frontend* click counter — 0 is the
    initialization sentinel (marimo processes no click), the first click sends
    1, then 2, 3, ... — but their element ``value`` differs. A ``button``'s
    value is its ``on_click`` return (``None`` when the handler returns
    nothing), so a side-effect-only handler leaves it unchanged. A
    ``run_button`` has no ``on_click``: a nonzero counter sets its value to
    ``True``, and the runtime resets it to ``False`` after the dependent cells
    run, so its value may also read unchanged (``False``) for a click that
    landed. For both element types the payload carries explicit delivery
    evidence — ``frontend_value_before`` / ``frontend_value_after``,
    ``click_delivered``, and ``handler_invoked`` (true / false / null) — plus
    ``side_effects_verified: false``, because the read-back never verifies the
    handler's arbitrary side effects. An unchanged element value is never
    reported as "already held, nothing changed". Submitting 0 reports
    ``handler_invoked: false`` with a warning; a repeated nonzero counter
    reports ``handler_invoked: null`` (unknown — the counter did not change,
    so the read-back cannot say whether the click was processed again). A
    ``button`` whose ``on_click`` raises returns ``status: error``,
    ``reason: on_click_failed``, ``handler_ran: true`` and
    ``side_effects_verified: false``, and states that the handler may have
    applied partial side effects before it raised.

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
        ``applied: true`` when the value moved, or ``applied: false`` (and,
        for a non-button, ``no_change: true``) when the element already held it
        and only the handler ran and failed; ``on_click_failed`` when a
        ``button``'s own handler raised; ``ui_update_failed`` when the traceback
        cannot be attributed to the target's own handler (never a false
        on_click claim). On success the payload carries ``applied``,
        ``verified``, and the element's value before and after. This call does
        **not** verify arbitrary downstream effects: in autorun mode the kernel
        re-runs dependent cells as part of the update, while in lazy mode they
        are only marked stale and re-run on demand — confirm their effects with
        the read tools either way.
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

    # marimo writes a traceback to the kernel's stderr for EVERY failure point
    # of a UI update: a rejected CONVERSION (unknown dropdown key) raises
    # BEFORE the element's value is assigned, an `on_change` handler raises
    # AFTER it (with no equality shortcut, so even a value the element already
    # holds runs the handler), and a button's `on_click` handler is caught
    # inside the button itself (its own marker, its own site). The traceback's
    # call site / marker says which one it was; the template's read-back says
    # whether the value moved. Reporting any of them as a plain `ok` is the
    # silent-no-op bug, and reporting a handler failure as "not applied"
    # contradicts what actually happened.
    rejection = _kernel_rejection(stderr_text)
    if rejection is not None:
        site = _rejection_site(stderr_text)
        if site == "on_click" and not _attributable_on_click(data, value):
            # The marker is present but cannot belong to THIS update: a
            # run_button (no on_click), any other element (an on_change handler
            # can print the literal text), or a 0 counter (marimo never calls
            # on_click for it). Fail conservatively — never a false on_click
            # attribution, never a handler_invoked claim.
            site = "unattributed"
        return _rejection_payload(variable_name, sid, value, data, rejection, site)

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

    # A button's element value is not the interaction (T20): report the click
    # counter evidence and the honest tri-state of handler invocation instead
    # of a no-change report. This runs before the generic `not verified` branch
    # so a button keeps its counter evidence even when the read-back failed.
    if data.get("element_type") in _BUTTON_TYPES:
        payload.update(_button_interaction_payload(value, data, verified))
        return payload

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
                "This call does not verify arbitrary downstream effects: in "
                "autorun mode the kernel re-runs dependent cells as part of "
                "the update, in lazy mode it only marks them stale — verify "
                "their effects with get_variables, get_cell_outputs, and "
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
