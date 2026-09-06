"""Marimo Inspection - a pluggable marimo notebook inspection and co-work toolkit.

This package provides two complementary surfaces over the same marimo kernel:

1. A Python client (importable with no MCP dependency) for driving a live
   marimo session interactively from any notebook or script:

       from marimo_inspection import MarimoClient, discover_servers

       servers = await discover_servers()
       async with MarimoClient(servers[0].url) as client:
           sessions = await client.list_sessions()

   This is the "plug into any repo" path: install the package, import it from a
   marimo notebook, and co-work on the session with the live kernel.

2. A FastMCP server exposing the same tooling over the Model Context
   Protocol, for AI agents (e.g. the marimo-pair pairing scripts):

       from marimo_inspection import create_server
       server = create_server()

The MCP surface is lazily imported so that importing marimo_inspection does not
require fastmcp unless the server is actually used.

Communication with marimo is via scratchpad execution through the HTTP API:
    POST /api/kernel/execute  ->  scratchpad namespace  ->  JSON result
"""

from __future__ import annotations

from marimo_inspection.client import ExecuteResult, MarimoClient, SessionInfo
from marimo_inspection.discovery import DiscoveredServer, discover_servers

__version__ = "0.2.0"

__all__ = [
    "DiscoveredServer",
    "ExecuteResult",
    "MarimoClient",
    "SessionInfo",
    "create_server",
    "discover_servers",
]


def __getattr__(name: str):
    """Lazily expose the FastMCP server so the client path needs no fastmcp."""
    if name == "create_server":
        from marimo_inspection.server import create_server as _create_server

        return _create_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
