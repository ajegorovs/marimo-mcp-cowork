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
- Bind a widget to a **bare** top-level name. marimo keeps a
  leading-underscore name cell-private, so `_slider` is invisible to every
  other cell AND unreachable from `set_ui_value` (`reason:
  unknown_variable`) — the tool cannot fix that for you.
- Update a widget from outside via `set_ui_value`, not by editing its cell.
  It never coerces the value: send the shape the element accepts (a `dropdown`
  option key goes inside a one-element list, e.g. `["beta"]`). A refused shape
  returns `reason: value_shape_mismatch` with the corrected payload in
  `did_you_mean`; a value the kernel rejected returns `reason:
  value_not_applied` with the kernel's own message. Read those — the widget is
  unmoved in both cases. `status: ok` means the value was read back
  (`verified: true`: `applied: true` if it moved, `no_change: true` if it
  already held that value), but dependent cells' reactive re-runs are not
  awaited.
