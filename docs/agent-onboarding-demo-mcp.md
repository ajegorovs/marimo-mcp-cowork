# Agent Onboarding Demo — MCP Tools Edition

> Redesigned demo that uses the unified MCP tools for discovery, verification,
> state-reading, **and mutation** (create/edit/run/delete cells). MCP-first
> writes go through first-class MCP tools; `execute-code.sh` is only a bounded
> fallback for arbitrary probes / multi-op `cm` blocks.

## Purpose

Showcase two things at once:

1. **MCP read + write tools** — the agent discovers, verifies, reads, AND mutates
   notebook state through structured MCP tools (no inline scratchpad code).
2. **User agency** — the scenario is conversational: the agent asks the user
   reasonably open (but topic-constrained) questions and waits for confirmation
   at each decision point before proceeding.

## Statefulness (Phase 1)

Since the stateful upgrade, `session_id` is **optional** on every read tool:
after `list_active_notebooks()` discovers and auto-binds a session, subsequent
calls (e.g. `get_cell_map`, `get_variables`, `get_errors`) work with
`session_id` **and** `server_url` omitted — `list_active_notebooks` binds both,
and every other tool falls back to that binding. (The binding lives in the MCP
server process/connection: a harness that spawns or reconnects the server per
call/turn wipes it, so pass `server_url`/`session_id` explicitly in that case.)
On this Linux machine, the discovery
registry lives at `~/.local/state/marimo/servers/` (Windows used
`~/.marimo/servers`); `list_active_notebooks()` with no args finds servers
there automatically. `set_active_session(session_id)` rebinds explicitly if
needed.

**Caveat:** if you edit MCP server code while the gateway connection is live,
the `--reload` respawn drops the bound session (and can wedge the gateway
connection — recover with `/reload-mcp` or a restart). For a stable interactive
demo, don't edit server code mid-demo. **Before starting:** confirm the gateway
actually exposes the write tools (`create_cell`/`edit_cell`/`run_cell`/
`delete_cell`). A gateway connection that started before those tools existed
will not have them until you run `/reload-mcp` (or restart Hermes). If
`list_active_notebooks` fails or the tools are missing, invoke `/reload-mcp`
first.

## Write surface (Phase 3)

Mutation is first-class MCP: `create_cell`, `edit_cell`, `run_cell`,
`delete_cell`. MCP is the normal co-work loop; `execute-code.sh` remains the
intentional fallback for arbitrary probes and complex multi-op `cm` blocks.
Key points:

- All four accept the same optional `session_id` / `server_url` as the
  read tools, and return structured JSON (`status`, `cell_id`, `code_hash`).
  Both are optional once a session is bound: `list_active_notebooks` binds
  `session_id` **and** `server_url`, and every other tool falls back to that
  binding. Pass them explicitly (or call `set_active_session`) when the harness
  spawns or reconnects the MCP server per call, since that wipes the in-process
  binding.
