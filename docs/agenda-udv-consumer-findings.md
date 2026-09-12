# Agenda (resolved): first-consumer integration findings (udv-echo-process)

> **Status:** **Round 1 closed; Round 2 open (T15/T17/T20/T21 since resolved)** —
> T15–T22: added 2026-09-12, extended the same day with T20–T22 from a third
> pass over that session (§Round 2). Round 2's T15 (execution modes), T17
> (validation-before-bind), T20 (truthful button click reporting) and T21
> (row-level staleness) are closed — the per-item state column and the Resolved
> log carry the decision trail; the prose below records each finding as it was
> filed, not as it stands today.
> T3, round 1's last item, was resolved by review on 2026-09-10: the DSH
> list-argument mangling it reported does not reproduce, and the defensive types
> it recorded as "no defensive type landed here" had in fact landed in `8a44b8c`
> (tag v0.2.0) — the same day T3 was filed. That review did surface one real
> provider-side defect, recorded and fixed as T14. T3 was the only item the
> harness ever owned, so nothing here is waiting on an upstream fix.
> **Created:** 2026-09-07 · **Condensed to open items only:** 2026-09-10 ·
> **T4 + T6 + T9-b + T10 + T11 + T12 + T13 closed:** 2026-09-10 ·
> **T3 + T14 closed (agenda fully resolved):** 2026-09-10.
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

None. Every item from this integration is resolved — T3 was the last, and
nothing here is waiting on an upstream harness fix. The log below keeps the
record and the evidence pointer for each item.

## Round 2 — findings (2026-09-12)

A second working session on the same consumer — a live sidebar notebook, a
throwaway probe, and a new example notebook in this repo — produced eight
findings, recorded below **as they were filed**. T15–T18 were **capability
gaps** (things the surface could not do); T19 is a **recipe gotcha** (something
it can do, that is easy to get wrong and cost real time here); T20–T22 were
**reporting gaps** — the surface does the thing but cannot say so: a click it
cannot confirm (T20), an output it restores without marking stale (T21), a
session whose owner it does not name (T22). Evidence is from the consumer's
session log; label conventions as above. The per-item state column and the
Resolved log are the current truth — each resolved item states its fix and the
test that pins it.

| id | item | state |
| --- | --- | --- |
| T15 | No run-all / run-with-descendants, so an unreferenced cell never runs and its widgets never register | **resolved — `run_cell` execution modes; see Resolved log** |
| T16 | A `mo.sidebar(...)` cell's content is unreadable (`visual_output: null`) | **revised 2026-09-12: no longer reproduces — see the T16 revision note** |
| T17 | Session identity is unstable without an attached client; an invented id binds but is then "not found" | **resolved — validation-before-bind; see Resolved log** |
| T18 | A server with no session is invisible, and two headless `marimo edit` launches disagreed about having one | open |
| T19 | Recipe: which session a browser or `/sse` stream ends up on, and why a page-vs-kernel divergence happens | open |
| T20 | `set_ui_value` reports `applied: false / no_change: true` for a button whose `on_click` only sets state, though the click fired | **resolved — frontend click-counter evidence and `handler_invoked`; see Resolved log** |
| T21 | `get_cell_outputs` carries no staleness signal, so a RESTORED cell output reads as current | **resolved — row-level state and stale flag; see Resolved log** |
| T22 | A session's provenance is invisible (agent-materialized vs frontend-owned), so a forced takeover is undiscoverable | open |

**T15 — no bulk execution (as filed 2026-09-12; RESOLVED — see the Resolved
log).** `run_cell` ran a cell plus its *ancestors*, so a cell that nothing else
depends on never ran and the widgets it defined were never registered:
`set_ui_value` then failed with `unknown_variable` ("not a live kernel global").
Observed twice — a probe's button variable, and the buttons cell of a two-cell
control block. A rollup tool (or a run-descendants flag) was the recorded ask;
without one, driving a notebook through MCP alone meant running every cell in
dependency order by hand, and on a `/sse`-created session (never instantiated)
that was the *first* obstacle on every fresh session. **Resolution:** `run_cell`
gained the `mode` execution modes (default `mode="cell"` unchanged, so existing
callers — including those passing a cell **name** — are untouched); the T15
entry in the Resolved log below holds the contract and the tests that pin it.

