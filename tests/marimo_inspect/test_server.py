"""Tests for server creation and tool registration.

Verifies that:
- The FastMCP server is created correctly
- All 8 tools are registered
- Tools have the correct names and signatures
- Server creation does not require a network connection
"""

from __future__ import annotations

from fastmcp.client import Client

# -------------------------------------------------------------------
# Server creation tests
# -------------------------------------------------------------------


class TestCreateServer:
    """Test create_server() factory function."""

    def test_server_creation(self):
        """Server creation succeeds without network."""
        from marimo_inspection.server import create_server

        server = create_server()
        assert server is not None

    def test_server_name(self):
        """Server has the correct default name."""
        from marimo_inspection.server import create_server

        server = create_server()
        assert server.name == "marimo-inspection"

    def test_server_name_override(self):
        """Server accepts a custom name."""
        from marimo_inspection.server import create_server

        server = create_server(name="custom-server")
        assert server.name == "custom-server"

    def test_server_instructions(self):
        """Server has helpful instructions."""
        from marimo_inspection.server import create_server

        server = create_server()
        assert server.instructions is not None
        assert "list_active_notebooks" in server.instructions
        assert "inspect" in server.instructions.lower()


# -------------------------------------------------------------------
# Tool registration tests
# -------------------------------------------------------------------


EXPECTED_TOOLS = [
    "list_active_notebooks",
    "get_cell_map",
    "get_cell_data",
    "get_cell_outputs",
    "get_variables",
    "get_dependency_graph",
    "get_errors",
    "lint_notebook",
    "set_active_session",
    "create_cell",
    "edit_cell",
    "run_cell",
    "delete_cell",
    "set_ui_value",
]


