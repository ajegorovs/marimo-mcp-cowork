import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import math

    import marimo as mo

    return math, mo


@app.cell
def _(mo):
    mo.md("""
    # Pattern — a slider with step buttons

    A slider you can also *nudge*: `−1` / `+1` for a fine step, `«` / `»` for a
    coarse jump, all driving **one shared value**.

    This is the shape to reach for when the slider indexes something you want to
    walk through — a time profile, a frame, a row — because a drag is imprecise
    and a keyboard arrow is not available to a mouse-only reader.

    The demo payload is **slicing a 2-D field `f(x, y)` into 1-D profiles**: the
    field is a ridge whose peak sweeps across `y` as `x` advances, so stepping `x`
    visibly slides the peak. That is the generic form of scanning profiles
    through a time-depth measurement.
    """)
    return


@app.cell
def _(math):
    # ---- the demo payload: a 2-D field, f(x, y) ---------------------------------
    N_X, N_Y = 48, 24  # 48 "profiles" of 24 "depths" each
    SIGMA = 2.2


    def centre(x: int) -> float:
        """Where the ridge sits in y at column x — a full sweep across the axis."""
        return 11.5 + 9.0 * math.sin(2.0 * math.pi * x / N_X)


    def f(x: int, y: int) -> float:
        """One profile: a Gaussian peak in y, whose position moves with x."""
        return math.exp(-((y - centre(x)) ** 2) / (2.0 * SIGMA**2))


    FIELD = [[f(x, y) for x in range(N_X)] for y in range(N_Y)]
    LAST = N_X - 1
    return FIELD, LAST, N_X, N_Y


@app.cell
def _(mo):
    # The ONE value. A mo.state getter/setter pair — not a widget, because a
    # widget's own value cannot be assigned from Python (see the notes).
    get_index, set_index = mo.state(0)
    return get_index, set_index


@app.cell
def _(LAST, get_index, mo, set_index):
    # The SLIDER half. It needs a cell of its own, APART from the buttons:
    # marimo never re-runs the cell that called a state setter (it skips that
    # cell to avoid self-loops), and this is the cell that must re-run for the
    # slider to render at a button-set value. It is only CREATED here — the
    # composition cell below puts it in the sidebar.
    step_slider = mo.ui.slider(
        0,
        LAST,
        value=get_index(),  # re-seeded whenever the state changes
        on_change=set_index,  # and the slider writes the same value back
        show_value=True,  # 0.24.x hides the number by default
        full_width=True,  # fill the sidebar instead of clipping the value
    )
    return (step_slider,)


