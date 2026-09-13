"""FastMCP server for marimo notebook inspection.

Creates and configures the MCP server with all inspection tools.
Supports both HTTP and STDIO transport.
"""

from __future__ import annotations

import logging

from fastmcp.server import FastMCP

logger = logging.getLogger(__name__)


def create_server(
    name: str = "marimo-inspection",
) -> FastMCP:
    """Create the marimo inspection MCP server.

    Args:
        name: Server name for MCP identification.

    Returns:
        Configured FastMCP server instance.
    """
    mcp = FastMCP(
        name,
        instructions="""Provides tools for inspecting AND editing live marimo notebooks:
        - List and switch active sessions (stateful binding)
        - Cell overview, full content, and outputs
        - Dependency graph traversal and variable ownership
        - Variable and table inspection
        - Error aggregation and static linting
        - Create/edit/run/delete cells (unified write surface)

        Start with `list_active_notebooks` to discover sessions. The first
        session found is auto-bound as the active session. The binding lives
        in two places: the MCP session's server-side state (visible when the
        client keeps one MCP session across calls — an `mcp`-SDK-based client
        does) and a process-global fallback consulted only when that state has
        nothing bound. Over stdio one process serves exactly one client, so
        the fallback carries the binding to every later call, including for a
        client that starts a fresh MCP session per request (fastmcp's own
        `Client` does, on the pinned fastmcp 4.0.3). Over HTTP/SSE one process
        serves many clients, so the fallback is served only while the process
        has seen a single client session; once a second client session
        appears, argument-less calls are refused with `reason:
        binding_ambiguous` — pass `session_id` and `server_url` explicitly
        there.

        Use `set_active_session` to switch to a different notebook session,
        or pass `session_id`/`server_url` explicitly to any tool to override
        the bound values for a single call.

        `list_active_notebooks` reports `provenance: "unknown"` and
        `owner: "unknown"` per **session** row, and `attached_client_count:
        null` in its summary, because marimo publishes neither a session's
        creator/owner nor a per-session client count. `binding is not
        ownership`: a bound session may still be held by a human's browser
        page. The summary counts are scoped: `total_notebooks` equals the
        truthful `session_count` (a connection-failure sentinel is a row, not a
        notebook, so read `result_row_count` for `len(notebooks)`), and
        `active_connections` is only a DEPRECATED alias for `session_count` (it
        was never a client count).

        For human co-work, prefer browser-first (marimo 0.24 edit mode, without
        an explicit `--session-ttl`): open the notebook so the page
        becomes/holds the main consumer connection for the session, then
        discover and bind it. `/sse` materialization is for headless agent-only
        work: without an explicit `--session-ttl`, closing that stream leaves
        the session an orphan that outlives it until a later connection takes
        it over, though a configured `--session-ttl` can reap that orphan. A
        human must take over and re-run the notebook before its widgets
        respond. A second distinct client joins the same kernel as a non-main,
        read-only consumer and a later reconnect can re-key the session id; run
        mode is out of scope. The re-key is separate from a page-vs-session
        divergence observed once, which is not diagnosed — neither the re-key
        nor any read-path explanation is confirmed as its cause, so do not
        assume one.

        Writes: use `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
        `edit_cell` refuses to overwrite a cell whose source changed since the
        agent last read it. Read the cell's full source with `get_cell_data`
        before editing it — a `get_cell_map` preview does NOT record the read
        baseline, so the first edit of a cell needs one `get_cell_data`.

        `run_cell(cell_id, mode=...)` takes `cell_id` as a cell ID **or cell
        name** (resolved like `ctx.cells`; `requested_cell_ids` always carries
        the resolved IDs, and `resolved_cell_id` reports the resolution).
        `mode="cell"` (default) queues just the target; `"descendants"` queues
        the target plus its kernel-graph descendants and refuses
        `reason: graph_unpopulated` (running nothing) when the target is not
        registered — e.g. on a fresh session, whose graph is empty; `"all"`
        queues every document cell in the notebook and requires `cell_id` to be
        empty (`reason: cell_id_not_allowed` otherwise), so an unreferenced cell
        and its widgets finally execute. Whatever the mode, the kernel may
        additionally run stale ancestors and autorun descendants outside the
        requested set, and the relative order of independent cells is
        unspecified. The response is per requested target
        (`cells[].runtime_state` / `.errors` / `.errors_readable`,
        `succeeded_cell_ids`, `failed_cell_ids`, `not_run_cell_ids`,
        `unverified_cell_ids`, `counts`): `succeeded` means `idle` with a
        readable, empty `errors` (`cells[].errors` is `null` when the channel
        could not be read — such a target is reported `not_run`/unverified,
        never succeeded), and `failed_cell_ids` covers the requested targets
        only. `status` is `ok` only when every requested target is idle,
        `partial` when any target failed or did not finish, and `error` for
        validation/planning/reporting failures (`cell_id_required`,
        `invalid_mode`, `cell_id_not_allowed`, `unknown_cell_ids`,
        `graph_unpopulated`, `planning_failed`, `reporting_failed`), each
        carrying a top-level `error` string plus the structured
        `status`/`reason`; a failing run reports `error`/`execution_error` +
        `stderr`.

        Widget interaction: `set_ui_value` sets a live UI element's value by
        its variable name. Value shapes are per widget and are never coerced
        (`dropdown` takes its option key inside a one-element list); a
        mismatched shape is refused with the corrected payload in
        `did_you_mean`, and the element's value is read back so `status: ok`
        means the widget actually moved. A `button`/`run_button` is the
        exception. Both expose a frontend click counter (`0` is the
        initialization sentinel, for which marimo processes no click), but
        their element value differs: a `button`'s is its `on_click` return
        (unchanged when the handler only sets state), while a `run_button` has
        no `on_click` and its value is set `True` on a click then reset
        `False` after its dependents run — so either can read unchanged for a
        click that landed. Those payloads report the counter evidence
        (`frontend_value_before`/`after`, `click_delivered`) and a tri-state
        `handler_invoked` — `false` for the sentinel, `true` when the counter
        moved to the submitted value, `null` when the counter already held it
        (unknown; never claimed either way) — plus `side_effects_verified:
        false` (the read-back never verifies the handler's arbitrary side
        effects; a `button`'s raising `on_click` is `reason: on_click_failed`
        with `handler_ran: true`, and a marker that cannot be attributed to
        the target is a generic `ui_update_failed`). It is a narrow widget tool
        and accepts NO source code.

        Lifecycle: `restart_kernel` closes the current kernel and
        re-materializes a fresh one, keeping the server process (and any open
        frontend page) alive. Use it only when the kernel itself is the
        problem — an imported package's source changed, a new dependency was
        installed, the kernel is wedged, or globals are poisoned. A notebook
        cell edit NEVER needs one: `edit_cell` + `run_cell` apply live. The
        restart discards all execution state (kernel globals, widget values),
        makes every cell stale, and can re-key a cell created in-session to a
        different cell id, so re-read the notebook and re-run cells
        afterwards; the change tracker is cleared, so cells report `needs_read`
        again. Success is point-in-time (`session_id_stable: false`,
        `session_verification: point_in_time`): the tool closes its own SSE
        stream, so a later browser reconnect can re-key the session and a
        configured session TTL can reap it — on `Invalid session id`, re-run
        `list_active_notebooks` and re-bind. It never reports success without
        confirming the expected session came back — `session_not_rematerialized`
        / `server_sessionless` mean the kernel was closed and no usable session
        was confirmed (the server may be at zero sessions). A transport failure
        on the restart POST reports `state_changed: null` (outcome unknown),
        and a 403 is `edit_required` (the endpoint is served in `edit` mode
        only).""",
    )

    # Register tools
    _register_tools(mcp)

    # Register static read-only documentation resources
    _register_resources(mcp)

    return mcp


