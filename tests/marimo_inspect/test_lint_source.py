"""Unit tests for server-side notebook linting (_lint_source).

No kernel or live server needed — linting runs in-process against marimo's
static lint engine, the same engine behind ``marimo check``. These tests
exercise ``_lint_source`` against real notebook source to confirm it returns
the expected structure and handles edge cases without raising.
"""

from __future__ import annotations

from marimo_inspection.tools.lint import _lint_source

CLEAN_NOTEBOOK = """\
import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    return
"""


async def test_lint_returns_expected_structure():
    """_lint_source returns a well-formed summary + diagnostics."""
    result = await _lint_source(CLEAN_NOTEBOOK, "<test>")
    assert "summary" in result
    assert "diagnostics" in result
    summary = result["summary"]
    assert "total_issues" in summary
    assert "breaking_issues" in summary
    assert "runtime_issues" in summary
    assert "formatting_issues" in summary
    assert result["summary"]["total_issues"] == len(result["diagnostics"])


async def test_lint_clean_notebook():
    """A cell-less marimo scaffold reports no breaking issues."""
    result = await _lint_source(CLEAN_NOTEBOOK, "<test>")
    assert result["summary"]["breaking_issues"] == 0


async def test_lint_unparseable_source():
    """Malformed source is handled gracefully, not raised."""
    result = await _lint_source("this is ((( not python", "<test>")
    assert "summary" in result
    assert "diagnostics" in result
