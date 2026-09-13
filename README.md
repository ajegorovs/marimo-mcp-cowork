# marimo-inspect

A pluggable toolkit for **inspecting and editing live marimo notebooks** — over
the wire or from inside a notebook.

It packages live-session co-work primitives as a standalone, installable
component you can add to any repo:

- A **Python client** for driving a live marimo session interactively.
- A **FastMCP server** exposing that tooling to AI agents over the Model Context
  Protocol.

## The two ways to use it

### 1. As a Python client (the "plug into any repo" path)

Install it into any project and co-work on a running marimo session from inside
a notebook or script — no copy-paste of the framework:

```python
import asyncio
from marimo_inspection import MarimoClient, discover_servers


async def main():
    servers = await discover_servers()
    async with MarimoClient(servers[0].url) as client:
        sessions = await client.list_sessions()
        result = await client.execute(sessions[0].session_id, "1 + 1")
        print(result.status, result.stdout)


asyncio.run(main())
```

Importing `marimo_inspection` does **not** require `fastmcp` — the MCP server
is lazily imported only when you call `create_server()`.

### 2. As an MCP server (for AI agents)

```python
from marimo_inspection import create_server

server = create_server()
server.run(transport="stdio")
```

Or run it directly:

```bash
marimo-inspect --transport stdio
```

## MCP tools

15 tools over the same live kernel.

