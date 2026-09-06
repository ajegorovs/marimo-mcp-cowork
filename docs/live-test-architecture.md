# Live Test Architecture (STALE — history only)

> **Stale.** Superseded by docs/live-tests.md (current truth) and
> docs/live-test-redesign-plan.md. Kept for historical context; do not treat
> as current.

> Updated: 2025-08-26
> Objective: Document the redesigned live test suite architecture

---

## 1. Architecture Overview

### Design Goals
1. **Direct Kernel Launch** - No manual server start required
2. **Test Isolation** - Each test gets a clean session
3. **Meaningful Assertions** - Test behavior, not just structure
4. **Proper Cleanup** - No residual state between tests
5. **Fast Execution** - < 10s total test time

### Architecture Diagram
```
┌─────────────────────────────────────────────────────────────┐
│                     Test Execution Flow                      │
└─────────────────────────────────────────────────────────────┘

pytest (session-scoped)
    ↓
┌─────────────────────────────────────────────────────────────┐
│                    MarimoServerManager                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Session-Scoped Fixtures                             │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │ kernel_manager (session-scoped)              │   │    │
│  │  │   - Start marimo server                      │   │    │
│  │  │   - Stop marimo server                       │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│                   Function-Scoped Fixtures                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ live_client (function-scoped)                       │    │
│  │   - Fresh MarimoClient per test                     │    │
│  │   - Avoids connection pool issues with SSE          │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ live_session (function-scoped)                      │    │
│  │   - Fresh session per test                          │    │
│  │   - No state pollution between tests                │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│                         Test Execution                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Template Execution                                   │    │
│  │   - Execute template via POST /api/kernel/execute   │    │
│  │   - Parse JSON response                              │    │
│  │   - Verify meaningful assertions                     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│                         Cleanup                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Session Cleanup                                      │    │
│  │   - Client.close()                                   │    │
│  │   - Session cleanup (if needed)                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│                     Session Cleanup                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Server Cleanup                                       │    │
│  │   - Stop marimo server                               │    │
│  │   - No residual state                                │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 2. Components

### MarimoServerManager
```python
class MarimoServerManager:
    """Manages a marimo server process for testing."""
    
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.server_url: str | None = None
        self.session_id: str | None = None
        self._sessions: list[str] = []
    
    async def start(self, notebook_path: str = "notebooks/test_marimo.py"):
        """Start a marimo server process."""
        # Starts marimo server with --no-token --headless
        # Fixed port 2718 for testing
        pass
    
    async def create_session(self):
        """Create a session using Playwright."""
        # Uses Playwright to access the notebook URL and create a session
        pass
    
    async def cleanup_session(self, session_id: str):
        """Cleanup a session after test completion."""
        # Removes session from tracking
        pass
    
    async def stop(self):
        """Stop the marimo server process."""
        # Terminates process with explicit cleanup
        pass
```

### Fixtures

#### Session-Scoped Fixtures
- `kernel_manager` - Session-scoped MarimoServerManager instance
- `live_server_url` - Server URL for all tests

#### Function-Scoped Fixtures
- `live_client` - Fresh MarimoClient per test
- `live_session` - Fresh session per test

#### Legacy Compatibility Fixtures
- `marimo_server` - Uses kernel_manager
- `direct_kernel_manager` - Compatibility with direct kernel launch tests
- `direct_server_url` - Compatibility with direct kernel launch tests
- `direct_client` - Compatibility with direct kernel launch tests
- `direct_session` - Compatibility with direct kernel launch tests

## 3. Test Files

### test_cell_map.py
- 4 tests
- Validates cell map template against live kernel

### test_cell_data.py
- 3 tests (1 skipped for empty notebooks)
- Validates cell data template

### test_cell_outputs.py
- 2 tests
- Validates outputs template

### test_variables.py
- 3 tests
- Validates variable inspection template

### test_dependency.py
- 3 tests
- Validates dependency graph template

### test_errors.py
- 3 tests (rewritten with meaningful assertions)
- Validates error detection template

### test_lint.py
- 3 tests (rewritten with stability checks)
- Validates lint template execution stability

## 4. Execution

### Run All Live Tests
```bash
uv run pytest tests/marimo_inspect/live/ -v -m live
```

### Run Specific Test File
```bash
uv run pytest tests/marimo_inspect/live/test_cell_map.py -v
```

### Run with Verbose Output
```bash
uv run pytest tests/marimo_inspect/live/ -v -m live --tb=short -s
```

## 5. Architecture Benefits

### Before (Old Architecture)
- Manual server start required
- MCP intermediary (blocks cache, auto-resurrects)
- No session isolation
- No-op tests that don't test anything
- Poor error handling

### After (New Architecture)
- Direct kernel launch (no manual start)
- No MCP intermediary
- Session isolation per test
- Meaningful assertions
- Proper cleanup

## 6. Success Criteria

- [x] All tests pass against marimo 0.24.0
- [ ] Tests complete in < 10s total
- [x] No manual server start required
- [x] Tests are deterministic (same notebook → same response)
- [x] Failure gives actionable output
- [x] No MCP process blocking cache
- [x] No auto-resurrection

## 7. Notes

- The original author agent had many issues with the kernel launch mechanism
- MCP process blocking cache is a critical issue
- Auto-resurrection makes testing difficult
- Direct kernel launch is preferred for testing
- The MarimoServerManager class provides explicit lifecycle management
- No more auto-resurrection with direct kernel launch (bypasses DSH)
- DSH restart required to apply configuration changes
