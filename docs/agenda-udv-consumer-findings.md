# Agenda (open issue): first-consumer integration findings (udv-echo-process)

> **Status:** Open — first real consumer of this stack outside the provider and
> the origin repo. Filed from the consumer repo on **2026-09-07** after running
> "Phase 0" of `udv-echo-process/docs/marimo-integration-plan.md`.
> Evidence labels: ✅ observed on the consumer machine (Linux, Python **3.14.7**
> kernel, marimo **0.24.0**, this repo's MCP server registered in the DSH
> harness) · 📄 from provider docs · ❓ unconfirmed.
> **Full raw journal:** `udv-echo-process/docs/marimo-integration-log.md`
> (entries F1–F13, S11–S14, O15–O24). This doc is the distilled *provider-side
> work list* from it.

## Consumer context

`udv-echo-process` (public GitHub repo, sibling checkout, **Python ≥ 3.14**,
uv-managed) is integrating live marimo notebooks + this MCP stack. Phase 0
passed: full agent loop (`list_active_notebooks` → `get_cell_map` →
`edit_cell` → `run_cell` → `get_variables` → `get_cell_outputs` →
`get_errors` → `marimo check`) worked end-to-end against a 3.14 kernel over an
ephemeral `uv run --with 'marimo[recommended]>=0.24.0,<0.25'` overlay.

## T1 — Headless `--no-token` servers are invisible to discovery ✅

- Launch: `marimo edit --headless --no-token --port <p> nb.py` (session created
  via the documented `/sse` handshake — that part works, 📄
  `agent-onboarding-demo-mcp.md` §Prerequisites).
- `~/.local/state/marimo/servers/` stayed **empty** for the whole live session;
  `list_active_notebooks()` with no `server_url` found 0 servers. Explicit
  `server_url` addressing worked perfectly.
- Cause (from marimo 0.24.0 source, ✅): `marimo/_server/server_registry.py`
  is invoked from `_server/api/lifespans.py` and `_server/start.py` —
  registration is skipped in headless mode (exact condition not yet isolated;
  could be `--headless` itself, or `--port`/`--no-browser` interaction).
- Why it matters: the harness-integration README's premise (axis B: the stdio
  server "sees every locally-discovered session") silently breaks for
  **browser-less** consumer setups — CI, remote boxes, headless agents. Every
  consumer will hit this.
- To do here:
  1. Reproduce: headless vs browser-connected `marimo edit --no-token`; diff
     registry entries.
  2. If by design → document it in `harness-integration/README.md` (the
     auto-bind story needs a "headless requires explicit server_url" caveat)
     and in the demo runbook; if not → upstream marimo issue.

## T2 — `list_active_notebooks` auto-bind semantics are murkier than documented ✅

- After `list_active_notebooks(server_url=…)`, the result says
  "session_id is now optional" — but the *next* call without `server_url`
  still failed with `Error: server_url is required`. The binding apparently
  holds only across **omitted**-arg calls of the discovery call itself, not
  reliably across tools/turns.
- To do here: clarify wording or make the bind sticky per session; consumer
  guidance meanwhile: always pass `server_url` explicitly. (Consumer plan/DoD
  already updated accordingly.)

## T3 — DSH harness MCP bridge mangles list-typed arguments ❌ consumer-side repro ✅

- `get_variables(variable_names=[...])` and `get_cell_outputs(cell_ids=[...])`
  fail with pydantic `list_type` errors — the harness delivers the JSON array
  as a **string**. Reproduced through the registered `marimo-inspect` stdio
  server (provider is pure HTTP client-side: `client.py` doesn't touch these,
  so the fault is the DSH MCP layer, not this repo).
- Workaround works (empty filter = all). To do here: nothing mandatory — maybe
  accept `str | list[str]` defensively (cheap). File against the harness.

## T4 — Private-API drift check: marimo `_code_mode` on 3.14 ✅

- Every live template ran against a 3.14.7 kernel (marimo 0.24.0): `cell_map`,
  `errors`, `variables`, `cell_outputs`, plus `edit_cell` (which round-trips
  through `_code_mode.get_context()` — its dry-run compile appears in
  tracebacks). All clean, including error paths (a cross-cell redefinition
  surfaced as `kind:"graph"` via `get_errors`).
