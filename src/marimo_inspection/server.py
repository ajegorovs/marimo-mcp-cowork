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
        calls can omit `session_id`.

        Use `set_active_session` to switch to a different notebook session,
        or pass `session_id` explicitly to any tool to override the bound
        session for a single call.

        Writes: use `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
        `edit_cell` refuses to overwrite a cell whose source changed since the
        agent last read it (read with `get_cell_map`/`get_cell_data` first).""",
    )

    # Register tools
    _register_tools(mcp)

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
