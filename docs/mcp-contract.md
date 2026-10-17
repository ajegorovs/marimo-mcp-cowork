# MCP contract — orientation for humans

A navigable, human-readable entry point to what the MCP surface promises: tool
groups, the response envelope, binding, mutation, execution, widget and output
semantics, and session lifecycle — for a reader who has not connected a client
yet.

**It is not a second copy of the runtime rules.** The three packaged MCP
resources are authoritative for runtime semantics; they ship in the wheel and an
agent reads them with `list_resources()` / `read_resource(uri)`. Where this
document and a resource disagree, the resource wins, and the disagreement is a
defect to fix here.

| Topic | Authoritative document |
| --- | --- |
| The step-by-step co-work loop, run modes, refusal families | `workflow://marimo-inspect/co-work-loop` |
| Read-before-edit, live-kernel safety, widget visibility rules | `workflow://marimo-inspect/live-safety` |
| What the surface deliberately does not cover, limits, binding scope | `reference://marimo-inspect/fallbacks-and-limits` |
| Installation and harness configuration | [`harness-integration/README.md`](harness-integration/README.md) |
| Local, remote and Tailnet deployment shapes | [`deployment-topologies.md`](deployment-topologies.md) |
| Why the package exists beside `marimo pair`, and the evidence | [`interface-positioning.md`](interface-positioning.md) |
| Released versus unreleased surface | [`../README.md`](../README.md) § Release and development boundary |

## Tool groups

Fifteen tools over one live kernel; the registration authority is
`src/marimo_inspection/server.py`.

| Group | Tools | One line |
| --- | --- | --- |
| Session | `list_active_notebooks`, `set_active_session` | Discover live sessions and bind one as the default target |
| Reads | `get_cell_map`, `get_cell_data`, `get_cell_outputs`, `get_variables`, `get_dependency_graph`, `get_errors`, `lint_notebook` | Cell structure, source, execution state, variables, errors, static lint |
| Writes | `create_cell`, `edit_cell`, `run_cell`, `delete_cell` | Cell lifecycle and execution |
| Widgets | `set_ui_value` | Set a live `mo.ui` element's value by its kernel-global name |
| Lifecycle | `restart_kernel` | Close the kernel and re-materialize a fresh one (`main` only; not in v0.3.3) |

Authoritative detail: `workflow://marimo-inspect/co-work-loop` §2–§8.

## Response envelope and refusals

Every cell- or session-targeting tool resolves its target through one shared
step, so a target problem is a **structured payload**, not a tool-level
exception: `status: error` with `target_resolved: false`, `operation_ran: false`
and `state_changed: false`. **No mutation is dispatched on a refusal** — a
session-target refusal is built before any call at all, and a cell or anchor
reference is validated against the live cells before any write is generated.

The reason vocabulary, by family:

- Target and access: `session_required`, `binding_ambiguous`, `notebook_mismatch`,
  `session_not_found`, `server_unreachable`, `server_query_failed`,
  `edit_scope_required`, `auth_required`, `session_census_denied`.
- Cell and anchor references: `unknown_cell_ids`, `conflicting_anchors`,
  `target_validation_failed`.
- Execution: `cell_id_required`, `cell_id_not_allowed`, `graph_unpopulated`,
  `planning_failed`, `reporting_failed`.
- Widget: `value_shape_mismatch`, `value_not_applied`, `on_change_failed`,
  `on_click_failed`, `ui_update_failed`, `unknown_variable`.
- Lifecycle: `skew_token_unavailable`, `edit_required`, `session_not_rematerialized`,
  `server_sessionless`.

Two invariants worth carrying: a denied census is classified by a **read-scope
probe** (`GET /api/version`), never guessed from the denial, because pinned
marimo 0.24.0 serves an API 403 as `401 {"detail":"Authorization header
required"}` and strips `WWW-Authenticate`; and absence is concluded **only** from
a census that was successfully read, so an unreadable census is
`server_query_failed` and never `session_not_found`.

Authoritative detail: the reason table in
`reference://marimo-inspect/fallbacks-and-limits`, and section 1 of
`workflow://marimo-inspect/co-work-loop`.

## Binding

- A bind is written to the MCP session's server-side state **and** to a
  process-global fallback consulted only when that state has nothing bound.
- Over **stdio** one server process serves one client, so argument-less calls
  always work. Over **HTTP** the process may serve many clients, so the fallback
  is served only while the process has seen a single client session; after that
  an argument-less call fails closed with `binding_ambiguous` rather than
  inheriting another client's notebook.
- The bind also records the bound notebook **path**. A call on the bound id is
  re-resolved against that path: an id-only rotation is repaired to the current
  id, and an id reused for a different notebook is refused `notebook_mismatch`.
  Only a *different* id is explicit targeting, and an explicit `server_url` does
  not detach the anchor.
- This is detection on a snapshot, **not** a lock or atomic prevention. A call
  naming a different id, the `MarimoClient` used directly, and anything after
  the check (a rebind inside the resolution/mutation gap) are uncovered.

Authoritative detail: `reference://marimo-inspect/fallbacks-and-limits` §Session
binding is process-global, §The bind anchors to the notebook path.

## Mutation

- `edit_cell` carries a staleness guard (`check_fresh=True` by default):
  never-read → `needs_read`, changed-since-read → `conflict`. Only
  `get_cell_data` records the read baseline — a `get_cell_map` preview is not a
  source read. Recovery is re-read then retry; `check_fresh=False` is a force
  escape hatch, not the recovery path.
- A successful edit returns the post-edit `code_hash`. A mutation refreshes only
  the cell it touched.
