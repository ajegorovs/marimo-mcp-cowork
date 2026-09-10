# Agenda (open issue): first-consumer integration findings (udv-echo-process)

> **Status:** **Open — 6 items left** (T3, T4, T6, T9-browser-half,
> T10-ownership, T11). Everything else from this integration is resolved; the
> §Resolved log keeps a one-line record plus the evidence pointer for each, so
> a resolved item never needs re-litigating from this file.
> **Created:** 2026-09-07 · **Condensed to open items only:** 2026-09-10.
> **Evidence labels:** ✅ observed (consumer machine, Linux, Python **3.14.7**
> kernel, marimo **0.24.0**, this repo's MCP server registered in the DSH
> harness — or provider-side, as stated) · 📄 from provider docs · ❌ not done /
> not covered · ❓ unconfirmed.
> **Raw sources (not repeated here):**
> `udv-echo-process/docs/marimo-integration-log.md` (entries F1–F13, S11–S14,
> O15–O24); `session-report-deepseek-harness-2026-09-09.md`;
> `session-report-hermes-mcp-2026-09-10.md`.
> **Current behavior is not documented here.** At runtime the authority for
> co-work is the three packaged MCP resources the server serves
> (`workflow://marimo-inspect/co-work-loop`, `…/live-safety`,
> `reference://marimo-inspect/fallbacks-and-limits`); `AGENTS.md` holds the
> contributor/consumer contracts, and the package version pin is
> `docs/marimo-version-support.md`.
>
> *Item ids are the original T-numbers, deliberately not renumbered — other
> docs and the consumer repo cite them.*

## Consumer context

`udv-echo-process` (public sibling repo, Python ≥ 3.14, uv-managed) was the
first consumer outside the provider repo. Phase 0 passed: a full agent loop
(`list_active_notebooks` → `get_cell_map` → `edit_cell` → `run_cell` →
`get_variables` → `get_cell_outputs` → `get_errors` → `marimo check`) ran
end-to-end against a 3.14 kernel over an ephemeral
`uv run --with 'marimo[recommended]>=0.24.0,<0.25'` overlay. The integration
is now MCP-first; `execute-code.sh` remains the documented fallback for
arbitrary kernel probes, multi-operation code-mode blocks, screenshots, and
server lifecycle.

## Open items

### T11 — a failed run reports through `run_cell`, not `get_errors` ❓

Observed while locking in the bare-name rule (2026-09-10): create a cell that
references a cell-private name (`int(_private_slider.value)`) and run it —
`run_cell` returns `{"error": "Execution failed", "stderr": <NameError
traceback>}` while a subsequent `get_errors` reports `has_errors: false`,
`total_errors: 0` for that same cell. So after a failed run the structured
error channel is empty and the only traceback is in the run's own payload.

- ❓ Open: is that marimo's own record (the cell never reached a normal
  execution path, so no `cell.errors` entry exists) or a gap in this repo's
  aggregation? Nothing pins the behavior today — the live mutation suite never
  asserts a *failing* run's payload.
- **Next action:** one probe — run a cell that raises an ordinary runtime error
  (`1/0`) through `run_cell`, then compare `get_errors` with the run payload.
  Then either document the split in `co-work-loop.md` §6 or widen the error
  aggregation. DoD: the behavior is documented, or the channels agree.

### T3 — DSH harness delivers list-typed MCP arguments as JSON strings ❌

- Repro ✅ (consumer side): `get_variables(variable_names=[...])` and
  `get_cell_outputs(cell_ids=[...])` fail with pydantic `list_type` errors
  through the registered stdio server. **Owner: the harness's MCP bridge, not
  this repo** — `client.py` is a pure HTTP client and never sees these
  arguments.
- Works today: the empty filter (no argument) returns everything.
- **Next action:** file against the DSH harness. Provider-side option, not
  scheduled: accept `str | list[str]` defensively on those two parameters
  (cheap). DoD: a harness-side repro, or the defensive type landing here with a
  unit test asserting a JSON-string list is accepted.

### T4 — the consumer's 3.14 kernel evidence is not recorded in the version contract ❌

