# MCP Tools Test Findings

Date: 2025-01-XX
Tested against: `test_marimo.py` (session ID: `s_phw25d`, port 8080)

## Summary

Two blocking bugs prevent all MCP tools from functioning with a live marimo kernel.

---

## Bug 1: `client.py` — `list_sessions()` parses API response incorrectly

**Severity**: Blocking — all tools fail after this point

**Location**: `src/marimo_inspection/client.py`, line 67

**Actual API response format**:
```json
{
  "s_phw25d": {
    "filename": "C:\\Repos\\python-image-processing-notebooks\\notebooks\\test_marimo.py",
    "path": "C:\\Repos\\python-image-processing-notebooks\\notebooks\\test_marimo.py"
  }
}
```
The session ID is the **key** in the top-level dict, and the value contains `filename`/`path`.

**Buggy code** (line 67-78):
```python
for session in data.get("running_notebooks", []):
    file_path = session.get("file", session.get("filename"))
    ...
    sessions.append(
        SessionInfo(
            session_id=session["id"],  # ← "id" doesn't exist in response
            ...
        )
    )
```

**Impact**:
- `list_active_notebooks` returns 0 notebooks (confirmed via test)
- `resolve_session(session_id=...)` fails with "Session not found" (confirmed via test)
- All subsequent tools (`get_cell_map`, `get_cell_data`, etc.) fail because they depend on `resolve_session()`

**Root cause**: The code assumes the API returns `{"running_notebooks": [...]}` but it actually returns `{session_id: {...}, ...}`.

**Fix**: Replace the iteration to use `data.items()`:
```python
for session_id, session_info in data.items():
    file_path = session_info.get("file", session_info.get("filename"))
    sessions.append(
        SessionInfo(
            session_id=session_id,  # ← Use the key directly
            file=file_path,
            basename=Path(file_path).name if file_path else None,
        )
    )
```

---

## Bug 2: All 7 templates use `asyncio.run()` in the scratchpad

**Severity**: Blocking — templates will fail with event loop conflict

**Locations** (one per template):
- `src/marimo_inspection/templates/cell_map.py` (line 48)
- `src/marimo_inspection/templates/cell_data.py` (line 59)
- `src/marimo_inspection/templates/cell_outputs.py` (line 55)
- `src/marimo_inspection/templates/variables.py` (line 96)
- `src/marimo_inspection/templates/dependency.py` (line 88)
- `src/marimo_inspection/templates/errors.py` (line 52)
- `src/marimo_inspection/templates/lint.py` (line 75)

**Problem**: The marimo scratchpad **already runs inside an event loop**. The marimo-pair skill explicitly states:

> The scratchpad supports top-level async code. Use `async with` directly;
> wrapping it in `asyncio.run(...)` is unnecessary and can conflict with the
> kernel's event loop.

**Current pattern** (in all 7 templates):
```python
async def get_cell_map():
    async with cm.get_context() as ctx:
        ...
    return json.dumps({...})


result = get_cell_map()
import asyncio

print(asyncio.run(result))  # ❌ Creates a new event loop → conflicts
```

**Expected error**: `RuntimeError: cannot schedule new futures after interpreter shutdown` or `RuntimeError: this event loop is already running`

**Fix**: Replace `asyncio.run()` with top-level `await`:
```python
async def get_cell_map():
    async with cm.get_context() as ctx:
        ...
    return json.dumps({...})


result = await get_cell_map()  # ✅ Uses the existing event loop
print(result)
```

---

## Additional Observations

### `get_errors` template — incorrect iteration pattern

The template iterates `ctx.cells.items()`, but the `marimo._code_mode` context uses a different iteration pattern. Should use:
```python
for cid in ctx.cells:
    c = ctx.cells[cid]
```

### `lint_notebook` template — private API dependency

Imports `marimo._lint.rule_engine.RuleEngine` — this is a private API (`_lint`) that may not exist or may change across marimo versions. Consider using the public linting API if available.

### `list_active_notebooks` tool — secondary issue

After fixing `client.py`, the tool's output processing in `tools/notebooks.py` may also need adjustment to handle the actual session structure correctly.

---

## Fixes Applied

Both bugs have been fixed:

### Bug 1 Fix (client.py)
- Changed `list_sessions()` to iterate over `data.items()` instead of `data.get("running_notebooks", [])`
- Session ID is now extracted from the dict key (not from a non-existent `"id"` field)
- **Verified**: Direct Python call returns 1 session correctly

### Bug 2 Fix (7 templates)
- All templates now use `await get_function()` instead of `asyncio.run(result)`
- **Verified**: All 144 unit tests pass

### Test Execution Log (After Fixes)

| Test | Result |
|------|--------|
| Direct client test (`list_sessions()`) | ✅ Returns 1 session |
| All 144 unit tests | ✅ Pass |
| DSH MCP tools (in-session) | ⚠️ Need MCP server restart to pick up changes |

**Note**: The DSH-hosted MCP tools (`mcp__marimo-inspect__*`) loaded the old module at session start. To test via DSH tools, the MCP server process needs to be restarted. The fixes are verified via direct Python execution and unit tests.

---

## Discovery was NOT affected by timing

The marimo server was running before the MCP server was developed. This did **not** affect discovery:
- The discovery module correctly found 1 server (`servers_discovered: 1`)
- The server health check succeeded (`/api/sessions` returned 200)
- The bug is purely in the **response parsing** layer, not discovery

The `~/.marimo/servers/*.json` registry file was written when the server started, and the discovery module reads it correctly. The `/api/sessions` endpoint returns live session data — the parsing bug in `client.py` is unrelated to server startup timing.