# FastMCP v4 Scout Report — Marimo Inspection MCP Server

## Purpose

Create a custom FastMCP v4 MCP server that exposes marimo notebook inspection
tools (cell map, dependency graph, variable inspection, errors, linting) as a
standalone service, complementing the existing `marimo-pair` CM scripts for
execution and mutation.

## Architecture

### Recommended: Scratchpad-Driven Standalone Server

```
┌─────────────────────────────────────────────────────────────────┐
│  marimo-inspection MCP Server (FastMCP v4)                      │
│  Transport: HTTP (localhost:8090) or STDIO                      │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Lifespan (server startup)                                 │  │
│  │  - Discover marimo servers via ~/.marimo/servers/*.json    │  │
│  │  - Health-check each via GET /api/sessions                 │  │
│  │  - Cache healthy URL → session_id mapping                  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  HTTP Client (marimo communication)                        │  │
│  │  - GET /api/sessions  ──► list active sessions             │  │
│  │  - POST /api/kernel/execute ──► scratchpad execution       │  │
│  │  - SSE stream parsing (execute response → JSON result)     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Scratchpad Code Templates                                 │  │
│  │  (Pre-written Python that runs inside marimo's kernel)    │  │
│  │                                                             │  │
│  │  Each template uses `cm.get_context()` to access:          │  │
│  │  - ctx.cells[cell_id].code (cell source)                   │  │
│  │  - ctx.graph.parents/children (dependency graph)           │  │
│  │  - ctx.globals (kernel variable values)                    │  │
│  │  - cell notifications (runtime state, errors)              │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  MCP Tool Handlers (@mcp.tool)                             │  │
│  │  - Validate input → select template → execute → parse      │  │
│  │  - Serialize scratchpad result to structured JSON           │  │
│  │  - Error handling with structured suggestions               │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │
         │ HTTP (scratchpad execution)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Marimo Server (already running)                                │
│  - GET /api/sessions (unauthenticated)                          │
│  - POST /api/kernel/execute (scratchpad namespace)              │
│  - SSE stream (execution output)                                │
└─────────────────────────────────────────────────────────────────┘
```

### Why Scratchpad Execution?

Marimo's built-in tools access internal state through **direct Python object
references** (`session.session_view.cell_notifications`, `app.graph`, etc.) —
not via HTTP. This works because they run **inside** marimo's process.

Our standalone server runs in a **separate process**, so it cannot access these
objects directly. The scratchpad is the only bridge that gives us full kernel
access:

| Access Method | Capabilities | Our Server Can Use? |
|--------------|--------------|-------------------|
| Direct Python objects (`session.session_view`, `app.graph`) | Full internal state | ❌ (runs in separate process) |
| HTTP API (`/api/sessions`, `/api/kernel/execute`) | Session listing + scratchpad execution | ✅ (our chosen approach) |
| File system (parse `.py` files) | Static structure only | ⚠️ (no runtime state) |

The scratchpad executes Python code in marimo's kernel with a shallow copy of
globals. This is the same mechanism `marimo-pair`'s `execute-code.sh` already
uses — proven and tested.

### Scratchpad Code Templates

Each tool maps to a pre-written Python code template that accesses marimo
internals via `cm.get_context()`:

```python
# Example: get_cell_map template
TEMPLATE_CELL_MAP = f"""
import marimo._code_mode as cm
import json

async with cm.get_context() as ctx:
    result = []
    for cid in ctx.cells:
        c = ctx.cells[cid]
        result.append({{
            "cell_id": str(cid),
            "name": c.name,
            "preview": (c.code or "")[:{preview_chars}],
            "status": c.status,
            "line_count": len((c.code or "").splitlines())
        }})
    print(json.dumps(result))
"""
```

The template is sent to `POST /api/kernel/execute`, the SSE response is parsed
for stdout, and the JSON result is returned to the MCP client.

### Why NOT HTTP Proxy?

The original scout report proposed an HTTP-client-only architecture. This was
based on an incorrect assumption that marimo's HTTP API exposes the same state
as its internal tools. It does not:

