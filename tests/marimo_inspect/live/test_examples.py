"""Live smoke + interaction regressions for the consumer-facing examples.

Purpose
-------
``examples/`` holds the marimo usage patterns a consumer copies from. They are
**not** fixtures (see ``examples/README.md``): no test imports them, and the
package never needs them. That contract used to leave them outside every
automated gate, so this module *is* their gate — and it deliberately stops at
booting them, never importing them:

* ``test_every_tracked_example_runs_green`` — parametrized over the tracked
  ``examples/**/*.py`` tree (discovered, never hardcoded): each example is
  copied into ``tmp_path``, booted on its own headless server through the
  ``notebook_server`` factory, and run whole through the real
  ``run_cell(mode="all")`` handler. The pass condition is ``status: ok`` with no
  failed / not-run / unverified target, so a marimo bump that invalidates an
  example's claims fails here instead of rotting silently.
* the two interaction regressions drive each shipped pattern's live controls
  through the real ``set_ui_value`` handler and read the outcome back with
  ``get_variables``:

  - the step buttons must move the ONE shared ``mo.state`` index of
    ``slider_with_step_buttons.py`` — repeated advancing counters, backward
    steps, clamping at both ends — with the slider re-seeded from the state;
  - changing the cascade parent of ``cascading_sidebar_controls.py`` must
    rebuild the child (new options, selection reset) and re-derive the result.

Scope
-----
A *frontend rendering* claim is out of reach here: this suite boots kernels,
not browsers (the repo-wide gap recorded in ``docs/live-tests.md``). What is
proven is that every example cell executes cleanly in a real 0.24.x kernel and
that the reactivity each example documents actually happens in-kernel.

Isolation: the examples are read from the repo and copied to ``tmp_path`` (the
``notebook_server`` factory mounts the copy), and each test asserts the repo
file is byte-identical afterwards — these tests must never edit an example.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marimo_inspection.tools.mutation import run_cell
from marimo_inspection.tools.ui import set_ui_value
from marimo_inspection.tools.variables import get_variables

REPO_ROOT = Path(__file__).parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples"


def _tracked_example_paths() -> list[Path]:
    """Every tracked ``examples/**/*.py``, discovered — never hardcoded.

    On the git-index path an untracked scratch file in ``examples/`` cannot
    silently join (or fail) the smoke gate. When the tree is not a git
    checkout the helper falls back to a filesystem walk, and that path carries
    no such guarantee — it sees whatever ``*.py`` is on disk under
    ``examples/``.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", "examples"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(EXAMPLES_DIR.rglob("*.py"))
    return sorted(
        REPO_ROOT / line for line in proc.stdout.splitlines() if line.endswith(".py")
    )


TRACKED_EXAMPLES = _tracked_example_paths()


def _example(filename: str) -> Path:
    """The tracked example with this basename (fails the test if absent)."""
    for path in TRACKED_EXAMPLES:
        if path.name == filename:
            return path
    raise AssertionError(
        f"tracked example {filename!r} not found under examples/; discovered: "
        f"{[str(p.relative_to(REPO_ROOT)) for p in TRACKED_EXAMPLES]}"
    )


def _inner_value(payload: dict, name: str):
    """Read a variable's value from ``get_variables`` output.

    A scalar arrives as ``{name: {"value": "<str>", "datatype": ...}}`` — the
    serialized form is a string — while a UI element nests one level deeper
    (``{"value": {"value": ..., ...}}``), so both shapes are unwrapped.
    """
    node = payload.get("variables", {}).get(name)
    if isinstance(node, dict):
        inner = node.get("value")
        if isinstance(inner, dict):
            return inner.get("value")
        return inner
    return None


async def _state(server_url: str, session_id: str) -> dict:
    """One unfiltered ``get_variables`` read of the live session."""
    payload = await get_variables(session_id=session_id, server_url=server_url)
    assert "variables" in payload, payload
    return payload


def _text(payload: dict, name: str) -> str:
    """A variable's serialized value as text (fails loudly when absent)."""
    value = _inner_value(payload, name)
    assert value is not None, (name, payload)
    return str(value)


@pytest.mark.live
def test_the_example_tree_is_discovered():
    """Guard: an empty discovery would make the smoke gate vacuous."""
    assert TRACKED_EXAMPLES, (
        "no tracked examples discovered — the example smoke gate would run "
        "nothing. Check `git ls-files examples` and EXAMPLES_DIR."
    )


