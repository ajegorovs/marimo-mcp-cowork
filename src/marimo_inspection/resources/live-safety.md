# Live-Safety Rules

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

These tools act on a **live kernel**. Treat every write as production.

## A session may not be yours

`list_active_notebooks` cannot say who created a session or who holds it: every
**session** row reports `provenance: "unknown"` and `owner: "unknown"`, because
marimo's public API (`GET /api/sessions`) publishes only a session's
filename/path — no creator, no owning client, no creation time, and no
per-session client count. **Binding is not ownership** — a bound session may be
a human's open page.

Prefer **browser-first** (marimo 0.24 **edit mode, without an explicit
`--session-ttl`**): open the notebook so the page **becomes/holds the main
consumer connection** for the session, then bind it. `/sse` materialization is
for headless agent-only work: without an explicit `--session-ttl`, closing that
stream leaves the session an **orphan** (it outlives the stream) until a later
connection takes it over — though a **configured `--session-ttl` can reap that
orphan**. A human must **take over** and **re-run the notebook** before its
widgets respond. A later reconnect can **re-key** the session id, and a second
distinct client joins the same kernel as a **non-main, read-only consumer**.

A `marimo run` server is a separate case: it is **not discoverable at all**. It
registers under `--no-token` exactly as an edit server does, but
`GET /api/sessions` requires `edit` scope and answers **`401`** in run mode, so
the census-200 health check drops it and it never contributes to
`servers_discovered`; only an explicit `server_url` reaches one, as a
connection-failure **sentinel**. Nor does a launch ever create a session in the
first place — only a client connect (`/ws`, or the browser's `/sse` stream)
does — so a session listed before any client attached is an earlier client's
**orphan**, never a startup artifact and never the on-disk `__marimo__` session
cache (which stores cell outputs).

The `summary` reports `total_notebooks` (`session_count`), `result_row_count`
(every row, including a connection-failure sentinel) and `attached_client_count:
null` — `active_connections` is only a DEPRECATED alias for `session_count`,
never a client count, and `/api/status/connections.active` counts sessions with
an open main consumer, not clients. The re-key behavior is real and *separate*
from a page/session divergence observed once: that divergence is **not
diagnosed**, and **neither the re-key nor any read-path explanation is confirmed
as its cause** — treat it as unexplained, don't assume one.

## Edits hit the running kernel

`edit_cell` and `run_cell` mutate the session the server is attached to. The
`.py` file on disk is reconciled by marimo separately. There is no undo.

## A kernel restart invalidates every id and read baseline

`restart_kernel` discards all execution state: kernel globals, imported-module
caches and widget values are gone, and a cell created in-session can come back
under a **different cell id**. The tool therefore clears the change tracker —
every cell reports `needs_read` again until you re-read it with `get_cell_data`
— and reports `cell_ids_stable: false`, so a cached cell id or a pre-restart
read baseline must not be reused. Re-read the notebook (`get_cell_map`), re-run
what you need (`run_cell(mode="all")` re-runs the document), and re-apply widget
values with `set_ui_value` before trusting anything you read back. The notebook
file and the server process itself survive.

The verified session id is **point-in-time, not durable**: the tool closes its
own `/sse` stream, so the re-materialized session is an ordinary orphan — a later
browser reconnect can re-key it to a new id and the server's session TTL can
reap it. The payload says so (`session_id_stable: false`,
`session_verification: "point_in_time"`); if a later call answers
`Invalid session id`, re-run `list_active_notebooks` and re-bind.

## `mode="all"` re-runs every document cell

`run_cell(mode="all")` queues **every document cell** in the notebook — not only
the stale ones: cells that are **already idle** re-run, and cells that
were deliberately left un-run (an unreferenced widget leaf, a scratch cell) run
for the first time. It needs an **empty** `cell_id`; passing both is refused with
`reason: cell_id_not_allowed` rather than ignored.

The other modes add cells too. Whatever the mode, the kernel may run cells
outside the requested set: still-uninstantiated **ancestors** of the targets,
and — in autorun mode — registered **descendants**. Treat any run as a fresh
execution of that cell's dependency neighbourhood, and remember that the
relative order of independent cells is **unspecified**.

On a fresh session the kernel dependency graph is empty, so
`mode="descendants"` cannot resolve a target: it returns `status: error`,
`reason: graph_unpopulated` and runs **nothing** (never a silent single-cell
run). Call `mode="all"` first — it registers the document — then retry.

Selective work avoids this blast radius: `mode="cell"` (default) queues one
cell, and after a run `cells[].runtime_state`, `failed_cell_ids` and
`not_run_cell_ids` tell you exactly which targets finished. Those lists cover
the **requested targets only** — a cell the kernel ran on its own (a stale
ancestor or an autorun descendant, outside `requested_cell_ids`) surfaces
through the run payload's `error`/`execution_error` + `stderr` instead.

## Read before edit

`edit_cell` defaults to `check_fresh=True`: it requires that you read that
exact cell's **full source** first. On a first touch it returns status
`needs_read` unconditionally — even on a session with no snapshot. If the
source changed since your last read it returns `conflict`.

`get_cell_data` records the read baseline; a `get_cell_map` preview does
**not**. The map is for orienting (ids, previews, state) — a 3-line preview is
not a read of the source, so `get_cell_map` alone does not make a cell
editable.

A mutation refreshes only the cell it touched: `create_cell`/`edit_cell`/
`delete_cell` record that one cell's baseline, so an unrelated write by another
co-worker can never bless a cell you have not read, and a `conflict` stays
reported until *you* re-read.

Recovery is a real re-read, then retry:

1. `get_cell_data` for that cell — this records the read baseline.
2. Retry `edit_cell`.

`check_fresh=False` is an explicit force escape hatch — never the normal
recovery path.

## Inspect before delete or merge

Run `get_dependency_graph` before `delete_cell` or merging cells: removing a
cell that defines a variable other cells reference breaks them.

## Verify after every write

Re-check `get_variables`, `get_cell_outputs`, and `get_errors`. A successful
tool call is not proof of a correct result.

## Widget visibility and dependencies

- A `mo.ui` control is only VISIBLE if it is the cell's final expression (or
  the last item of its final `mo.vstack`). A control assigned to a variable
  mid-cell still works but renders nowhere.
