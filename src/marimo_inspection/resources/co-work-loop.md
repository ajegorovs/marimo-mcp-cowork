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

### Browser-first: let the page hold the main consumer connection

For co-work with a person, let the **browser** create the session: launch the
notebook and open it in a page first, then `list_active_notebooks` and bind what
the page already holds. In marimo 0.24 **edit mode without an explicit
`--session-ttl`**, the page **becomes/holds the main consumer connection** for
that session — the payload `owner` still reads `"unknown"`, because the public
API does not publish who holds that role.

Materializing a session yourself with the `/sse` handshake is for **headless,
agent-only** work. Without an explicit `--session-ttl`, closing that handshake
stream leaves the session an **orphan** (it outlives the stream) until a later
connection takes it over — though a **configured `--session-ttl` can reap that
orphan**. A human who opens the page afterwards must **take over** the session
and **re-run the notebook** before its widgets respond. One session per server in
that mode means a second distinct client does not get its own kernel: it joins
the same kernel as a **non-main, read-only consumer**, and a later reconnect can
**re-key** the session id. Run mode is out of scope for these rules. The re-key
behavior is real and *separate* from a page-vs-session divergence observed once:
the divergence's cause is **not diagnosed**, and **neither the re-key nor any
read-path explanation is confirmed as its cause**. Treat a divergence as
unexplained and prefer the browser-first order instead of assuming a mechanism.

### Binding is not ownership

Each **session** row carries `provenance: "unknown"` and `owner: "unknown"`.
marimo 0.24's public API (`GET /api/sessions`) publishes only each session's
`filename`/`path` — no creator, no owning client, no creation time, and no
per-session client count — so these are honest placeholders, not claims that a
session is unowned. Binding a session does not make you its owner; a bound
session may be a human's page.

The `summary` counts are scoped deliberately:

| field | meaning |
| --- | --- |
| `total_notebooks` | real sessions — always equal to `session_count`; a connection-failure sentinel is a row, not a notebook |
| `session_count` | sessions the servers report via `GET /api/sessions` — one per live kernel session |
| `result_row_count` | `len(notebooks)` — every row, **including** a connection-failure sentinel |
| `attached_client_count` | always `null`: marimo publishes no per-session client count (and `/api/status/connections.active` counts sessions with an open main consumer, not clients) |
| `active_connections` | **DEPRECATED** alias for `session_count` — never a client count; read `session_count` |
| `servers_discovered` | how many servers were queried |

A server that cannot be queried adds one **sentinel row** whose keys are exactly
`name`/`path`/`session_id` (`"error"`)/`server_url`/`error`. It is not a session,
so it carries no `provenance`/`owner` and is counted only by `result_row_count`.

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

Each `get_cell_outputs` row also carries the kernel's live `runtime_state` and a
derived boolean `output_stale` (true exactly when the state is `"stale"`). A
stale cell keeps its last rendering visible — including one restored from an
earlier run — so that output is readable but is **not** proof the current source
produced it. Run the cell before trusting it as current.

**Read before edit.** `edit_cell` refuses to overwrite a cell you have not
just read (see `workflow://marimo-inspect/live-safety`). Only `get_cell_data`
records that baseline — a `get_cell_map` preview is not a source read.

## 4. Write and run

- `create_cell` — add a cell (visible by default, `hide_code=False`).
- `edit_cell` — change a cell's source; returns the post-edit `code_hash`.
- `run_cell` — execute a cell, a cell with its descendants, or the whole
  notebook (see below).
- `delete_cell` — only after checking the dependency graph (step below).

### `run_cell` modes

`run_cell(cell_id, mode=...)` says what the tool **queues** — not everything the
kernel ends up running. `cell_id` is a cell **id or cell name**, resolved the way
`ctx.cells` resolves a key; `requested_cell_ids` always carries the resolved
IDs, `cell_id` echoes what you passed, and `resolved_cell_id` reports the
resolution:

