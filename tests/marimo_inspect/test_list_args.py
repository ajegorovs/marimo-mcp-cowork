"""Tests for list-typed argument normalization (`tools/args.py`).

A harness MCP bridge may deliver a list-typed argument as a JSON-encoded
string rather than a native array. The earlier per-module helpers wrapped
*any* string as one literal name, so ``'["a", "b"]'`` became
``['["a", "b"]']`` and the filter silently matched nothing — worse than the
loud pydantic error it replaced. These tests pin the accepted shapes and
their boundary (a numeric-looking id stays a string).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marimo_inspection.tools.args import normalize_list_arg


def _make_session(session_id: str = "abc123"):
    return MagicMock(session_id=session_id, file="/test.py", basename="test.py")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ([], []),
        (["a", "b"], ["a", "b"]),
        ("a", ["a"]),
        ('["a", "b"]', ["a", "b"]),
        ('["a"]', ["a"]),
        ("[]", []),
        ('["a", "b"] ', ["a", "b"]),  # surrounding whitespace
        ('"a"', ["a"]),  # double-encoded single name
        ("5", ["5"]),  # numeric-looking id is NOT coerced to int
        ("[1, 2]", ["1", "2"]),  # non-string scalars become names
        ("[not json", ["[not json"]),  # malformed -> one literal name
        ("true", ["true"]),  # JSON scalar (not a container) -> literal
        ("[true]", ["[true]"]),  # non-scalar elements -> literal
        ("[[1]]", ["[[1]]"]),  # nested -> literal
    ],
)
def test_normalize_list_arg_shapes(value, expected):
    assert normalize_list_arg(value) == expected


async def test_json_string_variable_names_reaches_the_template():
    """A JSON-encoded array filters for both names, not one literal string."""
    from marimo_inspection.tools.variables import get_variables

    with patch("marimo_inspection.tools.variables.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        instance.execute = AsyncMock(
            return_value=MagicMock(
                status="ok", stdout=['{"variables": {}, "tables": {}}']
            )
        )
        cls.return_value = instance

        result = await get_variables(
            session_id="abc123",
            variable_names='["data", "filtered"]',
            server_url="http://127.0.0.1:8090",
        )

    assert "variables" in result
    code = instance.execute.await_args.args[1]
    assert 'target_names = ["data", "filtered"]' in code


async def test_json_string_cell_ids_reaches_the_cell_data_template():
    """The same shape works for get_cell_data (the second list-typed tool)."""
    from marimo_inspection.tools.cells import get_cell_data

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        instance.execute = AsyncMock(
            return_value=MagicMock(status="ok", stdout=['{"data": []}'])
        )
        cls.return_value = instance

        result = await get_cell_data(
            session_id="abc123",
            cell_ids='["Xref", "BYtC"]',
            server_url="http://127.0.0.1:8090",
        )

    assert "data" in result
    code = instance.execute.await_args.args[1]
    assert 'cell_ids = ["Xref", "BYtC"]' in code


async def test_json_string_cell_ids_reaches_the_cell_outputs_template():
    """get_cell_outputs shares the normalizer, so it accepts the same shape."""
    from marimo_inspection.tools.cells import get_cell_outputs

    with patch("marimo_inspection.tools.cells.MarimoClient") as cls:
        instance = MagicMock()
        instance.resolve_session = AsyncMock(return_value=_make_session())
        instance.execute = AsyncMock(
            return_value=MagicMock(status="ok", stdout=['{"cells": []}'])
        )
        cls.return_value = instance

        result = await get_cell_outputs(
            session_id="abc123",
            cell_ids='["Xref"]',
            server_url="http://127.0.0.1:8090",
        )

    assert "cells" in result
    code = instance.execute.await_args.args[1]
    assert 'cell_ids = ["Xref"]' in code
