# Agenda (open): bug-hunt #1 findings

> **Status:** **Open — 9 items: H7 fixed and pinned by two live regressions; 8
> open (H1–H6, H8, H3 verified against the source; H9/H10 recorded at the
> 2026-09-11 checkpoint, below).**
> **Found:** 2026-09-11, one hunt per `docs/bug-hunt-protocol.md` (real task in a
> live instantiated consumer notebook, driven through the MCP tool surface by a
> zero-context subagent).
> **Verification:** every item below was re-verified by reading the cited source;
> the mechanism in each entry is the one that was confirmed, not a paraphrase of
> the report.
> **Closure rule (this doc's contract):** an item is closed only when its repro
> is a test that **fails against the pre-fix code** and passes after, and any
> contradicted claim is updated in the same change.
> **Raw report + runnable repros** (gitignored, ephemeral — treat this doc as the
> record): `.hermes/hunts/2026-09-11-hunt-1/findings.md`, `…/repro/`.
> **Item ids** are local to this doc (`H<n>` = hunt finding `F<n>`); the udv
> agenda's `T`-ids are untouched.

## Priority (proposed — not yet agreed)

| # | Item | Severity | Fix shape | Priority |
| --- | --- | --- | --- | --- |
| H7 | ~~`edit_cell` guard disarmed by any unrelated write~~ (fixed) | blocks-work | scope the snapshot commit to the mutated cell | **resolved** (was P0) |
| H1 | binding promised, never in effect (non-Hermes clients) | misleads | truthful claim + a decision on a fallback binding | **P0** |
| H6 | `Error:` log text counted as a console exception | misleads | tighten the marker scan; stop asserting "traceback" | P1 |
| H4 | stale/absent cell ids silently dropped by read tools | misleads | report the ids that matched nothing | P1 |
| H2 | `get_dependency_graph` accepts and ignores `cell_id`/`depth` | misleads | implement the filter, or refuse the args loudly | P1 |
| H5 | `did_you_mean` contradicts `accepted_shape` in the same payload | misleads | suggest only a shape the element accepts | P1 |
| H9 | a *preview* read blesses a full-source baseline for every cell | misleads | **decision needed** (see entry) | P1 |
| H8 | `get_variables` reports the template's own namespace | misleads | exclude the template's scaffolding names | P2 |
| H3 | `get_dependency_graph.cell_name` is always `""` | cosmetic | populate it, or drop the field (contract change) | P2 |
| H10 | the H7 fix's premise is cited from a throwaway probe, not pinned | hardening | add the live invariant test | P2 |

---

## Checkpoint — 2026-09-11

**Landed and committed locally (not pushed):** `4660aec` fixes H7 with two live
regressions that fail against the pre-fix code plus four hermetic tracker cases;
`ac07eb7` adds `docs/bug-hunt-protocol.md` and this doc. Verified on the tree:
`-m "not live"` 303 passed, `-m live` 33 passed (was 31), ruff check/format
clean, `marimo check notebooks` exit 0.

**The pending work is the sections below — they are the durable specification.**
Per-fix plan files live under `.hermes/plans/` (gitignored, ephemeral), so treat
an item's entry here as the authority and a plan file as a convenience:

- `.hermes/plans/2026-09-11_002706-h7-edit-cell-guard-scope.md` — H7, executed.
- `.hermes/plans/2026-09-11_000750-t13-residual-ui-rejection-site.md` — the T13
  residual (see the cross-reference below), prepared and **not started**.

**Cross-reference — open work recorded in another agenda.** The T13 residual in
`docs/agenda-udv-consumer-findings.md` §T13 is still unfixed: a repeat of a value
the element already holds whose `on_change` handler then raises is reported
`value_not_applied` instead of `on_change_failed` (unpinned, unobserved in normal
use). That agenda is closed, so it is listed here to keep the open defects in one
place; its item id stays `T13`.

**Hunt lab.** The disposable lab hunt #1 used (a `/tmp` copy of a consumer
notebook served on `127.0.0.1:29417`) is still running in a tmux session owned by
the user. Nothing pending needs it — the live suite boots its own kernel.

