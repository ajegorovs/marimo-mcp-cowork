---
name: marimo-widgets
description: >-
  Author reusable custom UI components (anywidget) that marimo can render and
  drive, and register them for distribution with the marimo-inspect package
  (src/marimo_inspection/widgets/). Use when the user wants a custom widget,
  a preview/compare/picker control, or an interactive component that bundles a
  control AND its output (e.g. a scrubber next to the image it scrubs).
---

# Draft skill — authoring custom anywidget components for marimo

> **STATUS: DRAFT.** Written from first-hand experience building the
> `ImagePreview` widget (2026). It is a living reference, not gospel: treat
> every rule below as a good default to *challenge* when a concrete need
> shows it's wrong. Improve this skill when an opportunity appears (a second
> widget, a marimo version bump, a found gotcha).

## What a widget is

An **anywidget** pairs Python traitlets with a small ESM (plain) JavaScript
front-end, so a single component can bundle a *control* **and** its *output*.
That's the marimo-superpower here: `mo.ui.*` requires the widget and a reader
of its `.value` to live in **separate** cells; an anywidget does not — the
slider and the image it scrubs can be one cell.

Distributed widgets live in `src/marimo_inspection/widgets/` so consumers
install them via `uv add marimo-inspect` — no copying.

## Layout (one widget per module)

```
src/marimo_inspection/widgets/
├── __init__.py         # re-exports the public widget classes
├── _base.py            # load_esm() reads js/<name>.js into _esm (cached)
├── <widget>.py         # Python: traitlets + anywidget.AnyWidget subclass
└── js/<widget>.js      # the widget's ESM front-end, as a real file
```

Public classes are ALSO re-exported lazily from `marimo_inspection/__init__.py`
via `__getattr__` so the base package stays dependency-light:
`from marimo_inspection import ImagePreview`.

## Authoring steps

1. **`js/<name>.js`** — real ESM: `function render({ model, el }) { ... }`,
   `export default { render };`.
   - `model.get('t')` / `model.set('t', v)` + `model.save_changes()` syncs a
     `.tag(sync=True)` traitlet bidirectionally.
   - Listen: `model.on("change:<trait>", h)`. `model.on` is auto-cleaned when
     the view is removed; DOM `addEventListener` is NOT — clean with
     `AbortController`.
   - The `initialize`/`render` split is only for one-time per-instance setup
     (timers, shared connections). Render-only is the common case.
2. **`<widget>.py`** — subclass `anywidget.AnyWidget`; declare thin synced
   traitlets; `_esm = load_esm("<name>.js")`.
   - Prefer pre-processed payloads (flat pixel lists, small dicts, base64)
     over shipping heavy objects. Send the front-end only what it needs.
   - **Keep the widget free of marimo imports** — reusable/testable. The
     reactive bridge to `mo.state` is the caller's job, not the widget's.
3. **Re-export** from `widgets/__init__.py` AND the package `__init__.py`
   (lazy). Keep both in sync.
4. **Unit-test the Python trait-side**:
   `tests/marimo_inspect/test_<widget>.py` — flattening, index-clamping,
   trait defaults. No kernel or browser needed. JS rendering is verified in
   the browser by the user, not by tests.
5. Verify: `uv run --extra test ruff check src tests/...`,
   `.venv/bin/marimo check notebooks/<demo>.py`.

## Reactive bridging (the caller's job)

```python
preview = ImagePreview().update(images, names)
get_i, set_i = mo.state(preview.index)  # init from CURRENT trait
preview.observe(lambda _: set_i(preview.index), names=["index"])
preview
```
Downstream cell reads `i = get_i()`. Use `.observe(names=[...])` for precision;
`mo.ui.anywidget(widget)` to observe all synced traits at once. Pick ONE per
widget. Prefer specific named traits + `.observe` (do not use
`change["new"]` or `allow_self_loops=True`).

## Known gotchas (learned, to keep)

- **A cell only auto-displays its LAST expression.** An intro `mo.md(...)`
  followed by a `preview` line is shadowed. Put the description INTO the
  widget as a `title`: trait that renders above the content.
- **`textContent` shows `**bold**` literally.** Use `innerHTML` with a small
  escaped markdown renderer (bold + backtick code) for user-facing title text.
- **JS is cached at import (via `load_esm`).** Edits to `js/*.js` don't appear
  on cell re-run — you must restart the kernel/server to reload.
- **marimo does not render traditional Jupyter widgets.** If a library exposes
  a `.widget` (jscatter, ipyvolume, …), display that anywidget instance, not
  the top object.
- **marimo clips cell output ~610px** and scrolls; manage your own scrolling
  inside a fixed-height container to avoid it.
- Prefer reducing data on the Python side (aggregate/filter/sample) over
  shipping everything; for >2k rows of tabular data, send Arrow IPC bytes
  (`traitlets.Any().tag(sync=True)`) + deserialize in JS with `@uwdata/flechette`.
- CDN deps: `import ... from "https://esm.sh/pkg@ver"` — avoids a build step.

## Progression

Before writing a new custom widget, check the decision tree:
- Tiny static HTML → `_display_()` or `mo.Html`
- Built-in control as-is → `mo.ui.*`
- Custom output / interaction → **anywidget**

Wrap small pieces that compose. Do not over-engineer.

See `reference/` for worked examples. Ask a human if a rule here feels wrong
for the specific widget being built — this skill is explicitly a draft.