import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import numpy as np
    import marimo as mo
    from marimo_inspection import ImagePreview

    return ImagePreview, np


@app.cell
def _(ImagePreview, np):
    # Build a few demo images (grayscale + color)
    rng = np.random.default_rng(42)
    frames = []
    names = []
    for i in range(5):
        a = np.zeros((120, 200, 3), dtype=np.uint8)
        a[..., 0] = 255 * ((i + 1) / 5)      # red ramp by index
        a[..., 1] = 255 * ((i) / 4)          # green ramp
        a[..., 2] = 120 + 80 * (i % 2)       # blue alt
        # a diagonal stripe to make movement obvious
        for row in range(120):
            a[row, (row + i * 15) % 200] = 255
        frames.append(a)
        names.append(f"frame-{i}.png")

    preview = ImagePreview().update(
        frames,
        names,
        title=f"Browse **{len(frames)}** images — slider or ◀/▶ to step.",
    )
    preview
    return


if __name__ == "__main__":
    app.run()
