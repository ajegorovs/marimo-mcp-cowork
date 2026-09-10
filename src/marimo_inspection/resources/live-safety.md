# Live-Safety Rules

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

These tools act on a **live kernel**. Treat every write as production.

## Edits hit the running kernel

`edit_cell` and `run_cell` mutate the session the server is attached to. The
`.py` file on disk is reconciled by marimo separately. There is no undo.

## Read before edit

`edit_cell` defaults to `check_fresh=True`: it requires that you read that
exact cell first. On a first touch it returns status `needs_read`
unconditionally — even on a session with no snapshot. If the source changed
since your last read it returns `conflict`.

Recovery is a real re-read, then retry:

1. `get_cell_data` for that cell (this records the read baseline), or
   `get_cell_map`.
2. Retry `edit_cell`.

`check_fresh=False` is an explicit force escape hatch — never the normal
recovery path.

## Inspect before delete or merge

Run `get_dependency_graph` before `delete_cell` or merging cells: removing a
cell that defines a variable other cells reference breaks them.

## Verify after every write

Re-check `get_variables`, `get_cell_outputs`, and `get_errors`. A successful
tool call is not proof of a correct result.

## Widget visibility and dependencies

- A `mo.ui` control is only VISIBLE if it is the cell's final expression (or
  the last item of its final `mo.vstack`). A control assigned to a variable
  mid-cell still works but renders nowhere.
- A cell cannot read the `.value` of a UI element it created in the same cell.
  Split control creation and value reads into separate cells.
- Update a widget from outside via `set_ui_value`, not by editing its cell.
