# Integration Test Plan: Live Marimo Kernel

> Updated: 2025-08-26
> Status: Current

## Objective

Validate that MCP inspection templates return accurate data from a real marimo kernel.

## Problem

The unit tests mock `MarimoClient` with canned responses. They prove the MCP layer works (tool registration, error handling, parameter validation) but don't validate that the scratchpad templates actually return useful data from a live kernel.

**Risk:** Templates use `cm.get_context()` — marimo's internal code mode API. If the API changes between marimo versions, templates silently return incomplete or malformed data.

## Architecture

### Test Structure

```
tests/marimo_inspect/live/
├── conftest.py           # Fixtures: kernel_manager, live_client, live_session
├── test_cell_map.py      # Validate cell map template (4 tests)
├── test_cell_data.py     # Validate cell data template (3 tests, 1 skipped)
├── test_cell_outputs.py  # Validate outputs template (2 tests)
├── test_variables.py     # Validate variables template (3 tests)
├── test_dependency.py    # Validate dependency graph template (3 tests)
├── test_errors.py        # Validate errors template (3 tests)
└── test_lint.py          # Validate lint template (3 tests)
```

**Total: ~21 tests, 1 skipped** (for empty notebook case).

### Fixture Design

- `kernel_manager` — Session-scoped MarimoServerManager that manages server lifecycle
- `live_server_url` — Server URL for all tests
- `live_client` — **Function-scoped** (not session-scoped) to avoid HTTP connection reuse problems with SSE streaming
- `live_session` — **Function-scoped** to ensure test isolation

### Session Creation

The test suite uses **HTTP API polling** to create sessions:

1. MarimoServerManager starts the marimo server
2. Server polls `/api/sessions` until a session exists
3. Tests use the returned session for template execution

**No Playwright required** — marimo creates sessions automatically.

## Execution

```bash
# Run only live tests (~7-10s, server is session-scoped)
uv run pytest tests/marimo_inspect/live/ -v -m live

# Run only unit tests (fast, no kernel)
uv run pytest tests/marimo_inspect/ -m "not live"

# Run all tests
uv run pytest tests/marimo_inspect/
```

**No manual server start required** — the test suite handles everything automatically.

## Template Implementation Notes

### Scratchpad Execution

Templates execute via `POST /api/kernel/execute` with SSE streaming. Each template is a Python string that runs in the scratchpad context — a shallow copy of the notebook globals.

Key constraints:
- Templates use `cm.get_context()` (code mode) to access `ctx.cells`, `ctx.graph`, etc.
- Scratchpad execution is **non-persistent** — cells created in scratchpad don't persist to the notebook.
- `NotebookCell` objects are not JSON serializable — templates must convert them to dicts.
- `_CellsView` doesn't have a `.get()` method — use `ctx.cells[cid]` with try/except.

### API Compatibility (marimo 0.24.0)

The following APIs were validated against marimo 0.24.0:
- `cm.get_context()` — works, returns `AsyncCodeModeContext`
- `ctx.cells` — iterable, supports indexing but no `.get()` method
- `ctx.graph` — `DirectedGraph` with `.cells`, `.definitions`, `.parents`, `.children`
- `ctx.globals` — dict of notebook globals
- `ctx.create_cell()` — creates cells in scratchpad only

### Known Limitations

- **Lint template** — uses `RuleEngine.create_default()` which may not be available in all marimo versions. Tests verify execution structure rather than functional linting.
- **Error detection** — scratchpad execution doesn't persist cells, so creating error cells for testing is not possible. Tests verify template structure instead.
- **Empty notebooks** — the test notebook has 0 cells. Tests handle this gracefully (assert `total_cells >= 0`).

## pytest Configuration

```toml
[tool.pytest.ini_options]
markers = [
    "live: marks tests as live (deselect with -m 'not live')",
]
```
