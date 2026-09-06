"""Live integration tests for the variables template.

Behavioral assertions: against a live kernel the template returns status ok
and inspects the kernel-visible globals (e.g. the `json` module injected by
the kernel; a scratchpad-defined helper). It must not raise when optional
dependencies (numpy/pandas) are absent.
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
async def test_variables_sees_kernel_injected_global(live_client, live_session):
    """The `json` module (kernel-injected) is visible to the template."""
    result = await live_client.execute(live_session.session_id, TEMPLATE_VARIABLES)
    assert result.status == "ok"
    data = json.loads(result.stdout[0])

    variables = data["variables"]
    assert "json" in variables, (
        f"Expected kernel-injected 'json'; got {sorted(variables)}"
    )
    # json is a module; datatype reflects that.
    assert variables["json"]["datatype"] == "module"


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
