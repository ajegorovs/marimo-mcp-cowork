"""Kernel-free gates for the wrapper-arm session-identity probe's own logic.

The live finding (a recorded session id silently reaching a different notebook)
is established by ``benchmark/wrapper_churn_probe.py`` against a disposable
loopback server and recorded in ``benchmark/evidence/wrapper_session_reuse_20261016.json``.
These tests cover only the probe's pure helpers, so a refactor that quietly
breaks its census lookup or copy construction is caught without a kernel.

Loaded the same way as the sibling benchmark gates: the probe module is executed
from disk with ``importlib``.
"""

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "benchmark" / "wrapper_churn_probe.py"
spec = importlib.util.spec_from_file_location(
    "benchmark_wrapper_churn_probe", MODULE_PATH
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_id_for_path_finds_the_current_session_for_a_notebook(tmp_path):
    """After a rebind the ids move, so the id must be re-derived, not cached."""
    copy_a = tmp_path / "a.py"
    copy_b = tmp_path / "b.py"
    mapping = {
        "new-a": probe.norm(str(copy_a)),
        "recorded-a": probe.norm(str(copy_b)),
    }
    assert probe.id_for_path(mapping, copy_a) == "new-a"
    # The stale id now serves b: re-deriving is what surfaces the substitution.
    assert probe.id_for_path(mapping, copy_b) == "recorded-a"


def test_id_for_path_returns_none_for_unserved_notebook(tmp_path):
    assert probe.id_for_path({}, tmp_path / "absent.py") is None


def test_make_copy_inserts_exactly_one_tag_cell_before_main():
    tagged = probe.make_copy(probe.TAG_A).decode("utf-8")
    original = probe.FIXTURE.read_text(encoding="utf-8")
    assert tagged.count(f"probe_churn_tag = {probe.TAG_A!r}") == 1
    assert tagged.index("probe_churn_tag") < tagged.index('if __name__ == "__main__":')
    # The fixture body is otherwise carried through unchanged.
    for line in original.splitlines():
        if line.startswith(("@app.cell", "import")):
            assert line in tagged


def test_make_copy_keeps_copy_paths_distinguishable(tmp_path):
    """The two arms must be separable by tag, or a wrong target is invisible."""
    a = probe.make_copy(probe.TAG_A)
    b = probe.make_copy(probe.TAG_B)
    assert a != b
    assert probe.TAG_A.encode() in a and probe.TAG_A.encode() not in b


def test_run_cli_style_helper_is_absent_so_probe_stays_client_driven():
    """Guard against reintroducing a CLI subprocess into the wrapper arm.

    This probe must exercise the repo's own client; if it grew a `run_cli`
    helper the arm would be measuring the native CLI instead.
    """
    assert not hasattr(probe, "run_cli")


@pytest.mark.parametrize("name", ["census", "wait_for_census", "try_execute"])
def test_expected_async_seams_exist(name):
    assert callable(getattr(probe, name, None))
