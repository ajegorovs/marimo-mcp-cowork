# Live Test Suite Redesign (STALE — history only)

> **Stale.** Superseded by docs/live-test-redesign-plan.md (the executed plan)
> and docs/live-tests.md (current truth). Kept for historical context.

> Started: 2025-08-26
> Objective: Improve and redesign the live marimo kernel test suite

---

## 1. Current State

### Architecture
- **Location**: tests/marimo_inspect/live/
- **Tests**: 19 tests across 8 test files
- **Fixtures**: live_client, live_session, live_server_url
- **Server**: Manual launch required (marimo edit --no-token --headless)

### Problems Identified
1. **MCP Process Blocking Cache** - FIXED (disabled in cordis.patch.yml)
2. **Auto-Resurrection on Kill** - FIXED (disabled MCP + DSH restart)
3. **Test Design Issues**:
   - Tests verify structure, not behavior
   - No-op tests that don't test anything
   - Poor error handling
   - No session isolation
   - Manual server launch required

## 2. Redesign Goals

### Primary
1. **Direct Kernel Launch** - No manual server start required
2. **Test Isolation** - Each test gets a clean session
3. **Meaningful Assertions** - Test behavior, not just structure
4. **Proper Cleanup** - No residual state between tests
5. **Fast Execution** - < 10s total test time

### Secondary
1. **Version Compatibility** - Pin marimo version, add deprecation warnings
2. **Better Error Messages** - Include template output in failures
3. **Flaky Test Detection** - Mark tests that depend on external state

## 3. Architecture Changes

### Before (Current)
```
tests/marimo_inspect/live/
├── conftest.py           # Manual server discovery
├── create_session.py     # Helper for session creation
├── test_cell_map.py      # 4 tests
├── test_cell_data.py     # 3 tests (1 skipped)
├── test_cell_outputs.py  # 2 tests
├── test_variables.py     # 3 tests
├── test_dependency.py    # 3 tests
├── test_errors.py        # 3 tests (no-op)
└── test_lint.py          # 2 tests (accepts failure)
```

### After (Redesigned)
```
tests/marimo_inspect/live/
├── conftest.py           # Direct kernel launch + session management
├── fixtures.py           # Reusable fixtures
├── test_templates.py     # Template execution tests
├── test_cell_map.py      # Cell map validation
├── test_cell_data.py     # Cell data validation
├── test_dependency.py    # Dependency graph validation
├── test_variables.py     # Variable inspection validation
└── utils.py              # Helper functions
```

## 4. Fixture Redesign

### Current Issues
- `live_client` creates new client per test (good)
- `live_session` resolves session once (bad - state pollution)
- No cleanup mechanism
- Manual server launch required

### Proposed Fix
```python
@pytest.fixture(scope="session")
async def kernel_manager():
    """Direct kernel launch with proper cleanup."""
    manager = MarimoServerManager()
    await manager.start()
    yield manager
    await manager.stop()


@pytest.fixture(scope="function")
async def test_session(kernel_manager):
    """Isolated session per test."""
    session = await kernel_manager.create_session()
    yield session
    await kernel_manager.cleanup_session(session)
```

## 5. Test Improvements

### Remove No-Op Tests
- `test_errors_detects_error` - Doesn't actually test error detection
- `test_errors_reports_error_type` - Same as above
- `test_lint_template_executes` - Accepts failure, doesn't test linting

### Add Meaningful Assertions
- Test actual behavior, not just structure
- Include template output in failure messages
- Add version compatibility checks

### Better Error Handling
- Handle template execution failures gracefully
- Include stderr in test output
- Add timeouts for long-running templates

## 6. Session Management

### Current Issues
- Session state persists between tests
- Tests can interfere with each other
- No cleanup mechanism

### Proposed Fix
- Create fresh session per test
- Cleanup session after each test
- Use isolated notebooks for state-dependent tests

## 7. Direct Kernel Launch

### Implementation
- `MarimoServerManager` class in conftest_direct.py
- Subprocess management for marimo server
- Explicit lifecycle control
- No auto-resurrection

### Usage
```bash
# Run tests with direct kernel launch
uv run pytest tests/marimo_inspect/live/ -v -m live
```

## 8. Progress

### Completed
- [x] Disabled MCP server (cordis.patch.yml)
- [x] Restarted DSH to apply configuration
- [x] Verified no marimo-inspect processes running
- [x] Created MarimoServerManager class
- [x] Documented current architecture

### In Progress
- [ ] Implement direct kernel launch in conftest.py
- [ ] Add session isolation to fixtures
- [ ] Remove no-op tests
- [ ] Add meaningful assertions

### Pending
- [ ] Test end-to-end with direct kernel launch
- [ ] Add version compatibility checks
- [ ] Document new launch procedure
- [ ] Verify tests complete in < 10s

## 9. Implementation Plan

### Phase 1: Direct Kernel Launch
1. Update conftest.py to use MarimoServerManager
2. Add session cleanup to fixtures
3. Verify tests run without manual server start

### Phase 2: Test Improvements
1. Remove no-op tests
2. Add meaningful assertions
3. Better error handling

### Phase 3: Session Isolation
1. Create fresh session per test
2. Cleanup session after each test
3. Use isolated notebooks for state-dependent tests

### Phase 4: Cleanup
1. Remove obsolete files (conftest_direct.py)
2. Update documentation
3. Verify all tests pass

## 10. Success Criteria

- [ ] All tests pass against marimo 0.24.0
- [ ] Tests complete in < 10s total
- [ ] No manual server start required
- [ ] Tests are deterministic (same notebook → same response)
- [ ] Failure gives actionable output
- [ ] No MCP process blocking cache
- [ ] No auto-resurrection