@pytest.mark.live
@pytest.mark.parametrize(
    "example_path",
    TRACKED_EXAMPLES,
    ids=[str(p.relative_to(REPO_ROOT)) for p in TRACKED_EXAMPLES],
)
async def test_every_tracked_example_runs_green(example_path, notebook_server):
    """Boot an example on a real kernel and run every cell of it.

    The example is copied to ``tmp_path`` (never mounted from the repo) and
    booted by the existing ``notebook_server`` factory; ``run_cell(mode="all")``
    then executes the whole document through the real handler. ``status: ok``
    with no failed / not-run / unverified target is the contract — every
    requested cell must end ``idle`` with a readable, empty error channel.
    """
    original = example_path.read_bytes()
    source = example_path.read_text()
    _manager, server_url, session_id, _copy = await notebook_server(
        source, name=example_path.name
    )

    result = await run_cell(mode="all", session_id=session_id, server_url=server_url)

    assert result["status"] == "ok", result
    assert result["mode"] == "all", result
    # Every ``@app.cell`` in the source must be in the run plan: a target
    # silently dropped from `run_cell`'s plan would otherwise leave this smoke
    # green, so pin the planned count to the decorator count.
    expected_cells = source.count("@app.cell")
    assert result["counts"]["requested"] == expected_cells, (
        f"run_cell planned {result['counts']['requested']} cell(s) but "
        f"{example_path.name} defines {expected_cells} `@app.cell` block(s) — "
        "a cell was omitted from the run plan."
    )
    assert len(result["cells"]) == expected_cells, result
    assert result["cells"], result
    assert result["failed_cell_ids"] == [], result
    assert result["not_run_cell_ids"] == [], result
    assert result["unverified_cell_ids"] == [], result
    assert result["succeeded_cell_ids"], result
    assert result["counts"]["failed"] == 0, result
    assert result["counts"]["not_run"] == 0, result
    assert result["counts"]["succeeded"] == result["counts"]["requested"], result
    for cell in result["cells"]:
        assert cell["runtime_state"] == "idle", cell
        assert cell["errors"] == [], cell
        assert cell["errors_readable"] is True, cell

    # Hermeticity: the example on disk is never the mounted document.
    assert example_path.read_bytes() == original, (
        f"{example_path.relative_to(REPO_ROOT)} changed during the example "
        "smoke test — examples/ must stay untouched."
    )


@pytest.mark.live
async def test_slider_example_buttons_move_the_shared_state(notebook_server):
    """The step buttons of ``slider_with_step_buttons.py`` really move the index.

    Every button writes the ONE shared ``mo.state`` via ``set_index``; the
    slider cell re-runs and re-seeds itself from ``get_index()``, and the
    downstream ``index`` cell re-derives ``int(step_slider.value)``. So a click
    must move ``index`` *and* ``step_slider`` together, in both directions,
    across repeated advancing counters, and clamp at both ends.
    """
    example = _example("slider_with_step_buttons.py")
    original = example.read_bytes()
    _manager, server_url, session_id, _copy = await notebook_server(
        example.read_text(), name=example.name
    )

    boot = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert boot["status"] == "ok", boot

    start = await _state(server_url, session_id)
    # The expectations below are the example's own constants, not invented:
    # N_X = 48 profiles, LAST = 47, coarse = max(1, LAST // 4) = 11.
    assert _inner_value(start, "LAST") == "47", start
    assert _inner_value(start, "coarse") == "11", start
    assert _inner_value(start, "index") == "0", start
    assert _inner_value(start, "step_slider") == "0", start

    async def click(name: str, counter: int) -> dict:
        """Click a step button (the frontend counter is the value)."""
        result = await set_ui_value(
            name, counter, session_id=session_id, server_url=server_url
        )
        assert result["status"] == "ok", result
        assert result["handler_invoked"] is True, result
        return result

    async def expect(index: str, *, after: str) -> None:
        """The shared state moved and the slider was re-seeded from it."""
        payload = await _state(server_url, session_id)
        assert _inner_value(payload, "index") == index, (after, payload)
        assert _inner_value(payload, "step_slider") == index, (after, payload)

    # Repeated ADVANCING counters: each click advances the one shared value.
    await click("step_fwd", 1)
    await expect("1", after="step_fwd #1")
    await click("step_fwd", 2)
    await expect("2", after="step_fwd #2")
    await click("step_fwd", 3)
    await expect("3", after="step_fwd #3")

    # ...and backward.
    await click("step_back", 1)
    await expect("2", after="step_back #1")
    await click("step_back", 2)
    await expect("1", after="step_back #2")
    await click("step_back", 3)
    await expect("0", after="step_back #3")

    # Clamped at the bottom: the handler clamps, the value never goes negative.
    await click("step_back", 4)
    await expect("0", after="step_back #4 (clamped)")

    # The coarse pair moves a quarter of the axis, and clamps at the top.
    await click("step_far_fwd", 1)
    await expect("11", after="step_far_fwd #1")
    await click("step_far_back", 1)
    await expect("0", after="step_far_back #1")
    # Counters keep ADVANCING per button (`set_ui_value` reads the frontend
    # click counter back, so a repeated counter would report `handler_invoked:
    # null`); the state itself keeps walking to the top and clamps at LAST.
    for counter, expected in enumerate(["11", "22", "33", "44", "47", "47"], start=2):
        await click("step_far_fwd", counter)
        await expect(expected, after=f"step_far_fwd #{counter}")

    assert example.read_bytes() == original, (
        "slider_with_step_buttons.py changed during its live test."
    )