- 📄 `marimo-version-support.md` says drift evidence so far was provider-side
  only. To do here: record this as consumer evidence; when the live suite gains
  a 3.14 leg (agenda-live-test item), cite
  `udv-echo-process/docs/marimo-integration-log.md` O21.

## T5 — Publishing checklist (repo to be made public — owner decision 2026-09-07) ✅

Pre-publish audit of this tree: MIT LICENSE present; no tailnet/RFC1918 IPs;
no credential strings (`token`/`api_key` hits are docs *discussing* marimo
auth); test fixtures use `/home/user/…` placeholders; only personal identifier
is the `pyproject.toml` author email. **No edits needed before flipping.**

After flipping:
1. `AGENTS.md` §Remote / publishing: drop "(**private**)" and the
   "don't leak" line (line ~186/189) — they become false.
2. `docs/harness-integration/README.md` §A1: remove the "Needs access to a
   **private** git repo — do not paste this URL into public docs" warning;
   A1 becomes the default consumer path, A3 (package index) stays aspirational.
3. Consider tagging `v0.2.0` (current package version) so consumers pin a tag,
   not floating HEAD (`udv-echo-process` will reference the tag in Phase 1).
4. `README.md` §Install: verify no "private repo / needs access" caveats
   remain (AGENTS.md line ~189 and harness-integration §A1 are the known
   spots).

- **2026-09-07-b** ❗ Status: items 1–4 are **NOT done** — this is a checklist
  to execute *after* the owner flips the repo public (Settings → danger zone →
  publish). The flip has not happened yet (repo still `Not Found`
  unauthenticated as of this entry). Verification sequence: flip → re-probe the
  API (`private: false`) → run items 1–4 here → `git tag v0.2.0 && git push
  --tags` → only then update the *consumer* repos: udv Phase 1 gate (sibling
  pattern + tag pin) and IPN L1/L2 (add `<0.25`, close L2, re-pin to
  `@v0.2.0`). *(This line originally claimed items 1–4 were done and cited a
  bogus commit hash — corrected in-session; the "hash" was a mis-copied lockfile
  pin, not a real provider commit. Kept as a self-caught-drift example.)*

- **2026-09-07-c** ✅ Status: flip landed. API re-probe confirmed
  `private: false`; repo now returns HTTP 200 unauthenticated. Items 1–4
  executed: AGENTS.md §Remote / publishing now says **public** (and gained a
  "Git tags" subsection), harness-integration §A1 drops the private-repo
  warning and shows the `@v0.2.0` pin form, README §Install verified clean
  (no private caveats). Tag `v0.2.0` is **not yet cut** — the owner has never
  used tags; AGENTS.md now documents the tag/release practice for the next
  agent to follow.

## T6 — Consumer gotchas worth cross-referencing (no work here) ✅

- **Sandboxed agents:** `uv` fails on read-only `$HOME` (`.cache/uv` lock) and
  matplotlib fails on unwritable `~/.config/matplotlib`. Fix: `UV_CACHE_DIR` +
  `MPLCONFIGDIR` pointed at the workspace. Candidate for
  `harness-integration/deepseek-harness-web-profile.md` (that doc already
  covers the `uv run` vs venv-binary gotcha).
- **`marimo[recommended]` cold install ≈ 340 MB / ~13 min** on a cacheless
  consumer — advise consumers to pre-sync once, not per `--with` probe.
- **marimo 0.24.0 API:** `mo.mpl` exposes only `interactive` (no `mo.plt` /
  `mo.pyplot`) — relevant to consumers wiring matplotlib figures; consumer
  library that saves-and-closes figures must render via `mo.image(path)` until
  it adds a fig-returning mode.

## T7 — Consumer session report: reviewed & triaged (2026-09-09) ✅

The consumer filed a full "using the MCP felt like" report from a long live
feature session: [`session-report-deepseek-harness-2026-09-09.md`]
(session-report-deepseek-harness-2026-09-09.md) — nine frictions (F1–F9) with
a prioritized backlog (P1–P5). Reviewed provider-side 2026-09-09 against the
source (`src/marimo_inspection/`, installed marimo 0.24 code-mode, git
history). Every code-verifiable claim checked out except the headline one:

- **F7 does NOT confirm T2 — reclassified.** T2 (server_url auto-bind) was
  *fixed* in `8a44b8c` (2026-09-07, in tag v0.2.0): `bind_active_session`
  stores both `session_id` and `server_url` (`tools/session.py`), every
  handler falls back to bound `server_url` (`tools/cells.py` `_get_client`),
  covered by `tests/test_session_binding.py`. The consumer demonstrably ran
  ≥ `8a44b8c` (list-arg normalization — the same commit — "works on v0.2.0"
  in their own table), so a still-call-local bind would mean ctx state is
  lost across tool calls **on the DSH stdio transport**, not the old bug.
  Open question: does FastMCP ctx state (session-scoped state store) survive
  across calls under the DSH harness's process/reconnect model? If no →
  document "pass `server_url` every call" for that harness (or make binding
  transport-independent); if yes → report was stale habit. Do not re-open
  T2 as a provider bug without a repro on the DSH side.
- **P3 is bigger than reported — the flags are hardcoded stubs.**
  `get_cell_map.has_output` isn't "unreliable": `templates/cell_map.py`
  hardcodes `"has_output": False` (also `has_console_output`, `has_errors`),
  and `get_cell_data`'s `variables` is hardcoded `None`
  (`templates/cell_data.py`). Fix or drop, don't document. Exposing
  UI-element blocks/object-ids is a real data-model gap (code-mode snapshot
  holds one main output per cell — `CellOutputs.output` is
  `dict[CellId, CellOutput]`) and couples to the token-gated-instantiation
  coverage gap in AGENTS.md.
- **P2 (error channels) has a cheap implementable half.** `get_errors` reads
  only `cell.errors` and hardcodes `"stderr": []` — UI-handler exceptions
  land in console outputs, which code-mode *can* read. Scanning console
  stderr for exception markers covers the visible half today.
- **P4 (agent docs) is the highest ROI for the next consumer session** —
  one guide encoding: final-expression display, creator-can't-read-own-
  `.value`, list-form `set_ui_value`, screenshot/Playwright, explicit-args
  habit. The report's own §4 asks for exactly this.
- **P1 split decision:** `set_ui_value` = feasible MCP tool via marimo's
  existing `POST /api/kernel/set_ui_element_value` (object_ids+values, list-
  shaped — explains the scalar `AssertionError`); needs object-ids → depends
  on P3. Raw `execute` = keep `cm`-over-HTTP, document prominently
  (roadmap "What NOT to Change" keeps scratchpad as transport).
- **F4 addendum:** `create_cell` defaults `hide_code=True`
  (`tools/mutation.py`) — reconsider the default (agents create user-facing
  cells) or document loudly.
- **F6 re-owned:** `screenshot` is a marimo `ctx` capability reached via the
  consumer skill's `execute-code.sh`, not an MCP tool — fix target is that
  script / marimo docs, not this repo's surface.

Provider-side follow-ups from this triage are tracked as T8 (Hermes
enablement + replication doc) and in `docs/mcp-upgrade-roadmap.md`.

## T8 — Enable marimo-inspect MCP for Hermes + write the replication doc (2026-09-09) ⏳

The DeepSeek Harness got its per-harness note
(`docs/harness-integration/deepseek-harness-web-profile.md`) plus an index
row in `docs/harness-integration/README.md`. Hermes is the provider's own
daily agent — it should get the same treatment so the setup is replicable
and self-documenting.

- **Current state (2026-09-09, ✅): the Hermes MCP entry is stale/broken.**
  `~/.hermes/config.yaml` `mcp_servers.marimo-inspect` runs
  `uv run --directory <old consumer checkout> fastmcp run
  src/marimo_inspection/server.py:create_server` with a `--reload-dir` on
  that consumer checkout — but the file no longer exists there
  (that repo now installs
  `marimo-inspect = { git = ... }` from this repo; `ls` on the old path
  fails). The entry points at the pre-extraction layout.
- **Target config (✅ verified boots 2026-09-09):** point at this repo's
  venv binary, mirroring the DSH doc's hardened form (Finding 2 — venv
  binary over `uv run`):

  ```yaml
  mcp_servers:
    marimo-inspect:
      command: ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
      args: ["--transport", "stdio"]
      timeout: 60
  ```

  Verified: `.venv/bin/marimo-inspect --transport stdio` starts the
  `marimo-inspection` server cleanly on this machine.
