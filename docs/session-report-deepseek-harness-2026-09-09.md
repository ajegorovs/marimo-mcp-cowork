# DeepSeek Harness session — marimo-inspect MCP consumer experience report

> Date: 2026-09-09 · Consumer: `udv-echo-process` (sibling checkout, uv-managed,
> Python 3.14.7, marimo 0.24.0, marimo-inspect **v0.2.0 installed editable**).
> Session: built and iterated an interpolation-preview feature inside the live
> notebook `notebooks/channel_preview.py` (kernel `localhost:2718`, session
> `s_jb93sf`), driving the 13 MCP tools registered in the DSH web harness as
> `mcp__marimo__*`, plus `marimo._code_mode` (`cm`) through the skill's
> `execute-code.sh` HTTP path for everything the MCP surface cannot do.
> Result committed as `udv-echo-process` `2fb8f26..16e9010` (interpolation
> controls incl. extrapolation testing, measured-only heatmap, per-gate trace
> with its own gate dropdown, cell consolidation).

This is the "here is what using it felt like" report the consumer asked for, so
the provider knows what to improve. It complements the distilled work list in
`agenda-udv-consumer-findings.md` and the harness setup note in
`harness-integration/deepseek-harness-web-profile.md`.

## 1. Tool-by-tool experience (13 MCP tools)

