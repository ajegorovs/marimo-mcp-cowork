# How we run live kernel tests

> Updated: 2026-09-06 — verified against the current working tree (live suite
> green; see [Current status](#current-status)).
> This is the canonical, current-truth doc for the **live** test suite: what it
> is, the commands, the boot mechanics, and its *actual* status today.
> The redesign that made this suite green is documented in
> [live-test-redesign-plan.md](live-test-redesign-plan.md); this doc describes
> the resulting state of the world.

## What "live" means here

Most of the package's behavior depends on a **real marimo kernel**:

- the in-kernel templates (`src/marimo_inspection/templates/*.py`) are
  generated strings that run **inside** a live kernel via
  `import marimo._code_mode as cm; async with cm.get_context() as ctx`;
- the Python client (`MarimoClient`) talks to a live server's HTTP API.

The live suite boots its own headless marimo server and exercises both. It is
**excluded by default**: the fast path is

```bash
uv run pytest -m "not live"
```

because booting a kernel per run adds ~3s and a subprocess to the suite — fine
for the `-m live` opt-in, not for the default path.

## How to run them

```bash
# The whole live suite (boots its own headless marimo server):
uv run pytest tests/marimo_inspect/live/ -m live

# Verbose, with full server-side tracebacks and no capture:
uv run pytest tests/marimo_inspect/live/ -m live -v --tb=short -s

# One live file:
uv run pytest tests/marimo_inspect/live/test_cell_map.py -m live -v

# Pick a port explicitly (default: a free port chosen at startup):
MARIMO_TEST_PORT=3123 uv run pytest tests/marimo_inspect/live/ -m live

# Fast path (unit tests only):
uv run pytest -m "not live"
```

The marker is registered in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
  "live: tests requiring a real marimo kernel (deselect with '-m \"not live\"')",
]
```

**No manual server start is required.** The harness launches a headless marimo
server itself on a free port and creates its session automatically.

## What a run does mechanistically

All orchestration lives in `tests/marimo_inspect/live/conftest.py`
(`MarimoServerManager` + fixtures); tests themselves only use the
`live_client` / `live_session` fixtures.

1. **Session-scoped `kernel_manager`** starts one headless marimo edit
   subprocess per pytest session:

   ```bash
   python -m marimo edit notebooks/test_marimo.py --no-token --headless --port <free_port> --host 127.0.0.1
   ```

   - The port is either `$MARIMO_TEST_PORT` or a free port bound at startup
     (no fixed `2718` clash).
   - marimo's state dir (`XDG_STATE_HOME`) is isolated to a temp dir so the
     suite never touches the developer's real marimo state.
   - `start()` health-polls `GET /api/version` instead of sleeping.

2. **Session creation via the `/sse` handshake** (the key fix). marimo
   materializes a kernel session **only** when a client connects to
   `/sse?session_id=<uuid>&file=<notebook>` (or the `/ws` websocket) — the
   handshake the frontend performs. Polling `GET /api/sessions` can never
   create one (that was the old, red suite's bug). The manager opens the stream
   and waits for the `kernel-ready` event, which guarantees the kernel is up.

3. **One session per server.** marimo edit mode allows exactly one session per
   server; a second connection replaces the first. So tests share the single
   session rather than creating fresh ones per test.

4. **`live_client`** (function-scoped) — a **fresh** `MarimoClient` per
   test (function scope avoids SSE/connection-pool reuse problems), closed
   after the test.

5. **`live_session`** (function-scoped) — the shared session, resolved via
   `resolve_session(session_id=...)`.

6. Tests run templates against the live kernel via
   `POST /api/kernel/execute` (SSE streaming) or read notebook state via the
   HTTP API, asserting **behavior** (known cell code round-trips, hidden setup
   cell present, error cell present, structure types).

7. **Teardown**: `kernel_manager.stop()` terminates the server process
   (`terminate`, then `kill` after 5 s) and drains captured logs.

**Note on instantiation:** the headless session created by the `/sse`
handshake is *not instantiated* — notebook cells do not run, because cell
instantiation requires the token-gated `/api/kernel/instantiate` endpoint.
Templates that read executed cell state (`ctx.graph` for the dependency
template, error records) therefore report empty/fresh values. The live tests
assert the honest, verifiable contract for that state (structure + consistency)
and note the instantiate-gated limitation. `cell_map`, `cell_data`,
`cell_outputs`, `variables` read cell *source/structure* and work fully.

## What's tested (19 tests, 6 files)

| File | Tests | Asserts |
| --- | --- | --- |
| `test_cell_map.py` | 4 | map reports the fixture's 6 cells incl. hidden setup (`def _double` preview) and the intentional error cell; preview truncated to 3 lines |
| `test_cell_data.py` | 4 | per-cell code round-trips (computed-value cell, error cell); count agrees with cell map |
| `test_cell_outputs.py` | 3 | every cell listed with the documented output keys |
| `test_variables.py` | 3 | runs ok on a live kernel; sees kernel-injected globals; degrades gracefully without numpy/pandas (regression for the unguarded numpy import) |
| `test_dependency.py` | 2 | template executes against a live kernel; returns documented structure/types (graph content is instantiate-gated — see note above) |
| `test_errors.py` | 3 | template returns consistent, typed error summary; stable across repeated runs |

Lint tests (`test_lint_source.py`) moved out of here — they run in-process and
do **not** need a kernel, so they live in the fast path (`tests/marimo_inspect/`).

Fixture notebook: `notebooks/test_marimo.py` — a small, deterministic,
self-contained notebook (6 cells: hidden setup defining `_double`, an imports
cell, a computed-values cell, a dependency cell, a polars table cell, and one
hidden cell that raises `ValueError("integration_test_error")`). No
third-party image-processing lib, no numpy/pandas requirements.

Version contract: the live env couples the **in-process** lint (installed
marimo) and the **in-kernel** templates (server's marimo) to the same installed
version — see [marimo-version-support.md](marimo-version-support.md). That doc's
upgrade procedure (step 3) runs `uv run pytest -m live` before widening the
`<0.25` bound.

## Current status (verified 2026-09-06)

**The live suite is green.** On this tree:

```text
uv run pytest tests/marimo_inspect/live/ -m live -q
=> 19 passed in ~3s
```

Fast path:

```text
uv run pytest -m "not live" -q
=> 175 passed (incl. test_lint_source.py and 3 new SSE parser edge-case tests)
```

What fixed the red suite (see [live-test-redesign-plan.md](live-test-redesign-plan.md) §0 for the verified marimo internals):

1. **Session creation now performs the real `/sse` handshake** instead of
   polling `/api/sessions` (which can never create a session).
2. The fixture notebook is deterministic and dependency-free
   (`image_processing_lib`, numpy/pandas removed).
3. A real template bug surfaced and fixed: `variables` template did an
   unguarded `import numpy` and crashed in kernels without numpy.

## Related docs

- `docs/live-test-redesign-plan.md` — the redesign plan that made this green
  (verified findings, work items, definition of done).
- `docs/agenda-live-test-redesign.md` — the original open issue / rationale.
- `docs/marimo-version-support.md` — why the live suite gates marimo bumps.
- `docs/live-test-architecture.md`, `docs/live-test-redesign.md`,
  `docs/kernel-launch-progress.md`, `docs/integration-test-plan.md` — design
  history; treat as stale, not current truth.
