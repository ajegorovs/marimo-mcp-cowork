"""MCP tool handlers for mutating notebook cells.

Provides the unified write surface: `create_cell`, `edit_cell`, `run_cell`,
and `delete_cell`. These wrap marimo's `marimo._code_mode` API (the same layer
the pairing scripts use) behind single, validated tool contracts.

`edit_cell` carries a **staleness guard**: before mutating, it compares the
cell's live source hash against the agent's last full-source READ BASELINE
(recorded by `get_cell_data`, or by the cell's own create/edit). A
`get_cell_map` preview does NOT record it (H9). If the cell changed since that
read, the edit is REFUSED and the agent is told to re-read — impossible silent
stomps during simultaneous co-work. This mirrors Hermes' file-edit guard
(`check_stale`).

`run_cell` carries an execution `mode` (`cell` | `descendants` | `all`) and is
deliberately a **three-call** operation: a plan/read that validates every target
before anything is queued, the single code-mode context that queues them (so
marimo's own scheduler orders them), and a separate post-run report — marimo
discards the run payload when any target raises and the in-context snapshot is
frozen. The response reports per-cell terminal states, never a single
batch-level verdict (T15).

**Cell and anchor references are validated before dispatch.** `delete_cell` and
`create_cell(after=/before=)` name a *cell* (id or name, resolved the way marimo
resolves one); `ctx.delete_cell`/`ctx.create_cell` raise `KeyError` /
`RuntimeError` from *inside* the write scratchpad for an absent anchor, which
reached callers as the raw `{"error": "Execution failed", "stderr":
"Traceback…"}` envelope with no target flags (bug-hunt-2 F1/F4). Each mutation
therefore runs one **read-only** live-cell validation before generating the
write scratchpad and, on a miss, returns the shared structured refusal
(`status: error`; `target_resolved`/`operation_ran`/`state_changed` all false;
the echoed reference; `reason` one of `unknown_cell_ids` — not a live cell,
`conflicting_anchors` — both `before` and `after` supplied, or
`target_validation_failed` — the validation read itself failed). No write is
generated or queued on a refusal, so no `KeyError` traceback can reach a caller.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Literal

from fastmcp import Context

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.change_tracking import (
    CellFingerprint,
    get_tracker,
)
from marimo_inspection.tools.session import resolve_target

logger = logging.getLogger(__name__)

#: Accepted `run_cell` modes. `cell` is the default (single-target, backward
#: compatible); `descendants` adds the target's kernel-graph descendants; `all`
#: queues every document cell.
_RUN_MODES: tuple[str, ...] = ("cell", "descendants", "all")

#: Runtime states that mean the target did NOT finish cleanly. Everything else
#: that is not `idle` (stale, disabled, queued, running, unknown) is `not_run`.
_RUN_FAILED_STATES = frozenset(
    {"exception", "marimo-error", "cancelled", "interrupted"}
)

#: The kernel is the scheduler: it adds cells the caller did not name, and its
#: tie-breaking among independent cells is not a contract. Said plainly in the
#: payload so no caller reads `requested_cell_ids` as an execution order.
_KERNEL_SCHEDULING_NOTE = (
    "marimo schedules the queued cells itself: it may additionally run still-"
    "uninstantiated ancestors of the targets and, in autorun mode, registered "
    "descendants that are NOT in requested_cell_ids, and the relative order of "
    "independent cells is unspecified. requested_cell_ids is the set of targets, "
    "never an execution order."
)


async def _execute_json(client: MarimoClient, sid: str, code: str) -> dict:
    """Run a mutation/read snippet and return its parsed JSON payload.

    marimo may interleave its own status lines (e.g. "created cell 'x'") with
    our JSON result on stdout, so we scan the emitted lines and return the
    first/last one that decodes as a JSON object.
    """
    result = await client.execute(sid, code)
    if result.status == "error":
        return {
            "error": "Execution failed",
            "stderr": "\n".join(result.stderr or []),
        }
    all_lines = "\n".join(result.stdout or []).split("\n")
    for line in reversed(all_lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return {
        "error": "Failed to parse mutation result",
        "raw_output": "\n".join(result.stdout or []),
        "stderr": "\n".join(result.stderr or []),
    }


async def _refresh_snapshot(
    client: MarimoClient,
    sid: str,
    *,
    record: Iterable[str] = (),
    forget: Iterable[str] = (),
) -> dict[str, CellFingerprint] | None:
    """Re-read live hashes; refresh ONLY the cells this mutation touched.

    After a successful mutation the agent's baseline for the cell it just wrote
    is stale; refreshing that one cell (an implicit read, like Hermes'
    ``note_write``) keeps both tracker dimensions coherent — the read baseline
    the ``edit_cell`` guard compares against, and change detection, so our own
    write does not reappear as an external change.

    It must NOT replace the whole session. ``ChangeTracker.commit`` replaces
    the change-detection snapshot, which (pre-H7, when that snapshot doubled as
    the read baseline) forged a last-read baseline for every OTHER cell — a
    write to any cell would then disable the ``edit_cell`` guard for all of
    them (every foreign edit silently stops reporting ``conflict``, and a
    never-read cell stops reporting ``needs_read``). That is H7 in
    ``docs/agenda-bug-hunt-1.md``.

    Returns the fresh fingerprint map (the caller reports the post-write hash
    from it), or ``None`` if the hashes payload was an error — in which case
    the tracker is left UNTOUCHED. Callers must surface a warning on ``None``
    rather than crashing.
    """
    from marimo_inspection.templates.mutation import build_cell_hashes_template

    data = await _execute_json(client, sid, build_cell_hashes_template())
    if "error" in data:
        logger.warning(
            "Snapshot refresh failed for session %s: %s", sid, data.get("error")
        )
        return None
    hashes = {k: v for k, v in data.items() if isinstance(v, str)}
    fps = {cid: CellFingerprint(code_hash=h) for cid, h in hashes.items()}
    tracker = get_tracker()
    touched = {cid: fps[cid] for cid in record if cid in fps}
    if touched:
        tracker.record_cells(sid, touched)
    if forget:
        tracker.forget_cells(sid, forget)
    return fps


# ---------------------------------------------------------------------------
# Structured cell/anchor target refusals (F1/F4)
# ---------------------------------------------------------------------------
#
# `resolve_target` (tools/session.py) makes a *session* target a payload. A
# cell reference the caller names is a second target class: `ctx.delete_cell`
# and `ctx.create_cell(after=/before=)` resolve it through marimo's
# `_resolve_target`, which raises `KeyError` for an absent one — surfacing to
# callers as the raw `{"error": "Execution failed", "stderr": "Traceback…"}`
# envelope with no `status`/`reason`/target flags. Each mutation therefore
# validates its cell reference against a READ-ONLY snapshot of the live cells
# *before* it generates or dispatches the write scratchpad, and refuses with
# the same envelope (`target_resolved` / `operation_ran` / `state_changed` all
# false) the rest of the surface speaks.

#: The named cell/anchor is not a live cell (by id or by name).
REASON_UNKNOWN_CELL_IDS = "unknown_cell_ids"
#: `create_cell` was given BOTH `before` and `after` (input validation).
REASON_CONFLICTING_ANCHORS = "conflicting_anchors"
#: The live-cell read needed to validate the reference failed, so it could not
#: be checked — nothing was dispatched.
REASON_TARGET_VALIDATION_FAILED = "target_validation_failed"


def _target_refusal(
    reason: str,
    message: str,
    *,
    session_id: str,
    cell_id: str | None = None,
    next_steps: Iterable[str] = (),
    **extra: object,
) -> dict:
    """Build the structured refusal for a cell/anchor target problem.

    The envelope mirrors every other target refusal on this surface
    (``status: error`` plus ``target_resolved`` / ``operation_ran`` /
    ``state_changed`` all false), so one caller checks one vocabulary. It is a
    *cell* refusal, not a session one: the echoed bad reference is ``cell_id``
    for the tools that take one; `create_cell` echoes its placement anchor in
    ``anchor`` (``"after"``/``"before"``) + ``anchor_cell_id`` instead, because
    it has no ``cell_id`` argument of its own.
    """
    payload: dict = {
        "status": "error",
        "reason": reason,
        "error": message,
        "message": message,
        "session_id": session_id,
        "cell_id": cell_id,
        "target_resolved": False,
        "operation_ran": False,
        "state_changed": False,
        "next_steps": list(next_steps),
    }
    payload.update(extra)
    return payload


def _target_known(targets: list[dict], target: str) -> bool:
    """Whether ``target`` names a live cell by id or by name.

    Mirrors marimo's own ``_resolve_target`` (``ctx.cells._resolve``), which
    accepts either — validating ids alone would newly refuse a working name.
    """
    for row in targets:
        if str(row.get("cell_id")) == target:
            return True
        name = row.get("name")
        if name and str(name) == target:
            return True
    return False


async def _live_cell_targets(
    client: MarimoClient,
    sid: str,
    *,
    cell_id: str | None = None,
) -> tuple[list[dict], dict | None]:
    """Read the live cells as ``(cell_id, name)`` rows, or the refusal.

    Returns ``(targets, None)`` on a successful read, and ``([], refusal)``
    when the read itself failed — in which case the caller must return the
    refusal verbatim: the reference cannot be checked, so no mutation may be
    generated or dispatched. This read is READ-ONLY; it never queues a write.
    """
    from marimo_inspection.templates.mutation import build_cell_targets_template

    data = await _execute_json(client, sid, build_cell_targets_template())
    if "error" in data:
        detail = data.get("stderr") or data.get("error") or "unknown failure"
        return [], _target_refusal(
            REASON_TARGET_VALIDATION_FAILED,
            (
                f"reason: {REASON_TARGET_VALIDATION_FAILED} — the read-only "
                "live-cell validation needed to check the referenced cell "
                f"failed ({detail}), so nothing was dispatched and no write "
                "was queued. Retry once the kernel answers reads again; use "
                "get_cell_map to list the live cell ids and names."
            ),
            session_id=sid,
            cell_id=cell_id,
            stderr=data.get("stderr", ""),
            next_steps=[
                "Check the marimo server's health, then retry the call.",
                "Use get_cell_map to list the live cell ids and names.",
            ],
        )
    return list(data.get("targets") or []), None


async def create_cell(
    source: str,
    *,
    name: str | None = None,
    hide_code: bool = False,
    after: str | None = None,
    before: str | None = None,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Create a new cell in the notebook.

    Created cells are visible in the UI by default (hide_code=False); pass
    hide_code=True explicitly for setup/implementation cells you want hidden.

    Args:
        source: Source code for the new cell.
        name: Optional cell name.
        hide_code: Whether the code is hidden in the UI (default False).
        after: Optional cell_id to place this cell after.
        before: Optional cell_id to place this cell before.
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        Dict with status and the created cell_id. A placement anchor (`after` /
        `before`) that is not a live cell — by id or by name — is a structured
        refusal (`status: error`, `reason: unknown_cell_ids`,
        `anchor_cell_id`, `target_resolved`/`operation_ran`/`state_changed` all
        false, nothing created); supplying both anchors is
        `reason: conflicting_anchors`. Nothing is dispatched on either.
    """
    if not source.strip():
        return {"error": "source must not be empty", "status": "error"}

    # Input validation, ahead of any session work: marimo raises
    # `RuntimeError: Cannot specify both 'before' and 'after'` inside the
    # scratchpad, which used to surface as a raw traceback envelope.
    if before and after:
        return _target_refusal(
            REASON_CONFLICTING_ANCHORS,
            (
                f"reason: {REASON_CONFLICTING_ANCHORS} — create_cell cannot place "
                f"one new cell both before {before!r} and after {after!r}: supply "
                "at most one placement anchor. Nothing was created."
            ),
            session_id=session_id,
            after=after,
            before=before,
            next_steps=[
                "Pass only `after` or only `before`, never both.",
                "Use get_cell_map to confirm the anchor cell id or name.",
            ],
        )

    resolved = await resolve_target(
        session_id, server_url, ctx=ctx, client_factory=MarimoClient
    )
    if resolved.refusal is not None:
        return resolved.refusal
    client, session = resolved.unwrap()
    sid = session.session_id

    # Validate the placement anchor against the live cells BEFORE generating
    # the create scratchpad: an absent anchor is a target problem, and letting
    # marimo raise `KeyError` from inside the write would violate the
    # "nothing ran" guarantee the refusal makes.
    anchor = after or before
    if anchor:
        targets, refusal = await _live_cell_targets(client, sid)
        if refusal is not None:
            return refusal
        if not _target_known(targets, anchor):
            which = "after" if after else "before"
            return _target_refusal(
                REASON_UNKNOWN_CELL_IDS,
                (
                    f"reason: {REASON_UNKNOWN_CELL_IDS} — the {which} anchor "
                    f"{anchor!r} was not found among the live cells of session "
                    f"{sid} (it matched neither a cell id nor a cell name), so "
                    "nothing was created. Use get_cell_map to list the live "
                    "cell ids and names, then retry with one of them."
                ),
                session_id=sid,
                anchor=which,
                anchor_cell_id=anchor,
                unknown_cell_ids=[anchor],
                next_steps=[
                    "Use get_cell_map to list the live cell ids and names.",
                    "Retry create_cell with one of them as the anchor.",
                ],
            )

    if ctx:
        await ctx.info(f"Creating cell in session {sid}...")

    from marimo_inspection.templates.mutation import build_create_cell_template

    code = build_create_cell_template(
        source, name=name, hide_code=hide_code, after=after, before=before
    )
    data = await _execute_json(client, sid, code)
    if "error" in data:
        return data

    # Record the NEW cell's baseline (deliberate: the caller authored its
    # source, so the documented create → run → edit flow must not force a
    # re-read of a cell the agent just wrote). Only that cell is touched —
    # never the whole session (H7).
    new_cell_id = data.get("cell_id")
    fps = await _refresh_snapshot(
        client, sid, record=[new_cell_id] if new_cell_id else ()
    )
    response = {
        "status": "ok",
        "cell_id": new_cell_id,
        "session_id": sid,
        "next_steps": ["Use run_cell to execute it, or get_cell_map to see it."],
    }
    if fps is None:
        response["warning"] = (
            "Cell created, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    return response


async def edit_cell(
    cell_id: str,
    source: str,
    *,
    name: str | None = None,
    hide_code: bool | None = None,
    check_fresh: bool = True,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Edit an existing cell's source code.

    Includes a staleness guard: unless ``check_fresh=False``, refuses to edit
    a cell that has no full-source read baseline (``needs_read``) or whose
    source changed since that read (``conflict``). ``get_cell_data`` records
    the baseline; a ``get_cell_map`` preview does not. This prevents silently
    overwriting a concurrent edit.

    Args:
        cell_id: Target cell id.
        source: New source code.
        name: Optional new cell name.
        hide_code: Optional new hide_code value.
        check_fresh: Refuse to edit a cell that changed since last read.
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        Dict with status. On a guard refusal, status is 'conflict'/'needs_read'.
    """
    if not cell_id:
        return {"error": "cell_id is required", "status": "error"}
    if not source.strip():
        return {"error": "source must not be empty", "status": "error"}

    resolved = await resolve_target(
        session_id, server_url, ctx=ctx, client_factory=MarimoClient
    )
    if resolved.refusal is not None:
        return resolved.refusal
    client, session = resolved.unwrap()
    sid = session.session_id

    from marimo_inspection.templates.mutation import build_cell_hashes_template

    # Fresh live hashes (does NOT touch the tracker — we need the agent's own
    # earlier snapshot for the comparison, not the current state).
    live = await _execute_json(client, sid, build_cell_hashes_template())
    if "error" in live:
        return live
    if cell_id not in live:
        # Genuinely absent from the session (not just a None hash): the
        # staleness guard is meaningless for a nonexistent cell. Refuse
        # BEFORE mutating, in the shared structured-refusal envelope so a
        # caller checks one vocabulary for every absent cell reference.
        return _target_refusal(
            REASON_UNKNOWN_CELL_IDS,
            (
                f"reason: {REASON_UNKNOWN_CELL_IDS} — cell {cell_id} was not "
                f"found in session {sid}, so nothing was edited. Use "
                "get_cell_map to list the current cell ids, then read one with "
                "get_cell_data and retry."
            ),
            session_id=sid,
            cell_id=cell_id,
            unknown_cell_ids=[cell_id],
            next_steps=[
                "Use get_cell_map to list the live cell ids.",
                "Read the cell with get_cell_data, then retry edit_cell.",
            ],
        )
    live_hash = live.get(cell_id)

    tracker = get_tracker()
    prev = tracker.get_cell_fingerprint(sid, cell_id)

    if check_fresh:
        if prev is None:
            # No baseline for this cell — the agent has never read it, so
            # freshness cannot be proven. This holds even when the session
            # has no snapshot at all: a first edit of a never-read cell must
            # never silently bypass the guard.
            return {
                "status": "needs_read",
                "cell_id": cell_id,
                "message": (
                    f"Cell {cell_id} was never read by this agent. "
                    "Call get_cell_data for it (a get_cell_map preview does "
                    "not record the read baseline) so you edit against a "
                    "known source, then retry edit_cell."
                ),
            }
        if live_hash is not None and live_hash != prev.code_hash:
            return {
                "status": "conflict",
                "cell_id": cell_id,
                "message": (
                    f"Cell {cell_id} was modified since the agent last read it "
                    "(source hash changed). Re-read it with get_cell_data to "
                    "avoid overwriting a concurrent edit, then retry edit_cell. "
                    "Or pass check_fresh=False to force."
                ),
            }

    if ctx:
        await ctx.info(f"Editing cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_edit_cell_template

    code = build_edit_cell_template(cell_id, source, name=name, hide_code=hide_code)
    data = await _execute_json(client, sid, code)
    if "error" in data:
        return data

    # The template's own hash (if any) is computed inside the edit context,
    # BEFORE the context-exit applies the queued edit — i.e. stale. Always
    # report the POST-context-exit hash from the fresh snapshot instead.
    # Record ONLY this cell's baseline: an unrelated cell's baseline must
    # survive this write (H7).
    fps = await _refresh_snapshot(client, sid, record=[cell_id])
    response = {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
        "next_steps": ["Use run_cell to execute the edited cell."],
    }
    if fps is None:
        response["warning"] = (
            "Edit applied, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    else:
        fp = fps.get(cell_id)
        if fp is not None:
            response["code_hash"] = fp.code_hash
        elif fps:
            response["warning"] = (
                "Edit applied, but the refreshed snapshot does not contain "
                f"cell {cell_id}. Run get_cell_data to re-establish the baseline."
            )
        # fps == {} (a genuinely cell-free session) — leave the hash out.
    return response


async def run_cell(
    cell_id: str = "",
    mode: Literal["cell", "descendants", "all"] = "cell",
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Run cells: one cell, a cell plus its kernel-graph descendants, or ALL cells.

    `mode` selects what the tool QUEUES — not everything the kernel ends up
    running:

    - `mode="cell"` (default) — that cell only. The kernel may additionally run
      still-uninstantiated *ancestors* of it (never chosen by this tool).
    - `mode="descendants"` — the cell plus its `ctx.graph` descendants. This
      needs the target to be **registered in the kernel graph**: on a fresh,
      un-instantiated session the graph is empty, so it returns `status: error`,
      `reason: graph_unpopulated` and runs NOTHING (never a silent single-cell
      run). Use `mode="all"` to register the document first.
    - `mode="all"` — every document cell in the notebook, queued in one
      code-mode context so marimo's own scheduler orders them. `cell_id` must be
      empty; passing both is refused (`reason: cell_id_not_allowed`) rather than
      ignored. This is a full re-run: it re-runs cells that are already idle and
      cells that were deliberately left un-run.

    Whatever the mode, the kernel may also run cells outside
    `requested_cell_ids` (stale ancestors, autorun descendants), and the
    relative order of independent cells is unspecified.

    The target is resolved by cell **id or cell name**, exactly as `ctx.cells`
    resolves a key — so a name that reached the pre-modes `run_cell` still
    works. `requested_cell_ids` always carries the resolved ID(s), never the
    name you passed; `cell_id` echoes your input and `resolved_cell_id` reports
    the resolution.

    Every id is validated by a plan/read call BEFORE anything is queued: marimo
    raises at queue time on an unknown id and discards the whole batch, so an
    unknown id or name aborts with `status: error` and nothing runs. After the
    run a separate report call reads each target's terminal state (the run
    payload is discarded by marimo when any target raises, and the in-context
    snapshot is frozen), so the response is per-cell: `cells[]` carries
    `runtime_state`, `output_stale`, `known`, structured `errors` and
    `errors_readable` (`errors` is `null` when the channel could not be read);
    `succeeded_cell_ids` are `idle` with a
    readable, empty `errors`; `failed_cell_ids` are
    `exception`/`marimo-error`/`cancelled`/`interrupted`; and
    `not_run_cell_ids` is everything else (stale, disabled, unknown, or idle
    with an unreadable `errors` channel — those also appear in
    `unverified_cell_ids`). `failed_cell_ids` covers the requested targets
    only. `status` is `ok` only when every requested target is idle, `partial`
    otherwise, and `error` for a validation failure (`cell_id_required`,
    `cell_id_not_allowed`), a planning failure (`planning_failed`), a
    reporting failure (`reporting_failed`), `unknown_cell_ids` or
    `graph_unpopulated`. Every failure carries a top-level `error` string
    **and** the structured `status`/`reason` fields, so a caller written
    against the pre-modes `{"error": ...}` contract still sees the failure.

    `mode` is a **literal enum** in the published MCP input schema, so an
    out-of-enum value is rejected by the framework (`literal_error`) before
    this handler runs — no MCP caller ever receives a structured payload for
    it, which is why no out-of-enum reason appears in the vocabulary above. The
    defensive branch for that case remains only for a direct Python caller that
    bypasses schema validation.

    Args:
        cell_id: Target cell id **or cell name**. Required for
            `cell`/`descendants`; for `all` it must be empty.
        mode: Execution mode: "cell", "descendants", or "all".
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        Dict with `status`, `mode`, `requested_cell_ids`, per-cell outcomes,
        `succeeded_cell_ids` / `failed_cell_ids` / `not_run_cell_ids`, `counts`,
        and `execution_error` + `stderr` when the run call itself failed.
    """
    if mode not in _RUN_MODES:
        # DEFENSIVE ONLY (bug-hunt-2 F2): `mode` is a Literal in the published
        # signature, so FastMCP's schema validation rejects an out-of-enum
        # value before this function runs and this branch is unreachable over
        # MCP. It is kept for a direct Python caller, and `invalid_mode` is
        # deliberately NOT part of the public MCP reason vocabulary.
        message = (
            f"mode must be one of {list(_RUN_MODES)}; got {mode!r}. Nothing was run."
        )
        return {
            "status": "error",
            "reason": "invalid_mode",
            "mode": mode,
            "error": message,
            "message": message,
        }
    if mode == "all":
        if cell_id:
            message = (
                "mode='all' plans every document cell, so cell_id must be "
                f"empty (got {cell_id!r}). Nothing was run."
            )
            return {
                "status": "error",
                "reason": "cell_id_not_allowed",
                "mode": mode,
                "cell_id": cell_id,
                "error": message,
                "message": message,
            }
    elif not cell_id:
        return {
            "status": "error",
            "reason": "cell_id_required",
            "mode": mode,
            # The pre-modes literal, so an old `error` check still hits.
            "error": "cell_id is required",
            "message": f"mode={mode!r} requires cell_id. Nothing was run.",
        }

    resolved = await resolve_target(
        session_id, server_url, ctx=ctx, client_factory=MarimoClient
    )
    if resolved.refusal is not None:
        return resolved.refusal
    client, session = resolved.unwrap()
    sid = session.session_id
    if ctx:
        await ctx.info(f"Running cells (mode={mode}) in session {sid}...")

    from marimo_inspection.templates.mutation import (
        build_cell_status_template,
        build_run_cells_template,
        build_run_plan_template,
    )

    # 1. PLAN — validate every id/name and resolve the target set before queueing.
    plan = await _execute_json(client, sid, build_run_plan_template(cell_id, mode))
    if "error" in plan:
        message = "Run planning failed; nothing was executed."
        return {
            "status": "error",
            "reason": "planning_failed",
            "mode": mode,
            "session_id": sid,
            "error": plan.get("error") or message,
            "execution_error": plan.get("error"),
            "stderr": plan.get("stderr", ""),
            "message": message,
        }
    reason = plan.get("reason")
    resolved = plan.get("resolved_cell_id")
    if reason == "unknown_cell_ids":
        unknown = plan.get("unknown_cell_ids") or ([cell_id] if cell_id else [])
        return {
            "status": "error",
            "reason": "unknown_cell_ids",
            "mode": mode,
            "session_id": sid,
            "cell_id": cell_id,
            "resolved_cell_id": None,
            "unknown_cell_ids": unknown,
            "error": (
                f"Unknown cell id or name(s) {unknown}: the whole batch is "
                "refused and nothing was run. Use get_cell_map for the live "
                "ids and names."
            ),
            "message": (
                f"Unknown cell id or name(s) {unknown}: the whole batch is "
                "refused and nothing was run. Use get_cell_map for the live "
                "ids and names."
            ),
        }
    if reason == "graph_unpopulated":
        # `resolved` is the actual cell id behind a name or id target.
        target_id = str(resolved) if resolved else cell_id
        message = (
            f"Cell {target_id!r} is not registered in the kernel dependency "
            "graph, so its descendants cannot be computed (a fresh, "
            "un-instantiated session has an empty graph). Nothing was run. "
            "Call run_cell with mode='all' to queue every document cell "
            "(which registers them), then retry mode='descendants'."
        )
        return {
            "status": "error",
            "reason": "graph_unpopulated",
            "mode": mode,
            "session_id": sid,
            "cell_id": cell_id,
            "resolved_cell_id": target_id,
            "requested_cell_ids": [target_id],
            "error": message,
            "message": message,
            "next_steps": [
                (
                    "Call run_cell with mode='all' to queue every document cell "
                    "(which registers them), then retry mode='descendants'."
                ),
            ],
        }
    requested = [str(c) for c in plan.get("requested_cell_ids", [])]
    if not requested:
        return {
            "status": "ok",
            "mode": mode,
            "session_id": sid,
            "requested_cell_ids": [],
            "cells": [],
            "succeeded_cell_ids": [],
            "failed_cell_ids": [],
            "not_run_cell_ids": [],
            "unverified_cell_ids": [],
            "counts": {"requested": 0, "succeeded": 0, "failed": 0, "not_run": 0},
            "message": "No document cells to run.",
            "note": _KERNEL_SCHEDULING_NOTE,
        }

    # 2. RUN — one code-mode context for the whole batch, so marimo schedules.
    run = await _execute_json(client, sid, build_run_cells_template(requested))
    execution_error = run.get("error") if "error" in run else None
    execution_stderr = run.get("stderr", "") if "error" in run else ""

    # 3. REPORT — a SEPARATE call: marimo discards the run payload when any
    #    target raises, and the in-context snapshot is frozen.
    report = await _execute_json(client, sid, build_cell_status_template(requested))
    if "error" in report:
        message = (
            "The cells were queued but their post-run state could not be "
            "read back, so the outcome is unverified."
        )
        response = {
            "status": "error",
            "reason": "reporting_failed",
            "mode": mode,
            "session_id": sid,
            "requested_cell_ids": requested,
            "error": execution_error or report.get("error") or message,
            "execution_error": execution_error or report.get("error"),
            "stderr": execution_stderr or report.get("stderr", ""),
            "message": message,
        }
        if mode != "all":
            response["cell_id"] = cell_id
            response["resolved_cell_id"] = resolved
        return response

    rows = {str(row["cell_id"]): row for row in (report.get("rows") or [])}
    cells: list[dict] = []
    succeeded: list[str] = []
    failed: list[str] = []
    not_run: list[str] = []
    unverified: list[str] = []
    for target in requested:
        row = rows.get(target) or {
            "cell_id": target,
            "runtime_state": None,
            "known": False,
            "output_stale": False,
            "errors": None,
        }
        state = row.get("runtime_state")
        errors = row.get("errors")
        # A `null` errors channel is UNREADABLE, never "empty": an idle target
        # whose errors could not be read is never called succeeded, it is
        # reported as not run / unverified.
        errors_readable = errors is not None
        if state in _RUN_FAILED_STATES or (state == "idle" and errors):
            failed.append(target)
        elif state == "idle" and errors_readable:
            succeeded.append(target)
        else:
            not_run.append(target)
            if state == "idle":
                unverified.append(target)
        cells.append(
            {
                "cell_id": target,
                "runtime_state": state,
                "output_stale": state == "stale",
                "known": bool(row.get("known", False)),
                "errors": errors,
                "errors_readable": errors_readable,
            }
        )

    counts = {
        "requested": len(requested),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "not_run": len(not_run),
    }
    status = "ok" if not failed and not not_run and not execution_error else "partial"

    response = {
        "status": status,
        "mode": mode,
        "session_id": sid,
        "requested_cell_ids": requested,
        "cells": cells,
        "succeeded_cell_ids": succeeded,
        "failed_cell_ids": failed,
        "not_run_cell_ids": not_run,
        "unverified_cell_ids": unverified,
        "counts": counts,
        "note": _KERNEL_SCHEDULING_NOTE,
    }
    if mode != "all":
        # Backward-compatible single-target fields: `cell_id` echoes the input
        # (id or name), `resolved_cell_id` is the id it resolved to.
        response["cell_id"] = cell_id
        response["resolved_cell_id"] = resolved
    if execution_error:
        # Backward-compatible: a failing run keeps the top-level `error` key a
        # caller written against the pre-modes contract checks.
        response["error"] = execution_error
        response["execution_error"] = execution_error
        response["stderr"] = execution_stderr
    if status == "ok":
        response["next_steps"] = [
            (
                "All requested cells are idle. Use get_cell_outputs/get_variables "
                "to verify the results."
            ),
        ]
    else:
        next_steps = []
        if failed:
            next_steps.append(
                f"Read the failed cells' errors ({', '.join(failed)}) with "
                "get_errors / get_cell_outputs, fix them, then re-run."
            )
        if unverified:
            next_steps.append(
                "The post-run errors channel was unreadable for "
                f"{', '.join(unverified)}, so their outcome is UNVERIFIED, "
                "not succeeded — re-read them with get_cell_map/get_errors."
            )
        elif not_run:
            next_steps.append(
                f"These requested cells did not finish: {', '.join(not_run)}. "
                "Check their runtime_state and the stderr above."
            )
        if not next_steps:  # execution_error with every target idle
            next_steps.append(
                "The batch reported a failure outside requested_cell_ids (the "
                "kernel also runs ancestors and autorun descendants) — inspect "
                "the stderr above."
            )
        response["next_steps"] = next_steps
    return response


async def delete_cell(
    cell_id: str,
    *,
    session_id: str = "",
    server_url: str = "",
    ctx: Context | None = None,
) -> dict:
    """Delete an existing cell from the notebook.

    Args:
        cell_id: Target cell id **or cell name**.
        session_id: Session ID; omit only when the active-session binding holds
            for this call (see `list_active_notebooks`).
        server_url: Server URL override.

    Returns:
        Dict with status. A target that is not a live cell — by id or by name —
        is a structured refusal (`status: error`, `reason: unknown_cell_ids`,
        `target_resolved`/`operation_ran`/`state_changed` all false), validated
        against the live cells before the delete is generated; the raw marimo
        `KeyError` envelope is never returned.
    """
    if not cell_id:
        return {"error": "cell_id is required", "status": "error"}

    resolved = await resolve_target(
        session_id, server_url, ctx=ctx, client_factory=MarimoClient
    )
    if resolved.refusal is not None:
        return resolved.refusal
    client, session = resolved.unwrap()
    sid = session.session_id

    # Validate the target against the live cells BEFORE generating the delete
    # scratchpad. `ctx.delete_cell` raises `KeyError` for an absent id (or
    # name), which reached callers as a raw traceback envelope with no
    # structured refusal fields (F1).
    targets, refusal = await _live_cell_targets(client, sid, cell_id=cell_id)
    if refusal is not None:
        return refusal
    if not _target_known(targets, cell_id):
        return _target_refusal(
            REASON_UNKNOWN_CELL_IDS,
            (
                f"reason: {REASON_UNKNOWN_CELL_IDS} — cell {cell_id} was not "
                f"found among the live cells of session {sid} (it matched "
                "neither a cell id nor a cell name), so nothing was deleted. "
                "Use get_cell_map to list the current cell ids and names, then "
                "retry with one of them."
            ),
            session_id=sid,
            cell_id=cell_id,
            unknown_cell_ids=[cell_id],
            next_steps=[
                "Use get_cell_map to list the live cell ids and names.",
                "Retry delete_cell with one of them.",
            ],
        )

    if ctx:
        await ctx.info(f"Deleting cell {cell_id} in session {sid}...")

    from marimo_inspection.templates.mutation import build_delete_cell_template

    data = await _execute_json(client, sid, build_delete_cell_template(cell_id))
    if "error" in data:
        return data

    # The cell is gone, so there is nothing to record: drop its fingerprint
    # without touching any other cell's baseline (H7). A re-used id would
    # then report `needs_read` — the safe direction.
    fps = await _refresh_snapshot(client, sid, forget=[cell_id])
    response = {
        "status": "ok",
        "cell_id": cell_id,
        "session_id": sid,
    }
    if fps is None:
        response["warning"] = (
            "Cell deleted, but the change-tracking snapshot could not be "
            "refreshed (hash read failed). Run get_cell_data to re-establish "
            "the baseline."
        )
    return response
