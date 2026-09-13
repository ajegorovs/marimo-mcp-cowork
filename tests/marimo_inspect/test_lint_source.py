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

# Two trivially empty cells (the second comment-only). marimo's lint reports
# MF004 `empty-cells` for each, and diag.cell_id is the cell's *file position*
# in the parsed document — index 0 and 1 here, unrelated to any live session id.
EMPTY_CELL_NOTEBOOK = """\
import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    return


@app.cell
def _():
    # only a comment
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


# ---------------------------------------------------------------------------
# F5 — a diagnostic locates a SOURCE-FILE position, never a live session id.
# ---------------------------------------------------------------------------


async def test_diagnostic_reports_a_positional_cell_index_not_a_cell_id():
    """The positional index is `cell_index`; `cell_id` is a live-session id.

    marimo's ``diag.cell_id`` is the flagged cell's position in the parsed
    document, but the surface-wide convention reserves ``cell_id`` for the live
    ids ``get_cell_map`` returns. Reporting the position under ``cell_id``
    invited callers to feed it to ``get_cell_data``/``edit_cell``, which answer
    ``missing_cell_ids``/not-found — the F5 finding. The field is therefore
    exposed as ``cell_index``, and no diagnostic claims a live id.
    """
    result = await _lint_source(EMPTY_CELL_NOTEBOOK, "<test>")

    diags = [d for d in result["diagnostics"] if d["rule"] == "MF004"]
    assert len(diags) == 2, result["diagnostics"]

    for diag in diags:
        # The renamed positional field — and no live-id claim.
        assert "cell_id" not in diag, diag
        assert "cell_index" in diag, diag
        # The usable locator is the source-file position.
        assert diag["filename"] == "<test>", diag
        assert isinstance(diag["line"], int), diag

    # Indices are the document positions, in document order.
    assert [d["cell_index"] for d in diags] == ["0", "1"], diags
    # The rest of the diagnostic schema is unchanged.
    assert all(
        d["name"] == "empty-cells" and d["severity"] == "formatting" for d in diags
    ), diags
