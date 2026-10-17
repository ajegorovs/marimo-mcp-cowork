Done. Both diagnostics read and verified through the MCP surface.

**Observed values**

| gain | processed | normalized |
|---|---|---|
| 2 (default) | [4 8 12 16] | **1.25** |
| 4 | [8 16 24 32] | **2.5** |
| 2 (restored) | [4 8 12 16] | **1.25** |

The value was read from the cell's own rendered output (`<pre class='text-xs'>1.25</pre>`, then `2.5`, then `1.25` again) and cross-checked against the live kernel global `normalized`.

**What I changed**

- One new cell, `OLbz`, created with `create_cell` after `bkHC`:
  `normalized = float(abs(processed).mean() / abs(input_vector).max())` followed by the bare `normalized` as the final expression.
- Ran it with `run_cell(cell_id="OLbz", mode="cell")` — `status ok`, `runtime_state idle`, empty `errors`.
- `gain` 2 → 4 with `set_ui_value` (`applied: true`, `value_before 2`, `value_after 4`), then back to 2 (`applied: true`, `4 → 2`).
- Nothing else was touched; no cell was deleted, edited, or restarted. Mutation payloads all reported `notebook_path ...\run4-wrapper\root\notebook.py`, the tool's own serialization path back to disk.

**Verification**

`get_errors`: `has_errors: false`, 0 structured errors, 0 console-exception cells. `lint_notebook`: 0 issues. `get_cell_map`: 6 cells, `OLbz` present at document level with `runtime_state idle` and `has_errors false`.

**What surprised me about the interface**

1. **The session had never been executed.** On first contact every cell was `stale`, `get_variables` returned `{}` and the dependency graph was empty — a kernel existed but no client had ever run the document. So I ran `run_cell(mode="all")` first. Without that, the slider cell was an unreferenced widget leaf, unregistered and unreachable from `set_ui_value`. The docs' warning about "a session is not a run" was the operative fact here, not a footnote.
2. **The slider update re-ran its dependents by itself.** Autorun did the work: after `set_ui_value(gain, 4)` the diagnostic cell was already `idle`, `output_stale: false`, re-rendered to `2.5` — plus the matplotlib PNG regenerated — with no `run_cell` call from me. The tool still refuses to claim those downstream effects, and rightly so, but in this notebook they happened immediately.
3. **Numbers arrive as HTML.** The read-back of a displayed value came through as markup (`<pre class='text-xs'>2.5</pre>`), and the cell's `visual_output` for the plot is a base64 mimebundle. There is no plain scalar channel for a rendered expression, so the kernel-variable read was the cleaner confirmation.
4. **No fallback channel existed.** This session had no terminal/file tool, so I could not read the `.py` directly; on-disk confirmation rests on the tool's `notebook_path` reporting and the document-level cell set, not on my own byte-level inspection of the file. I'm flagging that rather than claiming I checked the file contents myself.

session_id: 20261017_131457_ef143c
