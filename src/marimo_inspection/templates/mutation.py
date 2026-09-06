"""Scratchpad templates for mutating notebook cells.

Each template returns a JSON payload on stdout describing the outcome, so the
MCP tool handler can parse a validated result. Mutation goes through marimo's
`marimo._code_mode` API (the same object the pairing scripts use) rather than
novel kernel machinery.
"""

from __future__ import annotations

import json


def _py_bool(value: bool) -> str:
    """Render a Python bool as a Python literal (True/False, not JSON)."""
    return "True" if value else "False"


def _json_src(code: str) -> str:
    """Encode a source string as a Python literal for safe injection."""
    return json.dumps(code)


# Shared preamble: get the live session context and expose helpers.
_PREAMBLE = """
import json
import marimo._code_mode as cm


def _code_hash(code):
    import hashlib
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]


async def _ctx_summary():
    \"\"\"Return {cell_id -> source-hash} for the whole session.\"\"\"
    async with cm.get_context() as ctx:
        out = {}
        for c in ctx.cells:
            code = getattr(c, "code", "") or ""
            out[str(c.id)] = _code_hash(code)
        return out
"""


def build_create_cell_template(
    source: str,
    *,
    name: str | None = None,
    hide_code: bool = True,
    after: str | None = None,
    before: str | None = None,
) -> str:
    """Build a scratchpad snippet that creates a new cell."""
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "    async with cm.get_context() as ctx:\n"
        + f"        cid = ctx.create_cell({_json_src(source)}, "
        + (f"name={_json_src(name)}" if name is not None else "name=None")
        + f", hide_code={_py_bool(hide_code)}"
        + (f", after={_json_src(after)}" if after else "")
        + (f", before={_json_src(before)}" if before else "")
        + ")\n"
        + '        return json.dumps({"status": "ok", "cell_id": str(cid)})\n'
        + "\n"
        + "print(await _run())"
    )


def build_edit_cell_template(
    cell_id: str,
    source: str,
    *,
    hide_code: bool | None = None,
    name: str | None = None,
) -> str:
    """Build a scratchpad snippet that edits an existing cell's source."""
    parts = ["    async with cm.get_context() as ctx:"]
    parts.append(f"        ctx.edit_cell({_json_src(cell_id)}, {_json_src(source)}")
    if hide_code is not None:
        parts.append(f", hide_code={_py_bool(hide_code)}")
    if name is not None:
        parts.append(f", name={_json_src(name)}")
    parts.append(")")
    # Then sanity-check the edit landed and return the new hash.
    parts.append(
        "        new_hash = None\n"
        "        for c in ctx.cells:\n"
        f"            if str(c.id) == {_json_src(cell_id)}:\n"
        "                new_hash = _code_hash(getattr(c, 'code', '') or '')\n"
        "        from json import dumps as _d\n"
        '        return _d({"status": "ok", "cell_id": '
        + _json_src(cell_id)
        + ', "code_hash": new_hash})'
    )
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "\n".join(parts)
        + "\n\nprint(await _run())"
    )


def build_run_cell_template(cell_id: str) -> str:
    """Build a scratchpad snippet that runs an existing cell."""
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "    async with cm.get_context() as ctx:\n"
        + f"        ctx.run_cell({_json_src(cell_id)})\n"
        + '        return json.dumps({"status": "ok", "cell_id": '
        + _json_src(cell_id)
        + "})\n\nprint(await _run())"
    )


def build_delete_cell_template(cell_id: str) -> str:
    """Build a scratchpad snippet that deletes an existing cell."""
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "    async with cm.get_context() as ctx:\n"
        + f"        ctx.delete_cell({_json_src(cell_id)})\n"
        + '        return json.dumps({"status": "ok", "cell_id": '
        + _json_src(cell_id)
        + "})\n\nprint(await _run())"
    )


# Pre-built defaults (mostly for tests / completeness).
TEMPLATE_CREATE_CELL = build_create_cell_template("")
TEMPLATE_EDIT_CELL = build_edit_cell_template("", "")
TEMPLATE_RUN_CELL = build_run_cell_template("")
TEMPLATE_DELETE_CELL = build_delete_cell_template("")


def build_cell_hashes_template() -> str:
    """Build a snippet that returns {cell_id -> code_hash} for the whole session.

    This is used for the edit staleness guard: it reads the *live* source
    without touching the change tracker, so the guard can compare it against
    the agent's last-read snapshot.
    """
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "    return json.dumps(await _ctx_summary())\n"
        + "\nprint(await _run())"
    )


TEMPLATE_CELL_HASHES = build_cell_hashes_template()


__all__ = [
    "TEMPLATE_CELL_HASHES",
    "TEMPLATE_CREATE_CELL",
    "TEMPLATE_DELETE_CELL",
    "TEMPLATE_EDIT_CELL",
    "TEMPLATE_RUN_CELL",
    "build_cell_hashes_template",
    "build_create_cell_template",
    "build_delete_cell_template",
    "build_edit_cell_template",
    "build_run_cell_template",
]