**T16 — sidebar is a blind spot.** `get_cell_outputs` returns
`visual_output: null` for a cell whose last expression is `mo.sidebar(...)` — the
cell genuinely has no output area, but the consequence is that neither the
sidebar's presence nor the widgets it hosts can be inspected through MCP.
Verifying one currently means `marimo export html`, decoding the JSON-escaped
payload, and searching only inside `<marimo-sidebar>` blocks — outside the tool
surface entirely. Observed on a probe's sidebar cell and on both sidebar cells of
the new example.

**T16 revision (2026-09-12, second session).** The blanket claim no longer
reproduces: `get_cell_outputs` returned a composed `mo.sidebar(...)` cell's whole
block (heading, slider and button row, as rendered HTML) on the consumer's
sidebar notebook, and likewise for its readout cell. What survives is a sharper
ambiguity — the payload did not say whether that block came from the cell's
*current* code or was restored from cache — filed as T21 below.

**T17 — session identity churn.** With no client attached, the `session_id`
advertised by `list_active_notebooks` changed between two consecutive calls
(`s_hbp1e0` → `s_mrh0bo`), and a call against the listed id then failed with
*"Session not found"*. Related: a session exists only while a client holds it —
a browser tab dying took its session with it, and so did killing the `/sse`
stream. Worse, `set_active_session` **accepts** an invented id (an
`/sse?session_id=<uuid>` id of one's own choosing) and later calls fail with
"not found", so the bind step reports success for a session that does not exist.
`marimo edit` allows exactly **one** session per server, so the id to target is
the one `/api/sessions` reports — never one invented by the caller.

**T18 — discovering a server is not discovering a session.** `marimo run` (app
mode) never appeared in `list_active_notebooks`; its server was counted in
`servers_discovered` but not listed in `notebooks`, and nothing could be attached
to it. That is the mechanism T1 already corrected — the tool enumerates
*sessions*, and a fresh headless server has none — but it is worth stating as
consumer guidance. One inconsistency is **unexplained**: two headless
`marimo edit` launches behaved differently (one listed a session before any
client connected; the other listed none until an `/sse` handshake). Both
observations are consistent with "enumerate sessions"; what created the first
session is not. Treat "launch in edit mode *and* materialize a session" as the
safe rule.

**T19 — recipe gotcha: which session does a client land on?** The working
verification (the one that produced trustworthy numbers) targeted
`s_na3rph` — the session the *frontend already held* — which is what T9-b's
recipe implies. Doing it the other way round (materialize a session with an
`/sse` handshake first, then attach a browser) produced divergent views: the page
rendered a slider at `0` while the session MCP was reading reported `5`, so
clicks moved one session's state while reads reported another's. With
one-session-per-server the two clients *should* share a session, so the
divergence is either a replacement on attach or a read-path difference — not
diagnosed. Recorded because it silently invalidates a whole browser pass: the
symptom is a click that appears to do nothing.

**Also observed (same session, same recipe).** Restarting a marimo server to load
a file edit rotates its per-process skew-protection token, and every page already
open keeps sending the previous one — each request then 401s with
`{"error": "Invalid server token"}` (`_server/api/middleware.py:161-172`) until
the user hard-reloads. The consumer read this as "I am not authorised to change
anything". Practical consequence for any agent-driven session: change a live
notebook through the write tools (which serialize back to the file) and reserve
restarts for what actually needs one, or announce the reload as part of the
restart.

**T20 — `set_ui_value` cannot report a click on a side-effect-only button (as
filed 2026-09-12; RESOLVED — see the Resolved log).** A
consumer notebook used step buttons (`±1`, coarse `±page`) whose handlers only
write a `mo.state` dict, and every click came back `verified: true,
applied: false, no_change: true, value_before: null, value_after: null` plus
"the element already held this value" — while each click HAD fired and moved the
value (`200 → 201 → 241 → 240`, confirmed via `get_variables` and a dependent
readout cell). Cause, from the widget itself: `mo.ui.button`'s frontend value is a
click counter and its element value is `on_click(counter)`
(`_plugins/ui/_impl/input.py`, `class button`:
`self._on_click = (lambda _: value) if on_click is None else on_click`,
`initial_value=0`). A handler that returns `None` therefore leaves the element's
own value unchanged, and the no_change branch reads that as "nothing happened".
The verdict is correct about the ELEMENT and misleading about the INTERACTION: a
caller cannot distinguish "click landed, side effect applied" from "click
dropped". The `next_steps` hint ("if the interaction was meant to trigger a re-run
of dependent cells, verify them with …") is the only reason this is diagnosable.
Consider a distinct status when `element_type == button` and the read-back value
is unchanged, or state in the payload that a no_change button report does not mean
the handler did not run.

**T21 — `get_cell_outputs` cannot say that an output is STALE or restored.** In
the consumer's sidebar notebook, `edit_cell` removed a `mo.sidebar(...)` call from
cell `Xref`; `get_cell_outputs(['Xref'])` then still returned a `<marimo-sidebar>`
block holding `<h3>Time</h3>` and a slider — that cell's *previous* rendering —
while `get_cell_map` listed the same cell as `runtime_state: "stale"`. The user
saw the consequence: "on the sidebar i see two time slider entries, one with
button set" (the restored block plus the newly composed one). `edit_cell` had
reported `status: ok` and both kernel and file sources were correct, so the only
wrong thing in the system was a payload the surface presented as current.
Mechanism: `marimo edit` persists per-notebook session state to
`notebooks/__marimo__/session/<notebook>.py.json` and restores each cell's output
from it on load — which is also why every cell reports `has_output: true` on a
freshly launched server where nothing has run. Re-running clears it (`run_cell`
on `Xref` replaced the output with the new, empty one, and the cache rewrote
itself), but nothing said so, and the wrong conclusion available to an agent is
that its own edit failed. Requested: carry the cell's `runtime_state` in the
outputs payload, or an explicit `output_stale: true`, or at minimum a line in the
tool description that a reported output may be restored from a prior run.

**T22 — a session's provenance is invisible, so a forced takeover is
undiscoverable.** A fresh `marimo edit` server reports **0 sessions**, and the
agent bridged that with the documented `/sse?session_id=…&file=…` handshake. That
made the session the *agent's*: the consumer's page then had to **take over** and
re-run the notebook before its widgets responded — "I had to press take over ->
run the notebook for sliders to work". `list_active_notebooks` reports
`session_id` and `active_connections: 1` but never *who* holds the session, so an
agent cannot tell whether binding is safe (a page owns it, per the T9-b order) or
whether it is about to displace a human. Requested: a provenance/owner field per
session (which client holds it, or `agent_materialized: true`), or a stated rule
that a session an agent had to materialize is one a human will have to take over.
Same pass, one useful observation: after the agent's `/sse` stream was killed the
session **survived** with `active_connections: 1` (the page) — a materialized
session can outlive the stream that created it.

## Resolved log

One line each, with the pointer that holds the detail. Ordered by item id.

- **T1** ✅ *A server alone is not a session* (mechanism corrected 2026-09-10) —
  a headless `--no-token` launch **does** write its registry entry (observed:
  `~/.local/state/marimo/servers/127.0.0.1_59309.json` present before any
  browser connected), and `discovery.py` calls a server healthy on
  `GET /api/sessions → 200` alone, so discovery is not what hides it:
  `list_active_notebooks` enumerates *sessions*, and a fresh server has none.
  The original phrasing ("invisible to discovery … registered only on the
  browser path") overstated the mechanism. The consumer caveat itself stands,
  as documented in `harness-integration/README.md` §Runtime prerequisites and
  `agent-onboarding-demo-mcp.md` §Prerequisites (`/sse` handshake, or launch
  with a browser).
- **T2** ✅ *`list_active_notebooks` auto-bind* — fixed in `8a44b8c` / v0.2.0:
  the bind stores **both** `session_id` and `server_url`
  (`tools/session.py`), every handler resolves from the bound state, covered by
  `tests/marimo_inspect/test_session_binding.py`. Residual "call-local"
  reports from the DSH harness are a harness process/reconnect artifact, not
  this bug (T7 triage; reconfirmed over Hermes stdio in T8/T9).
- **T3** ✅ *DSH list-argument mangling did not reproduce* — the reported
  pydantic `list_type` failures cannot occur on v0.2.0+: the defensive
  `str | list[str] | None` types landed in `8a44b8c` (2026-09-07, the first tag
  containing it is `v0.2.0`), the same day T3 was filed and under the subject
  "list-arg hardening"; T3's own raw source already recorded that the filtered
  list args "work on v0.2.0"
  (`session-report-deepseek-harness-2026-09-09.md`). Re-probed live through the
  registered DSH bridge (2026-09-10; now `dsh@0.1.5-rc.1` /
  `dsh-mcp-client@0.1.5-rc.2`, not the `0.1.2-rc.1` the harness note was written
  against) on an instantiated consumer session:
  `get_variables(variable_names=["data", "filtered"])` returned both variables,
  and `get_cell_outputs(cell_ids=["Xref", "BYtC"])` /
  `get_cell_data(cell_ids=["Xref"])` each filtered exactly. The bridge carries
  no mangling path — `dsh-mcp-client`'s `createExecutor` forwards the argument
  object verbatim (its only `JSON.stringify` renders *results*) and
  `dsh-agent-loop`'s `parseArguments` is a bare `JSON.parse` — so the
  "owner: the DSH bridge" attribution is withdrawn; the Hermes counter-evidence
  in T8 stands. The review that closed T3 found a real provider-side defect and
  became T14.
- **T4** ✅ *Consumer 3.14 evidence recorded* — `docs/marimo-version-support.md`
  gained a §Cross-version evidence subsection: provider marimo 0.24.0 on Python
  3.12 against a live kernel on **3.14.7**, every live template clean (O21: the
  loop ran green, `get_errors` 0, `marimo check` exit 0) including an error path
  caught as `kind: "graph"` (S13), plus the provider-side probe recorded in
  `agenda-remote-marimo-mcp.md`. Labelled *observed integration evidence, not a
  suite-enforced leg*: `-m live` boots from the dev environment (3.12), so a
  3.14-only regression is still invisible to the suite — that leg is deferred
  open work, deliberately unclaimed.
- **T5** ✅ *Publishing checklist* — repo flipped public; items 1–4 executed
  (AGENTS.md, `harness-integration/README.md` §A1, README); tags cut:
  `v0.2.0`, `v0.3.0`. The tag/release practice is documented in AGENTS.md
  §Git tags.
- **T6** ✅ *Sandbox/environment gotchas cross-referenced* —
  `harness-integration/README.md` §Sandbox notes gained the matplotlib
  config-dir finding, verified locally (matplotlib 3.11.1 / Python 3.12): an
  unwritable `MPLCONFIGDIR` **warns and falls back to a temp cache** — imports
  and plots still succeed, at the cost of a per-process font cache — so the
  consumer's "it fails" severity (S12) did not reproduce; the workaround
  (`MPLCONFIGDIR` → writable path) is the shared fact. Also recorded: the cold
  install cost (O18: ~340 MB / ~13 min cacheless → pre-sync once) and marimo
  0.24.0's `mo.mpl` exposing only `interactive` (no `mo.plt`/`mo.pyplot`; O23),
  which now lives under §What you inherit either way. The DSH note points at
  §Sandbox notes from Finding 2.
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
  `list_prompts`, `get_prompt`). This server's own catalog was 14 marimo tools
  when probed 2026-09-10 (15 now that `restart_kernel` landed) with **no**
  standard wrappers, so the docs' "15 tools" counts the marimo surface while the
  gateway figure counts its own additions too — the two numbers are different
  sets, and the gateway's list is expected to read 19 when it next re-registers
  (not re-measured). And
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
- **T9-b** ✅ *Browser pass executed* — a headless `--no-token` server on a
  scratch notebook (dropdown + reactive readback + a slider whose `on_change`
  raises), driven through this repo's MCP tools with a real browser attached to
  the *same* session. Verified: the widget **renders**
  (`<marimo-dropdown data-options='["alpha","beta","gamma"]'>` in the DOM after
  `run_cell`), and `set_ui_value("gate", ["beta"])` — kernel read-back
  `alpha → beta` — made the frontend re-render the dependent cell
  (`[readback] alpha` → `[readback] beta` on the page). The "browser-context
  exception" premise did not survive contact: an unknown dropdown key and a
  raising `on_change` both surface as **kernel stderr**, shown by the frontend
  in the widget cell's console-output area, and raise **no** JS exception (the
  only console noise was the unrelated copilot language server). Two provider
  defects fell out and became T12/T13. Recipe to repeat (~2 min): launch
  `marimo edit --headless --no-token --port <p> <nb>`, open the URL in a browser
  (the page performs the session handshake), then drive with the MCP tools
  passing `server_url`/`session_id` explicitly. Still not CI-coverable — but on
  demand, not "manual-unverifiable".
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
  shape table). **Ownership decided (2026-09-10):** package-install /
  harness-onboarding material stays in `README.md` §Install and connect an MCP
  client plus `docs/harness-integration/`; the three packaged resources remain
  **runtime-only** and no fourth resource is added (their authority is the
  co-work loop, not environment setup).
