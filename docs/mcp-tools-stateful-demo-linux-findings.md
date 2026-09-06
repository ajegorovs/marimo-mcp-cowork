# Stateful MCP Demo — Live Run on Linux (Findings)

Date: 2026-08-31
Machine: Arch Linux, repo `/home/alex/Repos/python-image-processing-notebooks`
What was tested: the "more stateful" marimo-inspect MCP tools (Phase 1 session
binding) via the runbook in `docs/agent-onboarding-demo-mcp.md`.

## Outcome

The full demo loop works end-to-end on Linux. **Two real bugs were found and
fixed** along the way (both were Linux/runtime-specific and were never caught
because the demo had only ever been run before on Windows).

The stateful binding itself is confirmed working: after `list_active_notebooks`
auto-binds a session, every read tool works with the `session_id` omitted.

## What I did (the run)

1. Launched the demo notebook detached:
   `uv run marimo edit notebooks/agent_demo.py --no-token --port 8123`
   (this repo's bundled demo notebook, not the image-processing repo)
   (launch script under `temp/`).
2. Called `list_active_notebooks(server_url=...)` → discovered session `s_nrdmwz`,
   auto-bound.
3. Called `get_cell_map` with **no** `session_id` → returned the bound session's
   3 cells ("stale").
4. Mutated via `execute-code.sh` (runs stale cells), then `get_cell_map` →
   all "idle".
5. `get_errors` (0), `get_variables` (`arr=[3 4 5]`), `get_dependency_graph`
   (clean), `get_cell_data`, `get_cell_outputs`, `set_active_session` — all
   worked without repeating `session_id`.

## Bug 1 — Linux server discovery returns 0 notebooks

**Symptom:** `list_active_notebooks()` with **no** args returned
`servers_discovered: 0` even though a marimo server was live on port 8123 and
`discover-servers.sh` found it fine.

**Root cause:** `src/marimo_inspection/discovery.py` `_get_registry_dir()`
hardcoded `~/.marimo/servers`. That is the **Windows** path. On Linux/POSIX
marimo writes the server registry to `$XDG_STATE_HOME/marimo/servers`
(default `~/.local/state/marimo/servers`). So the Python discovery always
looked in the wrong place on Linux.

**Fix:** made `_get_registry_dir()` platform-aware:
- Windows (`os.name == "nt"`): `~/.marimo/servers`
- POSIX: `$XDG_STATE_HOME/marimo/servers`, else `~/.local/state/marimo/servers`

Verified live: no-arg discovery now finds the server. `discover-servers.sh`
already handled this — the Python module just didn't match it.

## Bug 2 — `lint_notebook` always crashed

**Symptom:** `lint_notebook()` returned `Execution failed` with
```
AttributeError: 'AsyncCodeModeContext' object has no attribute 'notebook'
```

**Root cause:** the lint scratchpad template used `ctx.notebook`, which does not
exist on `AsyncCodeModeContext`. Linting through the kernel scratchpad could
never work with this API.

**Fix:** moved linting **server-side**. `tools/lint.py` now reads the notebook
file and runs marimo's static lint engine directly (same engine as
`marimo check`):
- `parse_notebook(source)` → builds the `NotebookSerialization`
- `await RuleEngine.check_notebook(notebook)` → diagnostics
  - ⚠️ do NOT use `check_notebook_sync` — it wraps in `asyncio.run()` and fails
    inside the MCP server's event loop
- Diagnostic fields are `code`/`name` (the old template read `rule`, which
  doesn't exist)

## Notes / limits discovered

- Only `session_id` became optional in Phase 1; **`server_url` is still required**
  on every tool call. That's by design (roadmap) but worth knowing when reading
  the tool schemas.
- **Hot reload is now enabled.** FastMCP ships a `--reload` watcher
  (`fastmcp run <module>:<factory> --transport stdio --reload --reload-dir <dir>`).
  The Hermes MCP config was switched to launch through it (option 1), so edits
  to `src/marimo_inspection/**` hot-reload the server. Caveats:
  - `fastmcp run --reload` forces `--stateless` mode, but `ctx.set_state/get_state`
    still persist across calls within one process (verified), so the stateful
    session binding keeps working.
  - **Every saved file edit kills and respawns the server process**, dropping the
    in-memory bound session. Re-run `list_active_notebooks` to rebind after
    editing server code during active development. For a stable interactive demo,
    don't edit server files mid-demo.
  - **`--reload` + persistent stdio gateway is NOT self-healing on edit.** The
    reload supervisor respawns the server child on the *same* stdin/stdout fd, so
    the Hermes MCP client never sees a disconnect and never re-runs the MCP
    initialize handshake — it gets stuck on `MCPError: Invalid request
    parameters`, and its auto-retry can't recover (the fd stays open). Recovery
    needs a user `/reload-mcp` or a Hermes restart. So hot-reload is clean only
    for a freshly-connected client; the long-lived gateway connection degrades
    after an edit.
  - Must restart Hermes once so the gateway launches the server via the new
    reload path (the previously running subprocess used the old console-script
    launch and still holds pre-fix bytecode).
- Running MCP gateway subprocesses must be **restarted** to load these two fixes —
  a restart was needed even after the source fix (the demo was verified against
  the fixed code via a fresh import + unit tests).
- The demo notebook `notebooks/agent_demo.py` is untouched on disk (marimo
  scratchpad mutations don't persist).

## Test status

- 150 non-live pytest tests pass (was 147 before the lint test rework).
- `ruff check` on the changed source files passes (remaining repo-wide errors are
  pre-existing, unchanged by this work).