def _register_tools(mcp: FastMCP) -> None:
    """Register all inspection tools with the MCP server."""
    from marimo_inspection.tools import (
        create_cell,
        delete_cell,
        edit_cell,
        get_cell_data,
        get_cell_map,
        get_cell_outputs,
        get_dependency_graph,
        get_errors,
        get_variables,
        lint_notebook,
        list_active_notebooks,
        restart_kernel,
        run_cell,
        set_ui_value,
    )
    from marimo_inspection.tools.session import set_active_session

    # Register each tool
    mcp.tool()(list_active_notebooks)
    mcp.tool()(get_cell_map)
    mcp.tool()(get_cell_data)
    mcp.tool()(get_cell_outputs)
    mcp.tool()(get_variables)
    mcp.tool()(get_dependency_graph)
    mcp.tool()(get_errors)
    mcp.tool()(lint_notebook)
    mcp.tool()(set_active_session)
    mcp.tool()(create_cell)
    mcp.tool()(edit_cell)
    mcp.tool()(run_cell)
    mcp.tool()(delete_cell)
    # set_ui_value is a write tool that mutates live widget state and is not
    # idempotent in the reactive sense (each call re-triggers dependent
    # re-runs), so it is annotated non-read-only, destructive, non-idempotent,
    # and not open-world (it mutates shared kernel state).
    mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    )(set_ui_value)
    # restart_kernel closes the session's kernel and discards all execution
    # state (globals, widget values, module caches), so it is annotated
    # non-read-only, destructive, non-idempotent (a second restart is a second
    # reset, even though it succeeds), and not open-world (it acts on one
    # already-bound local session).
    mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    )(restart_kernel)


