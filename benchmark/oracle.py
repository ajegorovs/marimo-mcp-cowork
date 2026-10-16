"""Independent, stdlib-only checks for disposable comparison inputs.

The oracle is intentionally not imported by either benchmark arm. It checks
initial bytes and the numeric expectation, not live execution or persistence.
"""

from __future__ import annotations

import ast
import hashlib
import statistics
from pathlib import Path

INPUT_VECTOR = (2, 4, 6, 8)
EXPECTED = {2: 1.25, 4: 2.5}


def expected_metric(gain: int) -> float:
    """Compute the mission's normalized diagnostic from independent inputs."""
    return statistics.fmean(abs(gain * x) for x in INPUT_VECTOR) / max(
        abs(x) for x in INPUT_VECTOR
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_initial_copies(canonical: Path, arm_a: Path, arm_b: Path) -> str:
    """Reject missing, aliased or non-identical initial notebook copies."""
    paths = (canonical, arm_a, arm_b)
    if any(not path.is_file() for path in paths):
        raise ValueError("all three notebook files must exist")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("arms must use distinct notebook files")
    hashes = [digest(path) for path in paths]
    if len(set(hashes)) != 1:
        raise ValueError("initial notebook bytes differ")
    return hashes[0]


def verify_fixture(path: Path) -> None:
    """Reject accidental mission-answer leaks or changed fixture structure."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cells = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Attribute) and decorator.attr == "cell"
            for decorator in node.decorator_list
        )
    ]
    if len(cells) != 5:
        raise ValueError("fixture must have five cells")
    text = path.read_text(encoding="utf-8")
    for marker in (
        "mo.ui.slider(1, 4, value=2",
        "np.array([2, 4, 6, 8])",
        "gain.value * input_vector",
        "ax.plot(",
    ):
        if marker not in text:
            raise ValueError(f"fixture missing {marker}")
    if "normalized" in text:
        raise ValueError("fixture contains the blind mission's answer")
    if {gain: expected_metric(gain) for gain in EXPECTED} != EXPECTED:
        raise ValueError("independent numeric expectations changed")
