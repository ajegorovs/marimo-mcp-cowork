"""Packaged static Markdown documents served as read-only MCP resources.

The Markdown files in this package are the source of truth for the three
native MCP resources the server publishes. They are loaded lazily via
``importlib.resources`` so that importing this package costs nothing at
import time and resolves from the *installed* package (wheel/editable/zip),
never a path relative to the working directory.
"""

from __future__ import annotations

from importlib.resources import files

#: URI -> packaged filename, in registration order.
RESOURCE_FILES: dict[str, str] = {
    "workflow://marimo-inspect/co-work-loop": "co-work-loop.md",
    "workflow://marimo-inspect/live-safety": "live-safety.md",
    "reference://marimo-inspect/fallbacks-and-limits": "fallbacks-and-limits.md",
}


def load_resource_text(filename: str) -> str:
    """Read a packaged Markdown resource by filename.

    Args:
        filename: Name of the Markdown file inside this package.

    Returns:
        The file's UTF-8 text.
    """
    return files(__package__).joinpath(filename).read_text(encoding="utf-8")
