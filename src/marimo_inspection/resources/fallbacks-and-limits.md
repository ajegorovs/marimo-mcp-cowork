# Fallbacks and Limits

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

What the MCP surface does not cover, and the intentional ways around it.

## Output coverage

The code-mode snapshot exposes ONE main output per cell plus console events —
not every frontend UI registration. If something rendered in the notebook is
missing from `get_cell_outputs`, inspect the cell's variables instead.

## Widget value shape

`set_ui_value` expects a widget-specific JSON value shape; a shape the element
cannot accept is refused before anything is applied, and the element's own value
is read back before the call returns (see
`workflow://marimo-inspect/co-work-loop` §5 for the per-widget shapes). What is
*not* awaited is the reactive re-run of dependent cells — confirm its effects
with `get_variables` or `get_cell_outputs`.

## Frontend refresh

Structural edits (add, delete, reorder) may not refresh an already-open
frontend immediately. Reload the browser if the view looks stale.

## Screenshots

Not part of the MCP surface. Capturing the rendered notebook needs Playwright
or other browser tooling.

## Server and kernel lifecycle

Launching or stopping the notebook **server** is a local/operator action — there
is no MCP tool for it. The **kernel** is different: `restart_kernel` closes the
current kernel and re-materializes a fresh one through the frontend's `/sse`
handshake, so the window in which the server is at zero sessions is closed
again; the server process — and therefore its skew-protection token, any
already-open page and its websocket — survives the restart. Use it when the
kernel itself is the problem: an imported package's source changed (module
cache), a new dependency was installed, the kernel is wedged, or globals are
poisoned. A cell edit never needs one: `edit_cell` + `run_cell` apply live, and
`edit_cell` already serializes the edit back to the `.py`.

What it costs, stated in the payload rather than implied:
`execution_state_reset` and `widget_values_reset` are true (kernel globals,
module caches and widget values are gone — values are back at their constructor
defaults), every cell is `stale` until re-run, and `cell_ids_stable` is `false`
because a cell created in-session can come back under a **different cell id**.
The change tracker is cleared (`change_tracking_cleared: true`), so every cell
reports `needs_read` again until `get_cell_data` re-reads it — cached ids and
code hashes from before the restart are not a valid basis for anything. The
notebook file on disk survives; the server process survives.

A success is **point-in-time, not durable**: the payload reports
`session_id_stable: false` and `session_verification: "point_in_time"`. The tool
opens its own `/sse` stream, verifies the expected id in `/api/sessions`, then
closes that stream — the re-materialized session is an ordinary orphan, so a
later browser reconnect can re-key it to a new id and the server's configured
session **TTL** can reap it. If a later call answers `Invalid session id`,
re-run `list_active_notebooks` and re-bind; do not treat the id as stable.

The restart endpoint requires the `Marimo-Server-Token` skew header. This
surface reads it from the server's own page: `GET /` renders a
`<marimo-server-token>` element whose `data-token` attribute carries it — the
same value the frontend sends. The token is server-process scoped, so it is
stable across a kernel restart and changes only when the server process is
relaunched —
`skew_token_rotated` is a *measurement*, not an inference, and is `null` when
skew protection is off or the rotation could not be measured;
`server_process_preserved` is `null` in those same cases and `false` when the
token was observed to rotate. The token and its fingerprint are never exposed in
a payload. A POST the endpoint refuses 401 is reported with
`reason: skew_token_unavailable` and states whether a token was read and tried;
if the endpoint returns 403 it is `reason: edit_required` (the endpoint is
served in `edit` mode only). If the session census itself is refused with 401
the call fails up front with `reason: auth_required`, and **nothing is closed**.

A restart is **never reported as success over a sessionless server**. The tool
refuses up front when the id is not live (`reason: session_not_found`, nothing
changed and `state_changed: false`), and if the kernel *was* closed but a
session with the **expected id** could not be confirmed afterwards it reports
`session_not_rematerialized` — or `server_sessionless` when the server is left
at zero sessions — with `state_changed: true`. A lone *different* live id is
never adopted as the re-materialized session. A transport failure on the restart
POST leaves the outcome *unknown*: `state_changed`/`restarted` are `null` and
the message says so, rather than claiming nothing was closed. When a confirmed
session is later gone, every tool call on that session fails (`Invalid session
id`): re-materialize a session (open the notebook in a browser, or run the
`/sse` handshake) and re-bind before continuing.

## A session is not a run

Materializing a session starts a kernel; it does not execute the notebook until
a client asks for it. Until the cells have run there are no committed widget
values, so `get_cell_outputs` and `get_errors` report an empty execution state —
accurately, not as a bug.

## Bulk execution is a `run_cell` mode, not a tool

The advertised 15-tool surface is fixed: there is no separate run-all tool.
Bulk execution lives in `run_cell(mode="all")`, which queues every document cell
and is the way to execute an unreferenced cell (and register the widgets it
defines) on a fresh, never-instantiated session. `run_cell`'s `cell_id` is a
cell **id or cell name**, resolved the way `ctx.cells` resolves a key, and every
failure keeps a top-level `error` string beside the structured
`status`/`reason`.

