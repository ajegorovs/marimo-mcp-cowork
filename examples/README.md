# Examples

Notebooks that demonstrate **marimo usage patterns** — the things worth copying
into a notebook of your own. They are deliberately free of any consumer's domain
code and dependencies: each one runs with `marimo` alone.

These are **not** fixtures. `notebooks/` holds the fixtures the live test suite
and the MCP demo boot against; nothing under `examples/` is imported by tests and
nothing here is needed to run the package.

| Example | Pattern |
| --- | --- |
| [`patterns/cascading_sidebar_controls.py`](patterns/cascading_sidebar_controls.py) | Two dependent controls (the child's options come from the parent's value) hosted in the sidebar, so they stay reachable while the notebook scrolls. |
| [`patterns/slider_with_step_buttons.py`](patterns/slider_with_step_buttons.py) | A slider paired with step buttons (`±1` and a coarse jump) over one shared `mo.state`, slicing a 2-D `f(x, y)` into 1-D profiles. Shows the cell-splitting rule that makes button-driven sliders work at all. |

Open one:

```
uv run marimo edit examples/patterns/cascading_sidebar_controls.py
```

Check them all:

```
uv run marimo check examples
```

> `marimo check` currently covers `notebooks/` only (see `AGENTS.md`
> §Common commands and the Development section of `README.md`). Wiring
> `examples/` into the check command, the docs map and CI is tracked in
> [`docs/agenda-example-notebooks.md`](../docs/agenda-example-notebooks.md).
