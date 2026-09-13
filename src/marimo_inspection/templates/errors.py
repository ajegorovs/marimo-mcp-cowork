"""Scratchpad template for getting notebook errors."""

from __future__ import annotations

from marimo_inspection.templates.cell_outputs import _CELL_OUTPUT_TO_DICT_SRC

# ── Shared scratchpad error extraction ──────────────────────────────────────
#
# The per-cell, two-channel error extraction is kept as scratchpad-source
# strings so that the errors template and the opt-in cell-data template embed
# the SAME implementation (single source of truth for structured errors,
# stderr serialization, and the conservative console-exception evidence scan —
# see the ``build_errors_template`` docstring for the semantics). Duplicating
# this source would let the two tools drift apart.
_CONSOLE_EXCEPTION_EVIDENCE_SRC = """def _console_exception_evidence(console_outputs):
    # Conservative: only REAL exception evidence flags a cell; an unreadable
    # console value is never treated as evidence. Two levels, strongest first:
    #   "traceback"      - the traceback header is present
    #   "exception_line" - an unindented ``SomeError: ...`` line (what a
    #                      traceback ends with), where the type name is an
    #                      exception class name rather than the bare word
    #                      "Error"/"Exception"
    # Ordinary log text such as ``Error: 3 rows skipped`` is NOT evidence: it
    # carries a message, not an exception type, and treating it as one made a
    # healthy cell report ``has_console_exception``.
    try:
        lines = []
        for o in console_outputs:
            if _channel_name(o) == "stderr":
                lines.extend(str(getattr(o, "data", "")).splitlines())
    except Exception:
        return ""
    if any("Traceback (most recent call last)" in ln for ln in lines):
        return "traceback"
    pattern = re.compile(r"^([A-Za-z_][\\w.]*(?:Error|Exception)):\\s")
    for ln in lines:
        match = pattern.match(ln)
        if not match:
            continue
        # A bare "Error:"/"Exception:" is a message label, not a type name.
        if match.group(1).split(".")[-1] in ("Error", "Exception"):
            continue
        return "exception_line"
    return ""
"""

_EXTRACT_CELL_ERRORS_SRC = """def _extract_cell_errors(cell):
    # One cell's two-channel error extraction, shared by get_errors and the
    # opt-in get_cell_data(include_errors=True) mode. Structured CellError
    # records and serialized stderr console events are reported separately and
    # never conflated; a clean (or unreadable) cell yields [], [], False, None.
    #
    # PRIVATE on purpose: the scratchpad runs in the KERNEL's namespace, so a
    # public helper name here would rebind a same-named notebook global on
    # every call. Every name this shared source binds follows the private
    # scaffolding convention, pinned by
    # test_templates.py::test_opt_in_embeds_the_shared_error_extractor.
    structured = []
    try:
        errors = cell.errors
    except Exception:
        errors = None
    if errors:
        for err in errors:
            kind = getattr(err, "kind", None)
            structured.append({
                "kind": str(kind),
                "cell": str(cell.id),
                "msg": getattr(err, "msg", "") or "",
                "exception": repr(getattr(err, "exception", None)),
            })

    try:
        console = cell.console_outputs
    except Exception:
        console = None
    console_stderr = []
    if console:
        console_stderr = [
            _cell_output_to_dict(o)
            for o in console
            if _channel_name(o) == "stderr"
        ]

    evidence = _console_exception_evidence(console or [])

    return {
        "structured_errors": structured,
        "console_stderr": console_stderr,
        "has_console_exception": bool(evidence),
        "console_exception_evidence": evidence or None,
    }
"""

# Console serializer + evidence scan + per-cell extractor, with ``re`` imported:
# exactly what a scratchpad needs to call ``_extract_cell_errors(cell)``.
CELL_ERROR_EXTRACTION_SRC = (
    "import re\n\n"
    + _CELL_OUTPUT_TO_DICT_SRC
    + "\n\n"
    + _CONSOLE_EXCEPTION_EVIDENCE_SRC
    + "\n\n"
    + _EXTRACT_CELL_ERRORS_SRC
)


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
    text: a cell qualifies only on REAL exception evidence — a traceback header
    (``Traceback (most recent call last)``), or an unindented
    ``SomeError: ...``/``SomeException: ...`` line naming an exception class
    (the last line of a traceback). A bare ``Error:``/``Exception:`` label is a
    message, not a type name, so ordinary log text such as
    ``Error: 3 rows skipped`` does NOT flag a cell. Each flagged cell also
    carries ``console_exception_evidence`` (``"traceback"`` /
    ``"exception_line"`` / ``None``) so the caller can say what was actually
    seen instead of asserting a cause. A cell appears in the response when it
    has structured errors OR console-exception evidence — so a console-only
    UI-handler exception is visible even with ``c.errors`` empty.

    Top-level totals:
    - ``has_errors`` / ``total_errors`` / ``total_cells_with_errors`` are the
      STRUCTURED-only counts (backward-compatible definition).
    - ``has_console_exception`` / ``total_console_exception_cells`` are the
      console channel's totals.

    The per-cell extraction is defined ONCE, as ``CELL_ERROR_EXTRACTION_SRC``
    (console serializer + evidence scan + ``_extract_cell_errors``), and is
    embedded verbatim by the opt-in ``get_cell_data(include_errors=True)``
    template — so the two tools cannot drift apart. All of its helpers are
    private: the scratchpad shares the kernel's namespace, so a public name
    would rebind a notebook global.

    Returns:
        Python code string that runs in the scratchpad.
    """
    return _TEMPLATE


_TEMPLATE = """
import json
import marimo._code_mode as cm

__CELL_ERROR_EXTRACTION__

async def get_errors():
    async with cm.get_context() as ctx:
        cells_with_errors = []
        for c in ctx.cells:
            cell_id = str(c.id)
            errs = _extract_cell_errors(c)

            if errs["structured_errors"] or errs["has_console_exception"]:
                cells_with_errors.append({
                    "cell_id": cell_id,
                    "structured_errors": errs["structured_errors"],
                    "console_stderr": errs["console_stderr"],
                    "has_console_exception": errs["has_console_exception"],
                    "console_exception_evidence": errs["console_exception_evidence"],
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
""".replace("__CELL_ERROR_EXTRACTION__", CELL_ERROR_EXTRACTION_SRC)


# Pre-built default template
TEMPLATE_ERRORS = build_errors_template()
