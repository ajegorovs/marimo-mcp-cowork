# Bug hunt 2 — MCP surface discovery (FastMCP stdio harness)

> Discovery record for the second agentic bug hunt of the `marimo-inspect` MCP
> surface. Findings here are **observations with runnable repros**, not fixes:
> nothing in this hunt was repaired. Read `docs/bug-hunt-protocol.md` for the
> evidence/closure rules this file follows, and treat the per-finding `repro`
> path (a real payload producer) as the unit that converts into a regression.
>
> Hunt run: 2026-09-13. Discovery is **complete** within the surfaces listed
> under "Deferred / uncovered". **F1–F6 are remediated** (Task 11 — see
> "Remediation" below): each accepted finding now has a fail-before/pass-after
> regression and the public claims were swept in the same change. The
> independent fresh zero-context validator required by
> `docs/bug-hunt-protocol.md` has passed, so the findings are independently
> closed (see "Closure status").

## Lab

- **Provider under test:** this checkout (`~/Repos/marimo-inspect`), working tree
  with the uncommitted Wave 1 + Wave 2 changes in place.
- **Live session:** one disposable copy of a purpose-built consumer notebook at
  `/tmp/marimo-inspect-task-02/consumer_hunt_notebook.py`, served by a headless
  `marimo edit --no-token` server on `http://127.0.0.1:54819`.
- **Session id:** `7e5b0b35-6719-4204-8d70-5de2123e9653` (ephemeral lab state,
  not a credential; the server was provisioned and instantiated by the parent
  before this hunt, and this hunt never started, stopped, restarted or killed it).
- **Instantiated:** yes — `/api/kernel/instantiate` had already run, so cells
  carried real outputs, variables, one structured error, and widget values.
- **Notebook shape at start (6 cells):** `Hbol` data setup, `MJUe` `mo.ui.dropdown`
  (`low|medium|high`, value `medium`, `allow_select_none=False`), `vblA`
  `mo.ui.slider(1, 5, value=2, show_value=True)`, `bkHC` text readout,
  `lEQa` `mo.ui.button`, `PKri` an intentional `raise ValueError("hunt_structured_error")`.
- **Disposable:** the hunt created/edited/deleted cells freely. Final lab state
  is 6 original cells + the mission readout cell (`WTHz`), no temp cells left
  behind; the notebook file is byte-identical to its post-mission state
  (`sha256 1abd4cbff8f8a6cb…`, 1395 bytes) — see `raw/50_final_state.json`.
- **Not a defect by definition:** the notebook's own intentional
  `ValueError` cell, an import that needs data, and a lint issue in the lab
  notebook are lab conditions, not server defects. The one intentional error was
  used as a *fixture* to exercise the error channels.

## Harness / MCP configuration

- **Transport:** FastMCP's own Python `Client` over **stdio**, config-dict form
  (a bare command string carrying args raises `could not infer transport`):

  ```python
  from fastmcp import Client
  CONFIG = {"mcpServers": {"marimo-inspect": {
      "command": "~/Repos/marimo-inspect/.venv/bin/marimo-inspect",
      "args": ["--transport", "stdio"]}}}
  async with Client(CONFIG) as client:
      r = await client.call_tool("<tool>", {<literal args>})
  ```

- **Boundary actually held:** the notebook was driven **only** through these
  stdio clients. The injected `mcp__marimo_inspect__*` gateway tools were not
  used; no `marimo_inspection` module was imported and no handler was invoked;
  the marimo HTTP API was not called by this hunt.
- **One persistent client per run script** (never a fresh client per tool call):
  `00`, `05`, `10`, `20`, `30`, `35`, `40`, `45`, `50` each open exactly one
  `Client` for the whole script. `40_concurrency_and_error_channels.py`
  deliberately opens a **second** independent client — two MCP server child
  processes over the same lab session — to exercise the cross-client staleness
  guard; the lab server process itself was never touched. Because the
  process-global binding fallback is per server process (documented), every
  binding-sensitive probe is self-contained inside one script.
- **Explicit ids:** every tool call sent both `server_url` and `session_id`
  except (a) the deliberate argument-less probes and (b) the explicit-empty
  probes, which are the objects under test.
- **Volume / coverage:** 194 logged tool calls across the seven run scripts plus
  6 fresh repro runs; **14 of the 15 advertised tools** exercised
  (`restart_kernel` deliberately not — see "Deferred / uncovered"). Raw payloads
  with literal args, order and client pid are in `raw/` (`*_calls_timeline.json`
  per script plus one JSON per call).
- **One supplementary static cross-check (not a second way to drive the
  notebook):** for the lint family, a byte snapshot of the notebook file was
  copied and `marimo check` was run on the copy, because `lint_notebook` is
  documented as the in-process static gate behind `marimo check`. The session
  itself was never driven by the CLI.

## Mission

Real work, not "find bugs": **add one small pipeline-oriented readout to the live
disposable notebook reporting the selected category, gain, count, mean, minimum
and maximum of the scaled data, then verify it through MCP reads.**

Executed with `create_cell` (name `scaled_readout`, placed after `bkHC`,
`hide_code=False`), then `run_cell`, then verified through four read families:

- `get_cell_data(include_errors=True)` → `code` matched the source sent
  byte-for-byte, `runtime_state: "idle"`, `structured_errors: []`
  (`raw/10_007_s2_data_readout.json`).
- `get_cell_outputs` → rendered markdown
  `Scaled pipeline readout: {'category': 'medium', 'gain': 2, 'count': 4, 'mean': 5.75, 'min': 2.0, 'max': 10.0}`
  with `output_stale: false` (`raw/10_008_*`).
- `get_variables(["scaled_summary"])` → the six-key dict with `count: 4`,
  `mean: 5.75`, `min: 2.0`, `max: 10.0` (`raw/10_009_*`).
- `get_errors` → the new cell absent from the error census (`raw/10_010_*`).

Then driven reactively: `set_ui_value(gain, 4)` → readout re-ran to
`gain: 4, mean: 11.5, min: 4.0, max: 20.0`, and `set_ui_value(category, ["high"])`
→ `category: 'high'`. Mission acceptance is **met**; the artifact at the end of
the hunt is `raw/50_final_state.json` (cell `WTHz`, `idle`, six fields correct).

## Raw evidence location

Everything raw lives in the gitignored lab directory
`~/Repos/marimo-inspect/.hermes/hunts/2026-09-task-02/` (not committed):

