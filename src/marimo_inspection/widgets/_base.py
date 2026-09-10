"""Shared helpers for authoring anywidget components distributed with marimo-inspect."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_JS_DIR = Path(__file__).resolve().parent / "js"


@cache
def load_esm(name: str) -> str:
    """Read a front-end ESM module (``js/<name>``) as a string.

    Widget classes assign the result to ``_esm`` at import time, so the JS
    lives in real files (editable, lintable) while ``_esm`` stays a string at
    runtime.
    """
    return (_JS_DIR / name).read_text(encoding="utf-8")
