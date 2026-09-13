# Examples

Notebooks that demonstrate **marimo usage patterns** — the things worth copying
into a notebook of your own. They are deliberately free of any consumer's domain
code and dependencies: each one runs with `marimo` alone.

These are **not** fixtures. `notebooks/` holds the fixtures the live test suite
and the MCP demo boot against; nothing under `examples/` is imported by tests and
nothing here is needed to run the package. What *does* touch them is the gate
below — they are booted and executed as notebooks, so their execution and the
reactivity they document cannot rot silently.

| Example | Pattern |
| --- | --- |
| [`patterns/cascading_sidebar_controls.py`](patterns/cascading_sidebar_controls.py) | Two dependent controls (the child's options come from the parent's value) hosted in the sidebar, so they stay reachable while the notebook scrolls. |
| [`patterns/slider_with_step_buttons.py`](patterns/slider_with_step_buttons.py) | A slider paired with step buttons (`±1` and a coarse jump) over one shared `mo.state`, slicing a 2-D `f(x, y)` into 1-D profiles. Shows the cell-splitting rule that makes button-driven sliders work at all. |

Open one:

```bash
uv run marimo edit examples/patterns/cascading_sidebar_controls.py
```

Check them all (the full notebook check — `examples/` is part of it, not
outside every gate):

```bash
uv run marimo check notebooks examples
```

## How these are verified

Every example is covered by `tests/marimo_inspect/live/test_examples.py`:

```bash
uv run pytest tests/marimo_inspect/live/test_examples.py -m live
```

- **Static** — `uv run marimo check notebooks examples` parses and lints every
  notebook in both trees.
- **Live smoke** — the tracked `examples/**/*.py` tree is *discovered* (never
  hardcoded), each example is copied into `tmp_path` and booted on its own
  headless marimo server, and the whole document is executed through the real
  `run_cell(mode="all")` handler. The pass condition is `status: ok` with no
  failed, not-run or unverified target, so a marimo bump that invalidates an
  example fails the live suite instead of silently rotting the example.
- **Interaction** — the two shipped patterns are driven through the live widget
  handler (`set_ui_value`) and read back with `get_variables`: the step buttons
  must move the one shared `mo.state` index (repeated advancing counters,
  backward, clamped at both ends, the slider re-seeded from the state), and a
  cascade parent change must rebuild the child (new options, selection reset)
  and re-derive the result.

Frontend *rendering* is not covered by any of this — the suite boots kernels,
not browsers (the repo-wide gap recorded in
[`docs/live-tests.md`](../docs/live-tests.md)). Re-check by hand in a browser
when an example's output markup changes.

## Per-parent memory (a recipe, not a third example)

The cascade example rebuilds the child whenever the parent changes, so the child
falls back to its first option. To remember the choice **per parent**, keep one
`mo.state` dict and seed the child's `value=` from it:

```python
# a state cell
remembered, set_remembered = mo.state({})

# the child cell (the rest of the cascade is unchanged)
selected = parent_picker.value
children = CONTENTS[selected]
prior = remembered().get(selected)
child_picker = mo.ui.radio(
    options=children,
    value=prior if prior in children else children[0],
    on_change=lambda value: set_remembered({**remembered(), selected: value}),
    label="Child",
)
```

That IS the whole variant — the cascade mechanism does not change. It is a
recipe rather than a shipped example on purpose: it is one control's `value=`
and `on_change=`, not a different kind of pattern. The recipe is still a
*verified* claim: `tests/marimo_inspect/live/test_examples.py` boots an
implementation of it as a test-local notebook — the same cell structure with
renamed locals — and asserts the child comes back per parent (including the
no-memory-yet fallback).

## Related

- [`docs/agenda-example-notebooks.md`](../docs/agenda-example-notebooks.md) —
  the contract decisions (what `examples/` is, how it is verified, why there is
  no third per-parent-memory example).
- [`docs/live-tests.md`](../docs/live-tests.md) — the live suite these gates
  belong to, and its current counts.