- A cell cannot read the `.value` of a UI element it created in the same cell.
  Split control creation and value reads into separate cells.
- Bind a widget to a **bare** top-level name. marimo keeps a
  leading-underscore name cell-private, so `_slider` is invisible to every
  other cell AND unreachable from `set_ui_value` (`reason:
  unknown_variable`) — the tool cannot fix that for you.
- Update a widget from outside via `set_ui_value`, not by editing its cell.
  It never coerces the value: send the shape the element accepts (a `dropdown`
  option key goes inside a one-element list, e.g. `["beta"]`). A refused shape
  returns `reason: value_shape_mismatch` with the corrected payload in
  `did_you_mean`; a value the element's conversion rejected returns `reason:
  value_not_applied` with the kernel's own message and the widget unmoved,
  while a value the element's `on_change` handler raised on returns `reason:
  on_change_failed` (`handler_ran: true`) — the value WAS accepted, with
  `applied: true` when it moved and `applied: false` + `no_change: true` when
  the element already held it (a button does **not** set `no_change` — an
  unchanged button value is its normal shape), so only the callback failed.
  Read those — never
  assume the interaction happened.
  `status: ok` means the value was read back
  (`verified: true`: `applied: true` if it moved, `no_change: true` if it
  already held that value — a button instead reports `handler_invoked` from the
  click counter, never `no_change`).
- **A button's value is not the click.** A `button` exposes the `on_click`
  return as `value` (unchanged when the handler only sets state); a
  `run_button` has no `on_click` — a click sets its `value` to `True` and the
  runtime resets it to `False` after the dependent cells run. Both expose a
  click **counter** as their frontend value, so either can leave `value`
  unchanged while the click landed. The payload reports the counter evidence
  (`frontend_value_before`/`after`, `click_delivered`) and `handler_invoked`:
  `false` for the `0` initialization sentinel (marimo processes no click for
  it), `true` when the counter moved to what you sent, and `null` when the
  counter already held it (a repeated counter is **unknown**; never report it
  delivered or skipped). `side_effects_verified` is always `false`, and a
  raising `on_click` is `reason: on_click_failed` with `handler_ran: true`
  (attributed only to a `button` clicked with a nonzero counter — otherwise the
  call fails as a generic `ui_update_failed`) — partial side effects may
  already have been applied. A no-change button report is never evidence the
  click was dropped.
- `set_ui_value` does **not** verify arbitrary downstream effects: in autorun
  mode the kernel re-runs dependent cells as part of the update, in lazy mode
  it only marks them stale (they re-run on demand). Confirm a handler's effects
  with `get_variables` / `get_cell_outputs` / `get_errors` either way.