| mode | queues | requires |
| --- | --- | --- |
| `mode="cell"` (default) | exactly the target cell | `cell_id` (id or name) |
| `mode="descendants"` | the target cell **plus** its `ctx.graph` descendants | `cell_id` (id or name), and the target must be registered in the kernel graph |
| `mode="all"` | **every** document cell in the notebook, in one code-mode context | an **empty** `cell_id` |

- `mode="all"` is the run-all fix: it executes cells that nothing else depends
  on, so their widgets finally register (an unreferenced widget leaf is
  unreachable from `set_ui_value` until its cell has run). It is a **full
  re-run** — already-idle cells and deliberately un-run cells run again.
- `mode="descendants"` **never silently degrades**. A fresh, un-instantiated
  session has an empty kernel graph, so a target that is not registered returns
  `status: error`, `reason: graph_unpopulated` and runs *nothing*; call
  `mode="all"` (which registers the document) and retry. On a populated graph
  in autorun mode the kernel already re-runs descendants, so this mode is
  explicit intent rather than new coverage.
- Passing both `mode="all"` and a `cell_id` is refused with
  `reason: cell_id_not_allowed` — never accepted-and-ignored.

**What the kernel adds.** Whatever the mode, marimo still runs cells outside
`requested_cell_ids`: still-uninstantiated **ancestors** of the targets, and (in
autorun mode) registered **descendants**. The relative order of independent
cells is **unspecified** — `requested_cell_ids` is a set of targets, never an
execution order.

**What the response reports.** Every id or name is validated by a plan/read
before anything is queued, so an unknown one aborts with
`reason: unknown_cell_ids` and nothing runs. After the run a separate report
reads each target's terminal state (marimo discards the run payload when any
target raises, and the in-context snapshot is frozen):

- `cells[]` — per requested target: `runtime_state`, `output_stale`, `known`,
  and the structured `errors` (`[{kind, message}]`) with `errors_readable`.
  `errors` is `null` when the post-run channel could not be read — treat that as
  UNKNOWN, never as "no errors".
- `succeeded_cell_ids` — targets that ended `idle` **with a readable, empty
  `errors`**.
- `failed_cell_ids` — targets that ended `exception`, `marimo-error`,
  `cancelled` or `interrupted`. It covers the **requested targets only**: the
  kernel may also run cells outside `requested_cell_ids`, and their failure
  surfaces through the run payload's `error`/`execution_error` + `stderr`, not
  here.
- `not_run_cell_ids` — everything else (stale, disabled, unknown), including an
  `idle` target whose `errors` channel was unreadable — those also appear in
  `unverified_cell_ids` and are never counted as succeeded.
- `counts` and `status`: `ok` **only** when every requested target is idle,
  `partial` when any target failed or did not finish, and `error` for
  validation/planning/reporting failures — `unknown_cell_ids`,
  `graph_unpopulated`, `cell_id_not_allowed`, `invalid_mode`,
  `cell_id_required`, `planning_failed`, `reporting_failed`. A failure always
  carries a top-level `error` string beside the structured `status`/`reason`.
- `execution_error` + `stderr` — present when the run call itself failed (a
  target raised), so a failed batch is never reported as a plain success; the
  same failure also sets the top-level `error`.

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

### Buttons: an element value is not the interaction

A `button` (and `run_button`) is the exception to "an unchanged value means
nothing happened". **Its frontend value is a click counter** — `0` during
initialization, then `1`, `2`, `3`, … for each click — assigned *before* the
conversion runs, so the counter read-back is the delivery evidence, and a button
interaction payload never reads "already held this value, nothing changed". The
element `value` differs by type: a `button`'s is its `on_click` return (`None`
for a handler that only sets state), while a `run_button` has **no `on_click`** —
a click sets its value `True` and the runtime resets it to `False` after the
dependent cells run, so its value may also read unchanged for a click that
landed:

