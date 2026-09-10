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

`set_ui_value(variable_name, value)` — set a live widget's value by its
kernel-global name. It accepts no source code, and it **never coerces**: send
the shape the element's own declaration accepts, derived from the widget type —

| Element | `value` shape | Example |
| --- | --- | --- |
| `slider`, `number`, `text`, `text_area`, `code_editor`, `date` | scalar | `7`, `"hello"` |
| `checkbox`, `switch` | bool | `true` |
| `radio` | option key (scalar) | `"alpha"` |
| `dropdown` | **one-element list** of the option key | `["beta"]` |
| `multiselect` | list of option keys | `["a", "b"]` |
| `range_slider` | two-element list | `[2, 8]` |
| `matrix`, `file`, `file_browser` | list (rows / file specs) | — |
| `table`, `array`, `dictionary`, `dataframe`, … | opaque — no shape guard, read-back only | — |

A shape the element cannot accept is refused **before** anything is applied: the
error carries `reason: value_shape_mismatch`, the declaration it read in
`accepted_shape` (e.g. `list[str]`), and the corrected payload in
`did_you_mean` — a scalar sent to a dropdown returns `did_you_mean: ["beta"]`.
Send that corrected form; do not repeat the rejected shape.

`status: ok` guarantees the read-back succeeded (`verified: true`): either the
element's own value **moved** (`applied: true`, with `value_before` /
`value_after`), or it already held that value (`applied: false` +
`no_change: true`). Only a read-back that failed reports `verified: false`, with
a `warning` saying the value is unconfirmed. A value marimo itself rejected (an
unknown dropdown key) comes back as `status: error` with
`reason: value_not_applied`, the kernel's own message in `kernel_message`, and
the widget unmoved — never treat that call as done.

## 6. Verify

After every write, confirm with `get_variables`, `get_cell_outputs`, and
`get_errors`. Widget updates and cell runs are queued and flushed on code-mode
context exit, and the reactive re-runs of *dependent* cells are not awaited by
the write call — so verify their effects, don't assume them. `get_errors`
reports `structured_errors` and `console_stderr` separately;
`has_errors`/`total_errors` count structured errors only.

## 7. Lint

`lint_notebook` — static checks that need no kernel execution.

For structural changes, call `get_dependency_graph` before `delete_cell` or
before merging cells. Then repeat steps 2–7 as the notebook evolves.
