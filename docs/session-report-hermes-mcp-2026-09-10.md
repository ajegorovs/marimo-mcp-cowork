# Hermes MCP live-validation report — 2026-09-10

> **Status:** Evidence report for review and verification; no provider code was
> changed. A live consumer notebook was launched locally from its project venv
> and addressed through the Hermes stdio MCP gateway. The notebook used marimo
> 0.24.0 and an editable checkout of this package, including the custom
> `TraceScrubber` widget.
>
> **Purpose:** Decide whether marimo-inspect can replace the older
> `marimo-pair` skill plus its `discover-servers.sh` and `execute-code.sh`
> helpers, and define the remaining work before an MCP-first agent workflow is
> safe.

## 1. Connection and session binding

The MCP tool catalog was available in Hermes. The launched notebook server at
`http://127.0.0.1:2718` was discovered as one active notebook session. After
one explicit `list_active_notebooks(server_url=...)` call, subsequent calls
omitted both `server_url` and `session_id` successfully.

**Observed:** on the Hermes stdio transport, active-session binding persists
across calls. This independently confirms the prior Hermes-side result in
`agenda-udv-consumer-findings.md` T8.

## 2. Read, inspection, and execution surface

The following MCP tools were invoked successfully against the live session:

- `list_active_notebooks`
- `get_cell_map`
- `get_cell_data`
- `get_cell_outputs`
- `get_variables`
- `get_dependency_graph`
- `get_errors`
- `lint_notebook`
- `run_cell`
- `create_cell`
- `delete_cell`

The notebook had 16 cells. They began stale after launch, then were run in
notebook dependency order through `run_cell`. Final verification showed:

- all **16 cells idle**;
- `get_errors` reported **0 errors**;
- `lint_notebook` reported **0 issues**;
- the project's `marimo check` command exited **0**;
- `get_variables` returned live dropdown selections and loaded measurement
  state;
- `get_cell_outputs` returned rendered Plotly, Marimo UI, and anywidget output;
- `get_dependency_graph` returned the full defs/refs/parents/children graph,
  with no multiply-defined names or cycles.

The notebook performed real work during the test: it loaded a four-channel
binary measurement, ran interpolation from 400 source samples to 1,742 samples
at the selected cadence, and applied a median filter. The custom
`TraceScrubber` widget imported from the editable provider checkout and
rendered as an anywidget output.

## 3. Write-surface probe: `edit_cell` false conflict

A disposable markdown cell was created after an existing introduction cell.
The following sequence was performed:

1. `create_cell` succeeded and returned the temporary cell ID.
2. `get_cell_data` read that exact cell.
3. `edit_cell` rejected the first edit with `status: "conflict"`, saying the
   cell changed since it was last read.
4. `get_cell_data` re-read the unchanged cell as the tool instructed.
5. The identical `edit_cell` retry returned the same conflict.
6. `delete_cell` removed the disposable cell successfully.

The notebook returned to its original 16 cells and the consumer working tree
remained clean.

**Finding:** the normal staleness-guard recovery protocol is not functioning in
this live Hermes run. A user/agent cannot safely edit an existing cell through
the MCP tool when repeated fresh reads still yield a conflict. Do not normalize
`check_fresh=False` as a routine workaround: it disables the concurrency
protection that is the rationale for this surface.

## 4. Replacement assessment

### Ready to replace

The MCP is already the better default for:

- active-session discovery and selection;
- notebook orientation and source reads;
- dependency-aware inspection;
- runtime variables, outputs, errors, and linting;
- deliberate cell execution;
- creating and deleting cells.

`discover-servers.sh` is redundant for the normal agent workflow. It can remain
as a human/debug fallback, but agents should prefer
`list_active_notebooks(server_url=...)` when discovery is unavailable.

### Not ready to replace

The older `execute-code.sh` / CodeMode fallback remains needed for:

1. **Editing existing cells safely.** `edit_cell` must first pass a live
   create → read → edit → run → verify → delete regression test with
   `check_fresh=True`.
2. **Programmatic widget interaction.** There is no MCP `set_ui_value` tool;
   this is necessary for reactive end-to-end UI tests.
3. **Arbitrary in-kernel probes.** There is no constrained general scratchpad
   execute tool. This should remain deliberately separate from ordinary MCP
   inspection unless the provider decides a safe scope and contract.
4. **Notebook server lifecycle.** The MCP cannot launch, restart, or stop a
   Marimo server. A small documented local-launch fallback remains necessary.

## 5. Recommended transition

Do not delete the legacy material immediately. Use this staged policy:

1. Make marimo-inspect the default co-work interface.
2. Mark `discover-servers.sh` deprecated for agents; retain it only as a
   diagnostic fallback.
3. Keep `execute-code.sh` as an explicitly named escape hatch until the
   edit-cell regression is fixed and widget setting is covered by MCP.
4. Replace the long, CodeMode-first `marimo-pair` workflow with a compact
   MCP-first workflow:

   `list_active_notebooks → get_cell_map → get_cell_data → run_cell →
   get_variables / get_cell_outputs → get_errors → lint_notebook`

5. After the provider fixes `edit_cell`, add a live regression that exercises
   the full guarded edit sequence above. Then re-run this consumer validation
   before retiring the fallback path.

## 6. Skills versus MCP resources

Do not republish the current large skills verbatim as MCP resources. Split the
knowledge by task and keep the agent-facing skill short:

- `workflow://marimo-inspect/co-work-loop` — normal inspect/run/verify loop;
- `workflow://marimo-inspect/live-notebook-safety` — active-runtime rule,
  read-before-write discipline, and post-write verification;
- `workflow://marimo-inspect/write-conflicts` — staleness protocol and the
  explicit fallback while the edit defect remains;
- `reference://marimo-inspect/output-and-widget-limits` — one-main-output
  constraint, final-expression display, and current widget limitations;
- `reference://marimo-inspect/server-lifecycle` — launch/restart expectations
  and browser/session materialization.

The resources should describe capabilities actually provided by the MCP. Keep
raw CodeMode recipes and shell transport details in a clearly labelled fallback
reference, not in the default co-work skill.

## 7. Implementation gates

Before full retirement of the legacy pairing workflow, require:

1. Fix and live-regression-test the repeatable `edit_cell` false conflict.
2. Add and validate a `set_ui_value` MCP tool, including list-shaped values and
   a useful error for invalid scalar input.
3. Decide whether a bounded execute/probe tool belongs in the MCP; otherwise
   document `execute-code.sh` as the intentional fallback.
4. Decide whether lifecycle management is in scope; otherwise publish the
   minimal supported launch/restart procedure.
5. Update the MCP-first skill/resources only after the implemented tool surface
   and its fallback boundary are stable.
