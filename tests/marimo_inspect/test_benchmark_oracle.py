"""Static, kernel-free gates for the published comparison fixture."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "benchmark" / "notebooks" / "mission_clean.py"
spec = importlib.util.spec_from_file_location(
    "benchmark_oracle", ROOT / "benchmark" / "oracle.py"
)
assert spec is not None and spec.loader is not None
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def test_fixture_and_independent_metric():
    oracle.verify_fixture(FIXTURE)
    assert {
        gain: oracle.expected_metric(gain) for gain in oracle.EXPECTED
    } == oracle.EXPECTED


def test_initial_copies_are_distinct_and_byte_identical(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(FIXTURE.read_bytes())
    b.write_bytes(FIXTURE.read_bytes())
    assert oracle.verify_initial_copies(FIXTURE, a, b)
    b.write_bytes(b.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="bytes differ"):
        oracle.verify_initial_copies(FIXTURE, a, b)
    with pytest.raises(ValueError, match="distinct"):
        oracle.verify_initial_copies(FIXTURE, a, a)
