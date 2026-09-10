"""TraceScrubber widget: view one 1-D trace (frame) at a time.

A reusable [anywidget](https://anywidget.dev) that steps through the rows of a
2-D array — e.g. a time series of profiles (echo/velocity per time step) —
and draws one row as a line chart on a canvas, with a slider plus prev/next and
percent-jump buttons:

```python
import marimo as mo
from marimo_inspection import TraceScrubber

scrub = TraceScrubber().update(
    x=gate_depths_mm,
    values=profiles,          # (n_frames, n_points)
    times=time_s,
    title="Browse **profiles** over time",
    y_lo=0.0, y_hi=2000.0,    # optional fixed y-range (stable axis)
)
scrub
```

Unlike ``mo.ui.*`` (a widget and a reader of its ``.value`` must live in
separate cells), this is self-contained: the scrubber and the chart it shows
live in one component. ``index`` is synced, so bridging it to ``mo.state``
drives downstream cells.
"""

from __future__ import annotations

from collections.abc import Sequence

import anywidget
import numpy as np
import traitlets

from marimo_inspection.widgets._base import load_esm


class TraceScrubber(anywidget.AnyWidget):
    """Step through rows of a (frames, points) array, one line chart at a time.

    The full frame set is shipped to the front-end once (rows of floats);
    redraws for scrubbing happen entirely in the browser, so moving the slider
    never round-trips the kernel.

    ``index`` is synced. Payloads are pre-processed in ``update()``: x
    positions, per-frame values, optional per-frame time labels, an optional
    fixed y-range (``y_lo``/``y_hi``) and the jump percentages for the
    ``±pct`` buttons.
    """

    index = traitlets.Int(0).tag(sync=True)
    title = traitlets.Unicode("").tag(sync=True)
    x = traitlets.List(trait=traitlets.Float()).tag(sync=True)
    frames = traitlets.List().tag(sync=True)  # rows of floats, one per frame
    times = traitlets.List(trait=traitlets.Float()).tag(sync=True)
    y_lo = traitlets.Float(0.0).tag(sync=True)
    y_hi = traitlets.Float(1.0).tag(sync=True)
    y_fixed = traitlets.Bool(False).tag(sync=True)
    pcts = traitlets.List(trait=traitlets.Float(), default_value=[0.01, 0.1]).tag(
        sync=True
    )
    _esm = load_esm("trace_scrubber.js")

    def update(
        self,
        x: Sequence[float] | np.ndarray,
        values: np.ndarray | Sequence[Sequence[float]],
        times: Sequence[float] | np.ndarray | None = None,
        title: str = "",
        y_lo: float | None = None,
        y_hi: float | None = None,
        pcts: Sequence[float] = (0.01, 0.1),
    ) -> TraceScrubber:
        """Load one dataset; clamp ``index``. Mutates in place, returns self.

        ``values`` must be 2-D ``(n_frames, n_points)``; ``x`` its length
        ``n_points`` (or a scalar-trick: length-1 arrays are broadcast).
        ``times`` (optional) must match ``n_frames`` and labels each frame on
        the scrub line. Passing both ``y_lo`` and ``y_hi`` pins the y-axis to
        that fixed range; passing neither lets the front-end autorange per
        frame.
        """
        arr = np.asarray(values, dtype=float)
        if arr.ndim != 2:
            raise ValueError("values must be 2-D (n_frames, n_points)")
        xv = np.asarray(x, dtype=float)
        if xv.size != arr.shape[1]:
            # tolerate scalars / single-point "x" for constant-x traces
            if xv.size == 1:
                xv = np.full(arr.shape[1], xv.item())
            else:
                raise ValueError(
                    f"x has {xv.size} points but values has {arr.shape[1]}"
                )
        self.x = [float(v) for v in xv]
        self.frames = [[float(v) for v in row] for row in arr]
        if times is None:
            self.times = []
        else:
            tv = np.asarray(times, dtype=float)
            if tv.size != arr.shape[0]:
                raise ValueError(
                    f"times has {tv.size} entries but values has {arr.shape[0]} frames"
                )
            self.times = [float(v) for v in tv]
        self.pcts = [float(p) for p in pcts]
        if y_lo is not None and y_hi is not None:
            self.y_lo = float(y_lo)
            self.y_hi = float(y_hi)
            self.y_fixed = True
        else:
            self.y_fixed = False
        self.title = str(title)
        if arr.shape[0] == 0:
            self.index = 0
        else:
            self.index = max(0, min(int(self.index), arr.shape[0] - 1))
        return self

    @property
    def frame_count(self) -> int:
        """Number of frames currently loaded."""
        return len(self.frames)
