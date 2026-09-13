# How we run live kernel tests

> Updated: 2026-09-13 — verified against the current working tree (live suite
> green, incl. the hermetic mutation, widget, run-mode (T15), kernel-restart
> (Wave 3), T-V3 dependency-completeness, T-V4 notebook-only-variables, T20
> button-click and T22 session-census-field-limit regressions; see
> [Current status](#current-status)).
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
     suite never touches the developer's real marimo state (and reaped in
     `stop()` so long pytest sessions don't accumulate temp dirs).
   - `MarimoServerManager.start(notebook_path=...)` accepts any path and
     `create_session()` hands that SAME path to the `/sse` handshake — the
     mutation suite relies on this to boot servers on disposable copies.
   - `start()` health-polls `GET /api/version` instead of sleeping.

2. **Session creation via the `/sse` handshake** (the key fix). marimo
   materializes a kernel session **only** when a client connects to
   `/sse?session_id=<uuid>&file=<notebook>` (or the `/ws` websocket) — the
   handshake the frontend performs. Polling `GET /api/sessions` can never
   create one (that was the old, red suite's bug). The manager opens the stream
   and waits for the `kernel-ready` event, which guarantees the kernel is up.

3. **One session per server.** marimo edit mode allows exactly one session per
   server; a second connection replaces the first. So tests share the single
   session rather than creating fresh ones per test — EXCEPT the hermetic
   mutation, widget and run-mode regressions, which boot one additional isolated
   server per test (see below).

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
Templates that read *executed* cell state therefore report empty/fresh values,
and the live tests assert the honest, verifiable contract for that state:

- `cell_map` and `cell_data` read cell *source/structure* and work fully.
- `get_dependency_graph` inventories **every** live notebook cell from the
  notebook structure, so its `cells[]` id set matches `get_cell_map` even before
  anything runs (the T-V3 regression); only its graph-derived `defs`/`refs` and
  parent/child edges stay empty for the unexecuted cells.
- unfiltered `get_variables` reports executed notebook-defined **public** names
  only (the T-V4 contract), so on the shared fixture — whose cells never run —
  it is legitimately empty rather than leaking kernel globals.
- `get_cell_outputs` and `get_errors` return their documented structure with
  empty executed content.

Executed-cell behaviour is covered by the per-test `mutation_server` fixtures
instead: cells created through the write tools *do* run, which is what makes the
dependency, variables and widget regressions behavioral rather than structural
(see the hermetic sections below).

## What's tested (60 tests, 12 files in `tests/marimo_inspect/live/`)

The directory holds 11 test modules plus the shared `conftest.py` harness
(`MarimoServerManager` + fixtures). Counts are stable as of the run in
[Current status](#current-status).

| File | Tests | Asserts |
| --- | --- | --- |
| `conftest.py` | — | harness only: boots the session-scoped headless server, creates its session via the `/sse` handshake, and exposes the per-test `mutation_server` and `notebook_server` fixtures |
| `test_cell_map.py` | 5 | map reports the fixture's 6 cells incl. hidden setup (`def _double` preview) and the intentional error cell; preview truncated to 3 lines; truthfulness flags (`has_output`/`has_console_output`/`has_errors`) are bool or None, never faked |
| `test_cell_data.py` | 4 | per-cell code round-trips (computed-value cell, error cell); count agrees with cell map |
| `test_cell_outputs.py` | 5 | every cell is listed with the documented output keys; `runtime_state` matches the cell map and `output_stale` marks a stale cell while preserving its prior rendering, then clears after a verified re-run (T21) |
| `test_variables.py` | 4 | runs against a live kernel and returns the documented structure; **T-V4** — an unfiltered call reports executed notebook-defined **public** names only, excluding kernel-injected globals (`input`, `spec_from_loader`), the template's own scaffolding and private leading-underscore names, with the allowance derived from the notebook graph's cell definitions and exercised via `mutation_server` cells that actually execute (explicit filtered lookup of a public name is preserved; a private/absent name reports nothing); degrades gracefully without numpy/pandas (regression for the unguarded numpy import) |
| `test_dependency.py` | 3 | template executes against a live kernel and returns the documented structure/types; **T-V3** — cell completeness: one dependency entry per live notebook cell with `set(cells ids) == set(get_cell_map ids)`, `cell_name` agreement for *every* cell, a real parent/child edge between a created import cell and its dependent, and a never-run fixture cell present with empty graph-derived lists (no invented edges, no dropped cells) — all through the real handlers against `mutation_server` |
| `test_errors.py` | 5 | template returns consistent, typed error summary; stable across repeated runs — plus **console-channel regressions**: a UI-handler traceback marimo never records structurally is flagged through `console_stderr` (`has_console_exception: true`), and a `print()` lands in `get_cell_outputs.stdout` |
| `test_mutation.py` | 8 | **hermetic mutation regressions**: create→read→guarded-edit→run→verify→delete, external-conflict→re-read→recover, and the guard-scope invariants — an unrelated write neither disarms the guard nor blesses a never-read cell, a `get_cell_map` preview records no read baseline, an insert/delete leaves every other cell's `code_hash` unchanged, and `get_cell_map` still reports `changes_since_last` — see [Hermetic mutation regressions](#hermetic-mutation-regressions) below |
| `test_run_cell_modes.py` | 5 | **hermetic run-mode regressions**: `mode="all"` on a fresh `/sse` session runs every document cell and registers an unreferenced widget leaf (unreachable via `set_ui_value` before); `mode="descendants"` refuses `graph_unpopulated` on an unregistered target (nothing runs) and resolves target + descendants once `mode="all"` populated the graph; a mixed batch reports `exception` and `cancelled` per cell while the run call itself errored; unknown ids/names and `all` + non-empty `cell_id` abort before anything runs, and the default `mode="cell"` stays single-target; a cell NAME resolves like a cell id (pre-modes compat) for `cell` and `descendants`, with the resolved id reported — see [Hermetic run-mode regressions](#hermetic-run-mode-regressions) below |
| `test_restart.py` | 3 | **hermetic kernel-restart regressions** (Wave 3): `restart_kernel` closes the kernel and **re-materializes** a replacement via the `/sse` handshake — never reporting success from the POST 200 alone — asserting `re_materialized: true`, one live session under the **expected** id, a genuinely new kernel (the scratchpad's `os.getpid()` changes), execution state reset (a kernel global is gone, every cell `stale`), the change tracker cleared (the first `edit_cell` of a file-parsed cell is `needs_read` again), the server process preserved (the skew token re-read from the page and unchanged), and a following `run_cell(mode="all")` re-running the document green; plus `session_id_stable: false` / `session_verification: "point_in_time"` with a later browser-like reconnect re-keying the id (the old id then answers `Invalid session id`); plus the zero-session/unknown-id guard (`reason: session_not_found`, `state_changed: false`, nothing closed, the live session named) — see [Hermetic kernel-restart regressions](#hermetic-kernel-restart-regressions) below |
| `test_sessions.py` | 3 | **T22** — a raw `GET /api/sessions` census on a hermetic server publishes only `filename`/`path` per session (no creator, owner, creation time, or per-session client count), which is exactly why `list_active_notebooks` reports `provenance`/`owner` as `"unknown"` and `attached_client_count: null`; the **real handler** is then called against that server and pins `session_count`/`total_notebooks`/`active_connections` (deprecated alias) = 1, `attached_client_count: null`, `result_row_count` = 1; and `/api/status/connections.active` is measured to have main-consumer semantics — 1 while the session's main `/sse` stream is open, 0 after it is closed/orphaned — and is never reported as a client count |
| `test_ui.py` | 15 | **widget regressions**: `set_ui_value` moves a live widget and reactively re-runs its dependent cell (3→7 and 103→107, both idle); a scalar sent to a `dropdown` is refused with `did_you_mean` and changes nothing; the corrected one-element list applies, is verified by read-back, and re-runs the dependent cell; a repeat is a verified no-op; an unknown option key surfaces the kernel's own `ValueError` as `status: error` with the widget unmoved; a widget bound to a leading-underscore name is unreachable (`reason: unknown_variable`) because marimo keeps such names cell-private — and that same case pins the error-channel split: its failing run reports through the `run_cell` payload and the cell's `console_stderr`, while the structured channel stays silent (`has_errors: false`, no structured error counted); a widget whose `on_change` handler raises returns `reason: on_change_failed` with `applied: true` and the value genuinely moved (1 → 5, confirmed by an independent read); a repeat of the value the widget already holds whose handler raises reads back unmoved and is *still* `on_change_failed` — `applied: false` + `no_change: true` + `handler_ran: true`, classified from the traceback's call site, never `value_not_applied`; a dropdown built from numeric options stores the number and is addressed by its STRING transport key — sending scalar `4` is refused with `did_you_mean: ["4"]` (never `[4]`, which the kernel itself rejects), and applying `["4"]` moves the element to the numeric 4 (`value_after: 4`, confirmed by a dependent read `4 * 2 == 8`); **T20** — a side-effect-only `button` click is reported from the frontend click counter (0 → 1, `handler_invoked: true`) with the `mo.state` side effect confirmed by an independent read, the `0` counter sentinel reports `handler_invoked: false` and clicks nothing, a repeated nonzero counter reports `handler_invoked: null` while the runtime *did* run the handler again, a raising `on_click` returns `reason: on_click_failed` acknowledging the partial side effect it applied before raising, and `run_button` carries the same counter evidence; missing/non-UI names are refused with clear payloads. Widgets are materialized by *creating* the cell through the MCP tools, so this needs no browser — see [Hermetic widget regressions](#hermetic-widget-regressions) below |

Lint tests (`test_lint_source.py`) moved out of here — they run in-process and
do **not** need a kernel, so they live in the fast path (`tests/marimo_inspect/`).

## Hermetic mutation regressions

`test_mutation.py` exercises the **real MCP handler functions**
(`marimo_inspection.tools.mutation/cells/errors`) — not mocks and not bare
templates — against a real marimo 0.24 kernel. Because the handlers run
in-process, the change-tracker singleton's staleness guard (`edit_cell`
refusing to stomp a concurrently-modified cell) behaves exactly as under the
MCP server. The end-to-end flows and their guard-scope invariants are locked in:

- **Ordinary path**: `create_cell` → `get_cell_data` (records the baseline) →
  guarded `edit_cell` (ok, reports the post-context-exit hash) → `run_cell`
  (runtime state proves execution: fresh cells are `stale`, run cells go
  `idle`) → re-read (exact edited source) → `get_errors` (no errors) →
  `delete_cell` in a `finally` → the id is gone from a subsequent read and
  nothing of it remains on disk.
- **Recovery path**: `get_cell_data` baseline → a RAW scratchpad snippet
  (not a package template — a second co-worker editing out-of-band) mutates
  the cell → guarded `edit_cell` returns `conflict` and mutates nothing →
  re-read re-arms the baseline → the retried guarded edit applies → cleanup
  run.
- **Guard-scope invariants (H7/H9/H10)**: an unrelated write to another cell
  must not disarm the guard or bless a never-read cell; a `get_cell_map` preview
  records no read baseline while still feeding `changes_since_last`; and an
  insert or delete leaves every *other* cell's `code_hash` unchanged.

Isolation (the `mutation_server` fixture): every test boots its **own**
`MarimoServerManager` on a `tmp_path` **copy** of `notebooks/test_marimo.py`
(own port, own `XDG_STATE_HOME`), creates its own session, and stops the
server on teardown. The shared session-scoped server is never mounted, and the
fresh uuid session id keeps the process-wide change tracker's keys separate
from the shared session's. marimo may re-serialize the notebook file at load,
which is why only a disposable copy is ever opened; teardown of a passing test
also re-checks that the repo fixture is byte-identical to what it was at boot
(a failed body raises out through the fixture first, so this check never masks
a real error). Observed: in this headless `/sse` flow marimo does **not**
autosave cell edits back to the `.py` file — the copy is still mandatory
insurance, and the byte-identity check is the real hermeticity gate.

Fixture notebook: `notebooks/test_marimo.py` — a small, deterministic,
self-contained notebook (6 cells: hidden setup defining `_double`, an imports
cell, a computed-values cell, a dependency cell, a polars table cell, and one
hidden cell that raises `ValueError("integration_test_error")`). No
third-party image-processing lib, no numpy/pandas requirements.

## Hermetic widget regressions

`test_ui.py` proves `set_ui_value` against a real kernel, in the same
`mutation_server` isolation.

The plan originally assumed widget behaviour could only be validated against a
**browser-instantiated** session — the shared fixture's notebook cells never run,
because the `/sse` session cannot be instantiated without the skew token (see
the coverage-gap note above). That assumption is too conservative: a cell
*created through the MCP write tools* runs fine, so a widget can be
materialized in-kernel with no browser at all. The reactivity test:

1. `create_cell` a cell whose **final expression** is a `mo.ui.slider` (so the
   control is visible), then `run_cell` it.
2. `create_cell` + `run_cell` a **dependent** cell reading
   `int(gate_slider.value) + 100` — a cell cannot read the `.value` of a widget
   it created, so the read must live downstream. That cell is also the
   reactivity probe.
3. Assert the baseline: widget `3`, dependent `103`.
4. `set_ui_value("gate_slider", 7)` → assert `verified`/`applied` are true with
   `value_before`/`value_after` = 3/7, the widget is `7`, **and the dependent
   cell re-ran** to `107`, with both cells `idle`.

The remaining fourteen tests cover the dropdown contract (including the
numeric-option string key), the rejection paths, the `on_change` failure classes,
the T20 button-click evidence and the cell-private name rule — they exist
because a flush is not proof of application (`set_ui_value` in `tools/ui.py`):

| Test | Locked-in behaviour |
| --- | --- |
| scalar → `dropdown` | `status: error`, `reason: value_shape_mismatch`, `accepted_shape: list[str]`, `did_you_mean: ["beta"]`, widget and dependent cell untouched. A scalar trips `assert len(value) == 1` inside `dropdown._convert_value`; marimo **catches** that, writes the traceback to the kernel's stderr and drops the update, so without the guard this returned `ok` with no change |
| `["beta"]` → `dropdown` | `status: ok` with `verified`/`applied` true, `value_before`/`value_after` = alpha/beta, dependent cell re-ran, and an immediate repeat is `applied: false` + `no_change: true` |
| `["nope"]` → `dropdown` | `status: error`, `reason: value_not_applied`, `kernel_message` is the kernel's own `ValueError` naming the valid options, widget unmoved — marimo's rejection is converted into a real error instead of a false `ok` |
| missing / non-UI name | refused with `reason` (`unknown_variable` / `not_a_ui_element`), `datatype` reported, no traceback dump |
| `on_change` raises, value moved | `status: error`, `reason: on_change_failed`, `applied: true`, 1 → 5 confirmed by an independent read — only the callback failed |
| `on_change` raises, value already held | `status: error`, `reason: on_change_failed` with `applied: false` + `no_change: true` + `handler_ran: true` — the read-back is unmoved, and only the traceback's call site (`self._on_change(self._value)` vs `self._convert_value(value)`) separates this from a rejected conversion, because marimo writes the **same** notice for both |
| scalar `4` → numeric-options `dropdown` | the element is keyed by the strings `"1"`..`"4"` and stores the number, so the scalar is refused with `did_you_mean: ["4"]` (`value_shape_mismatch`, nothing moved); the int `[4]` a caller would naively try is *rejected by the kernel* (`value_not_applied`, unmoved), and only `["4"]` applies — verified read-back to the numeric `4`, with the dependent cell reading `4 * 2 == 8` (not the string `"44"`) |

| side-effect-only `button` click | **T20** — the click lands but the element's own value does not move (it is the handler's `None` return). `status: ok` with `applied: false`, `value_before`/`value_after` `None`, yet `frontend_value_before`/`after` = 0/1, `click_delivered: true`, `handler_invoked: true`, `side_effects_verified: false`, and **no** `no_change`; the `mo.state` side effect is confirmed by an independent read of the dependent reader cell (the old payload said "already held this value", the T20 misread) |
| `button` counter `0` | the initialization sentinel: `handler_invoked: false`, `click_delivered: false`, a `warning`, and the reader cell still `0` — marimo's button conversion returns the initial value for 0 and never calls `on_click` |
| repeated `button` counter | `handler_invoked: null`, `click_delivered: null`, no `no_change`, a `warning` — the counter did not change, so the read-back cannot say whether the handler ran again. The runtime *did* invoke it (the reader cell grew 1 → 2), which is exactly the claim the tool declines to make |
| `button` `on_click` raises | `status: error`, `reason: on_click_failed`, `handler_ran: true`, `handler_invoked: true`, `side_effects_verified: false`; the message acknowledges the partial side effect the handler applied before raising (reader cell `1`), and no next step tells the caller to re-send the counter |
| `run_button` click evidence | `run_button` reuses the button component, so a nonzero counter reports the same counter evidence (`frontend_value_before`/`after` = 0/counter, `click_delivered: true`, `handler_invoked: true`, `side_effects_verified: false`) instead of a bare no-change |

`set_ui_value`'s failure *site* therefore comes from the kernel traceback and its
`applied`/`no_change` from the read-back; a truncated traceback with no readable
call site falls back to the read-back rule (hermetic cases in
`tests/marimo_inspect/test_ui.py`, which also pin the real stderr transcripts).

Use a **bare** widget name: marimo treats a leading underscore as
cell-private, so `_slider` is a poor `set_ui_value` target.

Frontend *rendering* has no automated test here — the suite boots kernels, not
frontends — but it is **not** an unverified claim: the T9-b browser pass settled
it (the `<marimo-dropdown …>` tag present in the DOM after `run_cell`, and the
page re-rendering the dependent cell when the widget's value moved; recipe in
`docs/agenda-udv-consumer-findings.md` §T9-b). Re-check by hand when widget
output markup changes. There is no separate "browser-context exception" class to
test for: a rejected UI update and a raising `on_change` both surface as
**kernel stderr** — the frontend merely displays it in the widget cell's console
area, raising no JS exception — so the failure is already covered by the cases
above.

Version contract: the live env couples the **in-process** lint (installed
marimo) and the **in-kernel** templates (server's marimo) to the same installed
version — see [marimo-version-support.md](marimo-version-support.md). That doc's
upgrade procedure (step 3) runs `uv run pytest -m live` before widening the
`<0.25` bound.

## Hermetic run-mode regressions

`test_run_cell_modes.py` pins the `run_cell` execution modes (T15) through the
**real handler** against a real 0.24 kernel. It uses the `notebook_server`
factory: a **purpose-built** notebook is written into `tmp_path` and mounted on
its own disposable server, because the repo fixture carries a deliberate
`ValueError` cell and a whole-notebook run on it could never be all-idle. The
factory applies the same hermeticity gate as `mutation_server` (teardown asserts
`notebooks/test_marimo.py` is byte-identical to what it was at boot).

The purpose-built documents hold a root (`seed = 1`), its descendant
(`doubled = seed * 2`) and an **unreferenced** widget leaf
(`mode_slider = mo.ui.slider(...)`, nothing depends on it); the failure document
adds a cell that raises `NameError` and a dependent that reads its name.

- **`mode="all"` is the run-all fix.** On a fresh, never-instantiated `/sse`
  session, `set_ui_value("mode_slider", …)` returns `unknown_variable` (the
  consumer's T15 symptom) — nothing has run the leaf. One `run_cell(mode="all")`
  queues every document cell, returns `status: ok` with all three in
  `succeeded_cell_ids` and `failed_cell_ids`/`not_run_cell_ids` empty, and the
  widget is then **registered and drivable** (`verified`/`applied`, 3 → 7). No
  widget-specific code: registration follows execution.
- **`mode="descendants"` refuses rather than degrades.** On the same fresh
  session the kernel graph is empty, so both the root and the widget leaf are
  refused with `reason: graph_unpopulated` and *nothing runs* (every cell is
  still `stale`). After `mode="all"` has registered the document, the same call
  succeeds and queues exactly `{root, descendant}` — the unrelated leaf is not a
  descendant and is not queued.
- **A mixed failure is reported per cell.** The failure document's batch returns
  `status: partial` with `execution_error` + the kernel traceback in `stderr`
  (marimo discards the run payload when a target raises), yet `cells[]` still
  gives the truthful terminal state of every target: the two healthy cells are
  `succeeded`, the raising cell is `failed` with `runtime_state: "exception"` and
  a structured `runtime` error naming the `NameError`, and the dependent that
  never executed is `failed` with a `cancelled`/`interrupted` state. A single
  batch-level verdict could not express that.
- **Validation aborts before any run.** An unknown id returns
  `reason: unknown_cell_ids`, and `mode="all"` with a non-empty `cell_id` returns
  `reason: cell_id_not_allowed` — after both, every cell is still `stale`. The
  default `mode="cell"` remains single-target: the root runs, the independent
  widget leaf does not.
- **A cell NAME resolves like a cell id (pre-modes compat).** `run_cell` used to
  forward its target straight to `ctx.run_cell`, which accepts an id **or a
  name**, so a name still works after the modes landed: an unknown name is
  refused before anything runs (`reason: unknown_cell_ids`, nothing stale
  cleared), and a created named cell runs from its name alone — the payload
  echoes the name (`cell_id`) and reports the resolved id (`resolved_cell_id`,
  `requested_cell_ids`), so `mode="cell"` and `mode="descendants"` both queue
  the resolved id (queuing the name would raise at queue time).

Non-live coverage of the same contract lives in `tests/marimo_inspect/`
(`test_mutation.py` handler cases, `TestRunCellTemplates` in `test_templates.py`
for the plan/run/report templates, `test_server.py` for the advertised
three-literal `mode` schema, and `test_resources.py` for the packaged
co-work/live-safety/fallbacks guidance). The pre-fix failure evidence for both
tiers is preserved in `.hermes/probes/t15-prefix/`.

## Hermetic kernel-restart regressions

`test_restart.py` drives the **real** `restart_kernel` handler
(`marimo_inspection.tools.lifecycle`) against a real marimo 0.24 kernel on a
**purpose-built** notebook written into `tmp_path` (the `notebook_server`
factory), so the restart target is deterministic and the repo fixture is never
mounted. The purpose-built document holds only file-parsed cells, whose ids stay
stable across a restart, which lets a test address a cell by the id it read
before.

- **Close + re-materialize is the operation.** `POST /api/kernel/restart_session`
  only *closes* the session (the server is left at zero sessions until a client
  reconnects), so the tool performs the frontend's `/sse` handshake itself and
  the contract is pinned on the outcome: `re_materialized: true`,
  `sessions_after == 1`, and the **expected** session id live in `/api/sessions`
  again (a different id is never adopted). A cell created through `create_cell` +
  `run_cell` defines a live global the probe reads back before the restart.
- **The kernel is genuinely new.** The scratchpad reports the kernel process's
  `os.getpid()` through `POST /api/kernel/execute`; it changes across the
  restart, so a "session is live" check cannot pass by reusing the old kernel.
- **Execution state is reset.** After the restart the probe's global is gone
  (scratchpad `NameError`), the re-read cell map is `stale` because nothing has
  run, and a following `run_cell(mode="all")` re-executes the document green —
  while the notebook file survived on disk.
- **The change tracker is cleared.** A pre-restart read baseline is dropped, so
  the first `edit_cell` of a file-parsed cell is refused `needs_read` again
  (cell ids are not a stable handle across a restart).
- **The server process survives.** The skew-protection token is re-read from the
  page HTML afterwards and is unchanged (`skew_token_rotated: false`,
  `server_process_preserved: true`) — a server *relaunch* would rotate it, which
  is why a kernel restart is the instrument.
- **The id is point-in-time, not durable.** The payload reports
  `session_id_stable: false` / `session_verification: "point_in_time"`, and a
  later browser-like reconnect (a fresh-id `/sse` handshake; marimo edit mode is
  single-session) re-keys the session — the tool's verified id then answers
  `Invalid session id`. A configured session TTL can likewise reap the orphaned
  session; that is a contract stated in the payload/resource docs rather than a
  separate live case.
- **The zero-session / unknown-id guard reports, never restarts.** An id no live
  session reports returns `reason: session_not_found`, `state_changed: false`,
  names the live session, and closes nothing (re-verified against `/api/sessions`
  and a working read afterwards).

The client primitives (token scrape from the page HTML, the `restart_session`
status/body classification incl. the 403 → `edit_required` row, the `/sse`
handshake) and the tool's guard rails (sessionless server, unknown id, unknown
file, failed re-materialization, a foreign single session that is never adopted,
an unknown-outcome transport failure, tracker reset, token-rotation honesty) are
unit-tested without a kernel in `tests/marimo_inspect/test_lifecycle.py`.

## Current status (verified 2026-09-13)

**The live suite is green.** On this tree the two tiers pin the same collection
split (545 tests collected in total):

```text
uv run pytest -m live
=> 60 passed, 485 deselected

uv run pytest -m "not live"
=> 485 passed, 60 deselected
```

(Elapsed times are machine-dependent and not part of the contract; the split and
the counts are. Re-derive them with `--collect-only` after adding tests.)

The widget regressions alone (they boot one isolated server per test):

```text
uv run pytest tests/marimo_inspect/live/test_ui.py -m live
=> 15 passed
```

The run-mode regressions alone (one purpose-built notebook per test):

```text
uv run pytest tests/marimo_inspect/live/test_run_cell_modes.py -m live
=> 5 passed
```

The mutation regressions alone:

```text
uv run pytest tests/marimo_inspect/live/test_mutation.py -m live
=> 8 passed
```

The kernel-restart regressions alone (one purpose-built notebook per test):

```text
uv run pytest tests/marimo_inspect/live/test_restart.py -m live
=> 3 passed
```

The console-channel regressions alone:

```text
uv run pytest tests/marimo_inspect/live/test_errors.py -m live
=> 5 passed
```

The session-census field-limit and main-consumer-count regressions alone (one
purpose-built notebook):

```text
uv run pytest tests/marimo_inspect/live/test_sessions.py -m live
=> 3 passed
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