- **T11** ✅ *A failed run reports through `run_cell`, not `get_errors`* — the
  channels are truthful; the split is marimo's. Probe (2026-09-10, real 0.24.0
  kernel, isolated server): a cell raising `1/0`, or a NameError for a name
  that exists nowhere, records a structured runtime error (status `exception`,
  `cell.errors` populated) that `get_errors` reports; a cell referencing
  another cell's **cell-private** (leading-underscore) variable ends
  `exception` with `cell.errors == []`, so `get_errors` counts no structured error
 (`has_errors: false`). Not an aggregation gap — widening it would mean inferring errors
  from output mimebundle channels, while the structured record already covers
  every other failure class. Documented in the packaged `co-work-loop.md` §6 and
  pinned by the private-name case in
  `tests/marimo_inspect/live/test_ui.py`
  (`test_set_ui_value_cannot_address_a_cell_private_widget`).
  *Amended 2026-09-10 after T12:* with the console channel actually working the
  traceback is no longer "run-payload only" — the cell now appears in
  `get_errors.cells` with an empty `structured_errors` and a populated
  `console_stderr`. The structured counts stay silent, and that is what the pin
  asserts.

- **T12** ✅ *`get_errors`' console channel was dead code* — found by the T9-b
  browser pass, fixed 2026-09-10. marimo's `CellChannel` is a `str`-mixin enum:
  `str(CellChannel.STDERR)` is `CellChannel.STDERR` while
  `CellChannel.STDERR == "stderr"` is True, so every filter written as
  `str(getattr(o, "channel", "")).lower() == "stderr"` compared against a string
  that never occurs. Four sites (`templates/errors.py` ×2,
  `templates/cell_outputs.py` ×2) now share one normalizer with the serializer
  (`_channel_name`), so `get_errors.console_stderr`, `has_console_exception` and
  `get_cell_outputs.stdout/stderr` deliver for the first time — the channel was
  added in `5eb9615` (2026-09-10) in answer to the DSH consumer's F3 finding
  ("UI-element handler exceptions are invisible here") and had never matched a
  single event. Pinned live by
  `test_console_stderr_flags_a_console_only_ui_handler_exception` and
  `test_print_output_lands_in_the_stdout_channel` (both fail pre-fix), plus 3
  hermetic template tests whose fakes carry the **real** enum — a plain
  `"stderr"` string hides the bug. Consequence for T11 recorded above.
