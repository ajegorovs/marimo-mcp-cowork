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

    async def test_co_work_loop_documents_run_cell_modes(self, mcp_server):
        """T15: the loop teaches the three modes and the kernel's own additions.

        A caller must be able to learn, from the packaged loop alone: what each
        mode queues, that ``mode="all"`` needs an empty ``cell_id``
        (``cell_id_not_allowed`` otherwise), that ``descendants`` refuses
        ``graph_unpopulated`` on an unregistered target instead of silently
        running one cell, how per-cell outcomes are reported
        (``not_run_cell_ids``), and that the kernel may run extra cells in an
        unspecified order.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(CO_WORK_URI))
        text = " ".join(raw.lower().split())

        assert 'mode="cell"' in text
        assert 'mode="descendants"' in text
        assert 'mode="all"' in text
        assert "graph_unpopulated" in text
        assert "cell_id_not_allowed" in text
        assert "not_run_cell_ids" in text
        assert "ancestors" in text
        assert "unspecified" in text
        # The compat contract: names resolve, failures stay structured, an
        # unreadable errors channel is never reported as success, and
        # failed_cell_ids covers the requested targets only.
        assert "id or cell name" in text
        assert "unverified_cell_ids" in text
        assert "planning_failed" in text
        assert "reporting_failed" in text
        assert "null" in text
        assert "requested targets only" in text

    async def test_live_safety_documents_the_bulk_run_blast_radius(self, mcp_server):
        """T15: a bulk run's blast radius and the empty-graph refusal are stated.

        ``mode="all"`` is the one mode that executes cells the caller never
        asked about (every document cell, including already-idle and
        deliberately un-run ones), and the kernel adds stale ancestors and
        autorun descendants to any run. The live-safety resource is where a
        co-worker checks that before calling it.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(LIVE_SAFETY_URI))
        text = " ".join(raw.lower().split())

        assert 'mode="all"' in text
        assert "every document cell" in text
        assert "already idle" in text
        assert "graph_unpopulated" in text
        assert "ancestors" in text
        assert "unspecified" in text
        # The per-cell outcome lists cover the requested targets only.
        assert "requested targets only" in text

    async def test_co_work_loop_documents_button_click_semantics(self, mcp_server):
        """T20: the loop teaches the button click counter and its evidence.

        A caller must be able to learn, from the packaged loop alone: that a
        button's element value is its ``on_click`` return while a ``run_button``
        has no ``on_click`` and resets to ``False`` after its dependents run,
        that both expose a click counter, that delivery is evidenced by
        ``handler_invoked`` (true / false / null), that an invocation is never
        verifiable when the counter did not move, that 0 is the initialization
        sentinel, and that arbitrary side effects are not verified.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(CO_WORK_URI))
        text = " ".join(raw.lower().split())

        assert "button" in text
        assert "on_click" in text
        assert "counter" in text
        assert "handler_invoked" in text
        assert "side_effects_verified" in text
        assert "sentinel" in text
        assert "on_click_failed" in text
        # button vs run_button are distinguished, not conflated.
        assert "run_button" in text
        assert "no `on_click`" in text
        assert "reset" in text
        # The old blanket "not awaited" claim is corrected, not merely kept.
        assert "not awaited" not in text
        assert "autorun" in text

    async def test_co_work_loop_scopes_the_downstream_effects_claim(self, mcp_server):
        """The loop states this call does not verify arbitrary downstream effects."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(CO_WORK_URI))
        # Drop Markdown emphasis so a bolded "does **not** verify" still matches.
        text = " ".join(raw.lower().replace("*", "").split())
        assert "this call does not verify" in text or "does not verify" in text

    async def test_live_safety_documents_the_button_click_contract(self, mcp_server):
        """T20: the safety rules carry the button read-back truth."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(LIVE_SAFETY_URI))
        text = " ".join(raw.lower().split())
        assert "on_click" in text
        assert "handler_invoked" in text
        assert "side_effects_verified" in text
        # No blanket "re-runs are not awaited" claim survives here either.
        assert "not awaited" not in text

    async def test_fallbacks_scopes_the_reactive_rerun_claim(self, mcp_server):
        """The limits reference must not blanket-claim re-runs are unawaited."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().split())
        assert "not awaited" not in text
        assert "autorun" in text or "does not verify" in text

    async def test_fallbacks_documents_that_bulk_run_is_a_mode_not_a_tool(
        self, mcp_server
    ):
        """T15: bulk execution stays on the fixed tool surface.

        There is no separate run-all tool; the packaged reference must say the
        capability lives in ``run_cell(mode="all")`` and that ``descendants``
        depends on kernel-graph registration.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().split())

        assert 'mode="all"' in text
        assert "15-tool" in text
        assert "graph_unpopulated" in text
        # Names resolve, and a null errors channel is a stated limit.
        assert "id or cell name" in text
        assert "errors_readable" in text
        assert "unverified_cell_ids" in text

    async def test_fallbacks_documents_kernel_restart_and_its_cost(self, mcp_server):
        """Wave 3: the lifecycle section names the tool, its cost, and the token.

        The reference must (a) stop claiming there is no kernel-restart tool,
        (b) state what a restart costs (execution state, stale cells, re-keyed
        ids, cleared baselines), (c) name the token acquisition path and its
        refusal, (d) promise that a sessionless server is never reported as
        success, and (e) state that the verified session id is point-in-time,
        not durable (a later reconnect/TTL can invalidate it).
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().split())

        assert "restart_kernel" in text
        assert "no kernel-restart tool" not in text
        assert "re-materialize" in text
        assert "stale" in text
        assert "needs_read" in text
        assert "<marimo-server-token" in text
        assert "auth_required" in text
        assert "skew_token_unavailable" in text
        assert "server_sessionless" in text
        assert "never reported as success" in text
        # No durable-id claim: the point-in-time contract and the TTL caveat.
        assert "session_id_stable" in text
        assert "point_in_time" in text
        assert "ttl" in text
        assert "never adopted" in text

    async def test_co_work_loop_documents_when_a_restart_is_warranted(self, mcp_server):
        """The loop says when a kernel restart is (and is not) the instrument."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(CO_WORK_URI))
        text = " ".join(raw.lower().split())

        assert "restart_kernel" in text
        # A cell edit never needs one — the consumer's own self-inflicted restart.
        assert "cell edit" in text
        assert 'run_cell(mode="all")' in text
        # The token path, and the auth-on refusal it produces.
        assert "skew token" in text
        assert "auth_required" in text
        # And that the confirmed id is point-in-time, not durable.
        assert "session_id_stable" in text
        assert "point_in_time" in text

    async def test_fallbacks_documents_one_main_output_limit(self, mcp_server):
        async with Client(transport=mcp_server) as client:
            text = _resource_text(await client.read_resource(FALLBACKS_URI)).lower()
        assert "one main output per cell" in text
        assert "console events" in text
        assert "frontend" in text

    async def test_fallbacks_scopes_empty_graph_metadata_to_registration(
        self, mcp_server
    ):
        """Empty graph metadata is tied to graph non-registration, not execution.

        ``create_cell``/``edit_cell`` register a cell and its edges before
        ``run_cell``, so an unexecuted cell can still carry graph metadata.
        The doc must make registration the sole condition and not imply that
        unexecuted means empty; pin that, and keep the whole-notebook
        cell-inventory / no-invented-edge guarantees intact.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().split())

        # Registration is the condition that grants graph metadata.
        assert "has not registered" in text
        assert "registration is the sole condition" in text
        # The complete inventory / no-invented-edge guarantees are preserved.
        assert "no edge is invented" in text
        assert "no cell is dropped" in text
        # The old execution-status shortcut must not creep back in.
        assert "unexecuted (or otherwise graph-unregistered)" not in text
        assert "typically one that has not executed" not in text

    async def test_fallbacks_documents_validation_before_binding(self, mcp_server):
        """A bind validates the exact live id first, or refuses cleanly.

        ``set_active_session`` must not report success for an id no live
        session reports: an explicit ``server_url`` is the deterministic path,
        discovery must find a unique endpoint, and a refusal changes no state.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().split())

        assert "set_active_session" in text
        assert "before it binds anything" in text
        assert "explicit `server_url` is deterministic" in text
        assert "exactly one" in text
        assert "session_ambiguous" in text
        assert "bound: false" in text
        assert "state_changed: false" in text

    async def test_co_work_loop_documents_browser_first_and_unknown_provenance(
        self, mcp_server
    ):
        """T19/T22: the loop teaches browser-first, not a guessed mechanism.

        A caller must be able to learn, from the packaged loop alone, that a
        session's provenance/owner are not knowable (binding is not ownership),
        that the summary counts are scoped (``session_count`` /
        ``attached_client_count: null`` / a deprecated ``active_connections``
        alias), that browser-first is the order to prefer, that a closed ``/sse``
        stream leaves an orphan a human must take over and re-run, that a later
        reconnect can re-key the id, and that a page/session divergence is not
        diagnosed.
        """
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(CO_WORK_URI))
        text = " ".join(raw.lower().replace("`", "").split())

        assert "provenance" in text
        assert "owner" in text
        assert "unknown" in text
        assert "binding is not ownership" in text
        assert "session_count" in text
        assert "attached_client_count" in text
        assert "deprecated" in text
        assert "active_connections" in text
        assert "browser" in text
        assert "take over" in text
        assert "re-run" in text
        assert "orphan" in text
        assert "re-key" in text
        assert "not diagnosed" in text
        # Scoping and the corrected counts: 0.24 edit mode w/o TTL, main
        # consumer role, and the failure-path totals.
        assert "edit mode" in text
        assert "session-ttl" in text
        assert "main consumer" in text
        assert "non-main" in text
        assert "total_notebooks" in text
        assert "result_row_count" in text
        assert "sentinel" in text

    async def test_live_safety_documents_that_a_session_may_not_be_yours(
        self, mcp_server
    ):
        """T22: the safety rules warn that binding is not ownership."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(LIVE_SAFETY_URI))
        text = " ".join(raw.lower().replace("`", "").split())

        assert "provenance" in text
        assert "owner" in text
        assert "binding is not ownership" in text
        assert "browser" in text
        assert "take over" in text
        assert "orphan" in text
        assert "attached_client_count" in text
        assert "deprecated" in text
        assert "not diagnosed" in text
        assert "session-ttl" in text
        assert "main consumer" in text
        assert "non-main" in text
        assert "result_row_count" in text

    async def test_fallbacks_documents_session_provenance_and_counts(self, mcp_server):
        """T22: the limits reference names the honest fields and counts."""
        async with Client(transport=mcp_server) as client:
            raw = _resource_text(await client.read_resource(FALLBACKS_URI))
        text = " ".join(raw.lower().replace("`", "").split())

        assert "provenance" in text
        assert "owner" in text
        assert "binding is not ownership" in text
        assert "session_count" in text
        assert "attached_client_count" in text
        assert "deprecated" in text
        assert "browser" in text
        assert "take over" in text
        assert "orphan" in text
        assert "re-key" in text
        assert "not diagnosed" in text
        assert "total_notebooks" in text
        assert "result_row_count" in text
        assert "sentinel" in text
        assert "session-ttl" in text
        assert "main consumer" in text


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
