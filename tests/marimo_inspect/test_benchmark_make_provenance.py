"""Kernel-free gates for the provenance bundler (``benchmark.make_provenance``).

The bundle is what a reviewer who cannot reach this machine actually reads, so
these tests pin its two promises: a hole in the batch refuses the whole bundle,
and no machine-specific path survives into the committed text.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"


def require(condition, *diagnostics):
    """Always-on verdict check; a bare ``assert`` is stripped under ``python -O``."""
    if condition:
        return
    if not diagnostics:
        raise AssertionError("required condition failed")
    raise AssertionError(diagnostics[0] if len(diagnostics) == 1 else diagnostics)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"no spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


provenance = _load("benchmark_make_provenance", BENCH / "make_provenance.py")


def _run(batch: Path, name: str, *, arm="wrapper", graded=True) -> Path:
    run = batch / name
    (run / "root").mkdir(parents=True, exist_ok=True)
    (run / "transcript.txt").write_text(
        f"agent read {batch}\\\\root\\\\notebook.py and wrote it\n",
        encoding="utf-8",
    )
    (run / "transcript.meta.json").write_text(
        json.dumps(
            {
                "arm": arm,
                "exit_code": 0,
                "started_at": "2026-10-17T00:00:00+00:00",
                "ended_at": "2026-10-17T00:01:00+00:00",
                "fingerprint": "f" * 64,
                "mission_sha256": "a" * 64,
                "interface_sha256": "b" * 64,
                "toolsets": ["marimo-inspect"],
                "terminal_state": "completed",
                "elapsed_s": 60.0,
            }
        ),
        encoding="utf-8",
    )
    if graded:
        (run / "grade.json").write_text(
            json.dumps(
                {
                    "pass": True,
                    "checks": [{"name": "canonical_fixture_unchanged", "pass": True}],
                    "observed": {"normalized": 1.25},
                }
            ),
            encoding="utf-8",
        )
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "root" / "notebook.py").write_text("import marimo\n", encoding="utf-8")
    return run


def test_a_complete_batch_yields_an_index_with_every_run(tmp_path):
    batch = tmp_path / "batch"
    _run(batch, "run1-wrapper")
    _run(batch, "run2-native", arm="native")

    out = tmp_path / "bundle"
    index = provenance.build_bundle(batch, out, parameters={"model": "m"})

    require(
        [record["run"] for record in index["runs"]] == ["run1-wrapper", "run2-native"]
    )
    require(index["parameters"] == {"model": "m"}, index["parameters"])
    require((out / "index.json").is_file(), "the index must be written")
    require((out / "runs" / "run2-native" / "transcript.md").is_file())


def test_machine_paths_do_not_survive_into_the_bundle(tmp_path):
    batch = tmp_path / "batch"
    _run(batch, "run1-wrapper")

    out = tmp_path / "bundle"
    provenance.build_bundle(batch, out)
    text = (out / "runs" / "run1-wrapper" / "transcript.md").read_text(encoding="utf-8")

    require("<batch>" in text, text)
    require(str(batch).split("\\")[0] + ":\\" not in text, text)
    require("mhd01" not in text.lower(), text)


def test_absent_artifacts_are_recorded_as_null(tmp_path):
    batch = tmp_path / "batch"
    _run(batch, "run2-native", arm="native")

    index = provenance.build_bundle(batch, tmp_path / "bundle")
    artifacts = index["runs"][0]["artifacts"]

    require(set(artifacts) == set(provenance.ARTIFACT_FILENAMES), artifacts)
    require(artifacts["root/ledger.jsonl"] is None, artifacts)
    require(isinstance(artifacts["manifest.json"], str), artifacts)


def test_a_run_without_a_grade_refuses_the_whole_bundle(tmp_path):
    batch = tmp_path / "batch"
    _run(batch, "run1-wrapper")
    _run(batch, "run2-wrapper", graded=False)

    with pytest.raises(provenance.BundleRefusal) as caught:
        provenance.build_bundle(batch, tmp_path / "bundle")

    require("run2-wrapper" in str(caught.value), str(caught.value))
    require(not (tmp_path / "bundle" / "index.json").exists(), "no partial bundle")


def test_an_empty_batch_directory_refuses(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    with pytest.raises(provenance.BundleRefusal):
        provenance.build_bundle(batch, tmp_path / "bundle")


def test_a_missing_batch_directory_refuses(tmp_path):
    with pytest.raises(provenance.BundleRefusal):
        provenance.build_bundle(tmp_path / "absent", tmp_path / "bundle")


def test_the_cli_refuses_with_json_on_stderr(tmp_path, capsys):
    code = provenance.main(
        ["--batch", str(tmp_path / "absent"), "--out", str(tmp_path / "bundle")]
    )
    captured = capsys.readouterr()

    require(code == 1, code)
    payload = json.loads(captured.err.strip().splitlines()[-1])
    require(payload["error"] == "bundle_refused", payload)
    require(captured.out.strip() == "", captured.out)
