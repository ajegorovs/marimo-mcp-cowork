"""Tests for the static read-only MCP resources.

The marimo-inspect server publishes three fixed, read-only Markdown documents
as native MCP resources (not tools):

- ``workflow://marimo-inspect/co-work-loop``
- ``workflow://marimo-inspect/live-safety``
- ``reference://marimo-inspect/fallbacks-and-limits``

These tests drive the server in-process (``Client(transport=server)``), exactly
like ``test_server.py``, and verify: the exact URI set, MIME type, names,
descriptions, tags, the annotations we actually registered (the installed
FastMCP/MCP resource annotation model is limited — see below), that each
document reads back with its operational rules, and that no resource text leaks
private data.

Annotation note (FastMCP 4.0.3): resource ``annotations`` is
``mcp.types.Annotations`` (``audience``/``priority``/``lastModified`` only).
The tool-only ``readOnlyHint``/``idempotentHint``/``destructiveHint`` fields
are **not** accepted for resources, so read-only-ness is signalled via tags
and descriptions instead. Do not "fix" the tests below to expect hints the API
cannot carry.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest
from fastmcp.client import Client

# ---------------------------------------------------------------------------
# Expected surface
# ---------------------------------------------------------------------------

CO_WORK_URI = "workflow://marimo-inspect/co-work-loop"
LIVE_SAFETY_URI = "workflow://marimo-inspect/live-safety"
FALLBACKS_URI = "reference://marimo-inspect/fallbacks-and-limits"

EXPECTED_URIS = {CO_WORK_URI, LIVE_SAFETY_URI, FALLBACKS_URI}

# Per-resource expectations. "phrases" are matched case-insensitively against
# the served Markdown; they are the operational rules the doc must carry.
EXPECTED = {
    CO_WORK_URI: {
        "name": "Co-work loop",
        "tags": {"read-only", "static", "workflow"},
        "phrases": [
            "list_active_notebooks",
            "get_cell_map",
            "get_cell_data",
            "create_cell",
            "edit_cell",
            "run_cell",
            "delete_cell",
            "set_ui_value",
            "get_variables",
            "get_cell_outputs",
            "get_errors",
            "lint_notebook",
            "read before edit",
        ],
    },
    LIVE_SAFETY_URI: {
        "name": "Live-safety rules",
        "tags": {"read-only", "static", "workflow"},
        "phrases": [
            "needs_read",
            "conflict",
            "check_fresh=false",
            "get_cell_data",
            "get_dependency_graph",
            "mo.vstack",
            "set_ui_value",
            "live kernel",
        ],
    },
    FALLBACKS_URI: {
        "name": "Fallbacks and limits",
        "tags": {"read-only", "static", "reference"},
        "phrases": [
            "one main output per cell",
            "playwright",
            "no general execute",
            "0.24.x",
            "set_ui_value",
            "restart",
        ],
    },
}

EXPECTED_ANNOTATIONS_AUDIENCE = ["assistant"]


def _resource_text(read_result) -> str:
    """Extract the concatenated text payload from a read_resource() result."""
    return "\n".join(getattr(item, "text", "") for item in read_result)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestResourceListing:
    """The server advertises exactly the three intended resources."""

    async def test_exact_resource_set(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            uris = {str(r.uri) for r in resources}
            assert uris == EXPECTED_URIS, f"unexpected resource set: {sorted(uris)}"

    async def test_resource_count(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            assert len(resources) == 3

    async def test_mime_type_is_markdown(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            for resource in resources:
                assert resource.mime_type == "text/markdown", (
                    f"{resource.uri} has mime_type {resource.mime_type!r}"
                )

    async def test_names_and_descriptions(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            by_uri = {str(r.uri): r for r in resources}
            for uri, spec in EXPECTED.items():
                resource = by_uri[uri]
                assert resource.name == spec["name"], f"{uri} name mismatch"
                assert resource.description, f"{uri} has no description"
                assert len(resource.description) > 20, f"{uri} description too short"

    async def test_tags_registered(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            by_uri = {str(r.uri): r for r in resources}
            for uri, spec in EXPECTED.items():
                meta = by_uri[uri].meta or {}
                fastmcp_meta = meta.get("fastmcp", {})
                tags = set(fastmcp_meta.get("tags", []))
                assert tags == spec["tags"], f"{uri} tags mismatch: {sorted(tags)}"

    async def test_annotations_registered(self, mcp_server):
        """We register the only meaningful supported annotation: audience.

        readOnlyHint/idempotentHint are tool-only in FastMCP 4.0.3 and cannot
        be expressed on a resource; this asserts what we actually set.
        """
        async with Client(transport=mcp_server) as client:
            resources = await client.list_resources()
            for resource in resources:
                assert resource.annotations is not None, (
                    f"{resource.uri} lost its annotations"
                )
                assert resource.annotations.audience == EXPECTED_ANNOTATIONS_AUDIENCE


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestResourceContent:
    """Each URI reads back with its required operational rules."""

    async def test_all_resources_read_back(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            for uri in EXPECTED_URIS:
                result = await client.read_resource(uri)
                text = _resource_text(result)
                assert text.strip(), f"{uri} read back empty"

    @pytest.mark.parametrize("uri", sorted(EXPECTED_URIS))
    async def test_required_phrases_present(self, mcp_server, uri):
        async with Client(transport=mcp_server) as client:
            result = await client.read_resource(uri)
            text = _resource_text(result)
            lowered = text.lower()
            for phrase in EXPECTED[uri]["phrases"]:
                assert phrase.lower() in lowered, (
                    f"{uri} is missing required phrase {phrase!r}"
                )

    async def test_documents_open_with_h1_and_scope(self, mcp_server):
        """Each doc starts with an H1 and a version/scope line."""
        async with Client(transport=mcp_server) as client:
            for uri in EXPECTED_URIS:
                result = await client.read_resource(uri)
                text = _resource_text(result).lstrip()
                assert text.startswith("# "), f"{uri} does not start with an H1"
                assert "marimo-inspect 0.3.x" in text, (
                    f"{uri} is missing the version/scope note"
                )
                assert "marimo 0.24.x" in text, (
                    f"{uri} is missing the marimo version note"
                )

    async def test_live_safety_documents_recovery_protocol(self, mcp_server):
        """The needs_read/conflict recovery sequence is spelled out."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(LIVE_SAFETY_URI))
        # Collapse Markdown line wrapping so multi-word phrases match.
        text = " ".join(raw.lower().split())
        assert "needs_read" in text
        assert "conflict" in text
        assert "retry" in text
        assert "re-read" in text
        # check_fresh=False is explicitly a force escape hatch, not recovery.
        assert "escape hatch" in text
        assert "never the normal recovery" in text

    async def test_fallbacks_documents_one_main_output_limit(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            text = _resource_text(await client.read_resource(FALLBACKS_URI)).lower()
        assert "one main output per cell" in text
        assert "console events" in text
        assert "frontend" in text


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

# Concrete leaks that must never appear in a publishable resource. Patterns are
# deliberately narrow (a real path/host/credential), not the generic words for
# the topics themselves.
_FORBIDDEN_PATTERNS = [
    (r"/home/", "absolute local home path"),
    (r"/Users/", "absolute local home path"),
    (r"[A-Za-z]:[\\/]+Users", "absolute Windows path"),
    (r"\bapi[_-]?key\b", "API key"),
    (r"\bpassword\b", "password"),
    (r"\bsecret\b", "secret"),
    (r"\bbearer\s+[A-Za-z0-9._-]+", "bearer token"),
    (r"BEGIN [A-Z ]*PRIVATE KEY", "private key"),
    (r"\budv\b", "consumer repo name"),
    (r"dop3000", "consumer project name"),
    (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "RFC1918 host"),
    (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "RFC1918 host"),
    (r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b", "RFC1918 host"),
    (r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "CGNAT host"),
    (r"\.ts\.net\b", "tailnet DNS name"),
]


class TestResourcePrivacy:
    """No resource text may leak local paths, credentials, or private hosts."""

    @pytest.mark.parametrize("uri", sorted(EXPECTED_URIS))
    async def test_served_text_is_publishable(self, mcp_server, uri):
        async with Client(transport=mcp_server) as client:
            text = _resource_text(await client.read_resource(uri))
        for pattern, label in _FORBIDDEN_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            assert match is None, f"{uri} leaks {label}: {match.group(0)!r}"

    @pytest.mark.parametrize("uri", sorted(EXPECTED_URIS))
    async def test_served_text_has_no_credentials(self, mcp_server, uri):
        async with Client(transport=mcp_server) as client:
            text = _resource_text(await client.read_resource(uri))
        assert not re.search(r"token\s*[:=]\s*\S+", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------

DIST_DIR = Path(__file__).resolve().parents[2] / "dist"
EXPECTED_WHEEL_MEMBERS = {
    "marimo_inspection/resources/co-work-loop.md",
    "marimo_inspection/resources/live-safety.md",
    "marimo_inspection/resources/fallbacks-and-limits.md",
}


class TestPackaging:
    """The Markdown must ship inside the built wheel, not only in the repo."""

    def test_markdown_is_loadable_as_package_resource(self):
        """importlib.resources resolves the docs from the installed package."""
        from marimo_inspection.resources import load_resource_text

        for filename in (
            "co-work-loop.md",
            "live-safety.md",
            "fallbacks-and-limits.md",
        ):
            assert load_resource_text(filename).strip()

    def test_wheel_contains_markdown(self):
        """Every built wheel under dist/ ships the three Markdown files."""
        wheels = sorted(DIST_DIR.glob("*.whl"))
        if not wheels:
            pytest.skip("no built wheel in dist/ — run `uv build` first")
        wheel = wheels[-1]
        with zipfile.ZipFile(wheel) as archive:
            members = set(archive.namelist())
        missing = EXPECTED_WHEEL_MEMBERS - members
        assert not missing, f"{wheel.name} is missing {sorted(missing)}"
