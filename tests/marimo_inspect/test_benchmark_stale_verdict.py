"""Optimized-mode verdict regression for the native stale-refusal probe.

``benchmark/native_stale_probe.py`` gates an independent-actor stale refusal
(CLI read → raw ``POST /api/document/transaction`` → guarded edit must trip
``StaleCellError``, changing neither the live source nor the saved copy). That
verdict is worthless if its checks are bare ``assert`` statements: CPython
compiles asserts away under ``python -O``, so the probe would print
``NATIVE_STALE_PROBE_PASS`` on a run whose observations are wrong.

This kernel-free regression drives the probe's *actual* verdict/setup
entrypoints — ``verify_stale_refusal``, ``verify_session_binding`` and
``load_execute_payload`` — inside ``-O`` subprocesses with controlled
observations. No marimo server or kernel is ever booted, and nothing on disk
outside ``tmp_path`` is touched.

- valid observations must pass;
- each tampered observation set must fail loudly (nonzero exit, no
  ``DRIVER_DONE`` marker), proving the checks survive ``-O``;
- a non-JSON ``pair execute`` stdout must never be decoded into a passing
  structured payload, and the raw stdout/stderr must survive into the failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "benchmark" / "native_stale_probe.py"

#: Python driver run under ``-O``. It loads the probe module by path (no kernel)
#: and calls the real verdict/setup entrypoints with controlled observations.
#: It deliberately avoids a bare ``assert`` so the driver itself cannot be
#: optimized away; every required outcome uses an explicit ``raise``.
_DRIVER = r"""
import importlib.util
import json
import sys

PROBE, MODE = sys.argv[1], sys.argv[2]

_spec = importlib.util.spec_from_file_location(
    "native_stale_probe_under_test", PROBE
)
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def _entrypoint(name):
    return getattr(module, name)


_STALE_MESSAGE = (
    "StaleCellError: cell 'x' was modified since the agent last read it"
)

VALID_OBSERVATIONS = {
    "attempt_exit": 1,
    "attempt_payload": {"success": False, "stdout": "", "stderr": _STALE_MESSAGE},
    "attempt_kernel_stderr": _STALE_MESSAGE,
    "live_before": "actor",
    "live_after": "actor",
    "actor_code": "actor",
    "original_code": "original",
    "disk_before": b"saved",
    "disk_after": b"saved",
    "canonical_before": b"canonical",
    "canonical_after": b"canonical",
}


def _tampered(overrides):
    observations = dict(VALID_OBSERVATIONS)
    observations.update(overrides)
    return observations


if MODE == "inspect":
    print(
        json.dumps(
            {
                "verdict": callable(getattr(module, "verify_stale_refusal", None)),
                "binding": callable(
                    getattr(module, "verify_session_binding", None)
                ),
                "loader": callable(getattr(module, "load_execute_payload", None)),
            }
        )
    )
    raise SystemExit(0)

if MODE == "valid":
    _entrypoint("verify_stale_refusal")(dict(VALID_OBSERVATIONS))
    _entrypoint("verify_session_binding")(
        {"s-1": {"path": "C:/tmp/copy.py"}}, "s-1", "C:/tmp/copy.py"
    )
    loaded = _entrypoint("load_execute_payload")(0, '{"success": true}', "")
    if loaded != {"success": True}:
        raise SystemExit("loader returned %r" % (loaded,))

if MODE == "invalid_refusal":
    # Every observable is the wrong outcome: the guarded edit did not fail, the
    # payload claims success, no stale marker is present, and both the live and
    # saved sources changed.
    _entrypoint("verify_stale_refusal")(
        _tampered(
            {
                "attempt_exit": 0,
                "attempt_payload": {"success": True, "stdout": "wrote"},
                "attempt_kernel_stderr": "",
                "live_before": "original",
                "live_after": "attempt",
                "disk_before": b"saved",
                "disk_after": b"changed",
                "canonical_before": b"canonical",
                "canonical_after": b"mutated",
            }
        )
    )

if MODE == "invalid_binding":
    # Census names a different session on a different file.
    _entrypoint("verify_session_binding")(
        {"s-other": {"path": "C:/tmp/other.py"}}, "s-1", "C:/tmp/copy.py"
    )

if MODE == "nonjson":
    _entrypoint("load_execute_payload")(
        1, "Traceback (most recent call last): boom", "raw stderr text"
    )

print("DRIVER_DONE")
"""


def _run_probe_driver(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    """Run the ``-O`` driver against the real probe for one *mode*."""
    driver = tmp_path / "probe_verdict_driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = os.environ.copy()
    env["MARIMO_BENCHMARK_SCRATCH"] = str(tmp_path / "probe-scratch")
    return subprocess.run(
        [sys.executable, "-O", str(driver), str(PROBE), mode],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def test_probe_exposes_verdict_and_setup_entrypoints(tmp_path):
    """The probe must factor its checks where they can be run without a kernel."""
    proc = _run_probe_driver(tmp_path, "inspect")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    found = json.loads(proc.stdout.strip().splitlines()[-1])
    assert found == {"verdict": True, "binding": True, "loader": True}, found


def test_valid_observations_pass_under_optimized_interpreter(tmp_path):
    """A correct observation set must still pass, so the guards are not blanket."""
    proc = _run_probe_driver(tmp_path, "valid")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "DRIVER_DONE" in proc.stdout


@pytest.mark.parametrize("mode", ["invalid_refusal", "invalid_binding"])
def test_tampered_observations_fail_under_optimized_interpreter(tmp_path, mode):
    """Tampered observations must fail loudly even when asserts are stripped."""
    proc = _run_probe_driver(tmp_path, mode)
    assert proc.returncode != 0, (
        f"{mode}: an optimized run did NOT reject tampered observations "
        f"(false PASS)\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "DRIVER_DONE" not in proc.stdout, proc.stdout


def test_non_json_execute_output_never_passes_structured_checks(tmp_path):
    """Non-JSON CLI output must be refused, with its raw streams preserved."""
    proc = _run_probe_driver(tmp_path, "nonjson")
    assert proc.returncode != 0, (
        f"non-JSON stdout was decoded into a passing payload\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "non-JSON" in combined, combined
    assert "Traceback (most recent call last): boom" in combined, combined
    assert "raw stderr text" in combined, combined