- Evidence exists ✅ (consumer, marimo 0.24.0 kernel on Python 3.14.7): every
  live template ran clean — `cell_map`, `errors`, `variables`, `cell_outputs`,
  and `edit_cell`'s `_code_mode.get_context()` round-trip — including error
  paths (a cross-cell redefinition surfaced as `kind: "graph"` via
  `get_errors`).
- `docs/marimo-version-support.md` still describes drift evidence as
  provider-side only, and the `-m live` suite has no 3.14 leg.
- **Next action:** cite `udv-echo-process/docs/marimo-integration-log.md` O21
  in that doc; add a 3.14 leg to the live suite when one is wanted. DoD: the
  doc names the consumer evidence, or explains why it stays provider-only.

### T6 — sandbox/environment gotchas still to cross-reference ❌

- ✅ Already covered: a read-only `~/.cache/uv` breaks `uv add`/`uv run`
  (`UV_CACHE_DIR` workaround) — `docs/harness-integration/README.md`
  §Sandbox notes.
- ❌ Still uncaptured: matplotlib needs a writable `MPLCONFIGDIR` (it fails on
  an unwritable `~/.config/matplotlib`) in the same sandboxed setups; the
  cold-install cost of `marimo[recommended]` (≈340 MB / ~13 min cacheless) —
  pre-sync once instead of probing per `--with`; and marimo 0.24.0's
  `mo.mpl` exposing only `interactive` (no `mo.plt`/`mo.pyplot`), which decides
  how a consumer renders saved matplotlib figures.
- **Next action:** fold these into
  `docs/harness-integration/deepseek-harness-web-profile.md` (already carries
  the `uv run` vs venv-binary gotcha) or §Sandbox notes — whichever stays
  self-sufficient. DoD: a sandboxed consumer following the doc does not hit
  them.

### T9-b — the browser-dependent half of widget verification ❌ (manual gate)

- ✅ Automated and hermetic now: the write surface (create → read → guarded
  edit → run → verify → delete, plus the external-conflict → re-read → recover
  path) and `set_ui_value` against a real widget with a real reactive re-run
  (`tests/marimo_inspect/live/test_mutation.py`, `…/test_ui.py`).
- ❌ No automation claims: whether a widget actually **renders** in a
  frontend, and console-only `on_change` handler exceptions raised **in the
  browser context** (`get_errors.console_stderr` is exercised only for
  kernel-side failures).
- **Next action:** one manual browser pass — launch the demo notebook *without*
  `--headless`, drive `set_ui_value`, confirm the control moves in the UI,
  induce a bad dropdown key, and read `get_errors` (`structured_errors` vs
  `console_stderr`). Record the evidence in this item. This is the last
  widget-behaviour gap; it needs a browser, so it cannot be CI-covered.

### T10 — widget value-shape contract ✅ resolved in code, ❓ one ownership decision left

- ✅ **Fixed** in the working tree (see §Resolved log for the mechanism). The
  live suite locks: scalar → `dropdown` refused with `did_you_mean` and no
  change; `["beta"]` applied + verified + dependent cell re-ran; a repeat with
  the same value is a verified no-op; an unknown option key returns the
  kernel's own `ValueError` as `status: error` / `reason: value_not_applied`
  with the widget unmoved; and a widget bound to a leading-underscore name is
  unreachable (`reason: unknown_variable`).
- ❓ **Still open (decision, not code):** does a package-install / harness
  onboarding resource belong in this repo's MCP resources, or does that
  material stay consumer-repo documentation? The three packaged resources cover
  *runtime* co-work only.
- **Next action:** decide; if "provider", add a fourth resource in its own
  change and update the resource tests' URI-set assertion. DoD: a recorded
  decision either way.

## Resolved log

One line each, with the pointer that holds the detail. Ordered by item id.

- **T1** ✅ *Headless `--no-token` servers are invisible to discovery* — by
  design: `marimo/_server/server_registry.py` is registered only on the
  browser path. Consumer caveat documented: `harness-integration/README.md`
  ("a server alone is not a session") and `agent-onboarding-demo-mcp.md`
  §Prerequisites (`/sse` handshake, or launch with a browser).
