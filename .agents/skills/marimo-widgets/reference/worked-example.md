# Reference: worked example — `ImagePreview` and widget authoring

> Companion to `SKILL.md`. Read the skill first; this is the concrete worked
> example that the generic rules are drawn from.

## Canonical working widget: `ImagePreview`

Source of truth (in the package):

- `src/marimo_inspection/widgets/image_preview.py` — the Python side
- `src/marimo_inspection/widgets/js/image_preview.js` — the ESM front-end
- `tests/marimo_inspect/test_image_preview.py` — trait-side unit tests
- `notebooks/widget_demo.py` — a runnable demo notebook for it

It views **one image at a time** from a list (grayscale/RGB), rendered on a
canvas with a slider + prev/next buttons + per-image labels + an optional bold
title. It exhibits every pattern the skill calls out:

- thin synced traitlets (`index`, `title`, `images`, `shapes`, `names`), all
  `.tag(sync=True)`
- `_esm = load_esm("image_preview.js")` — real file, loaded at import
- `update(images, names, title=...)` mutates and returns `self`, clamping
  `index`
- payloads pre-processed on the Python side (flat pixel lists + shapes), not
  heavy objects
- free of `marimo` imports; the caller bridges via `.observe(...)` + `mo.state`
- `title` rendered WITH `innerHTML` + an escaped markdown-subset renderer, so
  `**bold**` works and HTML injection is stopped

### Why the title lives in the widget

A marimo cell auto-displays only its **last** expression. An intro
`mo.md("Browse …")` followed by a `preview` line is shadowed. So the widget
owns a `title` trait that renders above the canvas — the description can't be
lost. This is a general rule for "intro text next to a widget" cells.

## Minimal skeleton to copy

Python (`<name>.py`):

```python
import anywidget
import traitlets
from marimo_inspection.widgets._base import load_esm


class MyWidget(anywidget.AnyWidget):
    value = traitlets.Int(0).tag(sync=True)
    _esm = load_esm("my.js")

    def update(self, ...):  # optional: convenience mutating setter
        ...
        return self
```

JS (`js/my.js`):

```js
function render({ model, el }) {
  const root = document.createElement("div");
  // build DOM...
  function update() { /* read model.get('value'); mutate DOM */ }
  // listen: model.on("change:value", update)
  // wire controls: model.set('value', x); model.save_changes()
  el.appendChild(root);
  update();
}
export default { render };
```

Re-export from `widgets/__init__.py` and from the package `__init__.py`
(lazily). Add `tests/marimo_inspect/test_<name>.py`.

## Widgets to build next (ideas), in rough priority

1. **Before/After compare** — two images side by side (with a wipe divider or a
   single control image), for image-processing pair review. Traits: `image_a`,
   `image_b`, `position`.
2. **Click-to-inspect** — hover/click a pixel on a canvas, report normalized
   coords (`point` trait) and show a zoomed crop. Traits: `image`, `shape`,
   `point`.
3. **Batch picker / labeller** — step through items, tag each with a label,
   store annotations in a synced trait so marimo reads them.

## What the skill would update (future revisions)

- `title` → a richer caption/format trait once a second usage exists
- RGB vs RGBA alpha handling if a widget needs true 32-bit
- Arrow IPC deserialization — not yet exercised by any distributed widget
- A `_css` trait if a widget needs component-scoped global styles
- Add a build/test hook if we ever need a bundler (ruled out for now)

Keep this list short; only add when a real widget hits the boundary.