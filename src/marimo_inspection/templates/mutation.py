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
    hide_code: bool = False,
    after: str | None = None,
    before: str | None = None,
) -> str:
    """Build a scratchpad snippet that creates a new cell.

    ``hide_code`` defaults to False: created cells are visible in the UI
    unless explicitly hidden (e.g. setup/implementation cells).
    """
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
    """Build a scratchpad snippet that edits an existing cell's source.

    The edit is queued inside the context and applied on context exit, so no
    hash computed here can reflect the edited source. The caller re-reads
    live hashes AFTER the edit and reports that post-exit hash instead.
    """
    parts = ["    async with cm.get_context() as ctx:"]
    parts.append(f"        ctx.edit_cell({_json_src(cell_id)}, {_json_src(source)}")
    if hide_code is not None:
        parts.append(f", hide_code={_py_bool(hide_code)}")
    if name is not None:
        parts.append(f", name={_json_src(name)}")
    parts.append(")")
    parts.append(
        "        return json.dumps("
        '{"status": "ok", "cell_id": ' + _json_src(cell_id) + "})"
    )
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + "\n".join(parts)
        + "\n\nprint(await _run())"
    )


def build_run_plan_template(cell_id: str, mode: str) -> str:
    """Build a scratchpad snippet that PLANS a run before anything executes.

    This is the validate-before-mutate step (H4/T15): marimo raises at queue
    time when any id is unknown and discards the whole batch, so the plan reads
    ``ctx.cells`` (the document cells, in document order) and
    ``ctx.graph.cells`` (the registered cells) and reports what *would* be
    queued, plus any refusal reason, without running anything.

    The target is resolved the way ``ctx.cells`` resolves a key — by cell **ID
    or by cell name** (marimo's documented lookup, and what a pre-``mode``
    ``run_cell`` accepted, because it forwarded the target straight to
    ``ctx.run_cell``). ``requested_cell_ids`` therefore always carries the
    RESOLVED ID(s), never the name the caller passed; ``resolved_cell_id``
    reports the resolution explicitly.

    Modes: ``"all"`` plans every document cell (graph-independent — a fresh
    session has an empty graph); ``"cell"`` plans exactly the resolved target;
    ``"descendants"`` plans the target plus ``ctx.graph.descendants(target)``
    and reports ``graph_unpopulated`` when the target has no graph entry
    (rather than silently degrading to a single-cell run).
    """
    return f"""
{_PREAMBLE}
async def _run():
    async with cm.get_context() as ctx:
        target = {_json_src(cell_id)}
        mode = {_json_src(mode)}
        document_ids = [str(c.id) for c in ctx.cells]
        graph_ids = [str(k) for k in ctx.graph.cells.keys()]
        requested = []
        unknown = []
        reason = None
        # Resolve an ID or a cell NAME exactly the way ctx.cells does, so a
        # name that worked before the modes existed still works. The RESOLVED
        # id is what gets queued and reported.
        resolved = None
        if target:
            for c in ctx.cells:
                if str(c.id) == target or (c.name and c.name == target):
                    resolved = str(c.id)
                    break
        if mode == "all":
            requested = list(document_ids)
        elif resolved is None:
            unknown = [target]
            reason = "unknown_cell_ids"
        elif mode == "descendants":
            if resolved not in graph_ids:
                reason = "graph_unpopulated"
            else:
                requested = [resolved]
                requested += [
                    str(x)
                    for x in ctx.graph.descendants(resolved)
                    if str(x) != resolved
                ]
        else:
            requested = [resolved]
        # Preserve order, never queue an id twice.
        requested = list(dict.fromkeys(requested))
        return json.dumps({{
            "status": "error" if reason else "ok",
            "mode": mode,
            "cell_id": target or None,
            "resolved_cell_id": resolved,
            "reason": reason,
            "requested_cell_ids": requested,
            "document_cell_ids": document_ids,
            "graph_cell_ids": graph_ids,
            "unknown_cell_ids": unknown,
        }})

print(await _run())
"""