- **T13** ✅ *`set_ui_value` mislabelled an `on_change` failure* — fixed
  2026-09-10. marimo prints the same stderr notice for both failure points of a
  UI update, but they differ: a rejected conversion (`_convert_value`, unknown
  dropdown key) raises *before* the assignment, while the element's `on_change`
  runs *after* it. The tool now classifies from the read-back it already does
  (`tools/ui.py::_rejection_payload`): value did not move →
  `value_not_applied` (`applied: false`); value moved → `on_change_failed` with
  `applied: true`, before/after, and the handler's message — and no payload
  claims the element was unchanged when `value_after != value_before`.
  Documented in the packaged `co-work-loop.md` §5 and in a minimal correction to
  `live-safety.md` (it had said "the widget is unmoved in both cases"), pinned
  live by `test_set_ui_value_reports_an_on_change_failure_as_applied`.
  *Residual (resolved 2026-09-11):* a repeat of the value the element already
  holds whose handler then raises reads back unmoved, so the read-back alone
  called it `value_not_applied` — wrong, because nothing was rejected. The
  failure **site** is now read from the kernel traceback's own quoted call site
  (`tools/ui.py::_rejection_site`: `self._on_change(self._value)` →
  `on_change`, `self._convert_value(value)` → `convert`; marimo prints the same
  stderr notice for both, so the notice text could not decide it), and the
  read-back still decides whether the value moved. `on_change_failed` therefore
  has two shapes — `applied: true` (moved) and `applied: false` +
  `no_change: true` + `handler_ran: true` (already held) — and neither tells the
  caller to re-send the value. A truncated traceback with no readable call site
  falls back to the read-back rule, never to a wrong success. Pinned by three
  hermetic cases (`test_on_change_failure_on_an_unchanged_value_is_not_value_not_applied`,
  `test_unverified_readback_with_a_handler_failure_stays_truthful`,
  `test_truncated_stderr_falls_back_to_the_readback_rule`, plus the
  `TestRejectionSite` site tests) and the live
  `test_set_ui_value_on_change_failure_on_an_already_held_value` — all fail
  pre-fix; both packaged resources describe the third combination.