| Capability | Internal API | HTTP Equivalent |
|-----------|-------------|-----------------|
| Session listing | `session_manager.sessions` | ✅ `GET /api/sessions` |
| Cell notifications | `session.session_view.cell_notifications` | ❌ No endpoint |
| Dependency graph | `app.graph` (DirectedGraph) | ❌ No endpoint |
| Variable values | `session_view.variable_values` | ❌ No endpoint |
| Cell errors | `cell_notifications` channel data | ❌ No endpoint |
| Lint engine | `RuleEngine.check_notebook()` | ❌ No endpoint |

The HTTP API only provides session listing and scratchpad execution. Everything
else requires executing code in the kernel.

### Transport Choice

**HTTP transport** (recommended) — Server runs as a persistent service on
`localhost:8090`. Clients (Claude, Cursor, custom tools) connect via URL.

**STDIO transport** (fallback) — For CLI/pipe-based clients that prefer
stdio communication. Use `mcp.run(transport="stdio")`.

### Session Targeting

| Method | Stability | Notes |
|--------|-----------|-------|
| `--file <ABSOLUTE_PATH>` | ✅ Stable | Survives browser reconnects |
| Auto-resolve (single session) | ⚠️ Fragile | Works when only one session exists |
| `--session <ID>` | ❌ Ephemeral | Changes on reconnect |

Always prefer `--file` with the full absolute Windows path.

---

## References

### FastMCP v4 Documentation
All FastMCP docs sourced from https://gofastmcp.com/:

| Document | URL | Key Info |
|----------|-----|---------|
| **What's New in v4** | https://gofastmcp.com/getting-started/whats-new.md | Sessionless protocol, stateful apps |
| **The FastMCP Server** | https://gofastmcp.com/servers/server.md | `FastMCP("name")`, `@mcp.tool`, transports |
| **Tools** | https://gofastmcp.com/servers/tools.md | `@mcp.tool`, auto-schema, docstring parsing |
| **MCP Context** | https://gofastmcp.com/servers/context.md | `ctx.info/debug`, `ctx.progress()` |
| **Lifespans** | https://gofastmcp.com/servers/lifespan.md | `@lifespan` decorator, composed lifespans |
| **Running Server** | https://gofastmcp.com/deployment/running-server.md | `mcp.run(transport="http")` |
| **Testing** | https://gofastmcp.com/servers/testing.md | `Client(transport=mcp)`, pytest fixtures |

### Marimo Internal Sources (0.24.0)
| File | Key Info |
|------|---------|
| `marimo/_ai/_tools/tools_registry.py` | All 10 tool classes |
| `marimo/_ai/_tools/base.py` | `ToolBase`, `ToolContext` (direct object access) |
| `marimo/_ai/_tools/tools/cells.py` | Cell map, runtime data, outputs |
| `marimo/_ai/_tools/tools/dependency_graph.py` | Full graph traversal |
| `marimo/_ai/_tools/tools/tables_and_variables.py` | Variables + tables |
| `marimo/_ai/_tools/tools/errors.py` | Error aggregation by cell |
| `marimo/_ai/_tools/tools/lint.py` | Static linting engine |
| `marimo/_mcp/server/main.py` | MCP server setup (embedded, uses ToolContext) |

### Marimo-pair Scripts
| Script | Key Info |
|--------|---------|
| `discover-servers.sh` | Server discovery, WSL/Windows URL resolution |
| `execute-code.sh` | Scratchpad execution via `/api/kernel/execute` |

---

## Proposed Tool Set

Based on the 1:1 comparison in `marimo-inspection-tools-comparison.md`.

### Tier 1: Core Inspection (Essential)

| Tool | Input | Output | Scratchpad Access |
|------|-------|--------|-------------------|
| `list_active_notebooks` | — | Notebooks (name, path, session_id) | `GET /api/sessions` |
| `get_cell_map` | `session_id`, `preview_lines` | Cell overview (id, preview, type, state) | `ctx.cells`, `ctx.graph` |
| `get_cell_data` | `session_id`, `cell_ids` | Full code, errors, variables | `ctx.cells[cid].code` |
| `get_cell_outputs` | `session_id`, `cell_ids` | Visual output + console streams | Cell notification parsing |
| `get_variables` | `session_id`, `variable_names` | Variables (name, value, datatype) | `ctx.globals` inspection |

### Tier 2: Analysis (Important)