- **To do:**
  1. Update `~/.hermes/config.yaml` to the target config above (drop the
     dead `--directory`/`--reload-dir` consumer path; keep `--reload` OFF —
     it wedges the long-lived Hermes gateway tool catalog, per the config
     note). Hermes registers them as `mcp__marimo_inspect__*` (entry key
     `marimo-inspect`), verified 2026-09-09 — 13 marimo tools + 4 standard
     FastMCP ones (list_resources/read_resource/list_prompts/get_prompt).
  2. Restart/reload the Hermes MCP connection and confirm the 13 tools
     register (or apply the `hermes config set mcp_servers.marimo-inspect`
     equivalent).
  3. Write `docs/harness-integration/hermes-agent.md` mirroring the DSH
     note's shape: TL;DR, harness background, minimal working config,
     fields table, findings (the stale-path finding is the first one),
     verification, hardening. Add the index row to
     `docs/harness-integration/README.md`.
- **DoD:** tools live in Hermes against a real marimo session; the new doc
  is self-sufficient for a fresh zero-context agent to replicate (run it
  through a regression-gate subagent before declaring done).

- **2026-09-09-b** ✅ **Executed & live-verified.** Config fixed via
  `hermes config set` (venv binary, stdio, `timeout: 60`); gateway
  hot-reloaded and registered the server — `registered 17 tool(s):
  mcp__marimo_inspect__list_active_notebooks, …` (13 marimo tools +
  list_resources/read_resource/list_prompts/get_prompt). Doc written:
  `docs/harness-integration/hermes-agent.md` (mirrors the DSH note's shape,
  stale-path finding first); index row + B1 pointer added in
  `docs/harness-integration/README.md`; AGENTS.md docs map updated.
  **End-to-end round-trip exercised in-session** against a real headless
  marimo 0.24 kernel (notebook fixture, /sse handshake): the full agent loop
  worked through the live MCP tools with **zero explicit server_url/session_id
  after auto-bind** — `list_active_notebooks` → `get_cell_map` (no args) →
  `run_cell` → `get_variables` → `get_errors` → `create_cell` → `delete_cell`.
  This also **answers T7's open question from the Hermes side: FastMCP ctx
  state survives across stdio calls — auto-bind persists** (contradicting the
  consumer's DSH "call-local" report, which is a harness lifecycle artifact,
  not a provider bug).
- **2026-09-09-c** ✅ **Regression gate PASSED** (zero-context subagent,
  read-only). Doc found internally consistent; binary boot, live config,
  17-tool registration, tool catalog, and sibling-README row all verified.
  Gate gaps all closed post-run: (1) Verification step 3's `grep | tail -1`
  could surface a *stale second gateway daemon's* "parking" noise instead of
  the registration line — command now filters for "registered"; (2) the
  apply path now also sets `timeout: 60`; (3) boot check uses `timeout` +
  `</dev/null` so it can't look hung; (4) doc notes `uv sync` produces the
  venv; (5) Finding 1 and a PENDING hardening note record that any gateway
  instance started pre-fix keeps re-spawning the dead path until restarted.
  T8 fully done.

## T9 — Hermes live MCP validation: guarded `edit_cell` false conflict (2026-09-10) ⚠️

Full evidence: [`session-report-hermes-mcp-2026-09-10.md`](session-report-hermes-mcp-2026-09-10.md).

A live consumer notebook was launched locally and reached through the Hermes
stdio MCP gateway. `list_active_notebooks(server_url=...)` discovered one
active session; the bind persisted across later calls with neither
`server_url` nor `session_id`, independently reconfirming T8's Hermes
transport conclusion.

- **Read/run/verify surface ✅:** `get_cell_map`, `get_cell_data`,
  `get_cell_outputs`, `get_variables`, `get_dependency_graph`, `get_errors`,
  `lint_notebook`, and `run_cell` all worked. All 16 cells were driven from
  stale to idle; errors and lint were both zero; the consumer's static
  `marimo check` also passed. Live outputs included Plotly, Marimo UI, and an
  anywidget.
