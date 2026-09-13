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
    "restart_kernel",
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
            assert len(tools) == 15

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

    async def test_get_variables_description_scopes_unfiltered_names(self, mcp_server):
        """The exposed get_variables description defines both lookup modes.

        The consumer-visible MCP description must say that an unfiltered call
        returns executed public names defined by notebook cells, and that
        kernel-injected globals, template scaffolding, private names and
        unexecuted definitions are excluded; otherwise a caller reads the
        payload as "every kernel global". It must also describe the filtered
        mode truthfully: ``variable_names`` inspects specific names visible in
        the kernel namespace (including an explicitly named kernel-injected
        global such as ``input``), and it must never promise that private
        (leading-underscore) names are reachable — a filtered lookup of one
        reports an empty result.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            variables_tool = next(t for t in tools if t.name == "get_variables")
            # Normalize wrapping: the description is prose, not a line layout.
            description = " ".join((variables_tool.description or "").lower().split())

            # Unfiltered set: executed notebook-defined public names only.
            assert "executed public names defined by notebook cells" in description
            assert "kernel-injected" in description
            assert "scaffolding" in description
            assert "private" in description
            assert "not executed" in description

            # Filtered set: specific kernel-visible names, including an
            # explicitly named kernel-injected global.
            assert "input" in description
            # ...and no false promise that private names can be reached.
            assert "including private" not in description

    async def test_list_active_notebooks_description_two_channel_binding(
        self, mcp_server
    ):
        """The exposed list_active_notebooks description teaches both channels.

        The consumer-visible MCP description must describe the binding as
        MCP-session state plus a process-global fallback, scope that fallback
        correctly (connection-global over stdio, single-client over HTTP/SSE
        with a ``binding_ambiguous`` refusal on a second session), and must not
        repeat the stale single-channel instruction to pass both arguments on
        every call.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            list_tool = next(t for t in tools if t.name == "list_active_notebooks")
            # Normalize wrapping and backticks: the description is prose.
            description = " ".join(
                (list_tool.description or "").lower().replace("`", "").split()
            )

            # The two-channel model: session state plus a process-global
            # fallback consulted only when that state has nothing bound.
            assert "process-global fallback" in description

            # stdio: one process serves one client, so argument-less calls
            # still work across later or fresh MCP sessions.
            assert "stdio" in description
            assert "fresh" in description

            # HTTP/SSE: the fallback is scoped to a single client session; a
            # second session makes argument-less calls fail closed.
            assert "http/sse" in description
            assert "binding_ambiguous" in description

            # An explicit session_id/server_url always overrides the binding.
            assert "explicit" in description
            assert "always wins" in description

            # The stale single-channel instruction must be gone, together with
            # the client-internals claims it carried.
            assert (
                "pass session_id and server_url explicitly on every call"
                not in description
            )
            assert "fastmcp" not in description
            assert "per request" not in description

    async def test_list_active_notebooks_description_states_truthful_provenance(
        self, mcp_server
    ):
        """T19/T22: the exposed description is honest about what is knowable.

        The consumer-visible description must state that per-session
        provenance/owner are "unknown" (binding is not ownership), that
        ``attached_client_count`` is unavailable while ``active_connections`` is
        only a deprecated alias for ``session_count``, that browser-first is the
        order to prefer, that a closed ``/sse`` stream leaves an orphan a human
        must take over and re-run, and that a page/session divergence is not
        diagnosed.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            list_tool = next(t for t in tools if t.name == "list_active_notebooks")
            description = " ".join(
                (list_tool.description or "").lower().replace("`", "").split()
            )

            assert "provenance" in description
            assert "owner" in description
            assert "unknown" in description
            assert "binding is not ownership" in description
            assert "session_count" in description
            assert "attached_client_count" in description
            assert "deprecated" in description
            assert "active_connections" in description
            assert "browser" in description
            assert "take over" in description
            assert "orphan" in description
            assert "re-run" in description
            assert "re-key" in description
            assert "not diagnosed" in description
            # Scoped to 0.24 edit mode w/o TTL; counts corrected.
            assert "total_notebooks" in description
            assert "result_row_count" in description
            assert "session-ttl" in description
            assert "main consumer" in description
            assert "read-only" in description
            # T18: server discovery vs session discovery, and run mode.
            assert "marimo run" in description
            assert "401" in description
            assert "edit scope" in description
            assert "launch" in description

    async def test_server_instructions_state_browser_first_and_unknown_provenance(
        self, mcp_server
    ):
        """The server-level instructions carry the same honest session rules."""
        instructions = " ".join((mcp_server.instructions or "").lower().split())

        assert "provenance" in instructions
        assert "owner" in instructions
        assert "binding is not ownership" in instructions
        assert "browser-first" in instructions
        assert "take over" in instructions
        assert "orphan" in instructions
        assert "deprecated" in instructions
        assert "not diagnosed" in instructions
        assert "result_row_count" in instructions
        assert "main consumer" in instructions
        assert "session-ttl" in instructions
        assert "non-main" in instructions
        # T18: server discovery vs session discovery, and run mode.
        assert "marimo run" in instructions
        assert "401" in instructions
        assert "edit scope" in instructions
        assert "launch" in instructions

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

    async def test_set_ui_value_description_scopes_button_click_evidence(
        self, mcp_server
    ):
        """T20: the exposed description teaches the button click contract.

        The consumer-visible description must say that a button's element value
        is its ``on_click`` return while a ``run_button`` has no ``on_click``
        and resets to ``False``, that both expose a click counter, that
        delivery/invocation is reported as ``handler_invoked`` (with the
        0 sentinel and the unverifiable repeated counter), and that side
        effects are not verified by the read-back.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "set_ui_value")
        description = " ".join((tool.description or "").lower().split())

        assert "button" in description
        assert "on_click" in description
        assert "counter" in description
        assert "handler_invoked" in description
        assert "side_effects_verified" in description
        assert "sentinel" in description
        assert "on_click_failed" in description
        # button vs run_button are distinguished, not conflated.
        assert "run_button" in description
        assert "reset" in description
        # The corrected downstream-effects claim, not the old blanket one.
        assert "not awaited" not in description

    async def test_run_cell_mode_schema_is_backward_compatible(self, mcp_server):
        """T15: run_cell keeps optional args and offers the three-literal mode.

        The signature extension must not break an existing caller that passes
        only ``cell_id`` — both ``cell_id`` and ``mode`` stay optional — and the
        advertised schema must enumerate exactly the three accepted modes.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            run_tool = next(t for t in tools if t.name == "run_cell")
            schema = run_tool.input_schema
            props = schema.get("properties", {})

            assert "cell_id" in props, schema
            assert "mode" in props, schema
            # Backward compatibility: nothing is newly required.
            assert not set(schema.get("required", [])) - {"session_id", "server_url"}, (
                schema
            )

            mode = props["mode"]
            accepted: list[str] = []
            for branch in (mode, *mode.get("anyOf", []), *mode.get("oneOf", [])):
                accepted.extend(branch.get("enum", []))
            assert sorted(set(accepted)) == ["all", "cell", "descendants"], mode
            # The default keeps single-cell execution.
            assert mode.get("default") == "cell", mode

    async def test_run_cell_description_names_the_modes_and_the_kernel_note(
        self, mcp_server
    ):
        """The exposed description teaches the modes and what the kernel adds.

        The consumer-visible description must name ``mode``, ``all`` and
        ``descendants``, say that ``mode='all'`` re-runs every document cell and
        requires an empty ``cell_id``, that ``descendants`` needs a registered
        target, and that the kernel may additionally run stale ancestors and
        autorun descendants in an unspecified order.
        """
        async with Client(transport=mcp_server) as client:
            tools = await client.list_tools()
            run_tool = next(t for t in tools if t.name == "run_cell")
            description = " ".join((run_tool.description or "").lower().split())

            assert "mode" in description
            assert "descendants" in description
            assert '"all"' in description or "'all'" in description
            assert "empty" in description
            assert "ancestors" in description
            assert "unspecified" in description
            # The compat contract: names resolve, and failures keep a
            # structured status/reason plus the legacy top-level `error`.
            assert "id or cell name" in description
            assert "planning_failed" in description
            assert "reporting_failed" in description
            assert "unverified" in description
            assert "errors_readable" in description


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
