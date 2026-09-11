"""Scratchpad template for getting cell runtime data."""

from __future__ import annotations

import json


def build_cell_data_template(cell_ids: list[str]) -> str:
    """Build the scratchpad code template for cell runtime data.

    Args:
        cell_ids: List of cell IDs to get data for. Empty list means all cells.

    Returns:
        Python code string that runs in the scratchpad.
    """
    cell_ids_json = json.dumps(cell_ids)

    return f"""
import json
import marimo._code_mode as cm

async def get_cell_data():
    async with cm.get_context() as ctx:
        # Resolve cell IDs
        cell_ids = {cell_ids_json}
        if not cell_ids:
            # Get cell IDs from ctx.cells.keys()
            cell_ids = list(ctx.cells.keys())

        results = []
        missing = []
        for cid in cell_ids:
            try:
                c = ctx.cells[cid]
                code = c.code or ""
                status = getattr(c, "status", None)
            except Exception:
                # A deleted or mistyped id is NOT the same as "nothing matched":
                # report it instead of silently returning an empty payload.
                missing.append(str(cid))
                continue

            results.append({{
                "cell_id": str(cid),
                "code": code,
                "runtime_state": str(status) if status else None,
                "variables": None,
            }})

        return json.dumps({{"data": results, "missing_cell_ids": missing}})

print(await get_cell_data())
"""


# Pre-built default template (empty = all cells)
TEMPLATE_CELL_DATA = build_cell_data_template([])