@pytest.mark.live
async def test_cascading_example_parent_change_rebuilds_the_child(notebook_server):
    """The cascade of ``cascading_sidebar_controls.py`` really re-derives.

    Level 2 reads ``parent_picker.value``, so marimo re-runs it whenever the
    parent changes: the child radio is rebuilt with the NEW option set and
    resets to its first entry, and the downstream ``result`` cell re-derives
    from both selections. The per-parent-memory variant (T-E6) is deliberately
    NOT part of the example, so switching back to a parent must NOT restore a
    remembered child — it resets again, which is what this test pins.
    """
    example = _example("cascading_sidebar_controls.py")
    original = example.read_bytes()
    _manager, server_url, session_id, _copy = await notebook_server(
        example.read_text(), name=example.name
    )

    boot = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert boot["status"] == "ok", boot

    start = await _state(server_url, session_id)
    # The example's own CONTENTS: group-a -> [alpha, beta, gamma]; sorted keys
    # put group-a first, and the child defaults to its first option.
    assert _inner_value(start, "parent_picker") == "group-a", start
    assert _inner_value(start, "child_picker") == "alpha", start
    assert _inner_value(start, "parent") == "group-a", start
    assert "group-a" in _text(start, "result"), start
    assert "alpha" in _text(start, "result"), start

    # Change the PARENT: the child's options come from it and the result
    # is re-derived from both selections.
    switched = await set_ui_value(
        "parent_picker", ["group-b"], session_id=session_id, server_url=server_url
    )
    assert switched["status"] == "ok", switched
    assert switched["verified"] is True, switched
    assert switched["applied"] is True, switched
    assert switched["value_before"] == "group-a", switched
    assert switched["value_after"] == "group-b", switched

    rebuilt = await _state(server_url, session_id)
    assert _inner_value(rebuilt, "parent") == "group-b", rebuilt
    # group-b -> [delta, epsilon]; the rebuilt child starts at the first entry.
    assert _inner_value(rebuilt, "child_picker") == "delta", rebuilt
    assert "group-b" in _text(rebuilt, "result"), rebuilt
    assert "delta" in _text(rebuilt, "result"), rebuilt

    # The child was rebuilt with group-b's options: a group-a-only option is
    # now rejected by the kernel, and one of the new options applies.
    stale_option = await set_ui_value(
        "child_picker", "alpha", session_id=session_id, server_url=server_url
    )
    assert stale_option["status"] == "error", stale_option
    assert stale_option["reason"] == "value_not_applied", stale_option
    still_delta = await _state(server_url, session_id)
    assert _inner_value(still_delta, "child_picker") == "delta", still_delta

    picked = await set_ui_value(
        "child_picker", "epsilon", session_id=session_id, server_url=server_url
    )
    assert picked["status"] == "ok", picked
    assert picked["applied"] is True, picked
    assert picked["value_after"] == "epsilon", picked
    derived = await _state(server_url, session_id)
    assert _inner_value(derived, "child_picker") == "epsilon", derived
    assert "epsilon" in _text(derived, "result"), derived

    # Switching back does NOT restore a per-parent memory: the example ships no
    # such memory (the recipe lives in examples/README.md instead), so the child
    # resets to group-a's first option again.
    back = await set_ui_value(
        "parent_picker", ["group-a"], session_id=session_id, server_url=server_url
    )
    assert back["status"] == "ok", back
    assert back["value_after"] == "group-a", back
    restored = await _state(server_url, session_id)
    assert _inner_value(restored, "child_picker") == "alpha", restored
    assert "group-a" in _text(restored, "result"), restored
    assert "alpha" in _text(restored, "result"), restored

    assert example.read_bytes() == original, (
        "cascading_sidebar_controls.py changed during its live test."
    )


