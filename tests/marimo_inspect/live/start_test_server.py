"""Start a marimo server for live integration tests.

Usage:
    uv run python tests/marimo_inspect/live/start_test_server.py

This starts a marimo server with a test notebook in headless mode.
The server runs until interrupted (Ctrl+C).

After starting, run the live tests:
    uv run pytest tests/marimo_inspect/live/ -v -m live
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main():
    # Use the test_marimo.py notebook
    repo_root = Path(__file__).parents[2]
    notebook = repo_root / "notebooks" / "test_marimo.py"

    if not notebook.exists():
        print(f"Notebook not found: {notebook}")
        sys.exit(1)

    print("Starting marimo server for live tests...")
    print(f"Notebook: {notebook}")
    print("Press Ctrl+C to stop.\n")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            str(notebook),
            "--no-token",
            "--headless",
            "--port",
            "0",
            "--host",
            "127.0.0.1",
        ],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping marimo server...")
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
