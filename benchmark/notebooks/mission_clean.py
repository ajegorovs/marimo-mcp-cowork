"""Disposable native-pair comparison fixture; copy before opening in an editor."""

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np

    return mo, np, plt


@app.cell
def _(mo):
    gain = mo.ui.slider(1, 4, value=2, step=1, label="gain")
    gain  # noqa: B018 - marimo renders the last expression
    return (gain,)


@app.cell
def _(np):
    input_vector = np.array([2, 4, 6, 8])
    return (input_vector,)


@app.cell
def _(gain, input_vector):
    processed = gain.value * input_vector
    return (processed,)


@app.cell
def _(plt, processed):
    fig, ax = plt.subplots()
    ax.plot(range(len(processed)), processed, marker="o")
    ax.set_title("processed signal")
    ax.set_xlabel("index")
    ax.set_ylabel("value")
    ax  # noqa: B018 - marimo renders the last expression
    return ax, fig


if __name__ == "__main__":
    app.run()