- **Write surface mixed:** `create_cell` and `delete_cell` worked. But the
  guarded `edit_cell` path failed reproducibly: create a disposable cell →
  `get_cell_data` → `edit_cell` gave `status: "conflict"`; a fresh re-read
  followed by the identical edit gave the same conflict. The cell was then
  deleted and the consumer tree remained clean.
- **Provider action:** diagnose and fix this false-conflict loop, then add a
  live regression for create → read → guarded edit → run → verify → delete.
  Do not advise routine `check_fresh=False`: that removes the concurrency
  safety the tool is meant to supply.
- **Adoption decision:** marimo-inspect can now be the default for discovery,
  inspection, execution, and verification. Do not retire `execute-code.sh`
  yet: it remains the fallback for safe existing-cell edits, programmatic UI
  changes (`set_ui_value` is not yet an MCP tool), arbitrary scratchpad probes,
  and server lifecycle. `discover-servers.sh` is redundant for the normal
  agent flow and may be deprecated to a human/debug fallback.
- **Documentation/resources:** replace the long CodeMode-first pairing skill
  only after the write defect is fixed. Publish short MCP-first workflow and
  safety resources, with raw CodeMode/shell recipes explicitly labelled as
  fallback material; the report proposes the resource breakdown and retirement
  gates.

## T9 RESOLVED — freshness repair, widget tool, truthful reads, resources (2026-09-10)

Provider fix landed in the working tree (uncommitted at time of writing).

**Root cause confirmed at source.** `edit_cell`'s guard compares the live hash
against the per-session `ChangeTracker` snapshot, but `get_cell_data` — the very
read the conflict message told agents to use — never updated that snapshot. Only
`get_cell_map` recorded fingerprints, so re-reading via `get_cell_data` could not
advance the baseline: a deterministic re-read → same-conflict loop. Two adjacent
defects found: a session with no snapshot let a never-read cell bypass the guard
entirely, and `edit_cell` returned the template's hash computed *before* the
code-mode context exit applied the queued edit (a stale success hash). A third
defect found during verification: `_refresh_snapshot` committed an empty
snapshot when its hash read failed, silently wiping the session baseline.

**Fix.**
- `ChangeTracker.record_cells()` — merge-only upsert (never erases fingerprints
  for cells not supplied), distinct from `commit()` which replaces the snapshot.
- `get_cell_data` now records the exact returned source hash + runtime state for
  each returned cell; a failed read (execution error or JSON parse failure)
  leaves the tracker untouched. Selective reads cannot erase other baselines.
- `edit_cell`: a never-read cell returns `needs_read` **unconditionally**
  (no-snapshot bypass removed); an absent cell id returns a clear error before
  mutating; success returns the post-context-exit hash from the refreshed
  snapshot; a failed refresh returns a warning and preserves the old baseline
  instead of committing an empty one. `check_fresh=False` remains an explicit
  force escape hatch, never the documented recovery path.

**Live regression (hermetic).** `tests/marimo_inspect/live/test_mutation.py`
drives the **real** handler functions against a real marimo 0.24 kernel on a
`tmp_path` copy of the fixture notebook, in-process so the change tracker is the
same singleton the MCP server uses:
- A: create → read (records baseline) → guarded edit (ok, post-exit hash) → run
  (state `stale`→`idle`) → re-read exact source → `get_errors` clean → delete in
  `finally` → gone from the live session and from disk.
- B: baseline read → **external** out-of-band code-mode edit → guarded edit
  returns `conflict` with nothing stomped → `get_cell_data` re-read re-arms the
  baseline → retried guarded edit succeeds.
The harness gained per-test notebook paths (`MarimoServerManager.notebook_path`,
previously hardcoded in the `/sse` handshake) and a byte-identity assertion that
the repo fixture is unchanged (the hermeticity gate).

**Also delivered (same change):**
- `set_ui_value` (14th tool) — sets a live `mo.ui` element by kernel-global name;
  no source-code argument by construction; the submitted JSON value is passed
  through without coercion, but widget acceptance is shape-specific. Its
  reactive re-runs are *verified* (`get_variables`/`get_cell_outputs`), not
  awaited. A widget updated from outside no longer requires an `edit_cell`.
