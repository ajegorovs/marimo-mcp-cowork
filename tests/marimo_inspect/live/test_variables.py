"""Live integration tests for the variables template.

Behavioral assertions: against a live kernel the template returns status ok
and inspects the kernel-visible globals. "All variables" means the session's
own names — the template's scaffolding (its `json`/`cm` imports and its
helpers) is excluded, since the scratchpad shares the kernel namespace. It
must not raise when optional dependencies (numpy/pandas) are absent.
"""

from __future__ import annotations

import json

import pytest

from marimo_inspection.templates.variables import TEMPLATE_VARIABLES


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
    """H8: "all" reports kernel names, not the template's own scaffolding.

    The scratchpad shares its namespace with the template, so the bare
    ``globals()`` enumeration used to return the tool's own imports and
    helpers (``json``, ``cm``, ``get_variables``) on every call — names no
    notebook defines. Pre-fix this test pinned that defect by asserting
    ``"json" in variables``; dropping the scaffold-name filter leaks those
    names back into the mapping and fails here.
    """
    result = await live_client.execute(live_session.session_id, TEMPLATE_VARIABLES)
    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    variables = data["variables"]
    scaffolding = {"json", "cm", "get_variables", "_is_ui", "_serialize"}
    leaked = scaffolding & set(variables)
    assert not leaked, f"Template scaffolding leaked into variables: {sorted(leaked)}"

    # "All" still reports the kernel's own globals. This session is never
    # instantiated, so no cell-defined names exist yet; the kernel-injected
    # `input` is the observable non-scaffolding name that must stay visible.
    assert "input" in variables, (
        f"Expected kernel-injected 'input'; got {sorted(variables)}"
    )
    assert variables["input"]["datatype"] == "function"


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