| Tool | Input | Output | Scratchpad Access |
|------|-------|--------|-------------------|
| `get_dependency_graph` | `session_id`, `cell_id`?, `depth`? | Defs/refs, parent/child, cycles | `ctx.graph` traversal |
| `get_errors` | `session_id` | All errors by cell | Cell notification errors |
| `lint_notebook` | `session_id` | Breaking/runtime/formatting | `RuleEngine.check_notebook()` |

### Tier 3: Optional / Future

| Tool | Notes |
|------|-------|
| `get_database_tables` | Only if notebooks use SQL |
| `get_marimo_rules` | Static rules; low value |

---

## File Structure

```
src/marimo_inspection/
├── __init__.py           # Package init, expose `main` entry point
├── server.py             # FastMCP server, lifespan, tool registration
├── client.py             # HTTP client: /api/sessions + /api/kernel/execute
├── discovery.py          # Server discovery from ~/.marimo/servers/
├── templates/            # Scratchpad code templates per tool
│   ├── __init__.py
│   ├── cell_map.py       # get_cell_map template
│   ├── cell_data.py      # get_cell_data template
│   ├── cell_outputs.py   # get_cell_outputs template
│   ├── variables.py      # get_variables template
│   ├── dependency.py     # get_dependency_graph template
│   ├── errors.py         # get_errors template
│   └── lint.py           # lint_notebook template
├── tools/                # MCP tool handlers
│   ├── __init__.py       # Re-export all tools
│   ├── notebooks.py      # list_active_notebooks
│   ├── cells.py          # get_cell_map, get_cell_data, get_cell_outputs
│   ├── variables.py      # get_variables
│   ├── dependency.py     # get_dependency_graph
│   ├── errors.py         # get_errors
│   └── lint.py           # lint_notebook
└── types.py              # Shared Pydantic/dataclass types
```

---

## Dependencies

```toml
[project]
dependencies = [
    "fastmcp>=4.0.0b1",
    "marimo[recommended]>=0.24.0",
]
```

**Critical:** Do NOT use `marimo[mcp]` — it depends on `mcp>=1.0.0,<2` which
conflicts with `fastmcp>=4` requiring `mcp>=2.0.0,<3`. Since we build our own
MCP server, marimo's built-in MCP extras are unnecessary. marimo's HTTP API is
fully functional without the `[mcp]` extra.

---

## Development Plan

### Phase 1 — Core Infrastructure
- Create project structure (`src/marimo_inspection/`)
- Implement `discovery.py` (server discovery from marimo registry)
- Implement `client.py` (HTTP client for `/api/sessions` + `/api/kernel/execute`)
- Implement SSE stream parsing (execution response → JSON result)

### Phase 2 — Tier 1 Tools (Core Inspection)
- Implement `list_active_notebooks` (HTTP-only, no scratchpad)
- Implement `get_cell_map` (scratchpad template + execution)
- Implement `get_cell_data` (scratchpad template + execution)
- Implement `get_cell_outputs` (scratchpad template + execution)
- Implement `get_variables` (scratchpad template + execution)

### Phase 3 — Tier 2 Tools (Analysis)
- Implement `get_dependency_graph` (scratchpad graph traversal)
- Implement `get_errors` (scratchpad error aggregation)
- Implement `lint_notebook` (scratchpad lint engine call)

### Phase 4 — Testing & Polish
- Add pytest fixtures with FastMCP Client
- Add inline-snapshot assertions
- Add CLI entry point
- Document usage

---

## Comparison to Marimo's Built-in MCP

| Aspect | Marimo built-in MCP | Our FastMCP Server |
|--------|---------------------|-------------------|
| **Transport** | Embedded in marimo process (`/mcp/server`) | Standalone HTTP service (localhost:8090) |
| **Access** | Direct Python objects (in-process) | Scratchpad via HTTP API |
| **Discovery** | Requires session ID first | Auto-discovers via `~/.marimo/servers/` |
| **Editing** | `edit_notebook`, `run_stale_cells` (chat panel only) | Read-only inspection focus |
| **Latency** | Zero (in-process) | ~50-200ms (HTTP round-trip per tool) |
| **Independence** | Tied to marimo version | Own version, own lifecycle |
| **Client** | marimo chat panel, Claude Code, Cursor | Any MCP client |

**Key difference:** Our server decouples inspection from the marimo editor. It
runs as an independent service that discovers marimo servers and communicates
via the HTTP API + scratchpad. This means it can inspect notebooks even when
marimo's own MCP server isn't enabled or accessible.