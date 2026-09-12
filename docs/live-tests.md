# How we run live kernel tests

> Updated: 2026-09-12 — verified against the current working tree (live suite
> green, incl. the hermetic mutation, widget, T-V3 dependency-completeness and
> T-V4 notebook-only-variables regressions; see
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
   mutation regressions, which boot one additional isolated server per test
   (see below).

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

## What's tested (41 tests, 9 files in `tests/marimo_inspect/live/`)

The directory holds 8 test modules plus the shared `conftest.py` harness
(`MarimoServerManager` + fixtures). Counts are stable as of the run in
[Current status](#current-status).

| File | Tests | Asserts |
| --- | --- | --- |
| `conftest.py` | — | harness only: boots the session-scoped headless server, creates its session via the `/sse` handshake, and exposes the per-test `mutation_server` fixture |
| `test_cell_map.py` | 5 | map reports the fixture's 6 cells incl. hidden setup (`def _double` preview) and the intentional error cell; preview truncated to 3 lines; truthfulness flags (`has_output`/`has_console_output`/`has_errors`) are bool or None, never faked |
| `test_cell_data.py` | 4 | per-cell code round-trips (computed-value cell, error cell); count agrees with cell map |
| `test_cell_outputs.py` | 3 | every cell listed with the documented output keys |
| `test_variables.py` | 4 | runs against a live kernel and returns the documented structure; **T-V4** — an unfiltered call reports executed notebook-defined **public** names only, excluding kernel-injected globals (`input`, `spec_from_loader`), the template's own scaffolding and private leading-underscore names, with the allowance derived from the notebook graph's cell definitions and exercised via `mutation_server` cells that actually execute (explicit filtered lookup of a public name is preserved; a private/absent name reports nothing); degrades gracefully without numpy/pandas (regression for the unguarded numpy import) |
| `test_dependency.py` | 3 | template executes against a live kernel and returns the documented structure/types; **T-V3** — cell completeness: one dependency entry per live notebook cell with `set(cells ids) == set(get_cell_map ids)`, `cell_name` agreement for *every* cell, a real parent/child edge between a created import cell and its dependent, and a never-run fixture cell present with empty graph-derived lists (no invented edges, no dropped cells) — all through the real handlers against `mutation_server` |
| `test_errors.py` | 5 | template returns consistent, typed error summary; stable across repeated runs — plus **console-channel regressions**: a UI-handler traceback marimo never records structurally is flagged through `console_stderr` (`has_console_exception: true`), and a `print()` lands in `get_cell_outputs.stdout` |
| `test_mutation.py` | 8 | **hermetic mutation regressions**: create→read→guarded-edit→run→verify→delete, external-conflict→re-read→recover, and the guard-scope invariants — an unrelated write neither disarms the guard nor blesses a never-read cell, a `get_cell_map` preview records no read baseline, an insert/delete leaves every other cell's `code_hash` unchanged, and `get_cell_map` still reports `changes_since_last` — see [Hermetic mutation regressions](#hermetic-mutation-regressions) below |
| `test_ui.py` | 9 | **widget regressions**: `set_ui_value` moves a live widget and reactively re-runs its dependent cell (3→7 and 103→107, both idle); a scalar sent to a `dropdown` is refused with `did_you_mean` and changes nothing; the corrected one-element list applies, is verified by read-back, and re-runs the dependent cell; a repeat is a verified no-op; an unknown option key surfaces the kernel's own `ValueError` as `status: error` with the widget unmoved; a widget bound to a leading-underscore name is unreachable (`reason: unknown_variable`) because marimo keeps such names cell-private — and that same case pins the error-channel split: its failing run reports through the `run_cell` payload and the cell's `console_stderr`, while the structured channel stays silent (`has_errors: false`, no structured error counted); a widget whose `on_change` handler raises returns `reason: on_change_failed` with `applied: true` and the value genuinely moved (1 → 5, confirmed by an independent read); a repeat of the value the widget already holds whose handler raises reads back unmoved and is *still* `on_change_failed` — `applied: false` + `no_change: true` + `handler_ran: true`, classified from the traceback's call site, never `value_not_applied`; a dropdown built from numeric options stores the number and is addressed by its STRING transport key — sending scalar `4` is refused with `did_you_mean: ["4"]` (never `[4]`, which the kernel itself rejects), and applying `["4"]` moves the element to the numeric 4 (`value_after: 4`, confirmed by a dependent read `4 * 2 == 8`); missing/non-UI names are refused with clear payloads. Widgets are materialized by *creating* the cell through the MCP tools, so this needs no browser — see [Hermetic widget regressions](#hermetic-widget-regressions) below |

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

The remaining eight tests cover the dropdown contract (including the
numeric-option string key), the rejection paths, the `on_change` failure classes
and the cell-private name rule — they exist because a flush is not proof of
application (`set_ui_value` in `tools/ui.py`):

| Test | Locked-in behaviour |
| --- | --- |
| scalar → `dropdown` | `status: error`, `reason: value_shape_mismatch`, `accepted_shape: list[str]`, `did_you_mean: ["beta"]`, widget and dependent cell untouched. A scalar trips `assert len(value) == 1` inside `dropdown._convert_value`; marimo **catches** that, writes the traceback to the kernel's stderr and drops the update, so without the guard this returned `ok` with no change |
| `["beta"]` → `dropdown` | `status: ok` with `verified`/`applied` true, `value_before`/`value_after` = alpha/beta, dependent cell re-ran, and an immediate repeat is `applied: false` + `no_change: true` |
| `["nope"]` → `dropdown` | `status: error`, `reason: value_not_applied`, `kernel_message` is the kernel's own `ValueError` naming the valid options, widget unmoved — marimo's rejection is converted into a real error instead of a false `ok` |
| missing / non-UI name | refused with `reason` (`unknown_variable` / `not_a_ui_element`), `datatype` reported, no traceback dump |
| `on_change` raises, value moved | `status: error`, `reason: on_change_failed`, `applied: true`, 1 → 5 confirmed by an independent read — only the callback failed |
| `on_change` raises, value already held | `status: error`, `reason: on_change_failed` with `applied: false` + `no_change: true` + `handler_ran: true` — the read-back is unmoved, and only the traceback's call site (`self._on_change(self._value)` vs `self._convert_value(value)`) separates this from a rejected conversion, because marimo writes the **same** notice for both |
| scalar `4` → numeric-options `dropdown` | the element is keyed by the strings `"1"`..`"4"` and stores the number, so the scalar is refused with `did_you_mean: ["4"]` (`value_shape_mismatch`, nothing moved); the int `[4]` a caller would naively try is *rejected by the kernel* (`value_not_applied`, unmoved), and only `["4"]` applies — verified read-back to the numeric `4`, with the dependent cell reading `4 * 2 == 8` (not the string `"44"`) |

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

## Current status (verified 2026-09-12)

**The live suite is green.** On this tree the two tiers pin the same collection
split (380 tests collected in total):

```text
uv run pytest -m live
=> 41 passed, 339 deselected

uv run pytest -m "not live"
=> 339 passed, 41 deselected
```

(Elapsed times are machine-dependent and not part of the contract; the split and
the counts are. Re-derive them with `--collect-only` after adding tests.)

The widget regressions alone (they boot one isolated server per test):

```text
uv run pytest tests/marimo_inspect/live/test_ui.py -m live
=> 9 passed
```

The mutation regressions alone:

```text
uv run pytest tests/marimo_inspect/live/test_mutation.py -m live
=> 8 passed
```

The console-channel regressions alone:

```text
uv run pytest tests/marimo_inspect/live/test_errors.py -m live
=> 5 passed
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
