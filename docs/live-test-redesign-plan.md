# Live Test Redesign — Implementation Plan (handoff)

> **Status:** **EXECUTED 2026-09-06** — live suite green (19 passed), fast path
> green (175 passed). See docs/live-tests.md for the resulting current truth.
> This plan remains as the record of what was done and why.
> **Date:** 2026-09-06
>
> This is the actionable plan that resolves
> [`agenda-live-test-redesign.md`](agenda-live-test-redesign.md). It supersedes the
> stale history docs (`live-test-redesign.md`, `live-test-architecture.md`,
> `kernel-launch-progress.md`, `integration-test-plan.md`) for any decision made
> here — those remain only as history.
>
> **Read this first, in order:** `docs/live-tests.md` (current status) →
> this plan → `docs/marimo-version-support.md` (before touching marimo deps).

---

## 0. Verified findings (do not re-derive)

These were verified against the installed marimo **0.24.0** source in
`.venv/lib/python3.12/site-packages/marimo/` on 2026-09-06. The dev agent should
**trust these** and not re-investigate, except the two open items in §0.3.

### 0.1 Root cause of the red suite

`marimo edit --headless` starts the HTTP server but **creates no kernel session on
its own.** A session is only materialized when a client performs the same
handshake the frontend does, against one of two endpoints in
`_server/api/endpoints/ws_endpoint.py`:

- `WS /ws?session_id=<uuid>&file=<notebook_path>` — **default transport**
- `GET /sse?session_id=<uuid>&file=<notebook_path>` — SSE transport

Both funnel into `SessionHandler._connect_session(...)`, which lazily calls
`SessionManager.create_session(...)`. Therefore
`GET /api/sessions` stays `{}` forever until someone connects, which is exactly
why `MarimoServerManager.create_session()` (pure polling) can never work and
`resolve_session()` raises `ValueError: No active sessions on server`.

### 0.2 The session-creation contract (verified)

- Query params are `session_id` (client-supplied **UUID string**, any value) and
  `file` (the notebook path). See `parse_connection_params` in
  `_server/api/endpoints/ws/ws_connection_validator.py`:
  - missing `session_id` → `WebSocketCloseReason.NO_SESSION_ID`
  - missing `file` (and no workspace fallback) → `NO_FILE_KEY`
- The **default transport is `"websocket"`** (`_config/config.py` lines 254–267):
  `"websocket"` (default) → `/ws`; `"sse"` → `/sse`, opt-in via
  **`MARIMO_SERVER_TRANSPORT=sse`**. The `/sse` endpoint is always registered but
  is only the correct transport when that config is set.
- Conclusion: **the reliable, dependency-free unblock is the `/ws` websocket with
  the default transport**, driven by `httpx2` (already a runtime dep). No
  Playwright, no `websockets` package.

### 0.3 Open items to confirm during implementation (two, small)