# An implementation of the T-E6 recipe in ``examples/README.md``
# §Per-parent memory: the cascade of ``cascading_sidebar_controls.py`` plus ONE
# ``mo.state`` dict, so the child's selection is remembered per parent. Locals
# are renamed relative to the README snippet (``parent``/``child``,
# ``contents``); the cell structure is the same. It is deliberately NOT shipped
# under ``examples/`` (it is one control's ``value=``/``on_change=``, not a new
# kind of pattern), so it lives here as a test-local document — and this is
# what proves the recipe the README hands a consumer actually works.
REMEMBERED_CHILD_NOTEBOOK = """import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _():
    contents = {
        "group-a": ["alpha", "beta", "gamma"],
        "group-b": ["delta", "epsilon"],
    }
    return (contents,)


@app.cell
def _(mo):
    remembered, set_remembered = mo.state({})
    return remembered, set_remembered


@app.cell
def _(contents, mo):
    parent = mo.ui.dropdown(
        options=sorted(contents), value=sorted(contents)[0], label="Parent"
    )
    parent
    return (parent,)


@app.cell
def _(contents, mo, parent, remembered, set_remembered):
    selected = parent.value
    children = contents[selected]
    prior = remembered().get(selected)
    child = mo.ui.radio(
        options=children,
        value=prior if prior in children else children[0],
        on_change=lambda value: set_remembered({**remembered(), selected: value}),
        label="Child",
    )
    child
    return (child,)


@app.cell
def _(child, parent):
    summary = f"{parent.value} -> {child.value}"
    summary
    return (summary,)
"""


@pytest.mark.live
async def test_per_parent_memory_recipe_is_verified(notebook_server):
    """The README's per-parent-memory recipe really restores the child.

    The shipped cascade rebuilds the child on every parent change (asserted
    above). The recipe the README documents (``examples/README.md``
    §Per-parent memory) instead seeds the child's ``value=`` from a ``mo.state``
    dict keyed by parent and stores the pick in ``on_change``. This test-local
    document is an implementation of that recipe — the same cell structure with
    renamed locals — so the recipe is a verified claim rather than prose, and it
    is the reason no third example is shipped (T-E6).
    """
    _manager, server_url, session_id, _copy = await notebook_server(
        REMEMBERED_CHILD_NOTEBOOK, name="remembered_child.py"
    )

    boot = await run_cell(mode="all", session_id=session_id, server_url=server_url)
    assert boot["status"] == "ok", boot

    start = await _state(server_url, session_id)
    assert _inner_value(start, "parent") == "group-a", start
    assert _inner_value(start, "child") == "alpha", start

    # Pick a non-default child, which the recipe remembers for group-a.
    picked = await set_ui_value(
        "child", "beta", session_id=session_id, server_url=server_url
    )
    assert picked["status"] == "ok", picked
    assert picked["applied"] is True, picked
    assert _inner_value(await _state(server_url, session_id), "child") == "beta"

    # A parent with no memory yet falls back to its first option...
    switched = await set_ui_value(
        "parent", ["group-b"], session_id=session_id, server_url=server_url
    )
    assert switched["status"] == "ok", switched
    fresh = await _state(server_url, session_id)
    assert _inner_value(fresh, "child") == "delta", fresh
    # ...and that pick is remembered too.
    second = await set_ui_value(
        "child", "epsilon", session_id=session_id, server_url=server_url
    )
    assert second["status"] == "ok", second

    # Round trip back: the child is restored, per parent — the whole point.
    back = await set_ui_value(
        "parent", ["group-a"], session_id=session_id, server_url=server_url
    )
    assert back["status"] == "ok", back
    restored = await _state(server_url, session_id)
    assert _inner_value(restored, "child") == "beta", restored
    assert _text(restored, "summary") == "group-a -> beta", restored

    # And forward again to prove the memory is keyed, not a one-off.
    await set_ui_value(
        "parent", ["group-b"], session_id=session_id, server_url=server_url
    )
    keyed = await _state(server_url, session_id)
    assert _inner_value(keyed, "child") == "epsilon", keyed
    assert _text(keyed, "summary") == "group-b -> epsilon", keyed
