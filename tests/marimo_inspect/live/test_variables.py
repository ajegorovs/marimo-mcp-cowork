"""Live integration tests for the variables template.

Behavioral assertions: against a live kernel the template returns status ok
and — for an unfiltered ("all") call — reports ONLY names defined and
executed by the notebook's own cells. The scratchpad shares the kernel
namespace, so a bare ``globals()`` enumeration also sees kernel-injected
globals (``input``, ``spec_from_loader``) and the template's own scaffolding
(its ``json``/``cm`` imports and helpers); none of those, and no cell-private
(leading-underscore) name, may be reported. It must not raise when optional
dependencies (numpy/pandas) are absent.
"""

from __future__ import annotations

import json
import sys

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.templates.variables import (
    TEMPLATE_VARIABLES,
    build_variables_template,
)
from marimo_inspection.tools.mutation import create_cell, delete_cell, run_cell


@pytest.mark.live
async def test_variables_returns_ok_with_variables(live_client, live_session):
    """The variables template succeeds and returns a variables mapping."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_VARIABLES)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])

    assert "variables" in data
    assert "tables" in data
    assert isinstance(data["variables"], dict)
    assert isinstance(data["tables"], dict)


@pytest.mark.live
async def test_all_variables_excludes_template_scaffolding(live_client, live_session):
    """H8/T-V4: "all" reports notebook-defined names, not shared kernel state.

    The scratchpad shares its namespace with the kernel, so the bare
    ``globals()`` enumeration used to return the tool's own imports and
    helpers (``json``, ``cm``, ``get_variables``) *and* kernel-injected
    globals such as ``input`` and ``spec_from_loader`` — names no cell
    defines. Pre-fix this test pinned the defect by asserting that ``input``
    stayed visible; the allowlist now comes from the notebook's own cell
    definitions, so a shared session whose cells never executed reports no
    notebook-defined names at all and none of these may appear.
    """
    result = await live_client.execute(live_session.session_id, TEMPLATE_VARIABLES)
    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    variables = data["variables"]
    scaffolding = {"json", "cm", "get_variables", "_is_ui", "_serialize"}
    leaked = scaffolding & set(variables)
    assert not leaked, f"Template scaffolding leaked into variables: {sorted(leaked)}"

    # Kernel-injected globals are not notebook-defined names: the old
    # contract required ``input`` to remain visible, the new one forbids it.
    assert "input" not in variables, sorted(variables)
    assert "spec_from_loader" not in variables, sorted(variables)


# Public scalar and a leading-underscore (cell-private) name created in the
# live kernel; marimo treats the private one as cell-local, not a session
# variable.
_TV4_PUBLIC_NAME = "tv4_public_value"
_TV4_PRIVATE_NAME = "_tv4_private_value"


@pytest.mark.live
async def test_unfiltered_all_reports_only_executed_notebook_names(mutation_server):
    """T-V4: unfiltered "all" is the notebook's executed public names only.

    Pre-fix the template enumerated the scratchpad's shared ``globals()``
    minus a five-name scaffold denylist, so kernel-injected globals
    (``input``, ``spec_from_loader``) and the template's own scaffolding
    leaked on every call. The allowlist is now derived from the notebook
    graph's cell definitions (``ctx.graph.definitions``) and intersected with
    ``globals()``, so only names an executed notebook cell actually defined
    are reported.

    Cells created through the write tools DO execute in this isolated live
    server, so the public name is observable; the private leading-underscore
    name is included on purpose and must never surface.
    """
    _manager, server_url, session_id, _notebook_copy = mutation_server
    client = MarimoClient(server_url)
    created: list[str] = []
    try:
        for source in (
            f"{_TV4_PUBLIC_NAME} = 41",
            f"{_TV4_PRIVATE_NAME} = 7",
        ):
            made = await create_cell(
                source, session_id=session_id, server_url=server_url
            )
            assert made["status"] == "ok", made
            created.append(made["cell_id"])

        for cell_id in created:
            ran = await run_cell(cell_id, session_id=session_id, server_url=server_url)
            assert ran["status"] == "ok", ran

        result = await client.execute(session_id, TEMPLATE_VARIABLES)
        assert result.status == "ok", f"Template failed: stderr={result.stderr}"
        variables = json.loads(result.stdout[0])["variables"]

        # The executed notebook-defined public name is reported...
        assert _TV4_PUBLIC_NAME in variables, sorted(variables)
        assert variables[_TV4_PUBLIC_NAME]["datatype"] == "int"

        # ...while kernel-injected globals and template scaffolding are not.
        for leaked in (
            "input",
            "spec_from_loader",
            "json",
            "cm",
            "get_variables",
            "_is_ui",
            "_serialize",
        ):
            assert leaked not in variables, f"{leaked!r} leaked: {sorted(variables)}"

        # No cell-private name survives — neither the bare name nor marimo's
        # mangled cell-local form (both start with an underscore).
        assert _TV4_PRIVATE_NAME not in variables, sorted(variables)
        assert not [n for n in variables if n.startswith("_")], (
            f"private names leaked: {sorted(variables)}"
        )

        # Explicit filtered lookup is preserved: a named public variable
        # still resolves, and a private/absent name reports nothing.
        filtered = await client.execute(
            session_id, build_variables_template([_TV4_PUBLIC_NAME])
        )
        assert filtered.status == "ok", filtered.stderr
        assert list(json.loads(filtered.stdout[0])["variables"]) == [_TV4_PUBLIC_NAME]

        private_lookup = await client.execute(
            session_id, build_variables_template([_TV4_PRIVATE_NAME])
        )
        assert private_lookup.status == "ok", private_lookup.stderr
        assert json.loads(private_lookup.stdout[0])["variables"] == {}
    finally:
        # Cleanup failures must be visible, but must never replace the primary
        # failure already propagating out of the body (only one exception can
        # leave a `finally`).
        cleanup_failures = []
        for cell_id in reversed(created):
            deleted = await delete_cell(
                cell_id, session_id=session_id, server_url=server_url
            )
            if deleted.get("status") != "ok":
                cleanup_failures.append((cell_id, deleted))
        await client.close()
        if cleanup_failures:
            if sys.exc_info()[0] is None:
                pytest.fail(f"delete_cell cleanup failed: {cleanup_failures}")
            print(f"[cleanup] delete_cell failed: {cleanup_failures}")


@pytest.mark.live
async def test_variables_does_not_require_numpy_or_pandas(live_client, live_session):
    """The template degrades gracefully when optional data libs are absent.

    Regression for the unguarded `import numpy as np` in _serialize that
    crashed in kernels without numpy. It must run fine in this env, which
    has neither numpy nor pandas.
    """
    result = await live_client.execute(live_session.session_id, TEMPLATE_VARIABLES)
    assert result.status == "ok", f"Template failed: stderr={result.stderr}"
    data = json.loads(result.stdout[0])
    assert "variables" in data
