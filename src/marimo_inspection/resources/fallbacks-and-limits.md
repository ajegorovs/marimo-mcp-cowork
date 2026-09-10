# Fallbacks and Limits

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

What the MCP surface does not cover, and the intentional ways around it.

## Output coverage

The code-mode snapshot exposes ONE main output per cell plus console events —
not every frontend UI registration. If something rendered in the notebook is
missing from `get_cell_outputs`, inspect the cell's variables instead.

## Widget value shape

`set_ui_value` expects a widget-specific JSON value shape. Do not infer that a
scalar form works for every single-select widget; after setting a value, confirm
the effect with `get_variables` or `get_cell_outputs`. Its reactive re-run is
verified, not awaited.

## Frontend refresh

Structural edits (add, delete, reorder) may not refresh an already-open
frontend immediately. Reload the browser if the view looks stale.

## Screenshots

Not part of the MCP surface. Capturing the rendered notebook needs Playwright
or other browser tooling.

## Server and kernel lifecycle

Launching, restarting, or stopping the notebook server is a local/operator
action — there is no MCP tool for it, and no kernel-restart tool. A live
kernel also caches imported modules, so a package source change needs a
server/kernel restart to take effect.

## Script escape hatch

Arbitrary kernel probes and complex multi-operation CodeMode blocks remain a
deliberate script escape hatch: run `marimo._code_mode` via the scratchpad.
The MCP surface is intentionally narrow — there is no general execute /
arbitrary-code tool.

## Version pin

marimo is pinned to 0.24.x because private APIs are used. Do not widen the
bound without running the live suite.
