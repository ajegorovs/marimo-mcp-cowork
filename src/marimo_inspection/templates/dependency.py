"""Scratchpad template for getting the dependency graph."""

from __future__ import annotations


def build_dependency_graph_template() -> str:
    """Build the scratchpad code template for dependency graph.

    The template always returns the FULL notebook graph: it takes no centring
    arguments, because the tool refuses ``cell_id``/``depth`` instead of
    accepting them and ignoring them.

    Returns:
        Python code string that runs in the scratchpad.
    """
    return _TEMPLATE


_TEMPLATE = """
import json
import marimo._code_mode as cm

async def get_dependency_graph():
    async with cm.get_context() as ctx:
        # Access the graph through the code mode context
        graph = ctx.graph

        # Cell names come from the SAME source get_cell_map reads (the
        # NotebookCell's ``name``), so both tools agree on what a cell is
        # called. A graph CellImpl has no usable name of its own.
        name_by_id = {}
        try:
            for cell in ctx.cells:
                try:
                    name_by_id[str(cell.id)] = getattr(cell, "name", "") or ""
                except Exception:
                    continue
        except Exception:
            name_by_id = {}

        # Build cell dependency info
        cells = []
        for cid, cell_impl in graph.cells.items():
            cell_name = name_by_id.get(str(cid), "")
            if not cell_name:
                cell_name = getattr(cell_impl, "name", "") or ""

            defs = []
            for var_name in sorted(cell_impl.defs):
                defs.append({
                    "name": var_name,
                    "kind": "variable",
                })

            cells.append({
                "cell_id": str(cid),
                "cell_name": cell_name,
                "defs": defs,
                "refs": sorted(cell_impl.refs),
                "parent_cell_ids": sorted(str(p) for p in graph.parents.get(cid, set())),
                "child_cell_ids": sorted(str(c) for c in graph.children.get(cid, set())),
            })

        # Variable owners (global)
        variable_owners = {}
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
            cycles.append({
                "cell_ids": sorted(cycle_cell_ids),
                "edges": edges_list,
            })

        return json.dumps({
            "cells": cells,
            "variable_owners": variable_owners,
            "multiply_defined": multiply_defined,
            "cycles": cycles,
        })

print(await get_dependency_graph())
"""


# Pre-built default template
TEMPLATE_DEPENDENCY_GRAPH = build_dependency_graph_template()