| Submitted counter | `handler_invoked` | Meaning |
| --- | --- | --- |
| `0` | `false` | The initialization sentinel: marimo's conversion returns the initial value (`False` for `run_button`) and processes **no click**. No click was delivered; a `warning` says so. Submit a nonzero, advancing counter instead. |
| nonzero, counter moved to it | `true` | The update was delivered and marimo ran the button's conversion (`click_delivered: true`, `frontend_value_before` → `frontend_value_after`): for a `button` that invokes its `on_click` handler, for a `run_button` it sets `value` true for the dependent re-run (then resets it `false`). |
| nonzero, counter already at it | `null` | The counter did not change, so the read-back **cannot tell** whether the click was processed again — even though marimo's runtime does process it. Do not report the click as delivered, and do not report it as skipped. Send a new, higher counter. |

Every button payload carries `side_effects_verified: false`: the read-back only
sees the element, so the handler's arbitrary side effects (a `mo.state` update,
a database write, the cells it re-ran) are **never** verified by
`set_ui_value`. `applied` still reports whether the element's own `value`
moved, but an unchanged element value on a button is the handler's normal shape,
not evidence the click was skipped. Confirm the effects with `get_variables`,
`get_cell_outputs`, and `get_errors` (see §6).

A value the kernel raised on while applying it is `status: error` with the
kernel's own message in `kernel_message`. The `reason` names the failure
**site**, read from the kernel traceback's own call site — marimo writes the
*same* notice for both generic failure points, so the notice text cannot decide
it — with the read-back filling in whether the value moved:

- `value_not_applied` — the element's conversion rejected the value *before*
  assigning it, so the element is genuinely unchanged (`applied: false`;
  `value_before` = `value_after`), e.g. an unknown dropdown key. Re-send the
  corrected value; never treat that call as done.
- `on_change_failed` — the value was **accepted** and the element's own
  `on_change` handler raised *after* the assignment. `handler_ran: true` says
  the handler ran; `applied` says whether the value moved:
  - `applied: true`, with `value_before`/`value_after` proving the move — the
    widget holds its new value; only the handler's side effects and the
    dependent cells it re-ran failed.
  - `applied: false` + `no_change: true` — the element already held the
    submitted value, so nothing moved, and the handler still ran and raised
    (marimo's value update has no equality shortcut). Nothing was rejected:
    re-sending the value cannot help. (For a `button`/`run_button`,
    `no_change` is **not** set: an unchanged element value is the type's normal
    shape, not a "nothing changed" result.)

  In both shapes, fix the handler in the widget's cell and re-run it — do not
  re-send the value, and do not report the interaction as "the widget did not
  change".
- `on_click_failed` — a **`button`'s** `on_click` handler ran and raised (this
  reason is attributed only to a `button` clicked with a nonzero counter; a
  `run_button` has no `on_click`). marimo catches that exception inside the
  button itself, so the call would otherwise look like a success: the click
  *was* delivered (`handler_ran: true`, `handler_invoked: true`), and the
  handler may have applied **partial side effects before it raised**. Nothing
  was rejected, so re-sending cannot help; fix the handler in the widget's
  cell, re-run it, then verify the state it was meant to change.
  `side_effects_verified` is `false`.
- `ui_update_failed` — stderr carried a UI-update traceback that could **not**
  be attributed to this element's own handler (for example marimo's button
  `on_click` marker cannot belong to a `run_button`, to another element, or to
  a `0` counter). The failure is real but the site is not this target's
  `on_click`, so the call makes no `handler_invoked` claim: inspect `get_errors`
  / `console_stderr` and read the element before deciding what happened.

If the traceback is truncated and its call site is unreadable, the read-back
alone decides (unmoved → `value_not_applied`, moved → `on_change_failed`); a
rejected update is never reported as a success.

## 6. Verify

After every write, confirm with `get_variables`, `get_cell_outputs`, and
`get_errors`. Widget updates and cell runs are queued and flushed on code-mode
context exit. `set_ui_value` does **not** verify arbitrary downstream effects of
a widget interaction: in autorun mode the kernel re-runs the dependent cells as
part of the update, while in lazy mode it only marks them stale and they re-run
on demand — so verify their effects, don't assume them. For a `button` this is
especially load-bearing: `handler_invoked` (true/false/null) is the only
statement the tool can make, and the handler's own side effects are never
verified by the read-back (see §5). `get_errors` reports `cells[].structured_errors`
and `cells[].console_stderr` separately;
top-level `has_errors`/`total_errors` count structured errors only.

