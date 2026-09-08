"""Tests for the Python-side of the ImagePreview anywidget.

These do not need a marimo kernel or a browser. They test the observable
Python behaviour: the synced traitlets the widget reads and the ``update``
flattening/clamping logic that would otherwise be exercised in the browser.
"""

from __future__ import annotations

import numpy as np
import pytest

from marimo_inspection.widgets import ImagePreview


def test_flattens_grayscale():
    """2-D grayscale input stacks a flat pixel list and a [h, w] shape."""
    p = ImagePreview().update([np.zeros((4, 5), dtype=np.uint8)])
    assert p.shapes == [[4, 5]]
    assert p.images == [[0] * 20]
    assert p.names == ["0"]
    assert p.index == 0


def test_rgb_is_interleaved():
    """3-D input (h, w, channels) is flattened as interleaved RGB."""
    img = np.zeros((2, 3, 3), dtype=np.uint8)
    img[0, 0] = [10, 20, 30]
    p = ImagePreview().update([img])
    assert p.shapes == [[2, 3]]
    flat = p.images[0]
    assert len(flat) == 2 * 3 * 3
    assert flat[0:3] == [10, 20, 30]


def test_uses_given_names():
    p = ImagePreview().update(2 * [np.zeros((2, 2))], names=["a", "b"])
    assert p.names == ["a", "b"]
    assert p.shapes == [[2, 2], [2, 2]]


def test_accepts_title():
    p = ImagePreview().update(
        [np.zeros((2, 2))], names=["x"], title="Browse 1 image"
    )
    assert p.title == "Browse 1 image"
    p.update([])
    assert p.title == ""  # empty update clears it


def test_empty_update_clears_and_resets_index():
    p = ImagePreview().update([np.zeros((2, 2))])
    p.update([])
    assert p.images == []
    assert p.shapes == []
    assert p.names == []
    assert p.index == 0


def test_update_clamps_index_to_list_length():
    p = ImagePreview()
    p.index = 10
    p.update(3 * [np.zeros((2, 2))])
    assert p.index == 0


def test_flatten_accepts_plain_lists():
    p = ImagePreview().update([[0, 0, 0, 0]])
    assert p.shapes == [[1, 4]]
    assert p.images == [[0, 0, 0, 0]]


def test_missing_file_path_raises():
    with pytest.raises(OSError):
        ImagePreview().update(["/nonexistent-nope-12345.png"])