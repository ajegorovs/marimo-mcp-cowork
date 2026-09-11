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


# Shared CellOutput serializer. Kept as a Python-level source string so that
# templates/errors.py embeds the SAME console serialization shape (single
# source of truth for console event serialization across both templates).
_CELL_OUTPUT_TO_DICT_SRC = """def _channel_name(out):
    # Bare lowercase console-channel name ("stdout"/"stderr"/"output"/...), or
    # "" when unreadable. marimo's CellChannel is a str-mixin enum: repr() is
    # "stderr" but str() is "CellChannel.STDERR", so a raw str comparison never
    # matches. Normalize to the final dotted segment, lowercased — the ONE
    # channel normalization shared by this serializer and the template filters.
    return str(getattr(out, "channel", "")).split(".")[-1].lower()


def _cell_output_to_dict(out):
    # Convert a marimo CellOutput into a serializable summary dict.
    if out is None:
        return None

    channel = _channel_name(out)
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
            "channel": channel,
            "mimetype": str(mimetype),
            "data": payload[:5000],
            "mime_subtypes": mime_keys,
        }

    if isinstance(data, list):
        return {
            "channel": channel,
            "mimetype": str(mimetype),
            "data": [str(d)[:500] for d in data[:20]],
            "mime_subtypes": None,
        }

    return {
        "channel": channel,
        "mimetype": str(mimetype),
        "data": str(data)[:5000],
        "mime_subtypes": None,
    }
"""


_TEMPLATE = """
import json
import marimo._code_mode as cm

__CELL_OUTPUT_TO_DICT__

async def get_cell_outputs():
    async with cm.get_context() as ctx:
        cell_ids = __CELL_IDS_JSON__
        if not cell_ids:
            cell_ids = list(ctx.cells.keys())

        results = []
        missing = []
        for cid in cell_ids:
            try:
                c = ctx.cells[cid]
            except Exception:
                # Keep the requested-but-unresolved ids visible: a deleted or
                # mistyped id must not look like "this cell has no output".
                missing.append(str(cid))
                continue

            out = c.output
            console = c.console_outputs

            results.append({
                "cell_id": str(c.id),
                "visual_output": _cell_output_to_dict(out) if out else None,
                "visual_mimetype": str(getattr(out, "mimetype", None)) if out else None,
                "stdout": [
                    _cell_output_to_dict(o)
                    for o in console if _channel_name(o) == "stdout"
                ],
                "stderr": [
                    _cell_output_to_dict(o)
                    for o in console if _channel_name(o) == "stderr"
                ],
                "console_events": [_cell_output_to_dict(o) for o in console],
            })

        return json.dumps({"cells": results, "missing_cell_ids": missing})


print(await get_cell_outputs())
""".replace("__CELL_OUTPUT_TO_DICT__", _CELL_OUTPUT_TO_DICT_SRC)


# Pre-built default template
TEMPLATE_CELL_OUTPUTS = build_cell_outputs_template([])
