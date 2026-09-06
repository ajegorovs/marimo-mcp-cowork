"""Scratchpad template for getting cell outputs."""

from __future__ import annotations

import json


def build_cell_outputs_template(cell_ids: list[str]) -> str:
    """Build the scratchpad code template for cell outputs.

    Args:
        cell_ids: List of cell IDs to get outputs for. Empty list means all cells.

    Returns:
        Python code string that runs in the scratchpad.
    """
    cell_ids_json = json.dumps(cell_ids)

    return _TEMPLATE.replace("__CELL_IDS_JSON__", cell_ids_json)


_TEMPLATE = """
import json
import marimo._code_mode as cm


def _cell_output_to_dict(out):
    '''Convert a marimo CellOutput into a serializable summary dict.'''
    if out is None:
        return None

    channel = str(getattr(out, "channel", "")).split(".")[-1]  # "OUTPUT" -> OUTPUT
    mimetype = getattr(out, "mimetype", None)
    data = getattr(out, "data", None)

    # The rich display output's data is a JSON string of a mimebundle
    # (e.g. {"image/png": "data:image/png;base64,..."}).
    if isinstance(data, str):
        # Try to decode the mimebundle if it is JSON.
        payload = data
        mime_keys = None
        if data.startswith("{"):
            try:
                bundle = json.loads(data)
                if isinstance(bundle, dict):
                    mime_keys = list(bundle.keys())
            except (ValueError, TypeError):
                payload = data
        return {
            "channel": channel.lower(),
            "mimetype": str(mimetype),
            "data": payload[:5000],
            "mime_subtypes": mime_keys,
        }

    if isinstance(data, list):
        return {
            "channel": channel.lower(),
            "mimetype": str(mimetype),
            "data": [str(d)[:500] for d in data[:20]],
            "mime_subtypes": None,
        }

    return {
        "channel": channel.lower(),
        "mimetype": str(mimetype),
        "data": str(data)[:5000],
        "mime_subtypes": None,
    }


async def get_cell_outputs():
    async with cm.get_context() as ctx:
        cell_ids = __CELL_IDS_JSON__
        if not cell_ids:
            cell_ids = list(ctx.cells.keys())

        results = []
        for cid in cell_ids:
            try:
                c = ctx.cells[cid]
            except (KeyError, Exception):
                continue

            out = c.output
            console = c.console_outputs

            results.append({
                "cell_id": str(c.id),
                "visual_output": _cell_output_to_dict(out) if out else None,
                "visual_mimetype": str(getattr(out, "mimetype", None)) if out else None,
                "stdout": [
                    _cell_output_to_dict(o)
                    for o in console if str(getattr(o, "channel", "")).lower() == "stdout"
                ],
                "stderr": [
                    _cell_output_to_dict(o)
                    for o in console if str(getattr(o, "channel", "")).lower() == "stderr"
                ],
                "console_events": [_cell_output_to_dict(o) for o in console],
            })

        return json.dumps({"cells": results})


print(await get_cell_outputs())
"""

# Pre-built default template
TEMPLATE_CELL_OUTPUTS = build_cell_outputs_template([])
