Done. Report:

Values observed (live kernel, read from the cell's own stdout on each run):
- gain at default 2: normalized = 1.25
- gain = 4: normalized = 2.5
- after restoring gain = 2: normalized = 1.25 again

What I changed (all through `pair`, never a file write):
- `pair create_cell --code '...'` added one cell (id MSSM) appended after the plot cell, with the exact assignment `normalized = float(abs(processed).mean() / abs(input_vector).max())`, a `print` of the value, and a bare `normalized` as the last expression so marimo renders it. The interface correctly derived its deps (`input_vector, processed`).
- `pair run_all` executed all 6 cells in the live kernel (this is what cleared the new cell's initial error — see below).
- `pair set_ui --name gain --value 4`, then `--value 2` to restore.

On-disk final state, verified: notebook.py has 6 cells, slider written as `value=2`, no cell errors (`pair errors` → all six cells `[]`), and sha256 1156bdd2fb9d349a809f2f53d0582eb1cd80c0fc422e82d9ee6651e19b778697, which equals the last `sha256_after` in the ledger (seq 14). I read the file with `cat` only to verify the result; no write of any kind touched it.

Surprises about the interface:
1. The interface note describes "cell reads" and "notebook and session inspection", but the authoritative `--help` lists only six subcommands (run_all, create_cell, run_cell, variables, errors, set_ui). There is no way to read a cell's source or its rendered output — so I could only recover the diagnostic value because I put a `print` in the cell and the CLI surfaces the kernel's stdout stream. The bare last-expression output goes to marimo's output channel, which the CLI does not expose.
2. `create_cell` runs the new cell immediately, before the notebook's own cells had ever executed, so it died with `NameError: name 'processed' is not defined` even though `processed` is defined by an upstream cell and the new cell was wired to depend on it. The same root cause made `pair variables --names ...` return `None` for existing names (and the no-names listing return junk that isn't in the notebook at all: `input`, `spec_from_loader`). Only after `run_all` did both behave sensibly — `variables` then showed `normalized = 1.25`, `processed = array([4,8,12,16])`.
3. `set_ui` is not passive: it re-runs the downstream cells and echoed the new diagnostic itself, which is how I read the gain=4 value.
4. The interface's save bumped `__generated_with` from "0.24.0" to "0.25.1" in the notebook — a disk edit made by the interface, not by me.

session_id: 20261017_131052_4f291b
