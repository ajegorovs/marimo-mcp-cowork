# Fallbacks and Limits

Applies to marimo-inspect 0.3.x / marimo 0.24.x.

What the MCP surface does not cover, and the intentional ways around it.

## Output coverage

The code-mode snapshot exposes ONE main output per cell plus console events —
not every frontend UI registration. If something rendered in the notebook is
missing from `get_cell_outputs`, inspect the cell's variables instead.

## Widget value shape

`set_ui_value` expects a widget-specific JSON value shape; a shape the element
cannot accept is refused before anything is applied, and the element's own value
is read back before the call returns (see
`workflow://marimo-inspect/co-work-loop` §5 for the per-widget shapes). What is
*not* awaited is the reactive re-run of dependent cells — confirm its effects
with `get_variables` or `get_cell_outputs`.

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

## Dependency graph is whole-notebook

`get_dependency_graph` always returns the **full** graph. `cell_id` and `depth`
are refused (`reason: unsupported_argument`, nothing is read) rather than
accepted-and-ignored; walk `cells[].parent_cell_ids` / `child_cell_ids` for a
neighbourhood yourself.

## Session binding needs a session-stable client

`session_id`/`server_url` are omittable only where the client keeps **one MCP
session across calls** — the binding is server-side state keyed by that
session's identity. An `mcp`-SDK-based client qualifies. fastmcp's own `Client`
does not (pinned fastmcp 4.0.3: a fresh MCP session per request, over stdio and
HTTP alike), so its argument-less calls fail with "no active session bound" even
though the bind returned success. Pass both arguments explicitly on such
clients; they always win. See `workflow://marimo-inspect/co-work-loop` §1.

## Version pin

marimo is pinned to 0.24.x because private APIs are used. Do not widen the
bound without running the live suite.