A cell is flagged on the console channel only on **real exception evidence**: a
traceback header, or an exception-typed line (`ValueError: ...`). Ordinary log
text such as `Error: 3 rows skipped` is a message, not an exception, and flags
nothing. Each flagged `cells[]` entry names the evidence it found in
`console_exception_evidence` (`"traceback"` / `"exception_line"`) — read that
entry's `console_stderr` events instead of assuming a UI-handler traceback.

**A failed `run_cell` reports through its own payload, and `get_errors`'
structured channel can be silent about it.** A run in which a target raises
returns `status: "partial"` with the kernel traceback in `execution_error` /
`stderr` and that target in `failed_cell_ids` (`cells[].runtime_state` names the
outcome, e.g. `exception`, or `cancelled` for a cell the kernel never got to),
while `get_errors` reads marimo's *structured* records — which are not written
for one failure class: a cell that references a name marimo resolves as another
cell's **cell-private** variable (a leading-underscore name, see §5). That cell
ends `status: "exception"` with an empty `cell.errors`, so `get_errors` reports
`has_errors: false` and counts no structured error for it — but its traceback is
still visible: in the run payload, and in that cell's `console_stderr` entry
under the console channel. Ordinary runtime failures (`1/0`, or a name that
exists nowhere) are recorded in both channels. So never use `get_errors`'
structured counts alone as the post-run check — read the run payload first, and
use `get_errors` (structured *and* console) for the notebook-wide picture.

## 7. Lint

`lint_notebook` — static checks that need no kernel execution.

For structural changes, call `get_dependency_graph` before `delete_cell` or
before merging cells. Then repeat steps 2–7 as the notebook evolves.

`get_dependency_graph` always returns the **full** graph: `cell_id` and `depth`
are refused (`reason: unsupported_argument`), never accepted-and-ignored. Walk
`cells[].parent_cell_ids` / `cells[].child_cell_ids` for a neighbourhood, and
`cells[].cell_name` matches the name `get_cell_map` reports.

## 8. Restart the kernel — the last resort, not a reflex

`restart_kernel` closes the current kernel and re-materializes a fresh one
through the frontend's `/sse` handshake, keeping the server process alive (so an
open page keeps working). Use it only when the **kernel** is the problem: an
imported package's source changed (module cache), a new dependency was
installed, the kernel is wedged, or globals are poisoned.

A **cell edit never needs one.** `edit_cell` + `run_cell` apply live, and
`edit_cell` already serializes the edit back to the `.py`. If a change did not
seem to take effect, re-read first (`get_cell_map` / `get_cell_data`) — a stale
cell marked `output_stale` is a re-run, not a restart.

What it costs: all execution state is discarded (kernel globals, module caches,
widget values back at their constructor defaults), every cell becomes `stale`
until re-run, and a cell created in-session can come back under a different cell
id. The tool clears the change tracker, so every cell reports `needs_read` again
until re-read, and it reports `cell_ids_stable: false` — never reuse a cached
cell id. Then re-run what you need: `run_cell(mode="all")` re-executes the whole
document, and `set_ui_value` re-applies widget values.

The endpoint needs the server's skew token, which the tool reads from the served
page (`GET /`); with marimo auth on the session census is refused outright, so
it returns `reason: auth_required` without closing anything (whether the page is
also gated so no token can be read is server-config dependent and is not
assumed). A restart is never reported as success without the expected session id
confirmed live afterwards — `session_not_rematerialized` or `server_sessionless`
means the kernel is closed and no usable session was confirmed (the server may be
at zero sessions).

That confirmation is **point-in-time**: the payload reports
`session_id_stable: false` and `session_verification: "point_in_time"`, because
the tool closes its own `/sse` stream and the session is then an ordinary orphan
— a later browser reconnect can re-key it and the server's session TTL can reap
it. On `Invalid session id`, re-run `list_active_notebooks` and re-bind.
