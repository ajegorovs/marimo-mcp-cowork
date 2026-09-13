"""Normalization of list-typed MCP tool arguments.

A harness's MCP bridge may deliver a list-typed argument as a JSON-encoded
string instead of a native array — ``'["a", "b"]'`` rather than
``["a", "b"]``. That bridge is outside this package, so the handlers
normalize defensively instead of rejecting the call. Both the `cell_ids`
and `variable_names` parameters share this one implementation so the
accepted shapes cannot drift apart (the two used to be separate private
helpers that disagreed on JSON-encoded input).
"""

from __future__ import annotations

import json


def normalize_list_arg(value: str | list[str] | None) -> list[str]:
    """Normalize a list-typed argument into a list of names.

    Accepted forms:

    - ``None`` (the caller omitted the filter) -> ``[]``, meaning "all";
    - an empty or whitespace-only string -> ``[]``, the same "all": a blank
      string is this surface's own "not provided" convention (``session_id``
      and ``server_url`` default to ``""``), so it must never normalize to one
      literal empty name (whose lookup legitimately finds nothing and is
      indistinguishable from "this session defines no names");
    - a native array -> returned as a list;
    - a bare name (``"x"``) -> ``["x"]``;
    - a JSON array (``'["x", "y"]'``) -> ``["x", "y"]``, including the
      empty array ``"[]"`` -> ``[]``;
    - a JSON-encoded string (``'"x"'``) -> ``["x"]``.

    A non-empty string that does not decode to a JSON array/string is treated
    as one literal name, so a numeric-looking id (``"5"``) still addresses
    ``"5"`` rather than being coerced to an integer, and a malformed
    JSON-looking string (``"[Hbol"``) stays a literal target that the caller
    sees reported back.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return list(value)
    if not value.strip():
        return []
    decoded = _decode_json_container(value)
    return [value] if decoded is None else decoded


def _decode_json_container(text: str) -> list[str] | None:
    """Decode a JSON array/string to names, or return None if it is neither.

    None means "not a JSON container" — distinct from ``[]``, which is an
    explicitly empty JSON array.
    """
    stripped = text.strip()
    if not stripped.startswith(("[", '"')):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, str):
        return [parsed]
    if isinstance(parsed, list) and all(
        isinstance(item, (str, int, float)) and not isinstance(item, bool)
        for item in parsed
    ):
        return [str(item) for item in parsed]
    return None
