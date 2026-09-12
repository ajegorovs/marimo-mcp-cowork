import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # Pattern — cascading controls in the sidebar

    Two dependent controls, both hosted in the sidebar so they stay within reach
    however far the rest of the notebook scrolls:

    **pick a parent → its children are discovered from it → pick one child.**

    The cascade is not a special mechanism: level 2 simply reads level 1's value,
    so marimo re-runs it whenever the parent changes.
    """)
    return


@app.cell
def _():
    # Stand-in for a source whose contents can only be learned by opening it —
    # a file, a table, an API response. Swap in the real read; nothing else changes.
    CONTENTS = {
        "group-a": ["alpha", "beta", "gamma"],
        "group-b": ["delta", "epsilon"],
        "group-c": ["zeta"],
    }
    return (CONTENTS,)


@app.cell
def _(CONTENTS, mo):
    # LEVEL 1 — the parent. Created and displayed here; never read here.
    parent_picker = mo.ui.dropdown(
        options=sorted(CONTENTS),
        value=sorted(CONTENTS)[0],
        label="Parent",
        full_width=True,          # fill the sidebar instead of clipping the value
    )
    mo.sidebar(
        [
            mo.md("### Parent"),
            parent_picker,
            mo.md(f"_{len(CONTENTS)} available_"),
        ]
    )
    return (parent_picker,)


@app.cell
def _(CONTENTS, mo, parent_picker):
    # LEVEL 2 — the child. Its options come FROM the parent's value, so this cell
    # re-runs (and the control is rebuilt) whenever level 1 changes.
    parent = parent_picker.value
    children = CONTENTS[parent]
    child_picker = mo.ui.radio(
        options=children,
        value=children[0],
        label="Child",
    )
    mo.sidebar(
        [
            mo.md("### Child"),
            child_picker,
            mo.md(f"_found in `{parent}`_"),
        ]
    )
    return child_picker, parent


@app.cell
def _(child_picker, mo, parent):
    mo.md(f"## {parent} → {child_picker.value}")
    return


@app.cell
def _(child_picker, parent):
    # Whatever the notebook exists to do, driven by the two selections.
    result = f"work would run on {parent!r} / {child_picker.value!r}"
    result
    return (result,)


@app.cell
def _(mo):
    mo.md("""
    ## What makes it work

    - **One definition site per control.** A name defined in two cells is a
      `MultipleDefinitionError` — that single-assignment rule is what makes the
      element *shared* rather than copied.
    - **The creating cell must not read `.value`.** marimo raises *"Accessing the
      value of a UIElement in the cell that created it is not allowed"*. Creation
      and display stay in one cell; every read happens downstream.
    - **A downstream cell may read the parent's value** — different cell, so that
      rule does not apply. That read is the whole cascade.
    - **Both levels are plain `mo.sidebar(...)` calls.** More than one is allowed;
      they stack in call order, so cell order is the visual order.
    - **Changing the parent rebuilds the child**, which therefore falls back to its
      initial value. To remember the previous choice per parent, keep a `mo.state`
      dict and seed the child's `value=` from it.
    - **The sidebar needs room.** marimo hides it below the `lg` breakpoint
      (~1024 px) and it is collapsible — treat it as a convenience layered on top
      of a single definition, not a replacement for one.
    - **No sidebar wanted?** Rendering the *same* element in several cells also
      works: every copy is one element, they stay in sync, and any of them can
      change it.
    """)
    return


if __name__ == "__main__":
    app.run()