`run_cell(mode="descendants")` is the narrower option: it needs the target
**registered in the kernel dependency graph**. A fresh session has an empty
graph, so an unregistered target returns `reason: graph_unpopulated` and runs
nothing — that refusal is deliberate (a silent single-cell run would look like
success). See `workflow://marimo-inspect/co-work-loop` §4 for the full mode and
response contract.

A `run_cell` `cells[]` row's `errors` is `null` (`errors_readable: false`) when
the post-run error channel could not be read — a private-API/read failure, not
"no errors". That is a stated limit, not a silent success: such a target is
reported in `not_run_cell_ids` / `unverified_cell_ids` and is **never** counted
in `succeeded_cell_ids`.

`get_variables` reports **nothing** until a notebook cell has defined and
executed a name, and even then an unfiltered call lists only that notebook's
executed **public** names: kernel-injected globals (e.g. `input`), the
inspection template's own scaffolding, private leading-underscore names, and
definitions from cells that have not run are all excluded.

Cell *structure* is independent of execution: `get_cell_map` and `get_cell_data`
read source, and `get_dependency_graph` still inventories every live notebook
cell, so its `cells[]` ids match `get_cell_map` before any cell has run. Only
its **graph-derived** `defs`/`refs`/`parent_cell_ids`/`child_cell_ids` are
empty for a cell the kernel dependency graph has not registered — registration
is the sole condition, not execution status — no edge is invented, and no cell
is dropped.

An in-process lint (`lint_notebook`, `marimo check`) executes nothing either.

A **browser** client instantiates the session by opening the notebook, which is
what runs the cells, and its controls only hold values from then on. A session
created with the `/sse` handshake is not instantiated: `/api/kernel/instantiate`
is skew-token-gated, and this surface deliberately does not call it — the token
is served in the page HTML, but a tool sold as a **reset** must not execute
arbitrary notebook code. Without a browser, the write
tools are the route — cells created or run by `create_cell` / `run_cell` do
execute, together with their dependents, and `run_cell(mode="all")` executes the
whole document (see "Bulk execution is a `run_cell` mode, not a tool" above).

## Script escape hatch

Arbitrary kernel probes and complex multi-operation CodeMode blocks remain a
deliberate script escape hatch: run `marimo._code_mode` via the scratchpad.
The MCP surface is intentionally narrow — there is no general execute /
arbitrary-code tool.

## Dependency graph is whole-notebook

`get_dependency_graph` always returns the **full** notebook: one entry per live
cell, so the `cells[]` id set matches `get_cell_map` even for a session whose
cells have not run. Graph metadata exists only where the kernel graph does:
registration is the sole condition, so a cell the graph has not registered is
reported with empty `defs`/`refs` and no parent/child ids rather than
disappearing or gaining invented edges. Do not infer registration from
execution status — `create_cell`/`edit_cell` register a cell and its edges
before `run_cell`, so an unexecuted cell may still carry metadata.
`cell_id` and `depth` are refused (`reason:
unsupported_argument`, nothing is read) instead of accepted-and-ignored; walk
`cells[].parent_cell_ids` / `child_cell_ids` for a neighbourhood yourself.

## Session binding is process-global, and scoped

A bind (`list_active_notebooks`' auto-bind or `set_active_session`) is written
to the MCP session's server-side state **and** to a process-global fallback,
consulted only when that state has nothing bound.

- **stdio**: one server process serves exactly one client, so the fallback is
  connection-global — `session_id`/`server_url` are omittable on every later
  call, including from fastmcp's own `Client`, which starts a fresh MCP session
  per request on the pinned fastmcp 4.0.3 (stdio and HTTP alike).
- **HTTP/SSE**: one process serves many clients, so the fallback is served only
  while the process has seen **one** client session. Once a second distinct
  client session appears, argument-less calls are refused with `reason:
  binding_ambiguous` — the server will not let a client that never bound
  inherit another client's notebook and mutate the wrong one. Pass both
  arguments explicitly on such a client; they always win.

### The bind validates before it writes

`set_active_session` checks the id against **live** sessions before it binds
anything, so `status: OK` means the binding can actually reach the session:

- An explicit `server_url` is deterministic: that one server's
  `GET /api/sessions` must report the exact id.
- Without one, discovery must find the id on **exactly one** endpoint. More
  than one endpoint reporting it is `reason: session_ambiguous` (which server
  is meant is never guessed); none is `reason: session_not_found`.
- A refusal reports `bound: false` and `state_changed: false` and leaves any
  earlier binding untouched — a bind is never a guess and never half-written.
  An empty id is `reason: invalid_session_id`; a live id with no MCP context
  to bind it to is `reason: binding_context_unavailable`.

See `workflow://marimo-inspect/co-work-loop` §1.

## Version pin

marimo is pinned to 0.24.x because private APIs are used. Do not widen the
bound without running the live suite.
