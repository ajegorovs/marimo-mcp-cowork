# marimo-inspect

A pluggable toolkit for **inspecting and editing live marimo notebooks** — over
the wire or from inside a notebook.

It ships the same machinery the `marimo-pair` agent skill relies on, but as a
standalone, installable package you can drop into any repo:

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

`list_active_notebooks`, `set_active_session`, `get_cell_map`,
`get_cell_data`, `get_cell_outputs`, `get_variables`,
`get_dependency_graph`, `get_errors`, `lint_notebook`, `create_cell`,
`edit_cell`, `run_cell`, `delete_cell`.

`edit_cell` carries a staleness guard that refuses to overwrite a cell an
agent has not freshly read, so simultaneous human/agent co-work can't silently
lose edits.

## Install

```bash
uv add marimo-inspect                      # from a package index
uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork"  # from VCS
uv add --editable /path/to/marimo-inspect  # local checkout
```

## Requirements

- Python 3.12+
- A running marimo server (start it with `--no-token` for registry-based
  discovery; see `discover_servers`).
- marimo **0.24.x** (private APIs are version-bound — see
  [docs/marimo-version-support.md](docs/marimo-version-support.md) for the
  pinned range and the upgrade validation procedure).

## Development

```bash
uv sync
uv run ruff check .
uv run pytest -m "not live"
```

The `live` tests need a real marimo kernel and are deselected by default.
