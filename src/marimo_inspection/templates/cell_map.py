"""Scratchpad template for getting the cell map."""

from __future__ import annotations


def build_cell_map_template(preview_lines: int = 3) -> str:
    """Build the scratchpad code template for cell map.

    Args:
        preview_lines: Number of lines to preview per cell.

    Returns:
        Python code string that runs in the scratchpad.
    """
    return f"""
import json
import hashlib
import marimo._code_mode as cm

def _code_hash(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]

def _flag(reader):
    # bool(reader()) for each truthfulness flag; None when the private
    # CodeMode field is unreadable (marimo API drift) — NEVER a false
    # assertion about output/console/error presence.
    try:
        return bool(reader())
    except Exception:
        return None

async def get_cell_map():
    async with cm.get_context() as ctx:
        cells = []
        # Iterate directly over ctx.cells (yields NotebookCell objects)
        for c in ctx.cells:
            try:
                code = c.code or ""
            except Exception:
                continue  # Skip cells that can't be read
            code_lines = code.split("\\n")
            preview = "\\n".join(code_lines[:{preview_lines}])
            line_count = len(code_lines)
            status = getattr(c, "status", None)

            cells.append({{
                "cell_id": str(c.id),
                "name": getattr(c, "name", ""),
                "preview": preview,
                "line_count": line_count,
                "runtime_state": status,
                "code_hash": _code_hash(code),
                "has_output": _flag(lambda: c.output is not None),
                "has_console_output": _flag(lambda: bool(c.console_outputs)),
                "has_errors": _flag(lambda: bool(c.errors)),
            }})

        return json.dumps({{
            "cells": cells,
            "total_cells": len(cells),
        }})

print(await get_cell_map())
"""


# Pre-built default template
TEMPLATE_CELL_MAP = build_cell_map_template()
