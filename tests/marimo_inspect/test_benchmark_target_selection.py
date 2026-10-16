"""Fail-closed selector contract for the native benchmark adapter."""

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "benchmark" / "target_selection.py"
spec = importlib.util.spec_from_file_location("benchmark_target_selection", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SelectorRefusal = module.SelectorRefusal
select_native_target = module.select_native_target


@pytest.mark.parametrize(
    ("selectors", "expected"),
    [
        ({"file_path": "B.py"}, ["--file", "B.py"]),
        ({"session_id": "A"}, ["--session", "A"]),
        ({"file_path": " B.py "}, ["--file", " B.py "]),
    ],
)
def test_exactly_one_preserves_literal_value(selectors, expected):
    assert select_native_target(**selectors) == expected


@pytest.mark.parametrize(
    "selectors",
    [
        {},
        {"file_path": "B.py", "session_id": "A"},
        {"file_path": "B.py", "session_id": "B"},
        {"file_path": "missing.py", "session_id": "A"},
        {"file_path": "", "session_id": "A"},
        {"file_path": "B.py", "session_id": " "},
        {"file_path": ""},
        {"session_id": " \t"},
        {"file_path": 123},
    ],
)
def test_ambiguous_missing_or_invalid_selector_refuses(selectors):
    with pytest.raises(SelectorRefusal):
        select_native_target(**selectors)
