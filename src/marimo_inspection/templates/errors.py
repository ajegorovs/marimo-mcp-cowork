"""Scratchpad template for getting notebook errors."""

from __future__ import annotations

from marimo_inspection.templates.cell_outputs import _CELL_OUTPUT_TO_DICT_SRC


def build_errors_template() -> str:
    """Build the scratchpad code template for error aggregation.

    Two DISTINCT channels, never conflated:

    - ``structured_errors``: marimo's structured ``CellError`` records
      (kind graph|runtime, msg, exception) — the authoritative source for
      runtime exceptions and graph errors (multiply-defined, cycles).
      Geometry: a ``NameError``/exception sets cell status to ``exception``
      (never ``error``), so ``c.errors`` is the source of truth, not status
      sniffing.
    - ``console_stderr``: serialized stderr console events from the cell's
      last execution, using the SAME serialization shape as
      ``templates/cell_outputs.py`` (shared ``_cell_output_to_dict`` source,
      and its shared ``_channel_name`` normalizer for the channel filter).
      This surfaces UI-handler exception tracebacks that marimo records only
      on the console channel.

    ``has_console_exception`` is a CONSERVATIVE marker scan over the stderr
    text (``Traceback (most recent call last)`` / ``Error:`` / ``Exception:``
    lines): only clear exception evidence flags a cell. A cell appears in the
    response when it has structured errors OR console-exception evidence —
    so a console-only UI-handler exception is visible even with ``c.errors``
    empty.

    Top-level totals:
    - ``has_errors`` / ``total_errors`` / ``total_cells_with_errors`` are the
      STRUCTURED-only counts (backward-compatible definition).
    - ``has_console_exception`` / ``total_console_exception_cells`` are the
      console channel's totals.

    Returns:
        Python code string that runs in the scratchpad.
    """
    return _TEMPLATE


_TEMPLATE = """
import json
import marimo._code_mode as cm

__CELL_OUTPUT_TO_DICT__

def _console_exception_evidence(console_outputs):
    # Conservative: only clear exception/traceback markers flag a cell;
    # an unreadable console value is never treated as evidence.
    try:
        text = "\\n".join(
            str(getattr(o, "data", ""))
            for o in console_outputs
            if _channel_name(o) == "stderr"
        )
    except Exception:
        return False
    markers = ("Traceback (most recent call last)", "Error:", "Exception:")
    return any(m in text for m in markers)

async def get_errors():
    async with cm.get_context() as ctx:
        cells_with_errors = []
        for c in ctx.cells:
            cell_id = str(c.id)

            structured = []
            try:
                errors = c.errors
            except Exception:
                errors = None
            if errors:
                for err in errors:
                    kind = getattr(err, "kind", None)
                    structured.append({
                        "kind": str(kind),
                        "cell": cell_id,
                        "msg": getattr(err, "msg", "") or "",
                        "exception": repr(getattr(err, "exception", None)),
                    })

            try:
                console = c.console_outputs
            except Exception:
                console = None
            console_stderr = []
            if console:
                console_stderr = [
                    _cell_output_to_dict(o)
                    for o in console
                    if _channel_name(o) == "stderr"
                ]

            has_exc = _console_exception_evidence(console or [])

            if structured or has_exc:
                cells_with_errors.append({
                    "cell_id": cell_id,
                    "structured_errors": structured,
                    "console_stderr": console_stderr,
                    "has_console_exception": has_exc,
                })

        total_structured = sum(
            len(c["structured_errors"]) for c in cells_with_errors
        )
        total_console_exc = sum(
            1 for c in cells_with_errors if c["has_console_exception"]
        )

        return json.dumps({
            "has_errors": total_structured > 0,
            "total_errors": total_structured,
            "total_structured_errors": total_structured,
            "total_cells_with_errors": sum(
                1 for c in cells_with_errors if c["structured_errors"]
            ),
            "has_console_exception": total_console_exc > 0,
            "total_console_exception_cells": total_console_exc,
            "cells": cells_with_errors,
        })


print(await get_errors())
""".replace("__CELL_OUTPUT_TO_DICT__", _CELL_OUTPUT_TO_DICT_SRC)


# Pre-built default template
TEMPLATE_ERRORS = build_errors_template()
