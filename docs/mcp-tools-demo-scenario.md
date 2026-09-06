# MCP Tools Demo Scenario

This document shows the redesigned demo scenario using MCP read tools for discovery, verification, and state-reading.

## Demo Flow

1. **Agent creates notebook** — `notebooks/agent_demo.py` with 2 cells (title + numpy import)
2. **Agent launches kernel** — `marimo edit --no-token --port 8123` (webpage auto-opens)
3. **MCP tools throughout** — discovery, cell map, errors, variables, dependencies

## Step-by-Step Results

### Step 1 — Discover via `list_active_notebooks()`

```python
from marimo_inspection.tools.notebooks import list_active_notebooks

result = await list_active_notebooks(server_url="http://127.0.0.1:8123")
```

**Result:**
```json
{
  "summary": {
    "servers_discovered": 1,
    "total_notebooks": 1
  },
  "notebooks": [
    {
      "session_id": "s_xhe4j1",
      "path": "C:\\Repos\\python-image-processing-notebooks\\notebooks\\agent_demo.py"
    }
  ]
}
```

### Step 2 — Initial cell map via `get_cell_map()`

```python
from marimo_inspection.tools.cells import get_cell_map

result = await get_cell_map(session_id, preview_lines=3, server_url=SERVER_URL)
```

**Result:** 2 cells in "stale" status (unrun)
```json
{
  "cells": [
    {
      "cell_id": "Hbol",
      "name": "_",
      "preview": "import marimo as mo\nmo.md(\"# Agent Demo — Function Plotting\")",
      "line_count": 2,
      "runtime_state": "stale"
    },
    {
      "cell_id": "MJUe",
      "name": "_",
      "preview": "import numpy as np",
      "line_count": 1,
      "runtime_state": "stale"
    }
  ],
  "total_cells": 2
}
```

### Step 3 — Run starter cells via mutation

```bash
bash scripts/execute-code.sh --url $SERVER_URL -c '
import marimo._code_mode as cm
async with cm.get_context() as ctx:
    for c in ctx.cells:
        if c.status == "stale":
            ctx.run_cell(c.id)
'
```

### Step 4 — Verify via `get_cell_map()` after mutation

**Result:** 2 cells now in "idle" status (cells were run)
```json
{
  "cells": [
    {
      "cell_id": "Hbol",
      "name": "_",
      "preview": "import marimo as mo\nmo.md(\"# Agent Demo — Function Plotting\")",
      "line_count": 2,
      "runtime_state": "idle"
    },
    {
      "cell_id": "MJUe",
      "name": "_",
      "preview": "import numpy as np",
      "line_count": 1,
      "runtime_state": "idle"
    }
  ],
  "total_cells": 2
}
```

### Step 5 — Error check via `get_errors()`

**Result:** No errors
```json
{
  "has_errors": false,
  "total_errors": 0,
  "cells_with_errors": 0
}
```

### Step 6 — Variables via `get_variables()`

**Result:** 7 kernel variables (includes `np`, `mo` from imports)
```json
{
  "variables": {
    "np": {"type": "module"},
    "mo": {"type": "module"},
    "input": {"type": "str"},
    "json": {"type": "module"},
    "cm": {"type": "module"},
    "get_variables": {"type": "function"},
    "spec_from_loader": {"type": "str"}
  }
}
```

### Step 7 — Dependencies via `get_dependency_graph()`

**Result:** 2 cells in graph, no cycles
```json
{
  "cells": [
    {"cell_id": "Hbol", "depends_on": []},
    {"cell_id": "MJUe", "depends_on": []}
  ],
  "multiply_defined": [],
  "cycles": []
}
```

## Key Improvements

| Aspect | Old (scratchpad only) | New (MCP tools) |
|--------|----------------------|-----------------|
| Discovery | `discover-servers.sh` | `list_active_notebooks()` |
| Cell state | Inline `cm.get_context()` | `get_cell_map()` |
| Variables | `print()` calls | `get_variables()` |
| Errors | Manual aggregation | `get_errors()` |
| Dependencies | Manual traversal | `get_dependency_graph()` |
| Verification | After-the-fact | After every mutation |

## Agent Loop Pattern

1. `list_active_notebooks()` → discover session
2. `get_cell_map()` → verify initial state
3. `execute-code.sh` → run starter cells (mutation)
4. `get_cell_map()` → verify cells ran
5. `execute-code.sh` → add control cells (mutation)
6. `get_cell_map()` → verify controls created
7. `get_variables()` → read widget state
8. `execute-code.sh` → create plot cell (mutation)
9. `get_errors()` → verify clean run
10. `get_cell_outputs()` → verify chart rendered

## Bug Fix

**Issue:** `get_cell_map()` returned 0 cells because the template used `list(ctx.cells)` which returns `NotebookCell` objects, not cell IDs. Then `ctx.cells[cid]` failed.

**Fix:** Changed the template to iterate directly over `ctx.cells` (which yields `NotebookCell` objects) instead of using them as keys.

**Before:**
```python
cell_ids = list(ctx.cells)  # Returns NotebookCell objects!
for cid in cell_ids:
    c = ctx.cells[cid]  # KeyError: can't use NotebookCell as key
```

**After:**
```python
for c in ctx.cells:  # Iterate NotebookCell objects directly
    code = c.code
    ...
```