1. **Exact `file` value.** Confirm whether `file` must be the *workspace-absolute*
   path (as marimo's file watcher/registry reports) vs. the relative
   `notebooks/test_marimo.py`. Test with `GET /api/sessions` after a successful
   connect and match `path`; adjust accordingly.
2. **Does the session survive the client disconnect?** The websocket/session has a
   TTL (`session_ttl`, default 120 s in `asgi.py`). If the harness closes the
   websocket immediately, confirm the session remains listed long enough for the
   test to run (it should — TTL governs idle *reap*, not close-on-disconnect).
   If it does not survive, hold the socket open in an `asyncio.Task` for the life
   of the session.

---

## 1. Goal

Make the live (real-kernel) suite **green, deterministic, fast, and honest** about
what it tests, without adding dependencies, and make the fast path actionable.

Non-goals (unchanged from the agenda): do **not** remove live coverage; do **not**
abstract marimo internals (`docs/notebook-backend-protocol.md` remains deferred).

---

## 2. Target architecture: three rings

| Ring | Marker | What it covers | When it runs |
| --- | --- | --- | --- |
| **1. Unit / no kernel** | none | protocol parsing, template string shape, discovery, lint | default `pytest` |
| **2. Live (real kernel)** | `live` | behavioral assertions against a live session | `uv run pytest -m live` |
| **3. Version matrix** | `live` | Ring 2 body across marimo versions | CI job only (later) |

The key shift: **push shape/structure assertions down to Ring 1** (cheap, always
run) and reserve Ring 2 for **behavior** that can only be true with a real kernel.

---

## 3. Work items (execute in this order)

### Step 1 — Relabel `test_lint.py` (trivial, do first)

`tests/marimo_inspect/live/test_lint.py` uses `@pytest.mark.live` but never talks
to a kernel or server — it calls `tools.lint._lint_source` in-process.

- Remove the `live` marker from all three tests (drop the `import pytest`
  dependency if unused), OR move the file to
  `tests/marimo_inspect/test_lint_source.py`.
- Effect on its own: `test_lint.py` joins the fast `-m "not live"` path.
- **Acceptance:** `uv run pytest -m "not live"` runs 3 more tests and they pass.

### Step 2 — Fix session creation (the unblock)

Rewrite `MarimoServerManager.create_session()` in
`tests/marimo_inspect/live/conftest.py` to perform a **real `/ws` handshake**
instead of polling `/api/sessions`:

```python
import uuid

# inside create_session(self, notebook_path: str | None = None)
self.session_id = str(uuid.uuid4())
ws_url = self.server_url.replace("http://", "ws://", 1) + "/ws"
notebook = notebook_path or str(os.path.abspath("notebooks/test_marimo.py"))
# Open "ws_url?session_id=...&file=..." with httpx2's websocket support.
# Read until a ready/kernel message appears, then close or hold open (§0.3.2).
```

**Preferred implementation** (default transport = websocket): open `WS /ws` with
`httpx2`'s websocket support, send nothing, read until a ready/kernel message
appears, then either close (if §0.3.2 shows the session survives) or keep it open
in an `asyncio.Task` held on the manager for the session lifetime.

Then change `live_session` to return the **created** `session_id` rather than
calling `resolve_session()`:

```python
@pytest.fixture(scope="function")
async def live_session(kernel_manager, live_client):
    session_id = await kernel_manager.create_session()
    # Ready-check: poll GET /api/sessions until session_id appears (bounded).
    yield session_id
```

Keep `live_client` function-scoped as-is (fresh client per test avoids SSE/conn
pool reuse — that reasoning is still valid).

- **Acceptance:** `uv run pytest tests/marimo_inspect/live/ -m live -v` reports
  **0 `ValueError: No active sessions on server`** errors.

### Step 3 — Make the fixture notebook deterministic

Rewrite `notebooks/test_marimo.py` to be minimal and self-contained:

- Remove the `import image_processing_lib as ipl` cell and the `version = ipl...`
  cell (not installed in dev; it's the second latent issue from `live-tests.md`).
- Keep a small, well-defined set of cells that exercise what the templates read:
  - a `setup` cell (hidden) that defines a helper or two,
  - a couple of visible cells with imports (`numpy`, `polars`, `altair` — already
    dev deps via `marimo[recommended]`) and simple values,
  - **one** intentionally-failing hidden cell `raise ValueError("integration_test_error")`
    ONLY if you keep an errors-detection test; otherwise remove all hidden raisers.
- Drop the `mo.ui.slider`/interactive/reactive chart cells — they add runtime and
  nondeterminism with no assertion value for the templates under test.
- Remove `image_processing_lib` from any fixture imports.

- **Acceptance:** the notebook runs with zero missing-import errors; `marimo check
  notebooks/test_marimo.py` is clean.

### Step 4 — Add Ring 1 tests (cheap, high-value)

Create `tests/marimo_inspect/` unit tests (no kernel, no server):

- **`test_client_execute_parsing.py`** — drive `MarimoClient.execute` against a
  canned `httpx` response. Mock `httpx.AsyncClient.stream` (or use a stub/respx
  equivalent) to return SSE frames covering: `stdout`, `stderr`, `done` (success +
  failure), `output`, and the `event:`/`event: ` (space vs no-space) variance the
  parser currently handles. Assert `ExecuteResult` fields. This is the most
  rot-prone code in the repo and currently has **zero** direct coverage.
- **`test_template_shape.py`** — for each `TEMPLATE_*` in
  `src/marimo_inspection/templates/`, assert the string contains
  `marimo._code_mode` import and `async with cm.get_context()`; assert it's a
  well-formed standalone `str` (no leftover f-string holes).
- **`test_discovery.py`** — `discover_servers` with a temp registry dir
  (`monkeypatch` `XDG_STATE_HOME` or `_get_registry_dir`), fake registry JSON,
  and a fake `/api/sessions` 200 vs 500 to assert healthy-first ordering.

- **Acceptance:** these pass on `uv run pytest -m "not live"` with no process.

### Step 5 — Shrink Ring 2 to true behavior

In `tests/marimo_inspect/live/`, **remove shape-only assertions** that now belong
to Ring 1, and replace with behavioral checks against the deterministic fixture:

- `test_cell_map.py`: assert a **specific known cell name/code** appears (e.g. the
  cell that defines `value_a`/`value_b`), not just `"cells" in data`. Delete the
  `if total_cells == 0: return` escape hatch — the fixture is now non-empty, so an
  empty map is a real failure.
- `test_cell_data.py`: assert the code of a known cell round-trips.
- `test_cell_outputs.py`: assert the known cell's output (from the fixture) is
  present, keyed correctly.
- `test_variables.py`: actually **create** a variable via a cell and assert the
  template reports it (this requires persisting state — see note below), instead
  of the current comment that "scratchpad doesn't persist".
- `test_dependency.py`: assert the **actual parent/child names** the graph reports
  for two known related cells in the fixture.
- `test_errors.py`: against the fixture's single intentional raiser, assert
  `has_errors == True` and `total_errors >= 1`, plus the error type. (Only keep if
  meaningful against the deterministic fixture.)

**Note on persistence:** `execute()` runs in the scratchpad (non-persistent). To
test "create variable → template detects it," the dev agent must either (a) mutate
the notebook via `create_cell`/`edit_cell`/`run_cell` tools and assert through the
cell-map/cell-data templates (this also finally covers the mutation surface), or
(b) run the template against fixture state that already exists. Prefer (a) since it
closes the currently-**untested mutation tooling** gap, but scope it as its own
clear sub-step.

### Step 6 — Delete dead scaffold + legacy aliases

- Delete `tests/marimo_inspect/live/start_test_server.py` and
  `tests/marimo_inspect/live/create_session.py` (obsolete — superseded by Step 2).
- Delete the five legacy alias fixtures in `conftest.py`: `marimo_server`,
  `direct_kernel_manager`, `direct_server_url`, `direct_client`, `direct_session`
  (verify no test references them first — they don't today).

### Step 7 — Lifecycle & diagnostics

In `MarimoServerManager`:

- Replace `await asyncio.sleep(2)` with a bounded health-poll (`GET /api/version`
  or `/api/health`) with timeout.
- Bind `--port 0` and capture the **actual bound port** (from server stdout or the
  registry file), instead of the fixed default `2718`; keep `$MARIMO_TEST_PORT`
  only as an explicit override.
- Add a teardown hook (pytest `pytest_runtest_makereport` + `pytest_sessionfinish`
  or a `try/finally` around `yield manager`) that dumps captured `stdout`/`stderr`
  on failure. The pipes are already captured but never read.

### Step 8 — Consolidate docs (last)

- Move this plan's final state into `docs/live-tests.md` as current truth.
- Mark `docs/live-test-redesign.md`, `docs/live-test-architecture.md`,
  `docs/kernel-launch-progress.md`, `docs/integration-test-plan.md` as stale
  (add a one-line header) or delete, and update `AGENTS.md`'s docs map accordingly.
- Update the "Live suite is currently red" section in `docs/live-tests.md`.

### Step 9 (future) — Version matrix

Defer until Steps 1–7 land and CI is green. Then parameterize the Ring-2 body over
marimo versions (e.g. `0.24.x` and the next release) as a separate CI job, per
`docs/marimo-version-support.md`.

---

## 4. Definition of done

- [x] `uv run pytest -m "not live"` — green: 175 passed (includes lint, client SSE parsing incl. new edge cases, template shape, discovery).
- [x] `uv run pytest tests/marimo_inspect/live/ -m live` — green: 19 passed in ~3s, health-poll instead of `sleep(2)`, free port instead of fixed `2718`.
- [x] No `image_processing_lib`, no Playwright, no `websockets` dependency in the suite (session created via plain-HTTP `/sse` handshake).
- [x] Live failures surface server logs automatically (`pytest_sessionfinish` + `kernel_manager` setup error dump).
- [x] `start_test_server.py` / `create_session.py` / legacy alias fixtures removed.
- [x] Docs consolidated; `docs/live-tests.md` accurately describes the new reality; stale docs marked.
- [ ] (later) CI matrix runs the live suite across the supported marimo range.

---

## 5. Explicit risks / gotchas for the dev agent

1. **Do not widen the marimo bound** (`>=0.24.0,<0.25`) while doing this work; the
   live env couples in-process lint and in-kernel templates to one version. See
   `docs/marimo-version-support.md` first.
2. The `<0.25` bound and private APIs (`marimo._code_mode`, `marimo._ast`,
   `marimo._lint`) are a **contract**; the new behavioral tests make a future bump
   *visible*. Don't weaken assertions to make them vacuously pass — see Step 5's
   "delete the escape hatch" note.
3. `httpx2` is the HTTP client (a distro of httpx), not `httpx`; keep imports as
   `import httpx2 as httpx` like `client.py` does.
4. Websocket stream teardown must not leak the `asyncio.Task` (Step 2, §0.3.2).
5. Run `uv run ruff check .` and optionally `uv run ruff format .` before commit;
   the repo has pre-existing `# noqa` annotations for the async-in-test patterns you
   may keep.
