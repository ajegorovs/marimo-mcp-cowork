import marimo

__generated_with = "0.24.0"
app = marimo.App()

with app.setup(hide_code=True):
    import json
    import datetime
    def hello(): return 'world'


@app.cell
def _():
    import altair as alt
    import image_processing_lib as ipl
    import marimo as mo
    import polars as pl
    import numpy as np

    return alt, ipl, mo, np, pl


@app.cell
def _(np):
    arr = np.array([1,2,3])
    arr
    return


@app.cell
def _():
    value_a = 1
    value_b = 3
    # return value_c as a sum of val a + sqr(val b)
    value_c = value_a + value_b**2
    value_c
    return


@app.cell
def _(mo):
    mo.md("""
    # Test notebook

    A smoke test to verify the `uv` + marimo environment works.

    It exercises the three marimo features this repo relies on:

    - `mo.ui` widgets (sliders)
    - reactive dataflow (dependent cells re-run when the slider moves)
    - interop with the packaged `image_processing_lib` building blocks

    Drag the slider below and watch the dependent cells update live.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Reactive computation

    Everything below is a dependent cell of `value`.
    """)
    return


@app.cell
def _(mo):
    value = mo.ui.slider(0, 100, value=50, label="value")
    value
    return (value,)


@app.cell
def _(mo, value):
    squared = value.value**2
    mo.md(f"**value** = `{value.value}`  →  **squared** = `{squared}`")
    return (squared,)


@app.cell
def _(mo):
    mo.md("""
    ## Library interop

    Confirm the packaged building-block library is importable from a notebook.
    """)
    return


@app.cell
def _(ipl):
    version = ipl.__version__
    version
    return


@app.cell
def _(alt, mo, pl, squared, value):
    mo.md("## Reactive chart\n\nThe bar heights track the slider (reactive dataflow).")
    chart = (
        alt.Chart(
            pl.DataFrame({"metric": ["value", "squared"], "amount": [value.value, squared]})
        )
        .mark_bar()
        .encode(x="metric", y="amount")
        .properties(height=200)
    )
    chart
    return


@app.cell
def _():
    import polars as pl
    import altair as alt

    # Create data for y = 0.5x^2 + x on interval [1, 25]
    data = pl.DataFrame({
        "x": range(1, 26),
        "y": [0.5 * x**2 + x for x in range(1, 26)]
    })

    # Create line chart
    chart = (
        alt.Chart(data)
        .mark_line(point=True)
        .encode(
            x=alt.X("x:Q", title="x", scale=alt.Scale(domain=[1, 25])),
            y=alt.Y("y:Q", title="y = 0.5x² + x"),
            tooltip=["x", "y"]
        )
        .properties(
            title="y = 0.5x² + x on interval [1, 25]",
            width=600,
            height=400
        )
        .configure_axis(
            grid=False
        )
    )

    chart
    return alt, pl


@app.cell(hide_code=True)
def _():
    return


@app.cell(hide_code=True)
def _():
    raise ValueError('integration_test_error')
    return


@app.cell(hide_code=True)
def _():
    raise ValueError('integration_test_error')
    return


@app.cell(hide_code=True)
def _():
    raise ValueError('integration_test_error')
    return


if __name__ == "__main__":
    app.run()
