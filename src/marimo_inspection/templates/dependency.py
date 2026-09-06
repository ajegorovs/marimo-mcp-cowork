"""Scratchpad template for getting the dependency graph."""

from __future__ import annotations


def build_dependency_graph_template(
    cell_id: str | None = None,
    depth: int | None = None,
) -> str:
    """Build the scratchpad code template for dependency graph.

    Args:
        cell_id: Optional cell ID to center the graph on.
        depth: Optional number of hops from center cell.

    Returns:
        Python code string that runs in the scratchpad.
    """
    cell_id_str = f'"{cell_id}"' if cell_id is not None else "None"
    depth_val = depth if depth is not None else "None"

    return f"""
import json
import marimo._code_mode as cm

async def get_dependency_graph():
    async with cm.get_context() as ctx:
        # Access the graph through the code mode context
        graph = ctx.graph

        center_cell = {cell_id_str}
        target_depth = {depth_val}

        # Build cell dependency info
        cells = []
        for cid, cell_impl in graph.cells.items():
            cell_name = ""

            defs = []
            for var_name in sorted(cell_impl.defs):
                defs.append({{
                    "name": var_name,
                    "kind": "variable",
                }})

            cells.append({{
                "cell_id": str(cid),
                "cell_name": cell_name,
                "defs": defs,
                "refs": sorted(cell_impl.refs),
                "parent_cell_ids": sorted(str(p) for p in graph.parents.get(cid, set())),
                "child_cell_ids": sorted(str(c) for c in graph.children.get(cid, set())),
            }})

        # Variable owners (global)
        variable_owners = {{}}
        for var_name, defining_cells in graph.definitions.items():
            variable_owners[var_name] = sorted(str(c) for c in defining_cells)

        # Multiply defined variables
        try:
            multiply_defined = sorted(graph.get_multiply_defined())
        except Exception:
            multiply_defined = []

        # Cycles
        cycles = []
        for cycle_edges in graph.cycles:
            cycle_cell_ids = set()
            edges_list = []
            for parent_id, child_id in cycle_edges:
                cycle_cell_ids.add(str(parent_id))
                cycle_cell_ids.add(str(child_id))
                edges_list.append([str(parent_id), str(child_id)])
            cycles.append({{
                "cell_ids": sorted(cycle_cell_ids),
                "edges": edges_list,
            }})

        return json.dumps({{
            "cells": cells,
            "variable_owners": variable_owners,
            "multiply_defined": multiply_defined,
            "cycles": cycles,
        }})

print(await get_dependency_graph())
"""


# Pre-built default template
TEMPLATE_DEPENDENCY_GRAPH = build_dependency_graph_template()
