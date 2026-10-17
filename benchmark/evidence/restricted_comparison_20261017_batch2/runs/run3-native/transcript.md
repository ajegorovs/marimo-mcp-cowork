Done. Both diagnostic readings came from the live kernel, all through `pair`.

What I observed
- gain at default 2: `normalized` = 1.25 (processed was array([4, 8, 12, 16]); mean|.|=10, max|input|=8)
- gain at 4: `normalized` = 2.5 (processed array([8, 16, 24, 32]); mean|.|=20, max|input|=8)
- after restoring gain to 2: back to 1.25, processed back to array([4, 8, 12, 16])

What I changed
- Created one new cell via `pair create_cell` containing exactly:
    normalized = float(abs(processed).mean() / abs(input_vector).max())
    normalized
  It was appended as cell id `EDfJ`, runs in the live kernel, and the trailing `normalized` is the cell's final expression, so it renders visibly.
- `pair set_ui --name gain --value 4`, then `--value 2` to restore.
- Verified on disk: notebook.py now contains cell `EDfJ`; sha256 4845a5610a30a5112b6344876f5774931e5d55d01a30675df92e67bc4ed02321 matches the ledger's last recorded hash. `pair errors` returned `[]` for all six cells (Hbol, MJUe, vblA, bkHC, lEQa, EDfJ) at the end. I wrote nothing to the file except through `pair`.

Surprises about the interface
1. A newly created cell does not get its ancestors materialized. My first `create_cell` succeeded as a write but ran immediately in a kernel where the notebook had never executed, so it failed with `NameError: name 'processed' is not defined` even though `processed` is defined in an earlier cell. I had to `run_all` (6 cells) before the cell could run cleanly. The cell was already on disk despite the error status.
2. Before that first run, `pair variables` (no `--names`) returned only `input` and `spec_from_loader` — the CLI's own import machinery leaking into the default "public names" listing, not the notebook's variables. Only after `run_all` did it report mo/np/plt/gain/input_vector/processed, etc. An empty-looking variable report means "not run yet", not "no variables".
3. `pair --help` lists no cell-read subcommand, though the interface note mentions cell reads; the only way to read the diagnostic was `pair variables --names normalized`, and `gain` only ever reports as `slider()` — the slider's value isn't visible directly, I inferred it from `processed`.
4. Any `create_cell` rewrites the whole file as a normal marimo save: the plot cell's `return ax, fig` was normalized to `return`, and `__generated_with` was bumped 0.24.0 → 0.25.1 (the running kernel's version). Pre-existing cells are not left byte-identical, even though I touched only one cell.

session_id: 20261017_131310_9c0337