Reads / state: `list_active_notebooks`, `set_active_session`,
`get_cell_map`, `get_cell_data`, `get_cell_outputs`, `get_variables`,
`get_dependency_graph`, `get_errors`, `lint_notebook`.
Writes: `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
Widget interaction: `set_ui_value`.
Lifecycle: `restart_kernel` (closes the kernel and re-materializes a fresh one;
the server process survives, but the confirmed session id is point-in-time —
`session_id_stable: false`).

`list_active_notebooks` discovers sessions and auto-binds the first one
(`session_id` **and** `server_url`); every other tool falls back to that
binding. The binding lives in the MCP session's server-side state plus a
process-global fallback consulted only when that state has nothing bound.
Restarting or spawning a fresh server process loses it (re-run
`list_active_notebooks`); over one persistent stdio process, fresh MCP
sessions still reach it through the fallback. Over HTTP the fallback is served
only while the process has seen a single client session — once a second client
session appears, argument-less calls fail closed with `reason:
binding_ambiguous`. When in doubt, pass `session_id`/`server_url` explicitly
(or call `set_active_session`).

**Binding is not ownership.** Each **session** row reports
`provenance: "unknown"` and `owner: "unknown"` — marimo's public API exposes
only a session's filename/path, so who created it and which client holds it are
not knowable from here. The `summary` is scoped the same way: `total_notebooks`
is the truthful session count (always equal to `session_count`), `session_count`
counts sessions reported by `GET /api/sessions`, `result_row_count` is
`len(notebooks)` — every row, including a connection-failure sentinel (keys
exactly `name`/`path`/`session_id`/`server_url`/`error`, no
`provenance`/`owner`), which is a row but not a session. `attached_client_count`
is always `null` (marimo publishes no per-session client count, and the
server-wide `/api/status/connections.active` value counts sessions with an open
main consumer, not attached clients), and `active_connections` is a
**deprecated** compatibility alias for `session_count` that was never a client
count.

For human co-work, prefer **browser-first** (marimo 0.24 **edit mode without an
explicit `--session-ttl`**): open the notebook so the page **becomes/holds the
main consumer connection** for the session, then call
`list_active_notebooks` and bind it — the payload `owner` still reads
`"unknown"` because the public API does not publish that role. Creating the
session yourself with the `/sse` handshake is for headless, agent-only work:
without an explicit `--session-ttl`, closing that stream leaves the session an
orphan (it outlives the stream) until a later connection takes it over — though
a **configured `--session-ttl` can reap that orphan**. A human who opens the
page must take over and re-run the notebook before its widgets respond, a
second distinct client joins the same kernel as a **non-main, read-only
consumer**, and a later reconnect can re-key the session id. The re-key is
separate from a page-vs-session divergence observed once: its cause is not
diagnosed, and neither the re-key nor any read-path explanation is confirmed as
its cause — so do not assume one.

**Discovering a server is not discovering a session.** Launching a server
creates no session in marimo 0.24 — edit and run mode alike; only a client
connect (`/ws`, or the browser's `/sse` stream) does. A fresh headless server is
therefore discoverable with an empty census, and a session listed before any
client attached is an earlier client's orphan on a still-running server (or the
browser marimo auto-opened when the launch was not headless) — never a launch
artifact, and never the on-disk `__marimo__` session cache, which stores cell
outputs. A **`marimo run`** server is not discoverable at all: it registers under
`--no-token` like an edit server, but `GET /api/sessions` requires `edit` scope
and answers `401` in run mode, so the census-200 health check drops it and it is
never counted by a discovery-based `servers_discovered`. Only an explicit
`server_url` pointed at one reaches it, as a single connection-failure sentinel
row (`session_count` 0, `servers_discovered` 1).

### Read before you edit

`edit_cell` carries a staleness guard (`check_fresh=True` by default) and
requires that you read that exact cell first:

- first touch of a never-read cell → `status: "needs_read"` (unconditionally,
  even on a session with no snapshot);
- source changed since your last read → `status: "conflict"`.

Recovery is a real re-read, then retry: call `get_cell_data` (which records the
read baseline), then retry `edit_cell`. A `get_cell_map` preview does **not**
record the baseline — a preview is not a source read. A successful edit
returns the post-edit `code_hash`. `check_fresh=False` is an explicit force
escape hatch, **not** the recovery path. A missing cell id returns a clear
error before anything is mutated.

### Running cells — and running the whole notebook

`run_cell(cell_id, mode=...)` queues what the `mode` names, and reports what
each target actually did. `cell_id` is a cell **id or cell name** (resolved the
way `ctx.cells` resolves a key — a name that worked before the modes existed
still works): `requested_cell_ids` always carries the resolved IDs, `cell_id`
echoes your input, and `resolved_cell_id` reports the resolution.

| mode | queues | needs |
| --- | --- | --- |
| `"cell"` (default) | just that cell | `cell_id` |
| `"descendants"` | that cell + its kernel-graph descendants | `cell_id`, registered in the kernel graph |
| `"all"` | every document cell, in one code-mode context | an **empty** `cell_id` |

`mode="all"` is how an unreferenced cell (and the widgets it defines) finally
runs on a fresh session: nothing else ever pulls it in, so `set_ui_value` on its
widget returns `unknown_variable` until then. It is a **full re-run** — already
idle cells run again. `mode="descendants"` refuses rather than degrades: a
target the kernel graph has not registered (every document cell, on a fresh
un-instantiated session) returns `status: error`, `reason: graph_unpopulated`
and runs nothing. Passing `mode="all"` together with a `cell_id` is refused
(`reason: cell_id_not_allowed`).

The kernel may additionally run cells outside `requested_cell_ids` — stale
ancestors, and registered descendants in autorun mode — and the relative order of
independent cells is unspecified; `requested_cell_ids` is a set of targets, never
an execution order. The response is per requested target: `cells[].runtime_state`
and `cells[].errors` (with `errors_readable`; `errors` is `null` when the
post-run error channel could not be read, and such a target is reported
`not_run`/unverified, **never** `succeeded`), plus `succeeded_cell_ids` (`idle`
with a readable, empty `errors`), `failed_cell_ids`
(`exception`/`marimo-error`/`cancelled`/`interrupted` — it covers the requested
targets only) and `not_run_cell_ids` / `unverified_cell_ids` (everything else,
e.g. stale/disabled/unknown). `status` is `ok` only when every requested target
is idle, `partial` when any failed or did not finish, and `error` for
validation/planning/reporting failures (`cell_id_required`, `invalid_mode`,
`cell_id_not_allowed`, `unknown_cell_ids`, `graph_unpopulated`,
`planning_failed`, `reporting_failed`). Every failure — and a run whose batch
call itself failed (`execution_error` + `stderr`) — carries a top-level `error`
string alongside the structured `status`/`reason`, so a caller that checked the
old `{"error": ...}` payload still sees the failure.

### New cells are visible by default

`create_cell` defaults to `hide_code=False`, so a new cell's code shows in the
UI. (This changed from the earlier hidden-by-default behavior.) Pass
`hide_code=True` explicitly for setup/implementation cells you want hidden.
Note that no read tool echoes `hide_code`, so visibility is decided at creation
time and cannot be confirmed back through the MCP read surface.

### Widget interaction

`set_ui_value(variable_name, value)` sets a live `mo.ui` element's value by its
kernel-global name and accepts **no source code**. It never coerces the value:
send the shape the element's declaration accepts — scalar for `slider`/`text`,
bool for `checkbox`, the option key **inside a one-element list** for a
`dropdown` (`["beta"]`), a list of keys for `multiselect`, a two-element list
for `range_slider`. A shape the element cannot accept is refused before
anything is applied, and the error carries the corrected payload in
`did_you_mean`.

The element's value is read back before the call returns, so `status: ok` with
`verified: true` means the read-back succeeded: either the widget's own value was
observed to move (`applied: true`) or it already held that value
(`applied: false` + `no_change: true`). A value marimo rejected — an unknown
dropdown key, say — is returned as `status: error` with `reason:
value_not_applied` and the kernel's message instead of a misleading success
(a refused shape uses `reason: value_shape_mismatch`); a value the element's own
`on_change` handler raised on is `reason: on_change_failed` with
`handler_ran: true` — the value was accepted, with `applied: true` when it moved
or `applied: false` + `no_change: true` when the element already held it, so
only the callback failed.

A `button`/`run_button` is the exception. Both expose a click **counter** as
their frontend value, but their element value differs: a `button`'s value is its
`on_click` return (unchanged when the handler only sets state), while a
`run_button` has no `on_click` and its value is set to `True` on a click and
reset to `False` after the dependent cells run — so either can show an unchanged
`value` even though the click landed. Those payloads carry the frontend counter
read (`frontend_value_before`/`after`), `click_delivered`, a tri-state
`handler_invoked`, and `side_effects_verified: false`. `0` is the
initialization sentinel (`handler_invoked: false`, no click — marimo processes
no click for it); a nonzero counter that moved to the submitted value is
`handler_invoked: true`; a counter that already held the submitted value is
`null` (unknown — the read-back cannot see a repeated click, so neither
"delivered" nor "skipped" is reported). A `button` whose `on_click` raises
returns `reason: on_click_failed` with `handler_ran: true` and acknowledges that
partial side effects may already have been applied; that marker is attributed
only to a `button` clicked with a nonzero counter (never a `run_button` or any
other element), otherwise the call fails as a generic `ui_update_failed` rather
than mis-attributing it.

The update is flushed and triggers reactive re-execution of dependent cells, but
`set_ui_value` does not verify arbitrary downstream effects: in autorun mode the
kernel re-runs dependent cells as part of the update, in lazy mode it only marks
them stale — confirm a handler's effects (including a side-effect-only button's)
with the read tools.

## MCP resources

The server also publishes three **static, read-only** documentation resources
(`text/markdown`, packaged in the wheel and loaded via `importlib.resources`)
that a client can read on demand:

| URI | Content |
| --- | --- |
| `workflow://marimo-inspect/co-work-loop` | the MCP-first co-work loop, step by step |
| `workflow://marimo-inspect/live-safety` | read-before-edit and live-kernel safety rules |
| `reference://marimo-inspect/fallbacks-and-limits` | what MCP does not cover + intentional fallbacks |

