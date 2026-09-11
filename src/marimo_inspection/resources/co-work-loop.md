# Co-Work Loop

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

The MCP-first loop for co-working on a live marimo notebook. Each step names
the exact tool to call.

## 1. Discover and bind

`list_active_notebooks` — lists live sessions and auto-binds the first one,
setting both `session_id` and `server_url`.

The binding is written to **two server-side places**: the MCP session's own
state, keyed by the MCP session identity your client negotiates, and a
**process-global fallback** consulted only when that state has nothing bound.

- **stdio — argument-less calls always work.** One server process serves
  exactly one client, so the process-global fallback is connection-global and
  reaches every later call. That includes fastmcp's own `Client`, which starts
  a fresh MCP session per request on the pinned fastmcp 4.0.3 (measured
  2026-09-11, over stdio and HTTP alike). Verified: after a bind, an
  argument-less `get_cell_map` through fastmcp's `Client` reached the bound
  server URL instead of refusing.
- **HTTP / SSE — the fallback is scoped to a single-client process.** One
  process serves many clients there, so the fallback is served only while the
  process has seen one client session. An `mcp`-SDK-based HTTP client (one MCP
  session for the connection) is served from its own session state; a
  session-per-request client is served only until a second client session
  appears. After that, an argument-less call is refused with `reason:
  binding_ambiguous` instead of picking up another client's notebook — the
  binding fails closed rather than being guessed.

So over HTTP/SSE, when the client is not known to keep one MCP session, pass
`session_id` and `server_url` explicitly on every call. Explicit arguments
always win, and they cost one line. The refusal tells you which case you hit:
`binding_ambiguous` means this process could not attribute the fallback to your
call, so it withheld it. This is a limitation of client-session handling, not
of the notebook session.

## 2. Orient

`get_cell_map` — cell ids, previews, line counts, runtime state, and the
`has_output` / `has_console_output` / `has_errors` flags. Start here to pick a
cell. Previews only: this does **not** record an `edit_cell` read baseline.

## 3. Read

`get_cell_data` — full source and runtime data for chosen cells. This is the
read that records the `edit_cell` baseline.
`get_cell_outputs` and `get_variables` cover execution state.

A requested `cell_id` that resolves to nothing (deleted or mistyped) is reported
in `missing_cell_ids` — an id that matched no cell must never look like "nothing
matched" while the write tools refuse the same id.

**Read before edit.** `edit_cell` refuses to overwrite a cell you have not
just read (see `workflow://marimo-inspect/live-safety`). Only `get_cell_data`
records that baseline — a `get_cell_map` preview is not a source read.

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
The correction is derived from the element's **own option keys**, so it is a key
the element actually accepts: a multiselect keyed by `"4"` is corrected to
`["4"]`, never `[4]`. Send that corrected form; do not repeat the rejected
shape.

`status: ok` guarantees the read-back succeeded (`verified: true`): either the
element's own value **moved** (`applied: true`, with `value_before` /
`value_after`), or it already held that value (`applied: false` +
`no_change: true`). Only a read-back that failed reports `verified: false`, with
a `warning` saying the value is unconfirmed.

A value the kernel raised on while applying it is `status: error` with the
kernel's own message in `kernel_message`; the `reason` says *which* failure it
was, straight from the read-back:

- `value_not_applied` — the element's value did **not** move (`applied:
  false`; `value_before` = `value_after`). marimo rejected the conversion before
  assigning it, e.g. an unknown dropdown key. Re-send the corrected value; never
  treat that call as done.
- `on_change_failed` — the value **did** move (`applied: true`, with
  `value_before`/`value_after` proving it) and the element's own `on_change`
  handler raised *after* the assignment. The widget holds its new value; only
  the handler's side effects and the dependent cells it re-ran failed. Fix the
  handler in the widget's cell and re-run it — do not re-send the value, and do
  not report the interaction as "the widget did not change".

## 6. Verify

After every write, confirm with `get_variables`, `get_cell_outputs`, and
`get_errors`. Widget updates and cell runs are queued and flushed on code-mode
context exit, and the reactive re-runs of *dependent* cells are not awaited by
the write call — so verify their effects, don't assume them. `get_errors`
reports `structured_errors` and `console_stderr` separately;
`has_errors`/`total_errors` count structured errors only.

A cell is flagged on the console channel only on **real exception evidence**: a
traceback header, or an exception-typed line (`ValueError: ...`). Ordinary log
text such as `Error: 3 rows skipped` is a message, not an exception, and flags
nothing. Each flagged cell names the evidence it found in
`console_exception_evidence` (`"traceback"` / `"exception_line"`) — read that
instead of assuming a UI-handler traceback.

**A failed `run_cell` reports through its own payload, and `get_errors`'
structured channel can be silent about it.** `run_cell` returns `{"error":
"Execution failed", "stderr": <traceback>}` while `get_errors` reads marimo's
*structured* records — which are not written for one failure class: a cell that
references a name marimo resolves as another cell's **cell-private** variable
(a leading-underscore name, see §5). That cell ends `status: "exception"` with
an empty `cell.errors`, so `get_errors` reports `has_errors: false` and counts
no structured error for it — but its traceback is still visible: in the run
payload, and in that cell's `console_stderr` entry under the console channel.
Ordinary runtime failures (`1/0`, or a name that exists nowhere) are recorded in
both channels. So never use `get_errors`' structured counts alone as the
post-run check — read the run payload first, and use `get_errors` (structured
*and* console) for the notebook-wide picture.

## 7. Lint

`lint_notebook` — static checks that need no kernel execution.

For structural changes, call `get_dependency_graph` before `delete_cell` or
before merging cells. Then repeat steps 2–7 as the notebook evolves.

`get_dependency_graph` always returns the **full** graph: `cell_id` and `depth`
are refused (`reason: unsupported_argument`), never accepted-and-ignored. Walk
`cells[].parent_cell_ids` / `cells[].child_cell_ids` for a neighbourhood, and
`cells[].cell_name` matches the name `get_cell_map` reports.
