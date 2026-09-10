# Agenda (resolved): first-consumer integration findings (udv-echo-process)

> **Status:** **Closed — no open items.** T3, the last one, was resolved by
> review on 2026-09-10: the DSH list-argument mangling it reported does not
> reproduce, and the defensive types it recorded as "no defensive type landed
> here" had in fact landed in `8a44b8c` (tag v0.2.0) — the same day T3 was
> filed. That review did surface one real provider-side defect, recorded and
> fixed as T14. T3 was the only item the harness ever owned, so nothing here is
> waiting on an upstream fix.
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
  *Open work:* carried in `docs/agenda-bug-hunt-1.md` §Checkpoint — a prepared,
  unstarted task exists
  (`.hermes/plans/2026-09-11_000750-t13-residual-ui-rejection-site.md`).

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
