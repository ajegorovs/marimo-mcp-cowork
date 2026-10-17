"""Assemble the sanitized provenance bundle for one comparison batch.

The four-run comparison is only as useful as a reviewer's ability to check it.
The raw batch lives in scratch space and carries absolute machine paths, so this
script copies the parts that matter — per-run metadata, the independent grade
verdict, the transcript, and the artifact hashes including the native arm's
ledger — into the repository with every machine-specific path replaced.

What is deliberately **not** copied:

* the notebooks themselves (both are copies of the committed fixture, whose
  digest is recorded instead);
* provider credentials (never read by this script);
* anything outside the batch directory.

Contract
--------
``python make_provenance.py --batch <dir> --out <dir> --parameters <json>``

The batch directory is the layout ``run_comparison`` writes: one directory per
run (``run1-wrapper``, ``run2-native``, ...) containing ``transcript.txt``,
``transcript.meta.json``, ``grade.json``, ``manifest.json`` and — for the native
arm — ``root/ledger.jsonl``. A run that lacks a grade verdict refuses the whole
bundle: a provenance package with a hole in it would read as a complete one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

__all__ = [
    "ARTIFACT_FILENAMES",
    "BundleRefusal",
    "build_bundle",
    "sanitize_text",
    "sha256_file",
]

#: Files a run directory must carry before it may enter a bundle.
REQUIRED_FILES = ("transcript.txt", "transcript.meta.json", "grade.json")

#: Artifacts hashed for the bundle, relative to the run directory. Absent files
#: are recorded as ``null`` rather than silently omitted.
ARTIFACT_FILENAMES = ("manifest.json", "root/ledger.jsonl", "root/notebook.py")

#: Absolute Windows paths (drive letter) and POSIX home paths, replaced by a
#: placeholder that keeps the shape but drops the machine.
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s'\"<>|,;:]*")
_POSIX_HOME = re.compile(r"/(?:home|Users)/[^\s'\"<>|,;:]+")


class BundleRefusal(RuntimeError):
    """A bundle that cannot be assembled truthfully."""


def sha256_file(path: str | Path) -> str | None:
    """The file's SHA-256, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def sanitize_text(text: str, *, replacements: dict[str, str] | None = None) -> str:
    """Replace machine-specific paths so the text can be committed.

    ``replacements`` maps an absolute prefix to a placeholder and is applied
    first, longest prefix first, so the batch root becomes ``<batch>`` rather
    than a series of drive-letter fragments. Whatever remains is caught by the
    generic patterns.
    """
    for prefix, placeholder in sorted(
        (replacements or {}).items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(prefix, placeholder)
        text = text.replace(prefix.replace("\\", "/"), placeholder)
    text = _WINDOWS_PATH.sub("<local-path>", text)
    return _POSIX_HOME.sub("<local-path>", text)


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BundleRefusal(f"cannot read {path.name}: {error}") from error
    except ValueError as error:
        raise BundleRefusal(f"{path.name} is not JSON: {error}") from error
    if not isinstance(payload, dict):
        raise BundleRefusal(f"{path.name} is not a JSON object")
    return payload


def _run_directories(batch: Path) -> list[Path]:
    if not batch.is_dir():
        raise BundleRefusal(f"batch directory does not exist: {batch}")
    runs = sorted(entry for entry in batch.iterdir() if entry.is_dir() and entry.name)
    if not runs:
        raise BundleRefusal(f"batch directory holds no run: {batch}")
    for run in runs:
        missing = [name for name in REQUIRED_FILES if not (run / name).is_file()]
        if missing:
            raise BundleRefusal(
                f"{run.name} carries no {missing} — a bundle with a hole in it"
                " would read as a complete one"
            )
    return runs


def build_bundle(
    batch: str | Path,
    out: str | Path,
    *,
    parameters: dict | None = None,
    replacements: dict[str, str] | None = None,
) -> dict:
    """Write the bundle and return its index.

    Returns:
        The bundle index: parameters, one record per run, and the artifact hash
        map. Written to ``<out>/index.json`` as well, so the bundle states its
        own contents.
    """
    batch_path = Path(batch)
    out_path = Path(out)
    runs = _run_directories(batch_path)
    batch_replacements = {str(batch_path): "<batch>"}
    batch_replacements.update(replacements or {})

    records: list[dict] = []
    for run in runs:
        grade = _load_json(run / "grade.json")
        metadata = _load_json(run / "transcript.meta.json")
        # Every artifact is recorded, absent ones as null: a missing ledger must
        # read as "not captured", never as "not expected".
        artifacts = {name: sha256_file(run / name) for name in ARTIFACT_FILENAMES}
        transcript = sanitize_text(
            (run / "transcript.txt").read_text(encoding="utf-8", errors="replace"),
            replacements=batch_replacements,
        )
        target = out_path / "runs" / run.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "transcript.md").write_text(
            transcript, encoding="utf-8", newline="\n"
        )
        (target / "grade.json").write_text(
            json.dumps(grade, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        record = {
            "run": run.name,
            "arm": metadata.get("arm"),
            "grade_pass": grade.get("pass"),
            "checks": grade.get("checks"),
            "observed": grade.get("observed"),
            "metadata": {
                key: metadata.get(key)
                for key in (
                    "arm",
                    "exit_code",
                    "started_at",
                    "ended_at",
                    "fingerprint",
                    "mission_sha256",
                    "interface_sha256",
                    "toolsets",
                    "terminal_state",
                    "elapsed_s",
                )
            },
            "artifacts": artifacts,
        }
        (target / "metadata.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        records.append(record)

    index = {
        "what_this_is": (
            "Sanitized provenance for one restricted interface-comparison batch."
            " Absolute machine paths are replaced; nothing outside the batch is"
            " copied."
        ),
        "how_to_check": (
            "Each run records the independent grade verdict with its per-check"
            " detail and the SHA-256 of the manifest, the ledger (native arm"
            " only) and the saved notebook. The transcript is the agent's own"
            " output, unedited apart from path substitution."
        ),
        "parameters": parameters or {},
        "runs": records,
    }
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return index


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: build the bundle, or refuse with JSON on stderr."""
    parser = argparse.ArgumentParser(
        prog="make_provenance.py",
        description="Assemble a sanitized provenance bundle for one batch.",
    )
    parser.add_argument("--batch", required=True, help="batch directory to read")
    parser.add_argument("--out", required=True, help="bundle directory to write")
    parser.add_argument(
        "--parameters",
        default=None,
        help="optional JSON file of run parameters to record in the index",
    )
    args = parser.parse_args(argv)
    try:
        parameters = _load_json(Path(args.parameters)) if args.parameters else {}
        index = build_bundle(args.batch, args.out, parameters=parameters)
    except BundleRefusal as error:
        print(
            json.dumps({"error": "bundle_refused", "detail": str(error)}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"runs": len(index["runs"]), "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