| Tool | Used | Verdict |
|---|---|---|
| `list_active_notebooks` | yes, every session start | Works. Auto-bind is call-local: **every** follow-up call still needs explicit `server_url` (+ `session_id`) — see F7. The `active_connections` count is ambiguous (agent-side connections count too; can't tell whether the human's browser is attached). |
| `set_active_session` | no | Explicit `server_url`/`session_id` everywhere made it unnecessary. |
| `get_cell_map` | yes, constantly | Previews are good for orientation. **`has_output` read `false` for every cell even when outputs existed** — treat it as unreliable (F2). |
| `get_cell_data` | yes | Reliable code + status source; `variables` field came back `null`. |
| `get_cell_outputs` | yes | Good for console streams (stderr tracebacks, stdout). Returns a **single** payload per cell; multiple blocks / auto-rendered UI elements are not visible (F2). |
| `get_variables` | yes | Solid; filtered list args work on v0.2.0. |
| `get_dependency_graph` | yes (1×) | Excellent for planning merges/deletes: defs/refs/owners/multiply-defined per cell made "which cells can we combine?" answerable in one call. |
| `get_errors` | yes, constantly | Accurate for **cell** errors. **UI-element handler exceptions are invisible here** — they land in the cell console instead (F3). |
| `lint_notebook` | no | Used `marimo check` via CLI instead (import-free, safe under `uv run`). |
| `run_cell` | yes | Executes and cascades dependents reactively. Caveat: after structural edits, running an upstream cell can cascade into a mid-refactor downstream cell (we hit a transient `NameError` and a multiply-defined dry-run reject) — sequence edits before runs. |
| `create_cell` | yes (1×) | Fine. Two gotchas: **does not auto-run** (separate `run_cell` needed) and defaults to `hide_code=True` (new cell's code hidden in the UI). |
| `edit_cell` | yes, heavily | Staleness guard + dry-run compile are the best part of the surface. Clear rejections (e.g. multiply-defined `heat`, `gate_idx` across cells) let us adapt: delete-first, or move a definition to its own cell. |
| `delete_cell` | yes (2×) | Clean. Check `get_dependency_graph` first for dependents. |

Outside the MCP surface we had to use `marimo._code_mode` through
`execute-code.sh` (HTTP) for: (a) arbitrary read/eval probes, (b)
`ctx.set_ui_value(...)` to move sliders/dropdowns and drive reactive reruns
like a user click, (c) `ctx.screenshot(...)` (failed — Playwright not
installed). **The MCP server has no execute-code and no set-UI-value tool** —
every UI interaction or one-off probe requires dropping out of MCP (F1).

## 2. What worked well

- The full agent loop (`list_active_notebooks → get_cell_map → get_cell_data →
  edit_cell → run_cell → get_variables → get_cell_outputs → get_errors`) stayed
  stable across a long session (~100+ tool calls, ~30 cell runs, 5 commits)
  against a live 3.14 kernel.
- Reactivity was testable end-to-end: `cm.set_ui_value` on a widget re-ran the
  dependent chain like a user interaction; we verified extrapolation policies
  (`error`/`nan`/`nearest`) and the gate dropdown numerically from kernel state
  after each change.
- Error paths were genuinely useful: edit dry-run surfaced graph violations
  with the offending names; the marimo runtime itself raised a clear
  `RuntimeError` for the creator-reads-own-value rule (see F5).
- `get_dependency_graph` made the final notebook restructuring (merge stats +
  heatmap, drop a cell, split a gate selector into its own cell) safe and quick.

## 3. Frictions & gaps (what to improve)

**F1 — No execute / set-UI-value surface in MCP.** All widget testing and
arbitrary probes went through `cm` over HTTP. Even worse, dropdown values must
be passed to `set_ui_value` as single-element **lists**; a scalar raises an
`AssertionError` deep inside `_update` (see F3). Request: add `execute` and
`set_ui_value` MCP tools (cm-backed), or document prominently that UI control
lives outside MCP.

**F2 — Output model hides UI-element outputs.** `get_cell_map.has_output` was
`false` for every cell although the kernel held real outputs, and
`get_cell_outputs` exposes one payload per cell. Cells whose visible output is
an auto-rendered UI element (dropdowns, plotly wrappers, sliders) look *empty*
to the agent while the user sees widgets. This directly caused the wrong
diagnosis earlier in the session ("no cell output") and a vstack band-aid that
turned out to be independently necessary (F4). Improvement: expose all output
blocks incl. UI-element registrations (their `object-id`s), and fix or drop
`has_output`.

**F3 — Two error channels; UI-handler exceptions invisible to `get_errors`.**
A bad programmatic dropdown set produced
`An exception was raised by a UIElement's on_change handler:` + a raw
`AssertionError` traceback in the **cell console**, while `get_errors` reported
0 errors and the user saw a sticky error banner in the GUI (cleared only by
rerunning the owner cell). Improvement: surface UIElement/`on_change`
exceptions in `get_errors` or per-cell status; and replace the deep
`AssertionError` on scalar-vs-list with a clear message naming the fix.

**F4 — The "a cell displays only its final expression" rule is not
discoverable from the tools.** Widget-only cells (assignments, no trailing
expression) produced empty kernel output after backend runs, so the agent
"implemented controls" that rendered nothing. Cost a round trip with the user
("you don't finalize the cell with a visualization command") before the fix
(`mo.vstack([...])` as the final expression) was clear. Improvement: state this
rule in the agent docs + a hint in `edit_cell`/`create_cell` tool text; and
clarify that UI-element registration may only happen on frontend runs (a page
refresh can be required after backend structural edits — F8).

**F5 — marimo rule "a cell cannot read the `.value` of a UI element it created
in the same run."** Surfaced as a clear `RuntimeError` and forced the gate
selector into its own upstream cell. The error message already names the fix;
it is just not documented anywhere in this repo's agent docs. Add it next to
F4's rule.

**F6 — `screenshot` fails hard without Playwright.** `ScreenshotError:
Playwright is not installed` lists pip steps but no graceful capability
detection and no consumer-venv install path. Feature-detect and return a
friendly "unavailable", and document the install for the skill.

**F7 — Auto-bind/session semantics remain awkward.** Confirmed again (already
T2 in `agenda-udv-consumer-findings.md`): the bind from
`list_active_notebooks` does not persist across tool calls; the agent must pass
`server_url` (+`session_id`) on every call. Consider a sticky session per
agent/tool-call chain, or at least surface *which* connections are human vs
agent.

**F8 — Frontend/kernel desync after backend edits.** After structural
cell edits + backend runs, the user's browser can show stale/no output while
the kernel holds correct state; the remedy is a page refresh. A hint to
broadcast a frontend refresh after structural mutation would remove this class
of "it doesn't work" reports.

**F9 — Multi-element cells are opaque.** E.g. the header cell that owns a
channel dropdown *and* shows a markdown summary: the MCP output payload shows
only the markdown; an agent cannot tell the cell also presents an interactive
dropdown. (Overlaps F2; listed because it bites during notebook surgery.)

## 4. Guide gap (what the consumer was missing)

The consumer repo's `AGENTS.md` documents the `marimo-pair` skill loop, but
there is **no standalone "how to use marimo-inspect MCP" guide** covering the
operational rules that actually shaped this session:

1. a cell displays only what its final expression evaluates to (F4);
2. a cell cannot read the `.value` of a UI element it created (F5);
3. dropdown `set_ui_value` needs the single-element list form (O25);
4. `get_errors` ≠ UI-handler errors (F3);
5. UI interaction testing goes through `cm`/HTTP, not MCP (F1);
6. pass `server_url`/`session_id` explicitly on every call (F7);
7. `edit_cell` staleness guard + dry-run compile (worked as documented);
8. check `get_dependency_graph` before delete/merge.

Recommend a short consumer-facing doc (or extend
`agent-onboarding-demo-mcp.md`) encoding rules 1–6, pointed to from the
consumer `AGENTS.md`. This report doubles as raw material until that exists.

## 5. Suggested improvement backlog (priority order)

1. **P1** — add `execute` / `set_ui_value` MCP tools (or explicitly document
   the `cm`-over-HTTP split).
2. **P2** — unify error channels: UIElement/`on_change` exceptions visible in
   `get_errors`; clear scalar-vs-list dropdown error.
3. **P3** — output model: expose all output blocks + UI elements per cell; fix
   `has_output`.
4. **P4** — docs: marimo agent rules (final-expression display, creator-can't-
   read-value, list-form sets, screenshot/Playwright) in one guide.
5. **P5** — sticky session semantics or clearer connection info.

## Cross-references

- Consumer append-only log: `udv-echo-process/docs/marimo-integration-log.md`
  (O25 dropdown list-form; this session adds O28–O32).
- Provider work list: `agenda-udv-consumer-findings.md` (T2 auto-bind, T3 DSH
  list-arg mangling, T4 3.14 drift).
- Harness setup: `harness-integration/deepseek-harness-web-profile.md`.
- Skill (cm HTTP path): `marimo-pair/scripts/execute-code.sh`.
