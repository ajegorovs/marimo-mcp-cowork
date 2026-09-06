# marimo-inspect MCP Server — Progress Report

## Status: Core Implementation Complete | Tests Needed

**Last updated:** 2026-08-25
**Implementation:** `src/marimo_inspection/`

---

## What Was Implemented

### Architecture
- **Standalone FastMCP v4 server** that inspects running marimo notebooks
- **Scratchpad execution** via marimo's HTTP API (`POST /api/kernel/execute`)
- **Server discovery** from marimo's registry (`~/.marimo/servers/*.json`)
- **8 inspection tools** exposed via MCP protocol

### Files Created

```
src/marimo_inspection/
├── __init__.py              # Package init (version 0.1.0)
├── server.py                # FastMCP server + CLI entry point
├── client.py                # HTTP client (httpx2, SSE parsing)
├── discovery.py             # Server discovery from registry
├── types.py                 # Shared dataclasses
├── templates/               # Scratchpad code templates
│   ├── __init__.py
│   ├── cell_map.py          # Cell overview template
│   ├── cell_data.py         # Full cell data template
│   ├── cell_outputs.py      # Cell outputs template
│   ├── variables.py         # Variable inspection template
│   ├── dependency.py        # Dependency graph template
│   ├── errors.py            # Error aggregation template
│   └── lint.py              # Lint engine template
└── tools/                   # MCP tool handlers
    ├── __init__.py
    ├── notebooks.py          # list_active_notebooks
    ├── cells.py              # get_cell_map, get_cell_data, get_cell_outputs
    ├── variables.py          # get_variables
    ├── dependency.py         # get_dependency_graph
    ├── errors.py            # get_errors
    └── lint.py              # lint_notebook
```

### CLI Entry Point
```bash
marimo-inspect --transport stdio   # For DSH MCP client
marimo-inspect --transport http    # Standalone HTTP server
```

### DSH Integration
- Added to `~/.dsh/profiles/web/cordis.patch.yml`
- Launches via stdio: `uv run marimo-inspect --transport stdio`
- Server banner: `Starting MCP server 'marimo-inspection' with transport 'stdio'`

---

## What Needs to Be Done (Test Agent Handoff)

### Priority 1: Research FastMCP Testing
**FastMCP v4 has built-in testing support** — research and implement.

**Research these sources:**
1. FastMCP testing docs: https://gofastmcp.com/servers/testing.md
2. Installed source: `.venv/Lib/site-packages/fastmcp/client/`
3. Key pattern: `Client(transport=mcp)` for in-process testing
4. pytest fixtures + inline-snapshot assertions

**Expected testing pattern (from docs):**
```python
import pytest
from fastmcp.client import Client
from marimo_inspection.server import create_server


@pytest.fixture
def mcp_server():
    return create_server()


@pytest.mark.asyncio
async def test_list_notebooks(mcp_server):
    async with Client(transport=mcp_server) as client:
        result = await client.call_tool("list_active_notebooks", {})
        assert "summary" in result
```

### Priority 2: Write Test Suite
**Target:** `tests/marimo_inspect/`

```
tests/marimo_inspect/
├── __init__.py
├── conftest.py                  # Shared fixtures
├── test_server.py               # Server creation + tool registration
├── test_discovery.py            # Server discovery (mocked)
├── test_client.py               # HTTP client (mocked responses)
├── test_templates.py            # Template generation
├── test_tools.py                # Tool handler contracts
└── fixtures/                    # Test data
    ├── mock_session.json        # Fake marimo session
    └── mock_cell_map.json       # Fake cell map response
```

### Priority 3: Mocking Strategy
**The scratchpad execution path requires a real marimo kernel.** For tests:

| Approach | Pros | Cons |
|----------|------|------|
| **Mock HTTP client** | Fast, isolated | Doesn't test scratchpad templates |
| **Test templates in isolation** | Verifies code generation | Doesn't test full round-trip |
| **Integration test** | Real kernel, end-to-end | Requires marimo server running |

**Recommended:** Mock HTTP responses for unit tests, add optional integration test that requires a running server.

### Priority 4: Lint & Format
- Run `uv run ruff check src/marimo_inspection/`
- Run `uv run ruff format src/marimo_inspection/`

---

## Tool Contracts

Each tool returns a dict with `next_steps` guidance. Key contracts:

### Discovery
| Tool | Input | Output | Notes |
|------|-------|--------|-------|
| `list_active_notebooks` | `server_url?` | `{summary, notebooks[], next_steps}` | HTTP-only (no scratchpad) |

### Cell Inspection
| Tool | Input | Output | Notes |
|------|-------|--------|-------|
| `get_cell_map` | `session_id, preview_lines?` | `{cells[], total_cells, next_steps}` | Scratchpad template |
| `get_cell_data` | `session_id, cell_ids[]?` | `{data[], next_steps}` | Scratchpad template |
| `get_cell_outputs` | `session_id, cell_ids[]?` | `{cells[], next_steps}` | Scratchpad template |

### Analysis
| Tool | Input | Output | Notes |
|------|-------|--------|-------|
| `get_dependency_graph` | `session_id, cell_id?, depth?` | `{cells[], variable_owners{}, multiply_defined[], cycles[]}` | Graph traversal |
| `get_errors` | `session_id` | `{has_errors, total_errors, cells[]}` | Error aggregation |
| `lint_notebook` | `session_id` | `{summary{}, diagnostics[]}` | Static linting |
| `get_variables` | `session_id, variable_names[]?` | `{variables{}, tables{}}` | Variable inspection |

---

## Known Issues / Open Questions

### Scratchpad Templates
The templates use `cm.get_context()` to access marimo internals:
- **Unverified:** Does scratchpad namespace have access to `cm.get_context()`?
- **Unverified:** Does `ctx.graph` expose the dependency graph?
- **Unverified:** Does `ctx.cells` contain runtime state?

**These need a live marimo server to validate.** The templates were written based on marimo source code inspection, not tested execution.

### SSE Parsing
The client uses SSE streaming for execution responses. Marimo's `/api/kernel/execute` returns an SSE stream — the parsing logic needs verification against actual response format.

### Error Handling
- Tool handlers return `{error, stderr}` dicts on failure
- No retry logic for transient failures
- No session lifecycle management (session disconnects, server restarts)

---

## Dependencies

```toml
[project]
dependencies = [
    "fastmcp>=4.0.0b1",
    "httpx2>=0.1.0",
    "marimo[recommended]>=0.24.0",
]

[project.scripts]
marimo-inspect = "marimo_inspection.server:main"
```

---

## References

- Architecture doc: `docs/fastmcp-v4-scout-report.md`
- Marito internals: `docs/marimo-agent-collaboration.md`
- Tool comparison: `docs/marimo-inspection-tools-comparison.md`
- FastMCP v4 docs: https://gofastmcp.com/
- Marimo MCP source: `.venv/Lib/site-packages/marimo/_mcp/server/main.py`

---

## Agent Handoff Checklist

For the test agent:

- [ ] Research FastMCP `Client(transport=mcp)` testing pattern
- [ ] Create `tests/marimo_inspect/` package
- [ ] Write server creation tests (no network)
- [ ] Write discovery tests (mocked HTTP)
- [ ] Write client tests (mocked SSE responses)
- [ ] Write template generation tests (string assertions)
- [ ] Write tool contract tests (input/output shape)
- [ ] Add optional integration test (requires running server)
- [ ] Run `uv run ruff check src/marimo_inspection/`
- [ ] Run `uv run ruff format src/marimo_inspection/`