- `create_cell` defaults to `hide_code=False`, so new cells are visible. No read
  tool echoes `hide_code`, so visibility is decided at creation time and cannot
  be confirmed back through the read surface.
- Writes hit the running kernel; the `.py` file is reconciled by marimo
  separately, and there is no undo.

Authoritative detail: `workflow://marimo-inspect/live-safety` §Read before edit,
§Edits hit the running kernel.

## Execution

`run_cell(cell_id, mode=...)` names what is *queued*, not everything the kernel
runs:

| `mode` | Queues | Requires |
| --- | --- | --- |
| `"cell"` (default) | exactly the target cell | `cell_id` (id or name) |
| `"descendants"` | the target plus its kernel-graph descendants | a target registered in the kernel graph |
| `"all"` | every document cell, in one code-mode context | an **empty** `cell_id` |

- The kernel may also run cells outside the requested set (stale ancestors,
  autorun descendants), and the relative order of independent cells is
  unspecified: `requested_cell_ids` is a set of targets, never an execution
  order.
- Per-target reporting: `cells[].runtime_state` and `cells[].errors` with
  `errors_readable`, plus `succeeded_cell_ids` / `failed_cell_ids` /
  `not_run_cell_ids` / `unverified_cell_ids`. `errors: null` means the channel
  could not be read — unknown, never "no errors" — and such a target is never
  counted as succeeded.
- `mode="all"` is a full re-run, and it is the way to execute an unreferenced
  cell (and register the widgets it defines) on a fresh session.

Authoritative detail: `workflow://marimo-inspect/co-work-loop` §4 and
`workflow://marimo-inspect/live-safety` §`mode="all"` re-runs every document
cell.

## Widgets

`set_ui_value(variable_name, value)` accepts no source code and never coerces:
send the shape the element's own declaration accepts (a `dropdown` takes exactly
one option key inside a one-element list; a `multiselect` takes any number,
`[]` included). A refused shape returns `value_shape_mismatch` — with the
corrected payload in `did_you_mean` only when one particular replacement can be
inferred.

The element's value is read back before the call returns, so `status: ok` with
`verified: true` means the read-back succeeded. A `button`/`run_button` is the
exception: its element value is not the interaction, delivery evidence comes
from the frontend click counter, `handler_invoked` is tri-state, and
`side_effects_verified` is always `false`. `set_ui_value` never verifies
arbitrary downstream effects — confirm them with the read tools.

Authoritative detail: `workflow://marimo-inspect/co-work-loop` §5, including the
per-element value-shape table and the failure-site reasons.

## Outputs, variables and lint

- marimo's code-mode snapshot exposes **one main output per cell** plus console
  events, not every frontend UI registration: something rendered in the notebook
  may be absent from `get_cell_outputs`. Inspect the cell's variables instead.
- `get_cell_data`'s `data[].variables` is a deprecated placeholder that is
  always `null`; live variables are `get_variables`. `include_errors=True` folds
  the two error channels (`structured_errors`, `console_stderr`) into the read.
- `get_errors` reports those two channels separately; top-level
  `has_errors`/`total_errors` count structured errors only.
- `lint_notebook` is static and in-process (the engine behind `marimo check`)
  and reports **source-file** positions: `filename`/`line`/`column`/`cell_index`.
  `cell_index` is not a live cell id; the two identifier spaces are separate.

Authoritative detail: `reference://marimo-inspect/fallbacks-and-limits` §Output
coverage, and `workflow://marimo-inspect/co-work-loop` §3 and §7.

## Session lifecycle

- **Discovering a server is not discovering a session.** A launch creates no
  session in marimo 0.24; only a client connect does. A `marimo run` server is
  not discoverable at all (its census requires `edit` scope and answers `401`),
  and a call against one is refused `edit_scope_required` — authenticating
  cannot fix it.
- **Starting or stopping the notebook server stays an operator action**; there
  is no MCP tool for it. `restart_kernel` restarts the kernel, not the server.
- **`restart_kernel` discards all execution state** (globals, module caches,
  widget values), makes every cell `stale`, can re-key cell ids, and clears the
  change tracker, so every cell reports `needs_read` again. A cell edit never
  needs one. A confirmed restart is **point-in-time**, not durable: the
  re-materialized session is an ordinary orphan until a later connection takes
  it over.
- A controller that insists on knowing who owns a session cannot be satisfied
  here: `provenance` and `owner` are `"unknown"` for every session row, because
  marimo's public API publishes only a session's filename/path.

Authoritative detail: `reference://marimo-inspect/fallbacks-and-limits`
§Server and kernel lifecycle, §Server discovery is not session discovery; and
`workflow://marimo-inspect/co-work-loop` §8.

## Deliberately out of scope

No general execute / arbitrary-code tool, no screenshot or rendered-output
capture, no notebook-server start/stop (an operator action), and no multi-cell
transaction. Arbitrary kernel probes remain a documented script escape hatch
rather than a tool. `reference://marimo-inspect/fallbacks-and-limits` is the
authority for the boundary and for the intentional fallbacks.

## Notes for MCP client implementers

- **Importing `marimo_inspection` does not import `fastmcp`.** The server is
  lazily imported by `create_server()`; the client path stays dependency-light.
- The three resources are static, read-only Markdown packaged in the wheel and
  loaded via `importlib.resources`.
- On pinned fastmcp 4.0.3, resource annotations carry only
  `audience`/`priority`/`lastModified`, so read-only intent is signalled by tags
  plus description. Tool annotations do support
  `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` (used by
  `set_ui_value` and `restart_kernel`).
- marimo is pinned to 0.24.x because private APIs are used; do not widen the
  bound without running the live suite (see
  [`marimo-version-support.md`](marimo-version-support.md)).