- **T14** ✅ *A JSON-encoded list argument was silently misread* — found while
  reviewing T3, fixed 2026-09-10. The normalization introduced with T2 wrapped
  *any* string as one literal name, so `cell_ids='["Xref"]'` became
  `['["Xref"]']`: the filter matched nothing and returned a structurally valid,
  empty payload — a **silent** miss, strictly worse than the loud pydantic
  error it had replaced (the exact DoD test T3 asked for was never written).
  Both list-typed parameters now share one implementation
  (`tools/args.py::normalize_list_arg`, replacing the separate
  `_normalize_names`/`_normalize_ids` that had drifted apart): a bare name, a
  native array, a JSON array (with `"[]"` meaning "all", as an omitted filter
  does) and a JSON-encoded string are all accepted, while a JSON scalar,
  nested array, or malformed input stays one literal name — so a
  numeric-looking id (`"5"`) is not coerced to an int. Pinned by
  `tests/marimo_inspect/test_list_args.py` (15 shape cases plus one
  handler-level case per list-typed tool); the JSON-array assertions fail
  against the pre-fix helper.
- **T15** ✅ *No bulk execution — `run_cell` now carries execution modes* —
  `run_cell(cell_id, mode="cell"|"descendants"|"all")`, default `"cell"`, with
  `cell_id` optional so existing callers are untouched and the advertised
  15-tool surface stays fixed (no new tool for bulk execution). `cell_id` is
  resolved by cell **id
  or cell name**, exactly as `ctx.cells` resolves a key — the pre-modes tool
  forwarded the target straight to `ctx.run_cell`, so a caller that passed a
  name keeps working; `requested_cell_ids` always carries the resolved ID(s)
  (never the name), with `cell_id` echoing the input and `resolved_cell_id`
  reporting the resolution. `mode="all"` queues every document
  cell in one code-mode context, so an unreferenced cell — and the widgets it
  defines — finally executes (the consumer's `unknown_variable` symptom);
  `mode="descendants"` adds the target's kernel-graph descendants and, on a
  fresh non-instantiated session whose graph is empty, refuses with
  `reason: graph_unpopulated` and runs **nothing** instead of silently
  degrading to one cell; `mode="all"` with a non-empty `cell_id` is refused
  (`reason: cell_id_not_allowed`), never accepted-and-ignored. The operation is
  three calls — a plan/read that validates every id or name before anything is
  queued (an unknown one aborts with `reason: unknown_cell_ids` and nothing
  runs), the single context that queues the batch (marimo schedules it), and a
  separate post-run report (marimo discards the run payload when a target
  raises, and the in-context snapshot is frozen) — so the response is per
  requested target: `cells[].runtime_state`, structured `errors` and
  `errors_readable` (`errors` is `null` when the channel could not be read —
  such a target is reported **not run/unverified**, never succeeded),
  `succeeded_cell_ids` (idle **with a readable, empty `errors`**),
  `failed_cell_ids` (`exception`/`marimo-error`/`cancelled`/`interrupted`; the
  requested targets **only**), `not_run_cell_ids` (everything else, incl.
  stale/disabled/unknown), `unverified_cell_ids`, `counts` and `status: ok`
  **only** when every requested target is idle (`partial` otherwise, `error` for
  validation/planning/reporting failures — `cell_id_required`, `invalid_mode`,
  `cell_id_not_allowed`, `unknown_cell_ids`, `graph_unpopulated`,
  `planning_failed`, `reporting_failed`), plus `execution_error` / `stderr`
  when the run call itself failed. Every failure keeps the pre-modes top-level
  `error` string beside the structured `status`/`reason`, so a caller written
  against the old `{"error": ...}` payload still sees it, and `error` is also
  set (with `execution_error`/`stderr`) when a run's batch call failed. The
  payload and the packaged resources state that the kernel may additionally run
  stale ancestors and autorun descendants outside the requested set and that
  independent ordering is unspecified. Pinned live by
  `tests/marimo_inspect/live/test_run_cell_modes.py` (a purpose-built notebook
  on the new hermetic `notebook_server` factory: all-mode on a fresh session +
  widget registration, the empty-graph refusal then resolution, per-cell
  exception/cancelled reporting under a failing run, abort-before-run
  validation, and running a target by **cell name** — cell and descendants —
  with an unknown name still refused) and non-live by the `run_cell` handler,
  template, server-schema and packaged-resource tests (incl. name resolution,
  the legacy `error` key on every failure path, and the unreadable-errors
  channel); the pre-fix failures of both tiers are preserved in
  `.hermes/probes/t15-prefix/`.
- **T17** ✅ *A nonexistent session can no longer be bound* —
  `set_active_session` now validates the exact ID against live
  `GET /api/sessions` data before changing MCP-session state or the scoped
  process-global fallback. An explicit `server_url` checks only that endpoint;
  without one, healthy registry servers are searched and exactly one matching
  endpoint is required. Zero matches, ambiguous matches, query failures, an
  empty ID, or a missing injectable binding context all fail closed with a
  machine-readable reason and `bound: false` / `state_changed: false`.
  Successful discovery also reports partial endpoint failures instead of
  hiding the validation limit. Covered by real HTTP stubs, subprocess
  stdio/HTTP isolation regressions, and a real marimo-kernel probe preserved in
  `.hermes/probes/t17-prefix/`; the packaged fallback reference teaches the
  validation-before-write rule.
- **T20** ✅ *A button click is reported truthfully* — `set_ui_value` no longer
  reads an unchanged `button`/`run_button` element value as "already held this
  value, nothing changed". The two types differ and the payloads say so: a
  `button`'s element value is its `on_click` return (often `None`), while a
  `run_button` has no `on_click` — a nonzero counter sets its value `True` and
  the runtime resets it to `False` after the dependent cells run — and both
  expose a **frontend** click counter
  (`_plugins/ui/_core/ui_element.py`: `_update` assigns `_value_frontend`
  *before* converting), so the template now reports
  `frontend_value_before`/`after` (JSON-safe) alongside the element value. The
  handler then classifies: submitting `0` is the initialization sentinel
  (`_impl/input.py::button._convert_value` returns the initial value without
  calling `on_click`) → `handler_invoked: false` + warning; a nonzero counter
  that moved to the submitted value → `handler_invoked: true` with
  `click_delivered: true`; a counter already at the submitted value →
  `handler_invoked: null` (unknown — the read-back cannot see a repeated click,
  even though the runtime does process it), never true or false. Every button
  payload carries `side_effects_verified: false` and actionable verification
  guidance, because the element read-back never verifies the handler's
  arbitrary side effects — including the `on_change` error path, which for a
  button also omits the generic `no_change`/"already held" framing. A `button`
  whose `on_click` raises is now caught: the handler's own stderr marker
  (`on_click handler for button`, a third `_UI_UPDATE_MARKERS` entry and its
  own `_rejection_site`) yields `status: error`, `reason: on_click_failed`,
  `handler_ran: true`, `handler_invoked: true`, `side_effects_verified: false`,
  and a message acknowledging partial side effects may already have been
  applied — with no re-send or "already held" instruction. That marker is
  attributed only to a `button` clicked with a nonzero counter (an adversarial
  review guard, `_attributable_on_click`): a `run_button`, any other element,
  or a `0` counter instead fails as a truthful generic `ui_update_failed` with
  no `handler_invoked` claim, so a literal marker or another element's handler
  can never be misread as this target's `on_click`. The corresponding
  `verified: true`-but-counter-unreadable branch reports
  `handler_invoked: unknown` because the counter is unavailable — it never
  interpolates `(None)` or blames the element read-back. The blanket
  "dependent re-runs are NOT awaited" claim was corrected in the tool, README,
  and all three packaged resources to "this call does not verify arbitrary
  downstream effects" with the autorun/lazy distinction stated. Pinned non-live
  by 20 `test_ui.py` cases (the frontend read-back, the four button branches,
  the `on_click` site, the attribution guard, the unreadable-counter branch and
  the `on_click` error payload), 4 resource-content cases, and a `set_ui_value`
  description case; live by 6 hermetic cases in
  `tests/marimo_inspect/live/test_ui.py` — a side-effect-only button click
  (counter 0 → 1, side effect confirmed by an independent read), the `0`
  sentinel (no click, no side effect), a repeated counter
  (`handler_invoked: null` while the runtime *did* run the handler again — the
  side effect grew), a raising `on_click` (partial side effect applied before
  the raise), and `run_button` click evidence — all fail pre-fix (pre-fix
  evidence in `.hermes/probes/t20-prefix/`).
- **T21** ✅ *Stale or restored output is explicitly labelled* — every
  `get_cell_outputs().cells[]` row now carries the kernel's `runtime_state`
  (the same value exposed by `get_cell_map`) and `output_stale`, true exactly
  when that state is `stale`. The previous rendering remains available, but
  the tool description, `next_steps`, and packaged co-work loop tell callers
  to run the cell before trusting it as current. The real-kernel regression
  proves run → edit-without-run → re-run transitions from `idle/false` to
  `stale/true` while preserving the prior rendering, then back to
  `idle/false` with the new rendering. The headless `/sse` harness does not
  restore created cells from marimo's on-disk session cache, so that narrower
  restart flavour was probed and recorded but not claimed as CI coverage.

## Measured state (2026-09-10, after T4 + T6 + T9 + T9-b + T10 + T11 + T12 + T13 + T3 + T14)

- `.venv/bin/python -m pytest -m "not live" -q` → **299 passed, 31 deselected**
  (+18 from `tests/marimo_inspect/test_list_args.py`). Invoked through the venv
  binary rather than `uv run`: the sandbox's `~/.cache/uv` is read-only, so
  `uv run` fails before pytest starts — see
  [harness-integration §Sandbox notes](harness-integration/README.md#sandbox-notes).
- `.venv/bin/python -m pytest tests/marimo_inspect/live/ -m live -q` → **31
  passed** (re-run for T14, which edits two live-exercised read handlers;
  includes the 7 widget regressions in `tests/marimo_inspect/live/test_ui.py`)
- `.venv/bin/python -m ruff check .` / `… ruff format --check .` → clean
- `.venv/bin/python -m marimo check notebooks` → exit 0

Counts drift as tests are added; the split (`-m "not live"` vs `-m live`), not
the exact numbers, is the contract.
