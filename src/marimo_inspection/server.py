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
        session found is auto-bound as the active session — subsequent tool
        calls can omit both `session_id` and `server_url`.

        Use `set_active_session` to switch to a different notebook session,
        or pass `session_id`/`server_url` explicitly to any tool to override
        the bound values for a single call.

        Writes: use `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
        `edit_cell` refuses to overwrite a cell whose source changed since the
        agent last read it (read with `get_cell_map`/`get_cell_data` first).

        Widget interaction: `set_ui_value` sets a live UI element's value by
        its variable name. Value shapes are per widget and are never coerced
        (`dropdown` takes its option key inside a one-element list); a
        mismatched shape is refused with the corrected payload in
        `did_you_mean`, and the element's value is read back so `status: ok`
        means the widget actually moved. It is a narrow widget tool and
        accepts NO source code.""",
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