- `raw/<script>_<seq>_<name>.json` — one file per tool call: literal args, raw
  returned MCP content (text and structured), client pid, sequence, timestamp.
- `raw/<script>_calls_timeline.json` — ordered call log per script.
- `raw/<script>_results.json` — a machine-readable check table where that run
  produced one; the raw per-call payload and timeline are the authoritative
  evidence for every script.
- `raw/*_stdout.txt` — full stdout of each run (server log lines included).
- `raw/snapshots/` + `raw/30_snapshots.json`, `raw/35_snapshots.json` — notebook
  byte snapshots and `marimo check` output for the lint family.
- `raw/50_final_state.json` — final lab state, notebook hash, git integrity.
- Runnable scripts in the same directory: `00_surface_survey.py`,
  `05_shape_probe.py`, `10_hunt_main.py`, `20_staleness_lint_autorun.py`,
  `30_lint_and_sentinels.py`, `35_lint_empty_cells.py`,
  `40_concurrency_and_error_channels.py`, `45_lint_id_semantics.py`,
  `50_final_state.py`, and one `repro_F*.py` per confirmed finding.

## Confirmed findings

### F1 — `delete_cell` on an absent id returns a raw execution traceback instead of the documented structured refusal

- **id:** F1
- **tool:** `delete_cell`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653", "cell_id": "ZZZZ_NOPE"}`
- **observed:** (verbatim)
  `{"error":"Execution failed","stderr":"Traceback (most recent call last):\n  File \"/tmp/marimo_820255/__marimo__cell___scratch___.py\", line 25, in <module>\n    print(await _run())\n … File \"…/marimo/_code_mode/_context.py\", line 1255, in delete_cell\n    cell_id = self._resolve_target(target)\n … KeyError: \"Cell 'ZZZZ_NOPE' not found in notebook or pending adds\"\n"}`
  (`raw/10_051_s5_delete_absent.json`, re-reproduced in
  `raw/repro_F1_delete_cell_absent.stdout.txt`)
- **expected:** a structured refusal — the sibling `edit_cell` with the same
  absent id answers
  `{"status":"error","cell_id":"ZZZZ_NOPE","message":"Cell ZZZZ_NOPE not found in session …"}`
  (`raw/10_050_*`). The packaged resources promise every cell/session-targeting
  tool (a list that includes `delete_cell`) turns a target problem into
  "`status: error` with `target_resolved: false`, `operation_ran: false`,
  `state_changed: false`, and **no read or write operation runs on a refusal**",
  i.e. "a structured refusal instead of a raw exception".
- **why_wrong:** the payload has no `status`, no `reason` and no target flags, so
  a caller branching on the documented refusal vocabulary cannot classify it; the
  actionable message is buried in a Python traceback whose file paths are internal
  scratchpad artifacts. Two write tools answer the identical class of input in two
  incompatible shapes.
- **repro:** `~/Repos/marimo-inspect/.hermes/hunts/2026-09-task-02/repro_F1_delete_cell_absent.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced)
- **severity:** misleads
- **status:** confirmed
- **doc_claim_ref:** `src/marimo_inspection/resources/fallbacks-and-limits.md:238`
  (§ "Target errors are payloads, not tool exceptions"); the same
  delete-cell-refusal expectation appears in the packaged
  `workflow://marimo-inspect/fallbacks-and-limits`.

### F2 — MCP schema rejects an out-of-enum `run_cell(mode=…)` before the handler, leaving the documented `reason: invalid_mode` vocabulary unreachable

- **id:** F2
- **tool:** `run_cell`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653", "mode": "bogus"}`
- **observed:** (verbatim)
  `ToolError: 1 validation error for call[run_cell]\nmode\n  Input should be 'cell', 'descendants' or 'all' [type=literal_error, input_value='bogus', input_type=str]`
  — raised as a client-side exception, no payload at all
  (`raw/20_023_d1_mode_bogus.json`, `raw/repro_F2_run_cell_invalid_mode.stdout.txt`).
  Contrast the reachable validations, which do answer structurally:
  `mode="all"` + `cell_id` → `{"status":"error","reason":"cell_id_not_allowed", …}`
  (`raw/10_061_*`) and `mode="cell"` with an empty `cell_id` →
  `{"status":"error","reason":"cell_id_required", …}` (`raw/20_024_*`).
- **expected:** the tool description and `workflow://marimo-inspect/co-work-loop`
  §4 list `invalid_mode` among the reasons that produce "`status` … `error` for a
  validation failure", and promise "Every failure carries a top-level `error`
  string **and** the structured `status`/`reason` fields".
- **why_wrong:** this is a **documentation/dead-vocabulary contradiction**, not a
  claim that FastMCP's schema validation is itself wrong. The generated input
  schema declares `mode` as a `literal`, so the framework rejects the value
  before the handler runs and the caller gets neither `status` nor `reason` nor
  the promised top-level `error`. A caller written against the documented
  contract has no structured branch for this input, and the listed branch is
  dead on the public MCP surface.
- **repro:** `…/repro_F2_run_cell_invalid_mode.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced)
- **severity:** misleads
- **status:** confirmed
- **doc_claim_ref:** `src/marimo_inspection/tools/mutation.py:408,434`
  (the `invalid_mode` reason), `src/marimo_inspection/server.py:123` and
  `src/marimo_inspection/resources/co-work-loop.md:233` (the documented reason
  list), plus the `run_cell` tool description. **Parent source check needed:**
  whether `invalid_mode` was meant to be reachable via a non-literal field; this
  hunt only observed that the enum rejects the value first (the handler literal
  was located by grep, its flow was not read).

### F3 — `get_variables(variable_names="")` returns an empty set silently, contradicting "empty means all variables"

- **id:** F3
- **tool:** `get_variables`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653", "variable_names": ""}`
- **observed:** (verbatim)
  `{"session_id":"7e5b0b35-…","tables":{},"variables":{},"next_steps":["Review table columns and row counts for DataFrames","Check variable values for scalar types","Use variable names in subsequent code execution"]}`
  — zero variables, and **no** `missing_variable_names`, no `reason`, no warning.
  The same session, same instant, with the argument omitted → 8 variables; with
  `variable_names="[]"` → the same 8 variables
  (`raw/10_*s4_varnames_*`, `raw/repro_F3_get_variables_empty_string.stdout.txt`).
- **expected:** "If variable_names is empty, returns all variables" — and the
  input schema advertises the parameter as "Specific variables to inspect.
  Empty = all. Accepts a single name, a native array, or a JSON-encoded array".