class TestToolRegistration:
    """Test that all tools are registered correctly."""

    async def test_all_tools_registered(self, mcp_server):
        """All expected tools are registered."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}

            for expected in EXPECTED_TOOLS:
                assert expected in tool_names, (
                    f"Tool '{expected}' not found in registered tools: "
                    f"{sorted(tool_names)}"
                )

    async def test_tool_count(self, mcp_server):
        """Exactly the expected number of tools are registered."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            assert len(tools) == 14

    async def test_tools_have_descriptions(self, mcp_server):
        """All tools have non-empty descriptions."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            for tool in tools:
                assert tool.description, f"Tool '{tool.name}' has no description"
                assert len(tool.description) > 20, (
                    f"Tool '{tool.name}' description is too short"
                )

    async def test_list_active_notebooks_signature(self, mcp_server):
        """list_active_notebooks has optional server_url param."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            list_tool = next(t for t in tools if t.name == "list_active_notebooks")
            assert list_tool.name == "list_active_notebooks"

    async def test_get_cell_map_signature(self, mcp_server):
        """get_cell_map signature (session_id optional)."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            cell_map_tool = next(t for t in tools if t.name == "get_cell_map")
            assert cell_map_tool.name == "get_cell_map"

    async def test_get_cell_data_signature(self, mcp_server):
        """get_cell_data requires session_id, optional cell_ids."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            cell_data_tool = next(t for t in tools if t.name == "get_cell_data")
            assert cell_data_tool.name == "get_cell_data"

    async def test_get_cell_outputs_signature(self, mcp_server):
        """get_cell_outputs requires session_id, optional cell_ids."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            cell_outputs_tool = next(t for t in tools if t.name == "get_cell_outputs")
            assert cell_outputs_tool.name == "get_cell_outputs"

    async def test_get_variables_signature(self, mcp_server):
        """get_variables requires session_id, optional variable_names."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            variables_tool = next(t for t in tools if t.name == "get_variables")
            assert variables_tool.name == "get_variables"

    async def test_get_dependency_graph_signature(self, mcp_server):
        """get_dependency_graph requires session_id, optional cell_id/depth."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            dep_graph_tool = next(t for t in tools if t.name == "get_dependency_graph")
            assert dep_graph_tool.name == "get_dependency_graph"

    async def test_get_errors_signature(self, mcp_server):
        """get_errors requires session_id."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            errors_tool = next(t for t in tools if t.name == "get_errors")
            assert errors_tool.name == "get_errors"

    async def test_get_errors_description_scopes_per_cell_fields(self, mcp_server):
        """The exposed get_errors description names the per-cell field paths.

        The consumer-visible MCP description must say that the error channels
        live on each entry of ``cells[]`` and that the top-level fields are
        summary-only; otherwise a caller can misread the payload hierarchy.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            errors_tool = next(t for t in tools if t.name == "get_errors")
            description = errors_tool.description or ""

            # Per-cell channels must carry their explicit path.
            assert "cells[].structured_errors" in description
            assert "cells[].console_stderr" in description
            assert "cells[].console_exception_evidence" in description

            # The top-level summary fields must be named and marked as such.
            assert "top-level" in description.lower()
            assert "has_console_exception" in description
            assert "total_console_exception_cells" in description

    async def test_lint_notebook_signature(self, mcp_server):
        """lint_notebook signature (session_id optional)."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            lint_tool = next(t for t in tools if t.name == "lint_notebook")
            assert lint_tool.name == "lint_notebook"

    async def test_set_active_session_signature(self, mcp_server):
        """set_active_session accepts session_id."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            sas_tool = next(t for t in tools if t.name == "set_active_session")
            assert sas_tool.name == "set_active_session"

    async def test_set_ui_value_signature(self, mcp_server):
        """set_ui_value accepts variable_name + value (optional session)."""
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            uv_tool = next(t for t in tools if t.name == "set_ui_value")
            props = set(uv_tool.input_schema.get("properties", {}))
            assert uv_tool.name == "set_ui_value"
            assert "variable_name" in props
            assert "value" in props
            assert "session_id" in props
            assert "server_url" in props


# -------------------------------------------------------------------
# CLI entry point tests (no transport started)
# -------------------------------------------------------------------


class TestMainEntrypoint:
    """Test the main() function signature (without actually running)."""

    def test_main_function_exists(self):
        """main() is importable."""
        from marimo_inspection.server import main

        assert callable(main)

    def test_create_server_function_exists(self):
        """create_server() is importable."""
        from marimo_inspection.server import create_server

        assert callable(create_server)

    def test_create_server_returns_fastmcp(self):
        """create_server() returns a FastMCP instance."""
        from fastmcp import FastMCP

        from marimo_inspection.server import create_server

        server = create_server()
        assert isinstance(server, FastMCP)


# -------------------------------------------------------------------
# Package structure tests
# -------------------------------------------------------------------


class TestPackageStructure:
    """Test that the marimo_inspection package is well-formed."""

    def test_package_has_version(self):
        """Package __init__.py exposes version."""
        import marimo_inspection

        assert hasattr(marimo_inspection, "__version__")

    def test_tools_module_exports(self):
        """tools.__init__.py exports all tool functions."""
        from marimo_inspection import tools

        expected = [
            "list_active_notebooks",
            "get_cell_map",
            "get_cell_data",
            "get_cell_outputs",
            "get_variables",
            "get_dependency_graph",
            "get_errors",
            "lint_notebook",
        ]
        for name in expected:
            assert hasattr(tools, name), f"tools.{name} is missing"

    def test_templates_module_exports(self):
        """templates.__init__.py exports all templates."""
        from marimo_inspection import templates

        expected = [
            "TEMPLATE_CELL_MAP",
            "TEMPLATE_CELL_DATA",
            "TEMPLATE_CELL_OUTPUTS",
            "TEMPLATE_VARIABLES",
            "TEMPLATE_DEPENDENCY_GRAPH",
            "TEMPLATE_ERRORS",
            "TEMPLATE_LINT",
        ]
        for name in expected:
            assert hasattr(templates, name), f"templates.{name} is missing"

    def test_types_module_exports(self):
        """types module exports all dataclasses."""
        from marimo_inspection import types

        expected = [
            "MarimoNotebookInfo",
            "LightweightCellInfo",
            "CellRuntimeData",
            "CellOutputData",
            "VariableValue",
            "DataTableMetadata",
            "VariableInfo",
            "CellDependencyInfo",
            "CellError",
            "CellErrors",
            "Diagnostic",
        ]
        for name in expected:
            assert hasattr(types, name), f"types.{name} is missing"

    def test_client_module_exports(self):
        """client module exports MarimoClient."""
        from marimo_inspection import client

        assert hasattr(client, "MarimoClient")
        assert hasattr(client, "SessionInfo")
        assert hasattr(client, "ExecuteResult")

    def test_discovery_module_exports(self):
        """discovery module exports discovery functions."""
        from marimo_inspection import discovery

        assert hasattr(discovery, "discover_servers")
        assert hasattr(discovery, "DiscoveredServer")
        assert hasattr(discovery, "list_sessions")