def _register_resources(mcp: FastMCP) -> None:
    """Register the static read-only documentation resources.

    Three fixed URIs serve packaged Markdown verbatim. They are native MCP
    resources (not tools, not templates, not ResourcesAsTools): immutable
    operational guides an agent can read on demand.

    Annotation note (FastMCP 4.0.3): resource ``annotations`` is
    ``mcp.types.Annotations`` (``audience``/``priority``/``lastModified``
    only). The tool-only ``readOnlyHint``/``idempotentHint`` fields are not
    accepted here, so read-only-ness is signalled through the
    ``read-only``/``static`` tags and the descriptions.

    The Markdown is loaded lazily via ``importlib.resources`` (never a CWD
    relative path), so it resolves from the installed package.
    """
    from marimo_inspection.resources import load_resource_text

    @mcp.resource(
        "workflow://marimo-inspect/co-work-loop",
        name="Co-work loop",
        description=(
            "The MCP-first co-work loop on a live notebook: discover/bind, "
            "orient, read, write/run, interact, verify, lint — with exact "
            "tool names and the read-before-edit rule."
        ),
        mime_type="text/markdown",
        tags={"read-only", "static", "workflow"},
        version="1.0",
        annotations={"audience": ["assistant"]},
    )
    def co_work_loop() -> str:
        """Return the co-work loop workflow guide (Markdown)."""
        return load_resource_text("co-work-loop.md")

    @mcp.resource(
        "workflow://marimo-inspect/live-safety",
        name="Live-safety rules",
        description=(
            "Safety rules for editing a live kernel: read-before-edit, the "
            "needs_read/conflict recovery protocol, dependency checks before "
            "delete/merge, post-write verification, and widget visibility."
        ),
        mime_type="text/markdown",
        tags={"read-only", "static", "workflow"},
        version="1.0",
        annotations={"audience": ["assistant"]},
    )
    def live_safety() -> str:
        """Return the live-safety rules guide (Markdown)."""
        return load_resource_text("live-safety.md")

    @mcp.resource(
        "reference://marimo-inspect/fallbacks-and-limits",
        name="Fallbacks and limits",
        description=(
            "What the MCP surface does not cover and the intentional "
            "fallbacks: output coverage, widget value shape, frontend "
            "refresh, screenshots, server lifecycle, and the script hatch."
        ),
        mime_type="text/markdown",
        tags={"read-only", "static", "reference"},
        version="1.0",
        annotations={"audience": ["assistant"]},
    )
    def fallbacks_and_limits() -> str:
        """Return the fallbacks-and-limits reference (Markdown)."""
        return load_resource_text("fallbacks-and-limits.md")


def main() -> None:
    """Main entry point for the MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Marimo Inspection MCP Server")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio", "sse", "streamable-http"],
        default="http",
        help="Transport type (default: http)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address (http transport only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port number (http transport only)",
    )
    args = parser.parse_args()

    server = create_server()

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport=args.transport,
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