- **why_wrong:** the empty string is this surface's own "not provided" convention
  (`session_id`/`server_url` default to `""`, and the sibling list-argument tool
  `get_cell_data(cell_ids="")` at least reports `missing_cell_ids: [""]` — it is
  not silent). Here `""` is normalized as a *literal variable name* whose lookup
  legitimately finds nothing, so the payload is indistinguishable from "this
  notebook has no variables": a plausible-but-wrong success with no marker. An
  agent that follows the schema wording and sends `""` goes blind about the whole
  session. `"[]"` and `[]` already take the "all" path, so the failure is
  specifically the empty-string form.
- **repro:** `…/repro_F3_get_variables_empty_string.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced)
- **severity:** misleads
- **status:** confirmed
- **doc_claim_ref:** `src/marimo_inspection/tools/variables.py:27` (tool
  description) + the same sentence in the exposed `get_variables` description.

### F4 — `create_cell` with a target/argument problem returns a raw execution traceback instead of the documented structured refusal

- **id:** F4
- **tool:** `create_cell`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653", "source": "repro_f4_marker = 1", "name": "repro_f4", "after": "ZZZZ_NOPE"}`
- **observed:** the fresh repro's verbatim payload starts
  `{"error":"Execution failed"}`; its full recorded `stderr` is the raw
  `Traceback … KeyError: "Cell 'ZZZZ_NOPE' not found in notebook or pending adds"`
  shown by `repro_F4_create_cell_absent_anchor.stdout.txt`. It carries no
  `status` or `reason`, and the cell count is unchanged (7 → 7), i.e. the
  refusal itself is correct but its shape is not. The original-hunt call has the
  same raw envelope at `raw/30_011_p4_create_absent_anchor.json`.
  Two more argument/target failures of the same tool have the same shape:
  both anchors supplied →
  `RuntimeError: Cannot specify both 'before' and 'after'` inside the same
  `{"error":"Execution failed","stderr":"Traceback…"}` envelope
  (`raw/30_009_*`); and a source marimo refuses to compile (a duplicate
  definition) → `RuntimeError: Multiply-defined names: - 'data_points' is already
  defined in cell 'Hbol' (_)` in the same envelope (`raw/20_012_*`).
- **expected:** the packaged resources state that every cell/session-targeting
  tool (the list includes `create_cell`) resolves its target through **one shared
  step**, so "a target problem comes back as a structured refusal instead of a raw
  exception", with `status: error` + target flags and nothing run.
- **why_wrong:** an absent `after` anchor is exactly a target problem, yet the
  caller gets a raw marimo traceback with no structured target flags, so the
  uniform refusal contract does not hold for this tool. Note the contrast that
  makes this actionable: the same tool refuses a genuinely empty source cleanly
  and structurally — `{"error":"source must not be empty","status":"error"}`
  (`raw/30_006_*`) — so the structured shape exists and is simply not applied to
  the anchor/validation failures.
- **repro:** `…/repro_F4_create_cell_absent_anchor.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced)
- **severity:** misleads
- **status:** confirmed
- **doc_claim_ref:** `src/marimo_inspection/resources/fallbacks-and-limits.md:238`
  (the "every … `create_cell` … target problem" list and the "structured refusal
  instead of a raw exception" promise). **Parent source check needed:** whether
  the shared target-resolution step is entered before or after marimo raises for
  each of these three argument classes.

### F5 — `lint_notebook` labels a file-positional diagnostic index as `cell_id`, a naming/documentation gap

- **id:** F5
- **tool:** `lint_notebook`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653"}` (after creating a comment-only cell with `create_cell`, `{"source": "# mid-notebook comment probe", "name": "hunt_lint_mid", "before": "bkHC"}`)
- **observed:** (verbatim)
  `"diagnostics":[{"rule":"MF004","name":"empty-cells","severity":"formatting","message":"Empty cell can be removed (contains only whitespace, comments, or pass)","cell_id":"3","line":31,"column":0,"filename":"/tmp/marimo-inspect-task-02/consumer_hunt_notebook.py"}]`
  — the flagged cell's **live** id is `GxkU` and its live file position is index
  3 (order `Hbol, MJUe, vblA, GxkU, bkHC, WTHz, lEQa, PKri`). Feeding the
  reported id back: `get_cell_data(["3"])` →
  `{"data":[], …, "missing_cell_ids":["3"]}` and `edit_cell("3")` →
  `{"status":"error","cell_id":"3","message":"Cell 3 not found in session …"}`
  (`raw/45_*`, `raw/repro_F5_lint_diagnostic_cell_id.stdout.txt`).
- **expected:** no explicit public diagnostic-schema claim currently defines this
  field. The public naming convention otherwise uses `cell_id` for the live IDs
  returned by `get_cell_map`; therefore a positional lint index needs an explicit
  name/schema (`cell_index`, for example) or a documented translation to a live
  ID.
- **why_wrong:** this is a **naming/documentation gap**, not a claim that marimo's
  positional diagnostic value is wrong. Under the current `cell_id` label the
  value is joinable to nothing an agent can act on: piping it into
  `get_cell_data`/`edit_cell` — the natural next step after "fix the issues lint
  found" — gets a not-found refusal. The only usable locator is `line`, over a
  filename the agent was not otherwise given.
