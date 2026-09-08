"""ImagePreview widget: view one image from a list at a time.

A reusable [anywidget](https://anywidget.dev) that lets you step through a list
of images — grayscale or RGB — and view a single one large on a canvas, with a
slider plus prev/next controls and optional per-image labels.

```python
import marimo as mo
from marimo_inspection import ImagePreview

preview = ImagePreview().update(images, names)  # numpy arrays or paths
preview
```

Unlike ``mo.ui.*`` (a widget and a reader of its ``.value`` must live in
separate cells), this is self-contained: the picker and the image it shows live
in one component, so a single cell drives and reads it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

import anywidget
import numpy as np
import traitlets

from marimo_inspection.widgets._base import load_esm


class ImagePreview(anywidget.AnyWidget):
    """View one image at a time from a list (grayscale/RGB), canvas-based.

    The list, the selection index, and the currently displayed image all live
    in one widget, so a single cell shows the full browsing UI. ``index`` is
    synced, so bridging it to ``mo.state`` drives downstream cells.
    """

    index = traitlets.Int(0).tag(sync=True)
    title = traitlets.Unicode("").tag(sync=True)
    images = traitlets.List().tag(sync=True)  # flat pixel lists (gray or RGB)
    shapes = traitlets.List().tag(sync=True)  # [h, w] per image
    names = traitlets.List(
        trait=traitlets.Unicode(), default_value=[]
    ).tag(sync=True)
    _esm = load_esm("image_preview.js")

    def update(
        self,
        images: Iterable,
        names: Iterable[str] | None = None,
        title: str = "",
    ) -> ImagePreview:
        """Load ``images`` and optional ``names``/``title``; clamp ``index``.

        A non-empty ``title`` is rendered *inside* the widget, above the image —
        so the intro line is not lost to marimo's "last expression only" cell
        output. Each entry may be a 2-D (grayscale) or 3-D (H, W, channel)
        numpy array, a list of pixel values, a path-string/bytes to an image
        file, or a PIL image. Pixels are flattened to 1-D lists for the
        front-end. Mutates in place and returns ``self``.
        """
        self.title = title
        images = list(images)
        if not images:
            self.images = []
            self.shapes = []
            self.names = []
            if self.index != 0:
                self.index = 0
            return self

        flat, shapes = _flatten(images)
        self.images = flat
        self.shapes = shapes
        self.names = [str(n) for n in (names or range(len(images)))]
        if self.index >= len(images):
            self.index = 0
        return self


def _flatten(images: Sequence[object]) -> tuple[list, list]:
    """Normalize a sequence of images to (flat_pixel_lists, shapes).

    Returns flat 1-D lists (single-channel grayscale, otherwise interleaved
    RGB) and per-image ``[height, width]`` shapes for the front-end.
    """
    flat: list[list] = []
    shapes: list[list] = []
    for img in images:
        arr = _to_array(img).astype(np.uint8)
        if arr.ndim == 1:  # flat list of pixel values -> a single row
            arr = arr.reshape(1, -1)
        h, w = arr.shape[:2]
        flat.append(arr.ravel().tolist())
        shapes.append([int(h), int(w)])
    return flat, shapes


def _to_array(img: object) -> np.ndarray:
    """Coerce one image source to a numpy array (grayscale or RGB)."""
    if isinstance(img, np.ndarray):
        return img
    if isinstance(img, (str, bytes, os.PathLike)) or hasattr(img, "read"):
        # File path / bytes / buffer: load via PIL (RGBA/RGB -> RGB).
        from PIL import Image

        with Image.open(img) as im:
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
            return np.asarray(im)
    return np.asarray(img)