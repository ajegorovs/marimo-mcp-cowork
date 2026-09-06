"""Scratchpad template for getting variables and tables."""

from __future__ import annotations

import json


def build_variables_template(variable_names: list[str]) -> str:
    """Build the scratchpad code template for variable inspection.

    Args:
        variable_names: List of variable names to inspect. Empty list means all.

    Returns:
        Python code string that runs in the scratchpad.
    """
    var_names_json = json.dumps(variable_names)

    return _TEMPLATE.replace("__VAR_NAMES_JSON__", var_names_json)


_TEMPLATE = """
import json
import marimo._code_mode as cm


def _is_ui(obj):
    '''True for marimo UI elements (have a .value and live under marimo).'''
    mod = type(obj).__module__
    return hasattr(obj, "value") and (
        mod.startswith("marimo._plugins.ui") or mod.startswith("marimo.ui")
    )


def _serialize(value, depth=0):
    '''Coerce a python value into a JSON-friendly description dict.'''
    if depth > 3:
        return {"value": str(value)[:500], "datatype": type(value).__name__}

    # numpy arrays / scalars (optional dependency - degrade gracefully)
    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None and isinstance(value, (np.ndarray, np.generic)):
        return {
            "value": str(value)[:500],
            "datatype": "numpy." + type(value).__name__,
            "shape": list(value.shape) if hasattr(value, "shape") else None,
            "dtype": str(value.dtype),
        }

    # recursable containers (bounded)
    if isinstance(value, list):
        return {"value": [_serialize(v, depth + 1) for v in value[:20]],
                "datatype": "list", "length": len(value)}
    if isinstance(value, tuple):
        return {"value": [_serialize(v, depth + 1) for v in value[:20]],
                "datatype": "tuple", "length": len(value)}
    if isinstance(value, set):
        return {"value": [_serialize(v, depth + 1) for v in sorted(value, key=str)[:20]],
                "datatype": "set", "length": len(value)}
    if isinstance(value, dict):
        keys = list(value.keys())[:20]
        return {"value": {str(k): _serialize(value[k], depth + 1) for k in keys},
                "datatype": "dict", "length": len(value)}

    return {"value": str(value)[:500], "datatype": type(value).__name__}


async def get_variables():
    async with cm.get_context() as ctx:
        target_names = __VAR_NAMES_JSON__

        if target_names:
            to_check = target_names
        else:
            to_check = [
                name for name in globals().keys()
                if not name.startswith("_") and name != "__builtins__"
            ]

        variables = {}
        tables = {}

        for name in to_check:
            if name not in globals():
                continue

            obj = globals()[name]

            # pandas DataFrames -> tables
            try:
                import pandas as pd
                if isinstance(obj, pd.DataFrame):
                    tables[name] = {
                        "source": "DataFrame",
                        "num_rows": len(obj),
                        "num_columns": len(obj.columns),
                        "columns": list(obj.columns),
                        "dtypes": obj.dtypes.astype(str).to_dict(),
                    }
                    continue
            except Exception:
                pass

            # marimo UI elements -> expose their .value (what the user set)
            if _is_ui(obj):
                variables[name] = {
                    "value": _serialize(obj.value),
                    "datatype": type(obj).__name__,
                }
                continue

            # numpy / generic
            variables[name] = _serialize(obj)

        return json.dumps({
            "variables": variables,
            "tables": tables,
        })


print(await get_variables())
"""

# Pre-built default template
TEMPLATE_VARIABLES = build_variables_template([])