def build_run_cells_template(cell_ids: list[str]) -> str:
    """Build a scratchpad snippet that queues every target in ONE context.

    One code-mode context for the whole batch is what lets marimo's own
    scheduler order the cells (dependency order, with independent cells in
    unspecified relative order) instead of a client-side topological guess.
    """
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + f"    targets = {json.dumps(cell_ids)}\n"
        + "    async with cm.get_context() as ctx:\n"
        + "        queued = []\n"
        + "        for t in targets:\n"
        + "            ctx.run_cell(t)\n"
        + "            queued.append(str(t))\n"
        + '        return json.dumps({"status": "ok", "queued": queued})\n'
        + "\nprint(await _run())"
    )


def build_cell_status_template(cell_ids: list[str]) -> str:
    """Build a scratchpad snippet that reports each target's terminal state.

    This must be a SEPARATE execution from the run: marimo discards the run
    snippet's JSON payload when any queued cell raises (the traceback goes to
    stderr), and the code-mode snapshot read inside the run context is frozen
    pre-execution. A fresh context run after the batch reads the truthful
    terminal state — including `exception` / `cancelled` for cells the kernel
    failed or never got to.
    """
    return (
        _PREAMBLE
        + "\nasync def _run():\n"
        + f"    targets = {json.dumps(cell_ids)}\n"
        + "    async with cm.get_context() as ctx:\n"
        + "        by_id = {str(c.id): c for c in ctx.cells}\n"
        + "        def _status(cell):\n"
        + "            try:\n"
        + "                return str(cell.status) if cell.status is not None else None\n"
        + "            except Exception:\n"
        + "                return None\n"
        + "        def _errors(cell):\n"
        + "            # None (never []) when the errors channel is unreadable: a\n"
        + "            # private field that raises OR is null must not be asserted\n"
        + "            # empty — the caller then reports the target as unverified.\n"
        + "            try:\n"
        + "                errs = cell.errors\n"
        + "            except Exception:\n"
        + "                return None\n"
        + "            if errs is None:\n"
        + "                return None\n"
        + "            try:\n"
        + "                out = []\n"
        + "                for e in errs:\n"
        + "                    out.append({\n"
        + '                        "kind": str(getattr(e, "kind", "")),\n'
        + '                        "message": str(getattr(e, "msg", "")),\n'
        + "                    })\n"
        + "                return out\n"
        + "            except Exception:\n"
        + "                return None\n"
        + "        rows = []\n"
        + "        for t in targets:\n"
        + "            cell = by_id.get(str(t))\n"
        + "            if cell is None:\n"
        + "                rows.append({\n"
        + '                    "cell_id": str(t),\n'
        + '                    "runtime_state": None,\n'
        + '                    "known": False,\n'
        + '                    "output_stale": False,\n'
        + '                    "errors": None,\n'
        + "                })\n"
        + "                continue\n"
        + "            state = _status(cell)\n"
        + "            rows.append({\n"
        + '                "cell_id": str(t),\n'
        + '                "runtime_state": state,\n'
        + '                "known": True,\n'
        + '                "output_stale": state == "stale",\n'
        + '                "errors": _errors(cell),\n'
        + "            })\n"
        + '        return json.dumps({"rows": rows})\n'
        + "\nprint(await _run())"
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
TEMPLATE_RUN_PLAN = build_run_plan_template("", "cell")
TEMPLATE_RUN_CELLS = build_run_cells_template([])
TEMPLATE_CELL_STATUS = build_cell_status_template([])
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
    "TEMPLATE_CELL_STATUS",
    "TEMPLATE_CREATE_CELL",
    "TEMPLATE_DELETE_CELL",
    "TEMPLATE_EDIT_CELL",
    "TEMPLATE_RUN_CELLS",
    "TEMPLATE_RUN_PLAN",
    "build_cell_hashes_template",
    "build_cell_status_template",
    "build_create_cell_template",
    "build_delete_cell_template",
    "build_edit_cell_template",
    "build_run_cells_template",
    "build_run_plan_template",
]