---

## Open items

### H7 — the `edit_cell` freshness guard is silently disarmed by any unrelated write — **RESOLVED 2026-09-11**
**blocks-work.** `tools/mutation.py:71-98` (`_refresh_snapshot`) reads every
cell's hash and calls `get_tracker().commit(sid, fps)` — a **whole-session**
baseline — after every successful `create_cell` (`:148`), `edit_cell` (`:267`)
and `delete_cell` (`:367`). A write to *any* cell therefore forges a last-read
baseline for *every* other cell, which defeats both guard branches in
`edit_cell` (`:227-252`): a foreign edit that was correctly reported as
`conflict` stops being reported after one unrelated `create_cell`, and a
never-read cell becomes editable with no `needs_read`. Observed with two client
processes plus an independent witness. Contradicts `live-safety.md:12-17` and the
module docstring's "impossible silent stomps during simultaneous co-work".
*Fix:* commit only the fingerprints that actually changed (at minimum the
mutated cell) instead of the full map; keep the refresh for the written cell, so
our own writes still do not resurface as external changes.

**Resolved (2026-09-11).** `_refresh_snapshot` (`tools/mutation.py`) no longer
commits a whole-session snapshot. It gained `record`/`forget` arguments and now
touches ONLY the cell the operation mutated: `create_cell` records the new
cell's baseline (deliberate — the caller authored its source, so the documented
create → run → edit flow must not force a re-read), `edit_cell` records
`[cell_id]`, and `delete_cell` drops the removed cell via the new
`ChangeTracker.forget_cells` (`tools/change_tracking.py`; a no-op without a
snapshot, never erases other baselines). The fresh hash read stays — `edit_cell`
still returns the post-exit `code_hash` and every `fps is None` warning path is
unchanged.

*Measured before fixing* (`/tmp/hunt_probe/hash_scope_probe.py`, 2026-09-11):
creating a cell leaves every existing cell's `code_hash` unchanged (10 → 11
cells, `changed hashes: []`; deleting the probe cell: 11 → 10, `changed hashes:
[]`). A selective commit therefore cannot introduce neighbour false-conflicts on
marimo 0.24.x.

*Pinned by* two live regressions in `tests/marimo_inspect/live/test_mutation.py`:
`test_unrelated_write_does_not_disarm_the_guard` and
`test_unrelated_write_does_not_bless_a_never_read_cell`. Both **fail against the
pre-fix code** — with `mutation.py` + `change_tracking.py` stashed, the first
returns `status: ok` instead of `conflict` and the second `ok` instead of
`needs_read` — and pass after. `resources/live-safety.md` §"Read before edit"
now states the narrowed invariant.

### H1 — `list_active_notebooks` / `set_active_session` promise a binding that never takes effect
**misleads.** Both return success and tell the caller `session_id`/`server_url`
are "now optional" (`tools/session.py:104-110`, `server.py:37-43`,
`resources/co-work-loop.md:10-14`), while with a harness-shaped FastMCP `Client`
over stdio every argument-less call raises "No session_id provided and no active
session bound" — for auto-bind, for a doubled auto-bind, and for an explicit
`set_active_session`. Root cause is outside this package: FastMCP 4.0.3 keys
session state by a prefix derived in `Context.session_id` and cached on
`session._connection`; where the SDK builds the session fresh per request and no
stable connection exists (stdio), a fresh `uuid4()` prefix is used per request,
so `set_state` and `get_state` never agree. Through the Hermes gateway the
binding *does* persist, so the claim is conditionally true and unconditionally
stated. *Fix:* make the promise conditional and name the condition (the current
resource text blames "a client that spawns or reconnects the server per call",
which is not the real condition), then decide separately whether a
process-global fallback binding is acceptable — that decision has multi-client
isolation consequences and should not be smuggled into the doc fix.

