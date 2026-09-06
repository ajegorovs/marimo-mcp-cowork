import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    mo.md("# Function Plotting Showcase")
    return (mo,)


@app.cell
def _():
    import numpy as np

    return (np,)


if __name__ == "__main__":
    app.run()
