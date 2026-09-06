"""Scratchpad template for getting notebook errors."""

from __future__ import annotations


def build_errors_template() -> str:
    """Build the scratchpad code template for error aggregation.

    Uses each `NotebookCell.errors` list (marimo's structured `CellError`
    objects: kind graph|runtime, msg, exception) — NOT status-string sniffing.

    Geometry: a `NameError`/exception sets cell status to ``exception`` (never
    ``error``), so checking status would miss raised cells. `cell.errors` is the
    authoritative source and covers both runtime exceptions and graph errors
    (multiply-defined, cycles).

    Returns:
        Python code string that runs in the scratchpad.
    """
    return """
import json
import marimo._code_mode as cm


async def get_errors():
    async with cm.get_context() as ctx:
        cells_with_errors = []
        for c in ctx.cells:
            cell_errors = []
            try:
                errors = c.errors
            except Exception:
                errors = None

            if errors:
                for err in errors:
                    kind = getattr(err, "kind", None)
                    cell_errors.append({
                        "kind": str(kind),
                        "cell": str(c.id),
                        "msg": getattr(err, "msg", "") or "",
                        "exception": repr(getattr(err, "exception", None)),
                    })

            if cell_errors:
                cells_with_errors.append({
                    "cell_id": str(c.id),
                    "errors": cell_errors,
                    "stderr": [],
                })

        total_errors = sum(len(c["errors"]) for c in cells_with_errors)

        return json.dumps({
            "has_errors": total_errors > 0,
            "total_errors": total_errors,
            "total_cells_with_errors": len(cells_with_errors),
            "cells": cells_with_errors,
        })


print(await get_errors())
"""


# Pre-built default template
TEMPLATE_ERRORS = build_errors_template()