### H6 — `get_errors` reports a console *exception* for a plain log line
**misleads.** `templates/errors.py:62` uses `markers = ("Traceback (most recent
call last)", "Error:", "Exception:")`; the bare `"Error:"` matches ordinary log
text. A cell that only printed `Error: 3 rows skipped (not an exception)` to
stderr (and returned normally) comes back with `has_console_exception: true`,
counts into `total_console_exception_cells`, and carries `next_steps` asserting
"Console stderr shows an exception traceback (UI-handler error)". The template
docstring (`:26-31`) claims the scan is conservative; it is not. *Fix:* require
real exception evidence (a traceback header, or an exception-typed line in a
traceback) and stop asserting a traceback/UI-handler cause when only a weak
marker matched.

### H4 — stale or absent cell ids are silently dropped by the read tools
**misleads.** `templates/cell_data.py:32-38` skips any id it cannot resolve
(`except (KeyError, Exception): continue`) and returns `{"data": []}` with
happy-path `next_steps`; `get_cell_outputs` behaves the same. A deleted or
mistyped id is indistinguishable from "nothing matched", while the sibling write
tools refuse loudly for the same id — so the surface trains callers to expect a
refusal and then returns an unqualified empty payload. *Fix:* report the
requested ids that resolved to nothing (e.g. a `missing_cell_ids` field) without
changing the happy-path shape, and mirror it in the tool docstrings.

### H2 — `get_dependency_graph` accepts `cell_id`/`depth` and ignores both
**misleads.** `templates/dependency.py:31-32` computes `center_cell` /
`target_depth` and never uses them; the loop at `:36` walks every cell in
`graph.cells`. Centring on a real cell at depth 1 and 2, and on a **nonexistent**
cell id, all return the byte-identical full graph with no error, while
`tools/dependency.py:29-35` documents the centring behaviour. *Fix:* implement
the hop-limited neighbourhood (and refuse an unknown `cell_id` explicitly), or
refuse the parameters with a clear message — silently ignoring a documented
argument is the defect either way.

