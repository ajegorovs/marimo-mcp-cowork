"""Scratchpad template for getting cell runtime data."""

from __future__ import annotations

import json

from marimo_inspection.templates.errors import CELL_ERROR_EXTRACTION_SRC

# The opt-in per-row error block is injected at BUILD time, so the default
# template neither carries nor executes the shared extraction helpers and its
# row shape stays byte-compatible with earlier releases.
_ERROR_ROW_BLOCK = """            errs = _extract_cell_errors(c)
            row["structured_errors"] = errs["structured_errors"]
            row["console_stderr"] = errs["console_stderr"]
            row["has_console_exception"] = errs["has_console_exception"]
            row["console_exception_evidence"] = errs["console_exception_evidence"]"""


def build_cell_data_template(cell_ids: list[str], include_errors: bool = False) -> str:
    """Build the scratchpad code template for cell runtime data.

    Args:
        cell_ids: List of cell IDs to get data for. Empty list means all cells.
        include_errors: When true, every returned row additionally carries the
            four per-cell error fields (``structured_errors`` /
            ``console_stderr`` / ``has_console_exception`` /
            ``console_exception_evidence``) produced by the SAME shared
            ``_extract_cell_errors`` helpers ``get_errors`` uses — a clean cell
            reports ``[]`` / ``[]`` / ``False`` / ``None``. Default false: no
            error keys are emitted at all, so the row shape is unchanged.

    Returns:
        Python code string that runs in the scratchpad.
    """
    cell_ids_json = json.dumps(cell_ids)
    helpers = f"{CELL_ERROR_EXTRACTION_SRC}\n" if include_errors else ""
    error_block = _ERROR_ROW_BLOCK if include_errors else ""
    return (
        _TEMPLATE.replace("__CELL_IDS_JSON__", cell_ids_json)
        .replace("__ERROR_HELPERS__", helpers)
        .replace("__ERROR_ROW_BLOCK__", error_block)
    )


_TEMPLATE = """
import json
import marimo._code_mode as cm

__ERROR_HELPERS__

async def get_cell_data():
    async with cm.get_context() as ctx:
        # Resolve cell IDs
        cell_ids = __CELL_IDS_JSON__
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

            row = {
                "cell_id": str(cid),
                "code": code,
                "runtime_state": str(status) if status else None,
                # Deprecated compatibility placeholder: this tool has never
                # carried variable data. Inspect live variables with
                # get_variables instead.
                "variables": None,
            }
__ERROR_ROW_BLOCK__
            results.append(row)

        return json.dumps({"data": results, "missing_cell_ids": missing})


print(await get_cell_data())
"""


# Pre-built default template (empty = all cells, no optional error fields)
TEMPLATE_CELL_DATA = build_cell_data_template([])