- **repro:** `…/repro_F5_lint_diagnostic_cell_id.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced;
  creates and then deletes its throwaway cell)
- **severity:** cosmetic
- **status:** confirmed
- **doc_claim_ref:** none explicit — the `lint_notebook` description does not
  define the diagnostic schema; the mismatch is against the surface-wide
  "cell ids come from `get_cell_map`" convention used by every other tool
  description. **Parent source check needed:** whether the diagnostic `cell_id`
  is intended as a positional index (then rename it and say so) or should be
  translated into the live id.

### F6 — `set_ui_value` accepts an empty list for a dropdown and applies `None`, contradicting the documented one-element-list shape and the element's own `allow_select_none=False`

- **id:** F6
- **tool:** `set_ui_value`
- **exact_args:** `{"server_url": "http://127.0.0.1:54819", "session_id": "7e5b0b35-6719-4204-8d70-5de2123e9653", "variable_name": "category", "value": []}`
- **observed:** (verbatim)
  `{"status":"ok","variable_name":"category","element_type":"dropdown","accepted_shape":"list[str]","verified":true,"applied":true,"value_before":"medium","value_after":null, …}`
  — the dropdown's value became `null`; the `get_variables` read-back is
  `{"variables":{"category":{"value":{"value":null,"datatype":"str"}, …}}}`.
  No `value_shape_mismatch`, no `did_you_mean`
  (`raw/10_078_s9_dropdown_empty_list.json`,
  `raw/repro_F6_dropdown_empty_list.stdout.txt`).
- **expected:** the documented per-widget shape table gives `dropdown` as
  "**one-element list** of the option key" (example `["beta"]`), the description
  says "Value shape is per widget and is NEVER coerced … a shape mismatch is
  refused before anything is applied", and this element's own rendered
  declaration carries `data-allow-select-none='false'`
  (`raw/10_008_*` / `raw/05_*`), i.e. `None` is not a value it advertises.
- **why_wrong:** a zero-element list is not the documented one-element shape, yet
  it passes the shape guard and is applied, leaving a widget that its own
  declaration says cannot be none holding `None` — reported as verified success
  with no warning. The neighbours behave as documented and make the gap
  actionable: `["low","high"]` is refused `value_not_applied` with the kernel
  message `AssertionError: Dropdowns only support a single value`, and an unknown
  key is refused `value_not_applied` with the option list. **Note:** the
  read-back itself is truthful (`applied: true`, `value_after: null`), so the
  defect is the missing refusal, not a lying field. **Parent source check
  needed:** whether the guard should require a non-empty list for `dropdown`, or
  whether `[]` is intentionally the "clear" shape and the doc/declaration is what
  is wrong.
- **repro:** `…/repro_F6_dropdown_empty_list.py`
  → `~/Repos/marimo-inspect/.venv/bin/python <path>` (exit 0 = reproduced;
  restores the widget to `["medium"]` at the end)
- **severity:** misleads
- **status:** confirmed
- **doc_claim_ref:** `src/marimo_inspection/resources/co-work-loop.md:251`
  (the dropdown row of the shape table), `src/marimo_inspection/resources/live-safety.md:148`,
  `src/marimo_inspection/tools/ui.py:769-770` (the exposed `set_ui_value`
  description: "NEVER coerced" / "one-element list").

## Unconfirmed findings

No repro can be shipped for an unconfirmed item by definition; each entry names
the one probe that would settle it.

- **U1 — `get_cell_map.has_output: true` for a cell whose only output record is
  empty.** `Hbol` (no display expression) reports `"has_output":true,
  "has_console_output":false, "has_errors":false`, while `get_cell_outputs` for it
  returns `visual_output: {"mimetype":"text/plain","data":""}`
  (`raw/05_02_bind_census.json`, `raw/05_07_outputs_all.json`). The flag may
  truthfully mean "an output record exists"; it reads as "this cell rendered
  something". Settle with: a cell that provably produces no output at all
  compared against one that prints an empty string, on a source-verified
  definition of `has_output`.
- **U2 — the source on disk can differ from the `code` a read returns (formatting
  normalization).** `create_cell(source='mo.md(\n    """\n    lint probe split\n    """\n)')`
  reads back that exact text from `get_cell_data`, while the serialized file
  stores `mo.md("""\n    lint probe split\n    """)` (`raw/30_snapshots.json`,
  `raw/snapshots/after_split_cell.py`). `lint_notebook` judges the **file**, so a
  lint verdict can be about text the kernel does not hold. Formatting-only in
  every observation here; the drift class is what needs a verdict. Settle with:
  a source whose normalization is *semantically* significant, or a source-verified
  statement of which artifact lint reads.
- **U3 — `get_cell_data(cell_ids="")` treats the empty string as a literal id
  rather than "all".** Observed `{"data":[], "missing_cell_ids":[""]}`
  (`raw/10_*s4_cellids_empty_string*`) while `cell_ids=[]` and `"[]"` return every
  cell, against the description "If cell_ids is empty, returns data for all
  cells". Not silent — `missing_cell_ids` names `""` — so this is a doc/argument
  convention mismatch, and it is the sharper contrast for F3. Settle with: the
  intended reading of "empty" in `tools/args.py` normalization.
- **U4 — an `int` slider silently rounds a float and still reports success.**
  `set_ui_value(gain, 2.5)` on `mo.ui.slider(1, 5, value=2, …)` →
  `{"status":"ok","verified":true,"applied":true,"value_before":3,"value_after":2}`
  (`raw/10_086_s9_slider_float_ok.json`) — the payload discloses `2`, but the
  documented slider shape is `int | float` and the description says the value is
  "NEVER coerced". Settle with: whether a float on a step-1 slider should be
  refused (like a list) or rounded, and whether `applied: true` should carry a
  coercion notice.
- **U5 — the `unknown_variable` payload drops the requested name.**
  `set_ui_value("no_such_widget", 1)` →
  `{"status":"error","reason":"unknown_variable","message":"Variable \"no_such_widget\" is not a live kernel global. Current UI element globals: […]"}`
  with `variable_name: null` (`raw/10_087_s9_unknown_variable.json`). The message
  names it, so this is cosmetic field hygiene: the echoed-name field is null in
  exactly the case where a caller most needs it.

## Checked and clean claims

Each line is a documented promise re-derived from raw payloads in this run; the
call file is the evidence. Claims the lab could not exercise are in the next
section instead.

**Target resolution / binding**

- No binding + no explicit ids → `{"status":"error","reason":"session_required",
  "target_resolved":false,"operation_ran":false,"state_changed":false,
  "available_sessions":[],"available_sessions_readable":false}`; the refusal
  reports no read/write. (`raw/10_001_*`)
- Over stdio, after `list_active_notebooks(server_url=…)`, an **argument-less**
  `get_cell_map` resolved the bound session — the documented stdio-only
  fallback promise holds for fastmcp's own `Client`. (`raw/10_003_*`)
- Stale-but-well-formed session id → `reason: session_not_found` with
  `available_sessions` = that server's real census (`[{server_url, session_id}]`)
  and `available_sessions_readable: true`. (`raw/10_068_*`)
- Unreachable server → `reason: server_unreachable`; reachable non-marimo root
  (`…/api/version`, `…/foo`) → `reason: server_query_failed` with the 404 and
  `available_sessions_readable: false`. (`raw/10_069..071_*`)
- An explicit unreachable `server_url` is honoured even with `session_id: ""`
  (no silent fall-back to the bound session), and likewise with an explicit bogus
  session id. (`raw/20_031_*`, `raw/20_032_*`)
- `set_active_session`: stale id → `session_not_found` + `bound:false` +
  `state_changed:false`; empty id → `invalid_session_id` + `bound:false`; live id
  → `status:"OK"`, `validated:true`, `server_url_source:"explicit"`.
  (`raw/10_073..075_*`)
- Argument-less discovery with no `server_url` found 0 servers (two dead registry
  entries skipped, no auto-bind of a foreign session) and **did not** destroy the
  earlier bind. (`raw/30_016_*`, `raw/30_017_*`)
- An unreachable explicit server yields exactly one sentinel row whose keys are
  exactly `name/path/session_id/server_url/error`, with `session_count: 0`,
  `result_row_count: 1`, `servers_discovered: 1`. (`raw/30_015_*`)

**Composite reads and error channels**

- `get_cell_data` default row keys are exactly
  `cell_id/code/runtime_state/variables` (the four error keys are absent) and
  `variables` is `null`; `include_errors=True` adds
  `structured_errors/console_stderr/has_console_exception/console_exception_evidence`
  to **every** row — clean → `[] / [] / false / null`, the intentional error cell
  → populated structured error + console traceback with
  `console_exception_evidence: "traceback"`. (`raw/10_018_*`, `raw/10_019_*`)
- An id that resolves to nothing is reported (`missing_cell_ids`) by
  `get_cell_data` and `get_cell_outputs`, including in a mixed request, rather
  than silently omitted. (`raw/10_018..021_*`)
- `get_errors` keeps the channels separate and counts structured errors at the
  top level: `total_errors == total_structured_errors == 1` for the one
  intentional error while `total_console_exception_cells` counts console-only
  cells separately. (`raw/10_022_*`, `raw/40_018_*`)
- The documented cell-private-name failure class behaves as documented: a cell
  referencing another cell's leading-underscore variable ends
  `runtime_state: "exception"` with `structured_errors: []` (`errors_readable:
  true`) and the `NameError` traceback on **both** the run payload's
  `execution_error`/`stderr` and the cell's `console_stderr`; `get_errors` counts
  no structured error for it. (`raw/40_017..019_*`)
- `get_dependency_graph` inventories every live cell and refuses the
  not-implemented arguments rather than ignoring them:
  `cell_id`/`depth:1` → `{"status":"error","reason":"unsupported_argument",…}`,
  `depth:0` accepted. (`raw/10_065..067_*`)

**List arguments**

- `cell_ids`: native array, single string, JSON-encoded array, `[]`, `"[]"`,
  whitespace-padded JSON all normalize correctly; malformed JSON (`"[Hbol"`) and a
  numeric-looking string (`"5"`) degrade to a literal id and are *reported* in
  `missing_cell_ids`, not silently dropped. (`raw/10_029..038_*`)
- `variable_names`: native array, JSON-encoded array, `[]`, `"[]"` and
  whitespace-padded JSON all normalize to the same lookup. (`raw/10_039..040_*`)

**Cell identity / staleness guard (the whole guard family)**

- First touch of a never-read cell → `needs_read`;
  `get_cell_map` preview (even `preview_lines=10`) does **not** record a baseline
  → still `needs_read`; `get_cell_data` then edit → `ok` with a `code_hash` that
  the following `get_cell_map` agrees with. (`raw/20_004..010_*`)
- A successful `edit_cell` refreshes its **own** baseline (a second guarded edit
  with no re-read succeeds). (`raw/20_009_*`)
- **Hunt #1's F1 class is absent from this tree:** an unrelated
  `create_cell` + `edit_cell` in the same session did **not** bless a never-read
  original cell (still `needs_read`), and a cell that a *different* client never
  read is still `needs_read` for that client. (`raw/20_004_*`, `raw/40_011_*`)
- Cross-client staleness: client A wrote a cell that client B had read → B's
  guarded edit returned `conflict`; after B re-read, its retry returned `ok`.
  (`raw/40_008..010_*`)
- Absent/deleted ids: `edit_cell` and `get_cell_data` refuse/report them
  (`raw/10_050_*`, `raw/10_053_*`, `raw/10_054_*`); `delete_cell` refuses too, but
  with the shape filed as F1.

**Widgets and reactivity**

- Shape refusal in both directions with the corrected payload:
  scalar → dropdown → `value_shape_mismatch`, `accepted_shape: "list[str]"`,
  `did_you_mean: ["low"]`; list → slider → `value_shape_mismatch`,
  `accepted_shape: "int | float"`, `did_you_mean: 3`; nothing applied in either
  case (`value_before`/`value_after` both `null`). (`raw/10_076_*`, `raw/10_082_*`)
- Unknown option key → `value_not_applied` with the kernel's own message and an
  unmoved value; unchanged value → `ok`, `applied:false`, `no_change:true`;
  a genuine move → `ok`, `applied:true`, `value_before`/`value_after` consistent.
  (`raw/10_079..085_*`)
- Buttons use the documented tri-state and never claim a click that cannot be
  seen: counter `0` → `handler_invoked:false` + a warning; `1` → `true` with
  `click_delivered:true`; the same nonzero counter again → `null` + a warning
  saying the read-back cannot tell; `2` → `true`; `side_effects_verified:false`
  throughout. (`raw/10_088..091_*`)
- Non-widget globals are classified, not coerced: `mo`/`data_points`/
  `scaled_summary` → `reason: "not_a_ui_element"` with `datatype` and the list of
  reachable UI globals. (`raw/30_012..014_*`)
- A leading-underscore widget is unreachable by design:
  `reason: "unknown_variable"`, and a filtered `get_variables` for that name
  reports an empty result. (`raw/20_028_*`, `raw/20_029_*`)
- Reactivity is real here: `set_ui_value(gain, 5)` re-ran the dependent readout
  with **no** `run_cell` — its output changed to
  `{'gain': 5, 'count': 4, 'mean': 14.375, 'min': 5.0, 'max': 25.0}` with
  `runtime_state: "idle"`. (`raw/20_019..021_*`)

**Execution**

- `run_cell(mode="all")` re-ran the whole document in one pass:
  `status: "partial"`, `counts: {requested: 7, succeeded: 6, failed: 1}`, the
  intentional `ValueError` cell in `failed_cell_ids`, the mission readout
  `succeeded`. (`raw/10_062_*`)
- `run_cell` resolves a **cell name** as a target (`cell_id: "scaled_readout"` →
  `resolved_cell_id: "WTHz"`), refuses an unknown target with
  `reason: "unknown_cell_ids"` and runs nothing, and refuses
  `mode="all"` + `cell_id` with `reason: "cell_id_not_allowed"`.
  (`raw/20_025_*`, `raw/10_063_*`, `raw/10_061_*`)
- A failing target reports through the run payload (`status:"partial"`,
  `failed_cell_ids:[…]`, `error`/`execution_error`/`stderr`) rather than as a
  plain success. (`raw/40_017_*`)

**Lint**

- `lint_notebook` is not blind and agrees with `marimo check` on the same on-disk
  state, in both directions: a comment-only cell created through `create_cell`
  produced `total_issues: 1`, `formatting_issues: 1`, diagnostic
  `{"rule":"MF004","name":"empty-cells",…}` — matching
  `marimo check` on the byte snapshot (`warning[empty-cells]`) — and after
  deleting the cell both returned to zero with the file back at its baseline
  hash. (`raw/35_*`, `raw/30_snapshots.json`)
- `create_cell` refuses an empty source structurally
  (`{"error":"source must not be empty","status":"error"}`). (`raw/30_006_*`)

## Deferred / uncovered surfaces

Marked UNCOVERED — **not** "clean". Each names why the lab could not exercise it.

- **UNCOVERED — `restart_kernel`** (the 15th tool; never invoked). It discards
  all kernel state and clears the change tracker, so calling it would have
  destroyed the mission notebook's executed state and the read baselines this
  hunt depends on, and the parent forbade managing the lab server. The documented
  restart contract — skew-token scrape, `edit_scope_required` / `auth_required` /
  `session_census_denied` classification, `session_not_rematerialized` /
  `server_sessionless`, `cell_ids_stable: false`, `session_verification:
  "point_in_time"` — is therefore **entirely unexercised**, and the
  `execution_state_reset` / `widget_values_reset` / `change_tracking_cleared`
  fields with it.
- **UNCOVERED — `reason: graph_unpopulated`.** The lab was already instantiated,
  so its kernel graph was populated; `mode="descendants"` on a registered target
  returned `ok` (documented for the populated case). A never-instantiated session
  is needed to observe the refusal.
- **UNCOVERED — access denials `edit_scope_required` / `auth_required` /
  `session_census_denied`.** These need a `marimo run`-mode server or a
  token-authenticated server, i.e. a process this hunt may not start. Not
  observed; the claim that a run-mode census is indistinguishable from an auth
  gate at the denied endpoint is untested here.
- **UNCOVERED — `reason: binding_ambiguous`.** Documented as HTTP/SSE-only (a
  second client session on one process); this lab is stdio with one client per
  server process.
- **UNCOVERED — `server_query_failed`'s unreadable-body subclass** (truncated
  JSON, invalid UTF-8, non-object body). A 404 non-auth response was produced;
  an unreadable body needs a controllable HTTP responder, i.e. another process.
- **UNCOVERED — `planning_failed` / `reporting_failed`** (no way to break the
  planner/reporter from the client side).
- **UNCOVERED — the `multiple-definitions` lint rule via MCP writes.** Attempting
  to create a duplicate-definition cell is refused by marimo's dry-run compile
  before lint is ever involved (`raw/20_012_*`), so a "broken" state that lint
  would flag as `critical` cannot be produced through the write tools. (Lint
  itself was exercised with the reachable `empty-cells` rule.)
- **UNCOVERED — `markdown-indentation` as a write-tool-created state**: the
  serializer normalizes the source (see U2), so the split form never reaches the
  file.
- **UNCOVERED — frontend rendering / browser behaviour.** No browser was
  attached; every widget claim here is kernel-side read-back only. Whether a
  rejected conversion or a click is *rendered* is not covered.
- **UNCOVERED — HTTP/SSE transport entirely.** All binding, refusal and target
  semantics here are stdio; the per-transport half of the binding claims is
  untested.
- **Not attempted by design:** no fixes, no source edits, no test edits, no git
  state changes.

## Regression-test plan

Rules: every accepted finding becomes a test that **fails against the pre-fix
code and passes after**, using the captured real payload (never a hand-written
plausible one). In this shared working tree, prove the pre-fix failure by pinning
`PYTHONPATH` to a copy of the pre-fix package in `/tmp` — never by stashing or
checking out.

| Finding | Test to add (hermetic unit first, live second) | Pre-fix failure to record |
| --- | --- | --- |
| F1 | `tests/marimo_inspect/…` handler-level test asserting `delete_cell` on an absent id returns `status: "error"` + a reason and **no** raw `stderr` traceback (mirror the existing `edit_cell` absent-id case) | raw `{"error":"Execution failed","stderr":"…Traceback…"}` from `repro_F1_*` |
| F2 | test asserting the `invalid_mode` path is reachable **or** that the documented vocabulary drops it; decide the contract, then pin the tool schema vs the reason list together | client-side `ToolError: 1 validation error … literal_error` from `repro_F2_*` |
| F3 | parametrized unit test over `variable_names` forms asserting `""` behaves like the other empty forms (all variables) or that the payload carries an explicit missing-name marker | `variables: {}` + no marker from `repro_F3_*` |
| F4 | handler test asserting `create_cell` with an absent `after`/`before` (and with both) returns the shared structured refusal shape | raw execution traceback from `repro_F4_*` |
| F5 | test asserting every `diagnostics[].cell_id` resolves through the same read path used by `get_cell_data`, or that the field is renamed to a positional index | `missing_cell_ids: ["3"]` from `repro_F5_*` |
| F6 | handler test asserting a non-conforming dropdown value (`[]`, and the element's declared `allow_select_none=False`) is refused with `value_shape_mismatch` + `did_you_mean`, or that the doc/declaration is corrected | `ok`/`applied:true`/`value_after:null` from `repro_F6_*` |

Additional gates for this batch:

1. Each fix's repro script must be runnable as-is and must flip from exit 0 to
   exit 1 (finding no longer reproduces) after the fix.
2. Any fix that changes a claim must change the claim in the same change —
   `README.md`, the packaged resources (`src/marimo_inspection/resources/*.md`)
   and the exposed tool descriptions drift apart easily; sweep all three.
3. Re-run the mission probe as a smoke test: `create_cell` the scaled readout,
   `run_cell`, `get_cell_data`/`get_cell_outputs`/`get_variables`/`get_errors`,
   then a widget change with no `run_cell` to confirm reactivity survives.
4. Re-run the `empty-cells` lint pair (`35_lint_empty_cells.py`) so a lint fix
   cannot silently regress the one lint family that was proven correct.

## Remediation (Task 11 — F1–F6 fixed)

The findings above are the discovery record and are left intact. This section
is the remediation record: one row per finding, with the source change, the
tests that now pin it, and the public claim that moved with it. Every accepted
finding was turned into a test that **failed against the pre-fix code and passes
after**; the pre-fix failures were captured by running the new tests in the
shared working tree before the source edits (21 failures across
`tests/marimo_inspect/{test_list_args,test_lint_source,test_mutation,test_ui,
test_resources,test_server}.py`), never by stashing or checking out.

| Finding | Source change | Tests that pin it | Public claims updated |
| --- | --- | --- | --- |
| **F1** — `delete_cell` on an absent id | `tools/mutation.py`: each mutation now runs one **read-only** live-cell validation (`templates/mutation.py::build_cell_targets_template`, which lists every live cell's **id and name**) *before* generating a write scratchpad, and refuses with the shared envelope via `_target_refusal` / `_live_cell_targets` / `_target_known` (`status: error`, `reason: unknown_cell_ids`, `target_resolved`/`operation_ran`/`state_changed` all false, echoed `cell_id`, no `stderr`) | `test_mutation.py::test_delete_cell_absent_id_is_a_structured_refusal`, `…::test_delete_cell_accepts_a_live_cell_name`, `…::test_delete_cell_unreadable_target_census_refuses_structurally`; live `live/test_mutation.py::test_absent_delete_and_anchor_are_structured_refusals` | `co-work-loop.md` §1 (new "Cell and anchor targets" subsection), `fallbacks-and-limits.md` (reason table rows), `README.md` (refusal list), `delete_cell` docstring |
| **F4** — `create_cell` with an absent `after`/`before`, or both | `tools/mutation.py::create_cell`: both anchors is input validation (`reason: conflicting_anchors`) before any session work; an absent anchor is `reason: unknown_cell_ids` with `anchor` + `anchor_cell_id`, checked against the live cells before the create scratchpad is generated | `test_mutation.py::test_create_cell_absent_after_anchor_is_a_structured_refusal`, `…::test_create_cell_absent_before_anchor_is_a_structured_refusal`, `…::test_create_cell_with_both_anchors_refuses_without_any_session_work`; live `…::test_absent_delete_and_anchor_are_structured_refusals` | same as F1 (`create_cell` docstring + the three docs) |
| **F2** — unreachable `invalid_mode` vocabulary | `tools/mutation.py::run_cell`: the defensive branch is **retained** (a direct Python caller), documented as defensive-only; FastMCP's schema validation is untouched | `test_server.py::TestToolRegistration::test_run_cell_mode_schema_is_backward_compatible` (pins the exhaustive literal enum **and** that the description promises no `invalid_mode`); `test_resources.py::…::test_co_work_loop_documents_run_cell_modes`, `…::test_fallbacks_never_promises_an_mcp_invalid_mode_reason` (forbid the vocabulary) | `server.py` instructions, `run_cell` docstring, `README.md`, `co-work-loop.md` §4, `fallbacks-and-limits.md` (all now say the literal enum is rejected by the framework before the handler and list no `invalid_mode`) |
| **F3** — `normalize_list_arg("")` | `tools/args.py`: an empty/whitespace-only string normalizes to `[]` (the surface's own "not provided" convention), so `variable_names`/`cell_ids` treat it exactly like the omitted argument; a non-empty malformed JSON-looking string stays a literal target | `test_list_args.py` (parametrized `""`, `"   "`, `"\t\n "` → `[]`), `…::test_blank_variable_names_takes_the_all_variables_path`, `…::test_blank_cell_ids_takes_the_all_cells_path` (both assert the unfiltered template path) | `normalize_list_arg` docstring; `get_variables`/`get_cell_data` descriptions already said "empty = all" — now true for the blank string too |
| **F5** — lint diagnostic `cell_id` | `tools/lint.py::_lint_source`: the positional index is emitted as **`cell_index`** and `cell_id` is never emitted; `filename`/`line`/`column` unchanged | `test_lint_source.py::test_diagnostic_reports_a_positional_cell_index_not_a_cell_id` (real `_lint_source` on a two-empty-cell notebook: `cell_index` `"0"`/`"1"`, no `cell_id`); vocabulary forbidden in `test_resources.py` (`cell_index` required, and `test_fallbacks_documents_the_lint_diagnostic_position`) | `lint_notebook` + `_lint_source` docstrings, `server.py` instructions, `README.md`, `co-work-loop.md` §7, `fallbacks-and-limits.md` (diagnostics locate SOURCE-FILE positions; a positional index is not a live id) |
| **F6** — empty dropdown selection | `templates/ui.py`: an element-type arity rule — a `dropdown` refuses a submitted list whose length is not exactly one **before** `ctx.set_ui_value`; `multiselect` keeps taking any number of keys. `did_you_mean` is present only when one replacement can be inferred (the element's sole option key); otherwise it is absent and the message names the option keys | `test_ui.py::TestSetUiValueTemplate::test_dropdown_refuses_a_zero_element_list`, `…::test_dropdown_refuses_a_multi_element_list`, `…::test_dropdown_zero_list_with_one_option_infers_it`, `…::test_multiselect_still_accepts_an_empty_list`; live `live/test_ui.py::test_set_ui_value_dropdown_empty_and_multi_lists_are_refused` (widget **and** dependent cell unmoved, then the one-element form still applies) | `set_ui_value` docstring, `templates/ui.py` module docstring, `server.py` instructions, `README.md`, `co-work-loop.md` §5, `live-safety.md`, `fallbacks-and-limits.md` |

### Contract choices made during remediation (differ from / extend the plan)

- **One cell-target refusal vocabulary, reused.** The plan said to use "the same
  public structured target-refusal family used by sibling mutation paths". The
  session-refusal family (`_target_refusal` in `tools/session.py`) carries a
  session census, which a cell reference has none of, so the *envelope* is
  shared (`status`/`reason`/`target_resolved`/`operation_ran`/`state_changed`/
  `next_steps`) and the cell rows are its own. The reason codes are
  `unknown_cell_ids` (reused from `run_cell`'s already-public vocabulary, so the
  surface does not grow a second name for "that is not a live cell"),
  `conflicting_anchors` and `target_validation_failed` (new — see below).
- **A new `target_validation_failed` reason** was added for the case the plan
  did not name: the read-only validation read itself fails, so the reference
  cannot be checked. Refusing (rather than dispatching blind, or returning the
  read's raw `{"error": "Execution failed"}` envelope) is what keeps the
  "no mutation is dispatched on a refusal" guarantee true in every outcome.
- **The anchor/id check accepts a NAME as well as an id.** marimo's own
  `_resolve_target` resolves either, so an id-only check would newly refuse a
  working name — a silent narrowing. `build_cell_targets_template` therefore
  returns ids *and* names.
- **The validation read is a kernel read, not a write.** The old public claim
  "no read or write operation runs on a refusal" was narrowed to "no mutation is
  dispatched on a refusal" (session targets are still refused before any call;
  cell targets are refused after a read-only validation of the live cells). The
  resource test that pinned the old phrase was updated in the same change.
- **`did_you_mean` is omitted, not nulled, when no replacement is inferable**
  (F6), and the one inferable case is the element's sole option key.
- **`edit_cell`'s absent-cell refusal was upgraded** to the same envelope (it
  previously lacked `reason` and the target flags) so all three mutation tools
  answer one shape; its live-id-only validation is unchanged.

### Disposition of the original `repro_F*.py` scripts

The six `repro_F*.py` scripts remain **historical evidence** in the gitignored
lab directory (`.hermes/hunts/2026-09-task-02/`). They embed the Task-02 lab's
own `server_url`/`session_id` and the now-stopped Task-02 server, so their exit
codes cannot be re-derived against a fresh disposable lab without editing them,
and none of them was re-run as evidence for this remediation. Each finding's
fixed behaviour is instead pinned by the hermetic and live regressions listed
above — a successor test per finding, run against a real kernel for F1/F4/F6
and in-process for F2/F3/F5:

- F1/F4/F6 → live regressions (`live/test_mutation.py`, `live/test_ui.py`) plus
  hermetic handler/template cases;
- F2 → the schema/registration and resource-vocabulary tests;
- F3/F5 → the `test_list_args.py` / `test_lint_source.py` cases.

A **successor repro** was additionally run end to end on a freshly provisioned
disposable lab (a purpose-built notebook copy on a free port, one session via
the `/sse` handshake — never the Task-02 server) driving the six findings'
original arguments through the **public MCP stdio surface**
(`.venv/bin/marimo-inspect --transport stdio`, fastmcp's own `Client`), which is
the only way F2/F3/F5 can be confirmed publicly. Exact outcome (2026-09-13):

```text
PASS  F1 delete_cell(absent):        status=error reason=unknown_cell_ids cell_id=ZZZZ_NOPE,
                                     target_resolved/operation_ran/state_changed false, no stderr
PASS  F4 create_cell(absent after):  status=error reason=unknown_cell_ids anchor=after
                                     anchor_cell_id=ZZZZ_NOPE, no stderr
PASS  F4 create_cell(both anchors):  status=error reason=conflicting_anchors
PASS  F2 run_cell(mode="bogus"):     ToolError "1 validation error … [type=literal_error,
                                     input_value='bogus']" — rejected before the handler
PASS  F3 get_variables(""):          blank=['seed_public'] == omitted=['seed_public']
PASS  F5 lint_notebook:              1 diagnostic, keys = [cell_index, column, filename, line,
                                     message, name, rule, severity] (no cell_id)
PASS  F6 set_ui_value(dropdown, []): status=error reason=value_shape_mismatch, did_you_mean
                                     absent, widget 'alpha' -> 'alpha' unchanged
=> 7/7 findings stayed fixed. (script exit 0; 15 tools on the surface)
```

The script is disposable (`/tmp/marimo-inspect-f-successor/`), not committed, and
the lab it booted was torn down at exit. This is the successor evidence for the
plan's "re-run every `repro_F*.py`" gate; the original scripts were not run
because they name the stopped lab.

## Closure status

- **Discovery: complete** for this lab and the surfaces enumerated above; the
  delegated coverage checklist was executed end to end, and 194 logged calls plus
  6 fresh repro runs remain in the evidence directory with their raw payloads.
- **Confirmed findings: 6** (F1–F6) — each with a runnable repro that reproduced
  on a fresh run against the still-running lab (all six exited 0). **Fixes
  landed (Task 11):** all six are remediated with fail-before/pass-after
  regressions, the public claims/resources/README/tool docstrings were swept in
  the same change, and the implementer's verification gate is green (see
  "Remediation" above). F1/F4/F6 are pinned against a real 0.24 kernel;
  F2/F3/F5 in-process. The 21 pre-fix failures are the recorded
  fail-before evidence.
- **Unconfirmed findings: 5** (U1–U5), each with the single probe that would
  settle it; **uncovered categories: 10** (the ten bullets listed above, some of
  which name several related reason codes), including `restart_kernel` entirely.
- **Checked and clean claims:** recorded with call-file evidence, including the
  two claims that most needed re-deriving — the `edit_cell` staleness guard
  (never-read → `needs_read`, preview does not count as a read, unrelated writes
  do not bless another cell, cross-client `conflict` then recovery) and
  `lint_notebook`'s agreement with `marimo check` on an on-disk
  `empty-cells` state.
- **Independent closure: passed.** A fresh zero-context validator re-derived
  F1–F6 from the fixed source, public documentation/resources, focused and live
  regressions, and a supplementary `/tmp` probe. It reported every finding
  **PASS**: F1/F4 refused before a mutation scratchpad was built, F2's MCP
  schema retained exactly the three literals while public prose omitted the
  unreachable handler-only reason, F3 preserved literal malformed inputs while
  treating blank strings as no filter, F5 emitted only `cell_index`, and F6's
  dropdown arity guard preceded `ctx.set_ui_value` without affecting
  multiselect. The validator ran the full non-live and live suites, Ruff,
  formatting, `marimo check notebooks examples`, and `git diff --check`, all
  green. Its only documentation correction was removal of the stale
  `invalid_mode` token from `agenda-udv-consumer-findings.md`; that correction
  is included in this remediation. F1–F6 are therefore **independently
  validated and closed** under `docs/bug-hunt-protocol.md`.
- **Scope integrity:** `git diff --check` clean. This hunt introduced this
  **currently untracked** discovery document and wrote raw evidence only under
  `.hermes/hunts/2026-09-task-02/` (gitignored); it did not modify the pre-existing
  Wave 1/Task 03 source, test, resource, or documentation changes, the
  user-owned untracked `assets/`, or `AGENTS.md`. No `git add`/`commit`/`stash`/
  `checkout`/`restore`/`clean` was run. The remediation wave (Task 11) likewise
  changed only source/tests/docs, wrote nothing under `assets/`,
  `.hermes/hunts/2026-09-task-02/` or `AGENTS.md`, and ran no git state command.