- Truthful reads: `get_cell_map`'s `has_output`/`has_console_output`/`has_errors`
  are computed from live code-mode fields (`None` when unreadable) — the
  hardcoded `false` stubs are gone. `get_errors` now reports two distinct
  channels (`structured_errors`, `console_stderr`) plus a conservative
  `has_console_exception` marker scan, so console-only UI-handler tracebacks are
  visible without counting every stderr line as a runtime error.
- `create_cell` now defaults to `hide_code=False` (visible by default); this is a
  deliberate behavior change, documented in the README and onboarding runbook.
- Three static read-only MCP resources: `workflow://marimo-inspect/co-work-loop`,
  `workflow://marimo-inspect/live-safety`,
  `reference://marimo-inspect/fallbacks-and-limits` (packaged Markdown, loaded
  via `importlib.resources`, `text/markdown`, present in the built wheel).

**Verification (measured 2026-09-10).**
- `uv run pytest -m "not live" -q` → **266 passed, 22 deselected**
- `uv run pytest tests/marimo_inspect/live/ -m live -q` → **22 passed**
- `uv run pytest tests/marimo_inspect/live/test_mutation.py -m live -q` → **2 passed**
- `uv run ruff check .` → clean; `uv run ruff format --check .` → clean
- `uv run marimo check notebooks` → exit 0
- `uv build` → wheel member list includes all three
  `marimo_inspection/resources/*.md`
- `create_server()` probe → 14 tools, 3 resources (exact URI set)

**Retained fallbacks (unchanged decision).** `execute-code.sh` stays as the
fallback for arbitrary kernel probes, complex multi-operation code-mode blocks,
screenshots and server lifecycle — MCP is the default loop, not a total
replacement. `discover-servers.sh` is now a human/debug fallback only.

**Widget behaviour IS now CI-covered (correcting the plan's assumption).** The
plan assumed widget validation required a browser-instantiated session. That was
too conservative: fixture-notebook cells never execute in the `/sse` session,
but a cell *created through the MCP write tools* runs fine, so a widget can be
materialized in-kernel. Proven and locked in
`tests/marimo_inspect/live/test_ui.py` (2 tests, hermetic): setting
`set_ui_value("gate_slider", 7)` moved the element 3 → 7 **and** reactively
re-ran its dependent cell (a derived global went 103 → 107), both cells ending
`idle`; missing and non-UI names are refused with clear payloads. Live suite is
now 24 tests.

**Still open, genuinely browser-dependent:** whether a widget actually *renders*
in a frontend, and console-only UI-handler exceptions raised in the browser
context. No automated test claims those; they remain a manual browser gate.

## T10 — Consumer MCP-first adoption review: widget shape contract and single authority (2026-09-10) ⏳

A fresh consumer-side live test exercised the updated MCP resources and tools
against a disposable copy of `udv-echo-process/notebooks/echo_explorer.py` on a
real marimo 0.24.0 kernel. Discovery, state binding, inspection, linting,
runtime error reporting, the full create/read/edit/run/verify/delete lifecycle,
and invalid graph-edit rejection all passed; the consumer repository remained
byte-identical after cleanup.

One contract weakness remains: `set_ui_value` returned `status: "ok"` for a
scalar value sent to a marimo dropdown, but the widget did not change. The
tested one-element-list form worked and reactively reran descendants;
multiselect list values also worked. The tool/resource wording currently says
the JSON shape is preserved and depends on widget type, but does not make this
required dropdown shape discoverable. Define and test the public contract:
either normalize scalar dropdown inputs or expose widget-specific expected
shapes in the tool error/schema/documentation. A successful mutation response
must not imply completion without read-back verification.

The three packaged resources (`co-work-loop`, `live-safety`, and
`fallbacks-and-limits`) now cover the MCP-first workflow and explicitly mark
screenshots, server/kernel lifecycle, and arbitrary CodeMode probes as
fallbacks. No MCP prompts are currently exposed. This supports retiring
consumer-installed marimo workflow skills in favor of the MCP as the single
operational authority, provided consumer documentation teaches installation
and the remaining fallback boundaries.

**Provider action:** clarify/fix the dropdown value contract, add a live
regression for scalar and list-shaped dropdown updates, and keep the resource
text aligned with the tested behavior. Separately consider whether a small
package-install/harness onboarding resource belongs here, or whether that
material should remain consumer-repository documentation.