- **T2** ✅ *`list_active_notebooks` auto-bind* — fixed in `8a44b8c` / v0.2.0:
  the bind stores **both** `session_id` and `server_url`
  (`tools/session.py`), every handler resolves from the bound state, covered by
  `tests/marimo_inspect/test_session_binding.py`. Residual "call-local"
  reports from the DSH harness are a harness process/reconnect artifact, not
  this bug (T7 triage; reconfirmed over Hermes stdio in T8/T9).
- **T5** ✅ *Publishing checklist* — repo flipped public; items 1–4 executed
  (AGENTS.md, `harness-integration/README.md` §A1, README); tags cut:
  `v0.2.0`, `v0.3.0`. The tag/release practice is documented in AGENTS.md
  §Git tags.
- **T7** ✅ *Consumer session-report triage* — every code-verifiable claim
  checked out except the headline (F7 did **not** confirm T2). Two findings
  were real and became work: the hardcoded read flags (`cell_map` `has_output`,
  `errors` `stderr: []`) and the missing console error channel; both landed
  under T9.
- **T8** ✅ *Hermes enablement + replication doc* — the stale pre-extraction
  consumer path was replaced by this repo's venv binary (stdio, `timeout: 60`),
  the gateway registered 17 tools on 2026-09-09 — the marimo surface was 13
  tools at that date (before `set_ui_value` landed) plus the 4 resource/prompt
  wrappers the gateway adds itself (`list_resources`, `read_resource`,
  `list_prompts`, `get_prompt`). This server's own catalog is 14 marimo tools
  (probed 2026-09-10) with **no** standard wrappers, so the docs' "14 tools"
  counts the marimo surface while the gateway figure counts its own additions
  too — the two numbers are different sets, and the gateway's list is expected
  to read 18 when it next re-registers (not re-measured). And
  `docs/harness-integration/hermes-agent.md` was written and passed a
  zero-context regression gate. Also answered T7's open question: FastMCP ctx
  state (auto-bind) **survives** across stdio calls.
- **T9** ✅ *Write-surface repair* — freshness guard made sound:
  `ChangeTracker.record_cells()` merge-only upsert; `get_cell_data` records the
  exact source hash it returned; `edit_cell` refuses unconditionally on a
  never-read cell, returns the post-context-exit hash, and no longer commits an
  empty snapshot when the hash refresh fails; `check_fresh=False` stays an
  explicit escape hatch. Plus truthful `get_cell_map` flags, split
  `structured_errors` / `console_stderr` in `get_errors`, `create_cell` visible
  by default, and the three packaged MCP resources. Residual browser half is
  T9-b.
- **T10** ✅ *Widget shape contract* — never coerces: the pre-flight guard
  derives the accepted shape from the element's own `UIElement[...]`
  declaration (`list[str]` for a dropdown, `int | float` for a slider) and
  refuses a mismatch with `did_you_mean` before anything is applied. The update
  is verified by re-reading `element.value` in a second code-mode context
  (`verified` / `applied` / `no_change`, before/after values), and a value
  marimo swallowed (it catches the exception and only writes the traceback to
  the kernel's stderr) is converted into `status: error`,
  `reason: value_not_applied` with the kernel's own message. Split across
  `templates/ui.py` (guard + read-back), `tools/ui.py` (stderr rejection scan,
  payload semantics), the widget tests, and `co-work-loop.md` §5 (per-widget
  shape table). Ownership question tracked in T10 above.

## Measured state (2026-09-10, after T9 + T10)

- `uv run pytest -m "not live" -q` → **278 passed, 28 deselected**
- `uv run pytest tests/marimo_inspect/live/ -m live -q` → **28 passed**
  (includes 6 hermetic widget tests: `tests/marimo_inspect/live/test_ui.py`)
- `uv run ruff check .` / `uv run ruff format --check .` → clean
- `uv run marimo check notebooks` → exit 0

Counts drift as tests are added; the split (`-m "not live"` vs `-m live`), not
the exact numbers, is the contract.