- **`edit_cell` has a staleness guard** (`check_fresh=True` by default; mirrors
  Hermes' file-edit guard). It compares the cell's live source hash against the
  change tracker's snapshot from the agent's last read (via
  `get_cell_map`/`get_cell_data`). A cell this agent has never read returns
  `status: "needs_read"` unconditionally; a cell whose source changed since the
  last read returns `status: "conflict"`. Recover by re-reading with
  `get_cell_data` (which records the read baseline) or `get_cell_map`, then
  retrying. `check_fresh=False` is an explicit force escape hatch, **not** the
  recovery path; a missing cell id errors before anything is mutated. A
  successful edit returns the post-edit `code_hash`.
- **New cells are visible by default** — `create_cell` defaults to
  `hide_code=False`, so the code shows in the UI. Pass `hide_code=True`
  explicitly for a setup/implementation cell you want hidden.
- The agent's own writes refresh the change-tracker snapshot, so they don't
  reappear as external `changes_since_last` events.
- `execute-code.sh` remains available as a fallback for complex `cm` blocks, but
  for normal co-work the MCP write tools are the surface.
- **Writes persist to the notebook file on disk.** `create_cell`/`edit_cell`/
  `delete_cell` rewrite the `.py` (new `@app.cell` blocks appear in the source).
  For a demo against a *committed* fixture notebook, git-restore the file
  afterward (or copy it to a scratch path first) so the run doesn't pollute the
  repo.

## Prerequisites

- **Dependencies come from `uv sync`.** The demo notebooks import `numpy` and
  the plot step imports `matplotlib`; both are declared in `pyproject.toml`
  (added 2026-09-06 after an interactive run failed with
  `ModuleNotFoundError: No module named 'numpy'`). `marimo[recommended]` does
  **not** pull either in — if you see that error, you're on a pre-fix tree or a
  stale venv: run `uv sync`.
- **A marimo server materializes a session only when a client connects.** A
  `--headless` launch never opens a browser and never creates a session on its
  own, so `list_active_notebooks()` returns `total_notebooks: 0` (and
  `GET /api/sessions` returns `{}`) until one exists. Two ways to get a session:
  - Launch **without `--headless`** (step 2 below) so the browser opens and
    performs the handshake for you.
  - Keep `--headless` and create the session yourself with the `/sse` handshake
    (the same mechanics the live-test suite uses — see `docs/live-tests.md`):
    ```python
    import asyncio, uuid
    import httpx2 as httpx


    async def handshake(server_url: str, notebook_path: str) -> str:
        session_id = str(uuid.uuid4())
        params = {"session_id": session_id, "file": notebook_path}
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream("GET", f"{server_url}/sse", params=params) as response:
                assert response.status_code == 200, await response.aread()
                async for line in response.aiter_lines():
                    if "kernel-ready" in line:
                        break
        return session_id
    ```
    Then pass the returned `session_id` to `list_active_notebooks(server_url=...)`
    (or straight to the read tools). `list_active_notebooks` takes only
    `server_url` — pass the handshake `session_id` to the read/write tools, or
    call `set_active_session(session_id)` to rebind explicitly.

## How an agent loads and runs this

1. **Use the committed starter notebook** `notebooks/function_plotting_demo.py`
   (two cells — a `# Function Plotting Showcase` title and a `numpy` import).
   It is already on disk; do not hand-write it. Its source, for reference:
   ```python
   import marimo

   __generated_with = "0.24.0"
   app = marimo.App(width="full")


   @app.cell
   def _():
       import marimo as mo

       mo.md("# Function Plotting Showcase")
       return (mo,)


   @app.cell
   def _():
       import numpy as np

       return (np,)


   if __name__ == "__main__":
       app.run()
   ```

2. **Launch without `--headless`** so the marimo page opens automatically:
   ```bash
   uv run marimo edit notebooks/function_plotting_demo.py --no-token
   ```
   Use a detached/session-surviving launch (an `setsid`/`nohup` wrapper) if the
   agent's shell would otherwise kill the server on exit; see AGENTS.md for the
   launch pattern. A bare `--headless` launch with no browser creates no
   session, so nothing is discoverable — if you must stay headless, create the
   session with the `/sse` handshake below.

3. **Discover via MCP** — use `list_active_notebooks()`; `discover-servers.sh`
   is a human/debug fallback only, not part of the agent flow:
   ```python
   from marimo_inspection.tools.notebooks import list_active_notebooks

   result = await list_active_notebooks(server_url="http://127.0.0.1:PORT")
   session_id = result["notebooks"][0]["session_id"]
   ```

4. **Step through the numbered scenario sections below in order.** Each MCP
   read is a single tool call — no inline Python needed.

   **Client plumbing** (if you talk to the server via raw MCP SDK, not a hosted
   tool): run the MCP `initialize` handshake before the first tool call, or you
   get `MCPError: Invalid request parameters`.

5. **When the demo is done, tear down cleanly** (delete demo cells, reconcile
   the notebook) so the session is reusable. **Note:** the auto-bind is
   per-connection. If you open a fresh MCP connection (e.g. for teardown), call
   `list_active_notebooks()` again on that connection before `delete_cell` —
   otherwise cells refuse without a bound `session_id`.

---

## Scenario: function plotting showcase (MCP edition)

**Topic constraint:** function plotting. The agent builds a small interactive
showcase that hands agency to the user at each decision point.

### Step 1 — User asks for a demo
The user requests an onboarding showcase. Load this runbook and start at Step 2.

### Step 2 — Create + launch the showcase notebook
Create the notebook file (see above) and detach-launch `marimo edit`.
**Discover via MCP** instead of terminal scripts.

**Call:** `list_active_notebooks(server_url)`

**Result:**
```json
{
  "summary": {"servers_discovered": 1, "total_notebooks": 1},
  "notebooks": [
    {
      "session_id": "s_xhe4j1",
      "path": "notebooks/function_plotting_demo.py"
    }
  ]
}
```
The first valid session is auto-bound — subsequent tools can omit `session_id`.

### Step 3 — Verify initial state via MCP
**Call:** `get_cell_map(session_id, server_url)`

**Result:** 2 cells in "stale" status (unrun); may already be "idle" if the
session auto-ran on open — Steps 4-5 handle both.
```json
{
  "cells": [
    {
      "cell_id": "Hbol",
      "name": "_",
      "preview": "import marimo as mo\nmo.md(\"# Function Plotting Showcase\")",
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

**Verify:** cells are in "stale" status, imports are defined but not executed.

### Step 4 — Run starter cells via MCP
**Mutate:** call `run_cell(cell_id, server_url)` for every stale cell id from
the `get_cell_map` result. (The ids may be anything; run each stale one.)

### Step 5 — Verify imports ran via MCP
**Call:** `get_cell_map(session_id, server_url)`

**Verify:** cells moved from "stale" to "idle" status.

### Step 6 — User adds a cell
The user adds a new cell to the notebook (e.g., `arr = np.array([3,4,5])`).

### Step 7 — Detect changes via MCP
**Call:** `get_cell_map(session_id, server_url)`

**Result:** 3 cells — the new cell is in "stale" status
```json
{
  "cells": [
    {"cell_id": "Hbol", "runtime_state": "idle", "preview": "import marimo as mo..."},
    {"cell_id": "MJUe", "runtime_state": "idle", "preview": "import numpy as np"},
    {"cell_id": "XQlR", "runtime_state": "stale", "preview": "arr = np.array([3,4,5])"}
  ],
  "total_cells": 3
}
```

**Detect:** cell `XQlR` is stale (user added it).

### Step 8 — Run the user's cell via MCP
**Mutate:** call `run_cell(cell_id, server_url)` on the new cell (XQlR).

### Step 9 — Verify via MCP
**Call:** `get_cell_map(session_id, server_url)`

**Verify:** all 3 cells are now "idle".

**Call:** `get_variables(session_id, server_url)`

**Result:** `arr` is now in the kernel
```json
{
  "variables": {
    "arr": {
      "value": "[3 4 5]",
      "datatype": "numpy.ndarray",
      "shape": [3],
      "dtype": "int64"
    }
  }
}
```

### Step 10 — Add controls via MCP
**Mutate:** call `create_cell(source, server_url)` for each
control cell, THEN `run_cell` each one. (`create_cell` defaults to
`hide_code=False` — visible — which is what a control cell wants.)
`create_cell` does **not** auto-run a
cell — a cell's variables only exist in the kernel once it runs. So run every
created cell before you depend on its names anywhere else.

**Cell — function picker:**
```python
f_pick = mo.ui.dropdown(
    options={"sin(x)": "np.sin(x)", "cos(x)": "np.cos(x)", "x**2": "x**2"},
    value="sin(x)",
    label="preset",
)
f_custom = mo.ui.text(value="", placeholder="your own, e.g. sin(2x)", label="custom")
mo.vstack([f_pick, f_custom])
```

**Cell — start/span sliders:**
```python
start_s = mo.ui.slider(-20, 20, value=-5, step=1, label="Start:", show_value=True)
span_s = mo.ui.slider(1, 40, value=20, step=1, label="Span:", show_value=True)
mo.vstack([start_s, span_s])
```

**Note:** Always include `show_value=True` on sliders so the user sees the current
value next to the control. This is required for the demo to be interactive.

**Dependency-order caveat:** run newly created cells in dependency order. If a
downstream cell references a name from a not-yet-run cell, `run_cell` of the
upstream cell can return `"error": "Execution failed"` carrying a `NameError`
from the still-stale downstream cell (marimo re-runs dependents automatically).
That is not a failure of your cell — run the remaining cells to resolve it.
For this demo, create the picker + slider cells first, then run both, before
creating any cell that reads `f_pick`/`start_s`/`span_s`.

### Step 11 — Verify controls ran via MCP
**Call:** `get_cell_map(session_id, server_url)`

**Verify:** new cells exist AND are `idle` (ran), not `stale`. Expected count is
**4 idle cells** from the bare 2-cell starter (2 starter + 2 controls), or **5**
if you also did the narrative Steps 6-8 (user-added `arr` cell).
`get_variables(["f_pick","f_custom","start_s","span_s"])` should return them
(see Step 13) — an empty `variables: {}` means the control cells have not been
run yet; run them first.

### Step 12 — Ask the user to pick a function
Prompt: *"Pick a function — sin(x), cos(x), x**2 — or type your own,
then confirm in chat."* Wait for the user to set the picker/sliders.

### Step 13 — Read widget state via MCP
**Call:** `get_variables(session_id, variable_names=["f_pick", "f_custom", "start_s", "span_s"], server_url)`

Returns structured JSON with widget values. Note the **real schema is nested**:
each variable carries `{value: {value, datatype}, datatype}`, and scalar
`value`s are always STRING-serialized:
```json
{
  "variables": {
    "f_pick":  {"value": {"value": "np.sin(x)", "datatype": "str"},     "datatype": "dropdown"},
    "f_custom": {"value": {"value": "",         "datatype": "str"},     "datatype": "text"},
    "start_s": {"value": {"value": "-5",        "datatype": "int"},     "datatype": "slider"},
    "span_s":  {"value": {"value": "20",        "datatype": "int"},     "datatype": "slider"}
  }
}
```
**Extract** the selection at `variables[name]["value"]["value"]` (the inner
`value` — a plain string). Cast numeric `value`s to `int`/`float` as needed:
`int(variables["start_s"]["value"]["value"])`.

### Step 14 — Create + run plot cell via MCP
**Mutate:** call `create_cell(source, server_url)` (visible by default) with the
materialized function from the user's choice. Then `run_cell` it.

**Cell — plot (use matplotlib, marimo renders it):**
```python
import matplotlib.pyplot as plt

x = np.linspace(start_s.value, start_s.value + span_s.value, 1000)

if f_pick.value == "np.sin(x)":
    y = np.sin(x)
    label = "sin(x)"
elif f_pick.value == "np.cos(x)":
    y = np.cos(x)
    label = "cos(x)"
else:
    y = x**2
    label = "x**2"

fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(x, y, label=label, linewidth=2)
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.set_title(f"Function Plot: {label}")
ax.legend()
ax.grid(True, alpha=0.3)

fig  # Return fig — marimo renders matplotlib figures automatically
```

**Note:** Marimo supports matplotlib, seaborn, plotly, and altair natively.
Just import and use them as normal — marimo renders the output automatically.
For interactive pan/zoom, use `mo.mpl.interactive(fig)`. For reactive
selections on the frontend, use `mo.ui.matplotlib(ax)`.

### Step 15 — Verify no errors via MCP
**Call:** `get_errors(session_id, server_url)`

**Verify:** `has_errors=False`, `total_errors=0`, and
`has_console_exception=False` (the console channel is reported separately —
`structured_errors` vs `console_stderr`).

### Step 16 — Confirm chart rendered via MCP
**Call:** `get_cell_outputs(session_id, [plot_cell_id], server_url)`

**Verify:** chart output exists in the response.

### Step 17 — Wrap up / teardown
**Optional:** call `lint_notebook(session_id, server_url)` for a final check.
Delete demo cells with `delete_cell(cell_id, server_url)` and reconcile the
notebook.

---

## Agent Loop Pattern

The core loop that repeats throughout the demo:

```
1. MCP READ:    get_cell_map() → detect stale/new cells (changes_since_last)
2. MCP MUTATE:  run_cell() / create_cell() / edit_cell()
3. MCP READ:    get_cell_map() → verify cells ran
4. MCP READ:    get_variables() → read kernel state
5. MCP READ:    get_errors() → verify clean run
```

Both reads and writes go through MCP tools — no inline Python needed.

---

## MCP Tool Reference

| Tool | Purpose | Replaces |
|------|---------|----------|
| `list_active_notebooks(server_url)` | Discover sessions | `discover-servers.sh` (human/debug fallback) |
| `get_cell_map(session_id, server_url)` | Cell structure + previews + changes | Inline scratchpad |
| `get_cell_data(session_id, cell_ids, server_url)` | Full cell content | Inline scratchpad |
| `get_cell_outputs(session_id, cell_ids, server_url)` | Execution outputs | Inline checking |
| `get_variables(session_id, server_url)` | Kernel variables | `print(var)` calls |
| `get_dependency_graph(session_id, server_url)` | Dataflow relationships | Manual traversal |
| `get_errors(session_id, server_url)` | Error aggregation | Manual aggregation |
| `lint_notebook(session_id, server_url)` | Lint diagnostics | `uv run marimo check` |
| `create_cell(source, ...)` | Write a new cell | `execute-code.sh` (fallback only) |
| `edit_cell(cell_id, source, ...)` | Update a cell (staleness guard) | `execute-code.sh` (fallback only) |
| `run_cell(cell_id)` | Execute a cell | `execute-code.sh` (fallback only) |
| `delete_cell(cell_id)` | Remove a cell | `execute-code.sh` (fallback only) |
| `set_active_session(session_id)` | Rebind the active session explicitly | passing `session_id` on every call |
| `set_ui_value(variable_name, value)` | Set a live widget value (accepts no source code) | manual UI interaction |

## Key Improvements

1. **Discovery** — `list_active_notebooks()` returns structured JSON instead
   of parsing terminal output.
2. **Verification** — `get_cell_map()` confirms cell state after every mutation.
3. **State reading** — `get_variables()` returns clean widget values instead of
   inline scratchpad code.
4. **Error checking** — `get_errors()` aggregates errors across all cells.
5. **Output verification** — `get_cell_outputs()` confirms charts rendered.
6. **Change detection** — `get_cell_map()` reports `changes_since_last`
   (new/edited/removed/state-changed) so the agent knows what changed since its
   last observation.
7. **Unified write surface** — `create_cell`/`edit_cell`/`run_cell`/`delete_cell`
   replace shell injection; `edit_cell` guards against clobbering concurrent edits.
8. **Composability** — all tools return JSON with `next_steps` guidance.
9. **Widget interaction** — `set_ui_value` sets a live widget's value by its
   kernel-global name (no source code), preserving the JSON value shape; the
   reactive re-run is verified, not awaited.

## Operative rules

- **Reads AND writes via MCP.** Read with `get_cell_map`/`get_cell_data`/
  `get_variables`/`get_errors`/`get_cell_outputs`; mutate with
  `create_cell`/`edit_cell`/`run_cell`/`delete_cell`; set a live widget value
  with `set_ui_value`. `execute-code.sh` is the bounded fallback for arbitrary
  probes / multi-op `cm` blocks — not the normal loop.
- **Respect the edit guard.** Before `edit_cell`, ensure you recently read the
  cell. If it returns `status: "conflict"`/`"needs_read"`, re-read via
  `get_cell_data`/`get_cell_map`, then retry.
- **Verify before proceeding.** After every mutation, call the appropriate MCP
  tool to confirm the change took effect.
- **Use explicit server_url.** The tools accept `server_url` as a parameter;
  pass the discovered server URL for reliability.
- **Handle errors gracefully.** MCP tools return structured error information
  that can be inspected before retrying.

---

## MCP Tool Usage Patterns

### Discovery Pattern
```python
# 1. Discover active notebooks
notebooks = await list_active_notebooks(server_url="http://127.0.0.1:PORT")
session_id = notebooks["notebooks"][0]["session_id"]

# 2. Get cell map
cells = await get_cell_map(session_id, server_url=server_url)
```

### Verification Pattern
```python
# After mutation, verify cell states
cells = await get_cell_map(session_id, server_url=server_url)
for cell in cells["cells"]:
    if cell["runtime_state"] == "stale":
        print(f"Cell {cell['cell_id']} is stale")
```

### State Reading Pattern
```python
# Read widget values
vars = await get_variables(
    session_id, variable_names=["widget_name"], server_url=server_url
)
# A UI element's value is NESTED: the outer "value" holds the serialized
# element, the inner "value" is the actual selection (see Step 13).
widget_value = vars["variables"]["widget_name"]["value"]["value"]
```

### Widget Interaction Pattern
```python
# Set a live widget's value by its kernel-global name (accepts NO source code).
# The accepted JSON shape is widget-specific; verify the effect after setting it.
r = await set_ui_value("start_s", 10, server_url=server_url)

# The call does not await downstream re-runs — verify the effect:
vars = await get_variables(
    session_id, variable_names=["start_s"], server_url=server_url
)
```

**Naming a widget target.** `variable_name` must be a *kernel global*: the
widget has to be assigned at cell top level and its cell must have run, so the
name exists in `ctx.globals`. Use a plain name (`start_s`, `f_pick`) — marimo
treats a leading-underscore name as cell-private, so `_slider` is a poor
target. A widget whose cell has never run is not addressable yet.
`set_ui_value` reads the value back only through `get_variables`/
`get_cell_outputs`; nothing echoes `hide_code`, so a cell's UI visibility
cannot be confirmed through the MCP read surface (it is set at creation time).

### Error Checking Pattern
```python
# Check for errors across all cells
errors = await get_errors(session_id, server_url=server_url)
if errors["has_errors"]:
    print(f"Errors detected: {errors['total_errors']}")
```

### Write Pattern
```python
# Create a cell
r = await create_cell(
    source="x = 42", server_url=server_url
)  # visible (hide_code=False by default)
cell_id = r["cell_id"]

# Run it
await run_cell(cell_id, server_url=server_url)

# Edit it (guard: must have read it recently to avoid a stale write)
r = await edit_cell(cell_id, source="x = 43", server_url=server_url)
if r.get("status") in ("conflict", "needs_read"):
    await get_cell_data(cell_id, server_url=server_url)  # re-read first
    r = await edit_cell(cell_id, source="x = 43", server_url=server_url)

# Delete it
await delete_cell(cell_id, server_url=server_url)
```

### Key Points
- **Reads AND writes go through MCP tools.** Mutate with
  `create_cell`/`edit_cell`/`run_cell`/`delete_cell`; set live widget values
  with `set_ui_value`. `execute-code.sh` stays as the bounded fallback for
  arbitrary probes / multi-op `cm` blocks.
- **Pass `server_url` explicitly.** Tools accept `server_url` as a parameter;
  do not rely on discovery for every call.
- **Check `next_steps` field.** Every tool response includes `next_steps` with
  guidance on what to do next.
- **Use cell IDs from `get_cell_map`.** The `cell_id` field identifies each cell;
  use these IDs with `get_cell_data`, `get_cell_outputs`, and mutation commands.
- **Respect `edit_cell`'s staleness guard.** Re-read the cell if you get
  `status: "conflict"`/`"needs_read"` rather than forcing with `check_fresh=False`.
- **Read widget values via `get_variables`.** For slider/dropdown/text widgets,
  call `get_variables(session_id, variable_names=["widget_name"], server_url)`
  to get the current selection.
- **Plot with matplotlib.** Use `plt.subplots()` and return `fig` for rendering.
  Marimo renders matplotlib figures automatically. For interactive viewers,
  use `mo.mpl.interactive(fig)`.