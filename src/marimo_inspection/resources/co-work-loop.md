# Co-Work Loop

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

The MCP-first loop for co-working on a live marimo notebook. Each step names
the exact tool to call.

## 1. Discover and bind

`list_active_notebooks` — lists live sessions and auto-binds the first one,
setting both `session_id` and `server_url`. The binding lives in the MCP
server process/connection: a client that spawns or reconnects the server per
call loses it, so pass `session_id`/`server_url` explicitly (or call
`set_active_session`) in that case.

## 2. Orient

`get_cell_map` — cell ids, previews, line counts, runtime state, and the
`has_output` / `has_console_output` / `has_errors` flags. Start here.

## 3. Read

`get_cell_data` — full source and runtime data for chosen cells.
`get_cell_outputs` and `get_variables` cover execution state.

**Read before edit.** `edit_cell` refuses to overwrite a cell you have not
just read (see `workflow://marimo-inspect/live-safety`). Reading with
`get_cell_data` or `get_cell_map` records the baseline.

## 4. Write and run

- `create_cell` — add a cell (visible by default, `hide_code=False`).
- `edit_cell` — change a cell's source; returns the post-edit `code_hash`.
- `run_cell` — execute a cell.
- `delete_cell` — only after checking the dependency graph (step below).

## 5. Interact

`set_ui_value(variable_name, value)` — set a live widget value by its
kernel-global name. It accepts no source code; the JSON shape is
widget-specific, so confirm the resulting state in the verification step.

## 6. Verify

After every write, confirm with `get_variables`, `get_cell_outputs`, and
`get_errors`. `get_errors` reports `structured_errors` and `console_stderr`
separately; `has_errors`/`total_errors` count structured errors only.

## 7. Lint

`lint_notebook` — static checks that need no kernel execution.

For structural changes, call `get_dependency_graph` before `delete_cell` or
before merging cells. Then repeat steps 2–7 as the notebook evolves.
