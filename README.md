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

14 tools over the same live kernel.

Reads / state: `list_active_notebooks`, `set_active_session`,
`get_cell_map`, `get_cell_data`, `get_cell_outputs`, `get_variables`,
`get_dependency_graph`, `get_errors`, `lint_notebook`.
Writes: `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
Widget interaction: `set_ui_value`.

`list_active_notebooks` discovers sessions and auto-binds the first one
(`session_id` **and** `server_url`); every other tool falls back to that
binding. The binding lives in the MCP server process/connection — a harness
that spawns or reconnects the server per call/turn loses it, so pass
`session_id`/`server_url` explicitly (or call `set_active_session`) in that
case. A gateway restart requires re-running `list_active_notebooks`.

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
only the callback failed. The update is flushed and
triggers reactive re-execution of dependent cells, but that re-run is not
awaited.

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
`idempotentHint`/`openWorldHint` (used by `set_ui_value`).

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
- A running marimo server (start it with `--no-token` for registry-based
  discovery; see `discover_servers`).
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
