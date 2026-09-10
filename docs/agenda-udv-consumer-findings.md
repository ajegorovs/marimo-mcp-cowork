# Agenda (open issue): first-consumer integration findings (udv-echo-process)

> **Status:** **Open — 1 item left** (T3, which the DSH harness owns).
> Everything else from this integration is resolved — T4's version evidence,
> T6's sandbox gotchas, T9-b's browser pass (which surfaced T12/T13), T10's
> ownership decision, T11's error-channel split, and T12/T13 themselves (all
> closed 2026-09-10) — and the §Resolved log keeps a one-line record plus the
> evidence pointer for each, so a resolved item never needs re-litigating from
> this file.
> **Created:** 2026-09-07 · **Condensed to open items only:** 2026-09-10 ·
> **T4 + T6 + T9-b + T10 + T11 + T12 + T13 closed:** 2026-09-10.
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

### T3 — DSH harness delivers list-typed MCP arguments as JSON strings ❌

- Repro ✅ (consumer side): `get_variables(variable_names=[...])` and
  `get_cell_outputs(cell_ids=[...])` fail with pydantic `list_type` errors
  through the registered stdio server. **Owner: the harness's MCP bridge, not
  this repo** — `client.py` is a pure HTTP client and never sees these
  arguments.
- Works today: the empty filter (no argument) returns everything.
- ✅ **Hermes counter-evidence (2026-09-10):** the same two tools accept a real
  JSON array through the Hermes MCP gateway — `get_variables(variable_names=
  ["filtered", "data"])` and `get_cell_outputs(cell_ids=["Xref", "BYtC"])` both
  filtered correctly against a live marimo 0.24.0 session. List transport is
  therefore not a property of this server or of MCP itself; the mangling is
  specific to the DSH bridge. (No defensive type landed here — see the item's
  owner note above.)
- **Next action:** file against the DSH harness. Provider-side option, not
  scheduled: accept `str | list[str]` defensively on those two parameters
  (cheap). DoD: a harness-side repro, or the defensive type landing here with a
  unit test asserting a JSON-string list is accepted.

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
  *Residual:* a repeat of the value the element already holds whose handler then
  raises reads back unmoved and so still reports `value_not_applied`; the message
  is truthful ("did NOT move (5 -> 5)") and `kernel_message` carries the handler
  traceback, but the traceback frame (`_on_change` vs `_convert_value`) is the
  available refinement. Unobserved in normal use, unpinned.

## Measured state (2026-09-10, after T4 + T6 + T9 + T9-b + T10 + T11 + T12 + T13)

- `uv run pytest -m "not live" -q` → **281 passed, 31 deselected**
- `uv run pytest tests/marimo_inspect/live/ -m live -q` → **31 passed**
  (includes 7 widget regressions: `tests/marimo_inspect/live/test_ui.py`)
- `uv run ruff check .` / `uv run ruff format --check .` → clean
- `uv run marimo check notebooks` → exit 0

Counts drift as tests are added; the split (`-m "not live"` vs `-m live`), not
the exact numbers, is the contract.
