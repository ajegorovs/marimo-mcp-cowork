# Custom widgets (anywidget) distributed with marimo-inspect

A small library of reusable **anywidget** components lives in
`src/marimo_inspection/widgets/` and ships inside the `marimo-inspect` package,
so any consumer repo can import them without copying code — exactly like the
MCP server and Python client are installed via `uv add`.

Unlike marimo's built-in `mo.ui.*`, an anywidget is **self-contained**: its own
small JavaScript front-end can bundle a control *and* its output (e.g. a scrubber
next to the image it scrubs), sidestepping marimo's rule that a `mo.ui` widget
and a reader of its `.value` live in separate cells.

## How a widget is structured

```
src/marimo_inspection/widgets/
├── __init__.py         # re-exports the public widget classes
├── _base.py            # load_esm() reads a front-end module into _esm
├── <widget>.py         # Python side: traitlets + anywidget.AnyWidget subclass
└── js/<widget>.js      # the widget's ESM front-end, as a real file
```

The public classes are also re-exported lazily from
`marimo_inspection/__init__.py` (via `__getattr__`), so the base package keeps
its no-extra-dependency import path until a widget is actually requested:

```python
from marimo_inspection import ImagePreview          # lazy re-export
from marimo_inspection.widgets import ImagePreview  # explicit
```

## Conventions (follow these when adding a widget)

1. **`widgets/js/<name>.js`** — real ESM file, `export function render({model, el})`,
   ending with `export default { render };`.
   - `model.get/set/save_changes` syncs a `.tag(sync=True)` traitlet.
   - Listen with `model.on("change:<trait>", h)`; use `AbortController` for DOM
     listeners (they are **not** auto-cleaned, unlike `model.on`).
   - The `initialize`/`render` split is only needed for one-time per-instance
     setup (e.g. a shared timer); render-only is the common case.
2. **`widgets/<name>.py`** — subclass `anywidget.AnyWidget`, declare thin synced
   traitlets, set `_esm = load_esm("<name>.js")` (from `widgets/_base`).
   - `_base.load_esm` reads the JS file at import so JS stays a real, editable,
     lintable file.
3. **Re-export** the class from `widgets/__init__.py` and the package
   `__init__.py` (`+` __all__), using the same lazy `__getattr__` pattern.
4. **Keep widgets free of marimo imports** and thin. Send pre-processed
   payloads (flat lists, small dicts) rather than heavy objects. The reactive
   link to `mo.state` / `mo.ui.anywidget` is a notebook concern — not the
   widget's.
5. **Unit-test the Python trait side** (`tests/marimo_inspect/test_<widget>.py`):
   the flattening logic and index clamping are pure Python and testable without
   a kernel or browser. JS rendering is verified in the browser.

## Reactive bridging (the notebook's job)

A widget becomes a reactive notebook citizen by bridging a traitlet to
`mo.state` — a two-cell pattern:

```python
# Cell creating the widget
preview = ImagePreview().update(images, names)
get_i, set_i = mo.state(preview.index)          # init from current value
preview.observe(lambda _: set_i(preview.index), names=["index"])
preview  # display the widget
```

```python
# Downstream cell re-runs when index changes
i = get_i()
```

Use `.observe(...names=[...])` to react to only the traits you care about; use
`mo.ui.anywidget(widget)` when you want every synced trait as one `.value` dict.
Pick one strategy per widget. For programmatic control from a scratchpad, assign
the trait directly: `preview.index = 3`.

## Registry of distributed widgets

| Widget | Purpose | Package import |
| --- | --- | --- |
| [`ImagePreview`](../src/marimo_inspection/widgets/image_preview.py) | View **one image at a time** from a list (grayscale/RGB), canvas + slider + prev/next + labels. Optional `title=` renders inside the widget (bold via `**…**`, code via backticks) | `from marimo_inspection import ImagePreview` |

**Suggested next widgets** (common tasks we frequently reach for):
- **Side-by-side before/after compare** (a/b toggle + wipe slider) — image
  processing pairs.
- **Click-to-inspect** canvas viewer (hover → pixel value / zoom region).
- **Grouped batch labeler** — step through items with per-item tags saved back
  to a traitlet (so marimo reads the annotation).

## Dependencies

`anywidget`, `traitlets`, and `pillow` are regular dependencies (so widgets
import out of the box), but the base package lazy-imports them. PIL is only
pulled at runtime by `ImagePreview` when given paths/bytes; numpy arrays need
no PIL.