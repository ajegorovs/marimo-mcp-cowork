import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")

with app.setup(hide_code=True):
    def _double(x):
        return x * 2


@app.cell
def _():
    import marimo as mo
    import polars as pl
    import altair as alt

    return alt, mo, pl


@app.cell
def _():
    value_a = 1
    value_b = 3
    value_c = value_a + value_b**2
    return (value_a, value_b, value_c)


@app.cell
def _(_double, value_c):
    doubled = _double(value_c)
    return (doubled,)


@app.cell
def _(doubled, pl):
    table = pl.DataFrame(
        {"x": [1, 2, 3], "y": [doubled, doubled + 1, doubled + 2]}
    )
    return (table,)


@app.cell(hide_code=True)
def _():
    raise ValueError("integration_test_error")
    return


if __name__ == "__main__":
    app.run()