### H5 — `set_ui_value.did_you_mean` contradicts its own `accepted_shape`
**misleads.** `templates/ui.py:242` builds the correction as `[value]`, wrapping
whatever scalar was submitted without consulting the element's option-key type.
For a multiselect whose declared shape is `list[str]` and whose keys are `'4'`,
sending the scalar `4` returns `did_you_mean: [4]`; following that advice
verbatim produces a second error (`value_not_applied`, "option name '4' is not a
valid option"), and the working form `["4"]` is never suggested. The advice in
`resources/co-work-loop.md:54-58` and `live-safety.md` says to send the corrected
form, so the wrong correction is load-bearing. *Fix:* derive the suggestion from
the element's key/option type, not from the submitted value's type.

### H8 — `get_variables` reports the evaluating template's namespace as session variables
**misleads.** `templates/variables.py:79-82` enumerates `globals()` when no names
are given, which includes the template's own `import json`, `import
marimo._code_mode as cm` and `get_variables` itself — so every "all variables"
call deterministically returns names no cell defines (cross-checked against
`get_dependency_graph.variable_owners`). A caller iterating the map sees five
foreign names. *Fix:* exclude the template's scaffolding names (or derive the
set from the notebook's own definitions) while keeping the documented
"empty = all" semantics for real session names.

### H3 — `get_dependency_graph.cell_name` is a hardcoded empty string
**cosmetic.** `templates/dependency.py:37` sets `cell_name = ""` and emits it at
`:48`, for every cell, while `get_cell_map` reports the same cells' real names
(`"_"`, and a `create_cell(name=…)` name). Nothing consumes the field. *Fix:*
populate it from the cell implementation if it is available, or remove the field
(a payload-contract change, so it needs a release note).

### H9 — a *preview* read blesses a full-source baseline for every cell
**misleads — mechanism verified; the harm is a design decision.** `tools/cells.py:79`
has `get_cell_map` call `tracker.commit(session_id, fingerprints)` for **every**
cell it returns — correct for that tool's own purpose (`changes_since_last` is a
notebook-wide observation diff) — while it returns `preview_lines: 3` by default.
In staleness-guard terms that is a full read of every cell, so after a
`get_cell_map` an `edit_cell` overwrites a co-worker's newer source with
`status: ok`, and the H7 fix does not change that. `co-work-loop.md` §2 tells
agents to "Start here", and `live-safety.md:21-22` names `get_cell_map` as one of
the two ways to record the read baseline.
Surfaced by the H7 task's own test scaffolding, which could not construct a
never-read cell after a cell-map call (the deviation is recorded in that task's
report); the mechanism was re-verified at the source.
*Decision needed, not a patch:*
1. accept it and say so plainly — a cell-map read *is* a read, preview or not;
2. separate change-detection from read-baseline tracking so a preview read no
   longer blesses source freshness — note `changes_since_last` is built on the
   same snapshot, so this needs a second tracker dimension, not a one-liner;
3. make the baseline explicit-only (`get_cell_data`, or a dedicated read).
Whichever wins, the losing option must be removed from `live-safety.md`'s recovery
instructions, or the doc will keep teaching the behaviour we decided against.

### H10 — the H7 fix's premise is cited from a throwaway probe, not pinned
**hardening.** Narrowing the tracker commit is safe only because inserting a cell
leaves every existing cell's `code_hash` unchanged on marimo 0.24.x — measured
once with `/tmp/hunt_probe/hash_scope_probe.py`, cited in H7's entry, and now
gone. If a marimo bump changes that property, the guard starts returning false
`conflict`s for cells nobody touched and no test would say so.
*Fix:* add a live test asserting the invariant (insert a cell, assert every
pre-existing cell's `code_hash` is unchanged; same across a delete) and point
H7's entry at the test instead of the probe.
*Closure:* the test exists and is shown to fail when the assumption is violated —
demonstrate by temporarily perturbing the assertion, and record that observation,
since there is no pre-fix code to stash here.

---

## Checked and clean (do not re-derive)

Recorded by the same hunt, each with a repro, so a later run spends its budget
elsewhere:

- **`lint_notebook` reads the on-disk file and the file tracks live edits** — a
  live `edit_cell` was on disk 0.19 s after the call started, and a
  `create_cell` was on disk immediately; a deliberately broken cell produced a
  runtime issue that cleared after `delete_cell`.
- **`run_cell` / `delete_cell` / `create_cell(after=|before=)` on an absent id
  fail loudly**, and `edit_cell` on an absent id returns an explicit error.
- **List-typed argument normalization holds** (T14 regression check): native
  array, single string, JSON-array string, JSON scalar string, `[]`, `""`,
  `"[]"`, whitespace-padded JSON, malformed JSON, numeric-looking `"5"` and
  `["5"]` all round-trip; absent names are dropped (H4) but nothing is mangled.
- **The cell-private-name rule** (`co-work-loop.md` §6) holds exactly, including
  the documented channel split (structured channel silent, traceback visible in
  the run payload and in `console_stderr`).
- **The same-cell `.value` rule** (`live-safety.md`) holds, with the failure
  visible in both channels.
- **Widget reason codes in the passing paths** are self-consistent with the
  read-back (unchanged value, unknown dropdown key, unknown variable,
  non-widget global).
- **`get_variables` does see notebook-defined names on an instantiated
  notebook** — `AGENTS.md` §Live tests' coverage-gap note describes the
  non-instantiated live-suite tier, not this case; no action, just not what that
  paragraph might suggest to a reader in a hurry.