@app.cell
def _(LAST, get_index, mo, set_index):
    # The BUTTONS half. This cell IS the setter's cell, so its own clicks never
    # re-run it — that is what stops a loop, and the slider's cell above is what
    # re-renders instead. Also creation only.
    def _step(delta: int):
        return lambda _: set_index(max(0, min(LAST, get_index() + delta)))

    coarse = max(1, LAST // 4)  # a drag across the whole axis is still 1 profile
    step_far_back = mo.ui.button(label="«", on_click=_step(-coarse))
    step_back = mo.ui.button(label="−1", on_click=_step(-1))
    step_fwd = mo.ui.button(label="+1", on_click=_step(+1))
    step_far_fwd = mo.ui.button(label="»", on_click=_step(+coarse))
    return coarse, step_back, step_far_back, step_far_fwd, step_fwd


@app.cell
def _(coarse, mo, step_back, step_far_back, step_far_fwd, step_fwd, step_slider):
    # ONE sidebar entry. Separate `mo.sidebar` calls stack as separate blocks, so
    # neither half displays itself: this cell composes them into a single item.
    # The splitting above is required for reactivity; this composition is what
    # makes the block read as one control. Rendering an element from a second
    # cell is legal — only *creating* it twice is not.
    mo.sidebar(
        mo.vstack(
            [
                mo.md("### Profile"),
                step_slider,
                mo.hstack(
                    [step_far_back, step_back, step_fwd, step_far_fwd],
                    justify="center",
                ),
                mo.md(f"_±1, or ±{coarse} coarse_"),
            ]
        )
    )
    return


@app.cell
def _(FIELD, LAST, N_X, N_Y, mo, step_slider):
    # Everything downstream reads the value. A different cell, so reading the
    # element's `.value` is legal here.
    index = int(step_slider.value)

    _stops = [(68, 1, 84), (33, 145, 140), (94, 201, 98), (253, 231, 37)]

    def _colour(value: float) -> str:
        t = max(0.0, min(1.0, value)) * (len(_stops) - 1)
        i = min(int(t), len(_stops) - 2)
        u = t - i
        rgb = [
            round(_stops[i][k] + u * (_stops[i + 1][k] - _stops[i][k]))
            for k in range(3)
        ]
        return "#%02x%02x%02x" % tuple(rgb)

    _rows = "".join(
        "<tr>"
        + "".join(
            "<td style=\"width:11px;height:10px;background:%s;%s\"></td>"
            % (
                _colour(FIELD[_y][_x]),
                "box-shadow:inset 0 0 0 2px #ff1744;" if _x == index else "",
            )
            for _x in range(N_X)
        )
        + "</tr>"
        for _y in range(N_Y)
    )
    _field = mo.Html(
        '<div style="font-size:0">'
        '<table style="border-collapse:collapse;table-layout:fixed">'
        f"{_rows}</table></div>"
        '<div style="font:12px/1.4 monospace;color:#888;margin-top:4px">'
        "x → profile (0…%d), y → depth. The outlined column is the slice."
        "</div>" % LAST
    )
    _bars = "".join(
        "%2d │%-24s│ %.2f\n"
        % (
            _y,
            "█" * round(FIELD[_y][index] * 24),
            FIELD[_y][index],
        )
        for _y in range(N_Y)
    )
    _slice = mo.md(f"**Profile {index} of {LAST}**\n\n```\n{_bars}```")

    mo.vstack([_field, _slice], align="start")
    return (index,)


@app.cell
def _(index, mo):
    mo.md(f"""
    Downstream cells just use the value: `profile = {index}`.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## What makes it work

    - **The buttons do not move the slider — the state does.** They write one
      `mo.state`, and the slider is *created* at that value, so the two never
      disagree and no custom widget is involved.
    - **The slider must live in a DIFFERENT cell from the buttons.** marimo never
      re-runs the cell that called a state setter — it skips that cell to avoid
      self-loops (`_runtime/runner/cell_runner.py`, rule 3: *"a state update in a
      given cell will never re-trigger the same cell to run"*). Put the slider and
      the buttons in one cell and a click updates the state, that cell is skipped,
      and *nothing visibly happens*: the slider is never re-seeded and downstream
      cells keep reading its old `.value`. Splitting them into two cells is the
      whole fix.
    - **That same rule is what keeps a drag stable:** moving the slider does not
      re-run the slider's own cell, so a re-seed can never fight your drag.
    - **`UIElement.value` cannot be assigned.** Its setter raises *"Setting the
      value of a UIElement is not allowed … consider using `mo.state()`"*. That
      message is the pattern: one `mo.state`, every control writing it.
    - **Clamp in the handler.** The slider bounds itself, but a button writing the
      state does not — `max(0, min(LAST, …))` is what stops `−1` at the ends from
      going out of range.
    - **`on_click` receives the button's click count**, so the handler takes one
      argument and ignores it; the value it needs comes from `get_index()`.
    - **`show_value=True`.** 0.24.x hides the number beside a slider by default,
      which makes a stepped slider much harder to read.
    - **Steps are worth having whenever the axis is long.** A drag is imprecise,
      so ±1 plus a coarse jump is the practical pair; here ±1 is one profile and
      the coarse jump a quarter of the axis.
    - **One sidebar entry needs a composition cell.** Two `mo.sidebar` calls stack
      as two separate blocks, which is why the slider cell and the buttons cell
      each render nothing and a third cell composes them into a single
      `mo.vstack(...)`. The split is for reactivity; the composition is for looks.
    - **No sidebar wanted?** Render that same stack from an ordinary cell — the
      block, the shared value and the reactivity are unchanged; the sidebar is
      only a placement choice.
    """)
    return


if __name__ == "__main__":
    app.run()