A client lists them with `list_resources()` and fetches one with
`read_resource(uri)`; through the Python client you can also read them with
FastMCP's in-process transport:

```python
from fastmcp import Client
from marimo_inspection.server import create_server

async with Client(transport=create_server()) as client:
    for r in await client.list_resources():
        print(r.uri, r.mime_type)
    doc = await client.read_resource("workflow://marimo-inspect/live-safety")
```

FastMCP 4.0.3 resource annotations only carry
`audience`/`priority`/`lastModified`, so read-only intent is carried by tags +
description; tool annotations do support `readOnlyHint`/`destructiveHint`/
`idempotentHint`/`openWorldHint` (used by `set_ui_value` and `restart_kernel`).

## Output limits

marimo's code-mode snapshot exposes **one main output per cell** plus console
events — not every frontend UI registration. `get_cell_outputs` returns that
main output and the serialized console events, so a widget rendered to the user
may be absent from it; inspect the cell's variables instead.
`get_cell_map`'s `has_output` / `has_console_output` / `has_errors` flags are
computed from live fields (`None` when a private field is unreadable, never
faked). `get_errors` reports `cells[].structured_errors` (marimo `cell.errors`) and
`cells[].console_stderr` (console events, including UI-handler tracebacks) as
two separate per-cell channels; flagged entries name the matched marker in
`cells[].console_exception_evidence`. Top-level error flags and totals summarize
the two channels; `has_errors`/`total_errors` cover structured errors only.

Arbitrary kernel probes, complex multi-operation CodeMode blocks, screenshots,
and notebook-server lifecycle stay outside the MCP surface — see
`reference://marimo-inspect/fallbacks-and-limits`.

## Install and connect an MCP client

The MCP resources explain how to operate a connected live notebook; they cannot
bootstrap their own installation. Start here, then use the packaged resources
once the harness reports the server connected.

### Normal consumer installation

Add a pinned release to the notebook project. This is the standard path for
users and consumer-repository contributors; it is a normal, non-editable
installation in that project's environment.

```bash
uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v0.3.3"
uv sync
```

Configure the harness to execute that environment's console script, not
`uv run` and not a provider checkout:

```text
<project-root>/.venv/bin/marimo-inspect --transport stdio
```

Use the relevant configuration block in
[docs/harness-integration/README.md](docs/harness-integration/README.md), then
verify the installed script with `.venv/bin/marimo-inspect --help`. Start a
marimo notebook with `--no-token`, open it in a browser to materialize a
session, and call `list_active_notebooks`. After connection, read the MCP
resources for the co-work loop and safety rules.

### Provider contributors: local override only

Use an editable sibling checkout only when testing unreleased changes to this
provider against a consumer project. It is not a consumer-installation mode:
replace the consumer's pinned dependency temporarily, resync, test, then
restore the pinned version. Do not commit a machine-local dependency source.

## Requirements

- Python 3.12+
- A running marimo **edit** server (start it with `--no-token` for
  registry-based discovery; see `discover_servers` — a `marimo run` server
  registers but is not discoverable, because its session census requires edit
  scope).
- marimo **0.24.x** (private APIs are version-bound — see
  [docs/marimo-version-support.md](docs/marimo-version-support.md) for the
  pinned range and the upgrade validation procedure).

## Development

```bash
uv sync --all-extras           # test deps are an optional extra; a bare sync prunes pytest
uv run ruff check .
uv run pytest -m "not live"   # unit tests (fast, no kernel)
uv run pytest -m live         # live kernel tests (boots its own headless server)
```

The `live` tests need a real marimo kernel and are deselected by default. See
[docs/live-tests.md](docs/live-tests.md) for how the live suite is run and its
current status.
