"""Custom anywidget components distributed with marimo-inspect.

Each widget is a self-contained web component (Python traitlets + a small ESM
front-end under ``js/``) that marimo can render and drive. Components here
deliberately do **not** import marimo or the marimo-inspect MCP server, so they
stay reusable/testable and the reactive wiring to ``mo.state`` (or
``mo.ui.anywidget``) is left to the notebook.

This subpackage is **not** imported by ``marimo_inspection.__init__``; import
it explicitly so the base package keeps its no-extra-dependency import path:

    from marimo_inspection import ImagePreview            # lazy re-export
    from marimo_inspection.widgets import ImagePreview    # explicit

One widget per module; front-end code in ``js/<name>.js`` loaded via the
``_base.load_esm`` helper.
"""

from marimo_inspection.widgets.image_preview import ImagePreview
from marimo_inspection.widgets.trace_scrubber import TraceScrubber

__all__ = ["ImagePreview", "TraceScrubber", "load_esm"]


# Re-export for convenience without forcing an import at package import time.
def load_esm(name: str) -> str:
    """Read a front-end ESM module (``js/<name>``) as a string (thin wrapper)."""
    from marimo_inspection.widgets._base import load_esm as _load

    return _load(name)