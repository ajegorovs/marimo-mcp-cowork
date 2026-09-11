# Agenda (open): bug-hunt #1 findings

> **Status:** **Open — 4 items: H8, H9, H10, H11.** Resolved and kept in
> §Resolved log: H1's *claim* (`efac57d`), the payload cluster H2–H6
> (`ad72710`) and H7 (the guard, `4660aec`); H1's capability gap is open as H11.
> H8 is verified against the source; H9/H10 were recorded at the 2026-09-11
> checkpoint (§Checkpoint), and H9 now carries a **decision** awaiting
> implementation; H11 was split out of H1 on 2026-09-11. One further open defect
> lives in another agenda — the `T13` residual, id unchanged (§Checkpoint).
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
> agenda's `T-ids` are untouched.

## Priority (proposed — not yet agreed)

| # | Item | Severity | Fix shape | Priority |
| --- | --- | --- | --- | --- |
| H11 | argument-less calls fail on a session-per-request client | misleads | process-global fallback, scoped after an isolation analysis | P1 |
| H9 | a *preview* read blesses a full-source baseline for every cell | misleads | **decided**: read baseline becomes explicit-only — implementation pending | P1 |
| H8 | `get_variables` reports the template's own namespace | misleads | exclude the template's scaffolding names | P2 |
| H10 | the H7 fix's premise is cited from a throwaway probe, not pinned | hardening | add the live invariant test | P2 |

---

## Checkpoint — 2026-09-11

**Landed and committed locally (not pushed):** `4660aec` fixes H7 with two live
regressions that fail against the pre-fix code plus four hermetic tracker cases;
`ac07eb7` adds `docs/bug-hunt-protocol.md` and this doc; `8ad4b3a` records the
H9/H10 checkpoint; `13acfb6` folds H7 out of §Open items into §Resolved log;
`efac57d` scopes the session-binding promise (H1, H11 split out); `ad72710` fixes
the payload cluster H2–H6. Verified on the tree after each: `-m "not live"` 316
passed, `-m live` 33 passed, ruff check/format clean, `marimo check notebooks`
exit 0.

**The pending work is the sections below — they are the durable specification.**
Per-fix plan files live under `.hermes/plans/` (gitignored, ephemeral), so treat
an item's entry here as the authority and a plan file as a convenience:

- `.hermes/plans/2026-09-11_140500-h1-binding-claim-conditional.md` — H1's claim
  fix (H11 split out), executed 2026-09-11.
- `.hermes/plans/2026-09-11_000750-t13-residual-ui-rejection-site.md` — the T13
  residual (see the cross-reference below), prepared and **not started**.

**Cross-reference — open work recorded in another agenda.** The T13 residual in
`docs/agenda-udv-consumer-findings.md` §T13 is still unfixed: a repeat of a value
the element already holds whose `on_change` handler then raises is reported
`value_not_applied` instead of `on_change_failed` (unpinned, unobserved in normal
use). That agenda is closed, so it is listed here to keep the open defects in one
place; its item id stays `T13`.

**Hunt lab — gone.** The disposable lab hunt #1 used (a `/tmp` copy of a consumer
notebook served on `127.0.0.1:29417`) is dead: nothing listens on that port and
the tmux session that owned it no longer exists (re-checked 2026-09-11 after a
full system restart — the only tmux sessions now are the user's own agent
instances, not labs). Its registry entry
`~/.local/state/marimo/servers/127.0.0.1_29417.json` is a stale leftover and
harmless (a dead server fails `discovery.py`'s health check). Nothing pending
needs a lab — the live suite boots its own kernel.

---

## Open items

### H9 — a *preview* read blesses a full-source baseline for every cell
**misleads — mechanism verified; the harm was a design decision, and the
decision is taken.** `tools/cells.py:79` has `get_cell_map` call
`tracker.commit(session_id, fingerprints)` for **every** cell it returns —
correct for that tool's own purpose (`changes_since_last` is a notebook-wide
observation diff) — while it returns `preview_lines: 3` by default. In
staleness-guard terms that is a full read of every cell, so after a
`get_cell_map` an `edit_cell` overwrites a co-worker's newer source with
`status: ok`, and the H7 fix does not change that. `co-work-loop.md` §2 tells
agents to "Start here", and `live-safety.md:21-22` names `get_cell_map` as one of
the two ways to record the read baseline.
Surfaced by the H7 task's own test scaffolding, which could not construct a
never-read cell after a cell-map call (the deviation is recorded in that task's
report); the mechanism was re-verified at the source.

**Decision (2026-09-11, user): the read baseline becomes explicit-only.** A
3-line preview is not "I read the source": only a full-source read
(`get_cell_data`, or a dedicated read) blesses a cell's freshness. `get_cell_map`
keeps its change-detection snapshot — that is a different question — so the
tracker needs a **second dimension** rather than a re-pointed `commit` (the
snapshot `changes_since_last` is built on must not be the freshness baseline).

*Implementation checklist (the remaining work):*
1. split the tracker so change-detection and read-baseline are separate
   (new baseline store; `commit` keeps feeding `changes_since_last`);
2. `get_cell_map` stops writing the freshness baseline, `get_cell_data` keeps
   writing it (it already does, via `record_cells`);
3. update `live-safety.md` §"Recovery is a real re-read" to name
   `get_cell_data` only — `get_cell_map` must be removed as a recovery step, or
   the doc keeps teaching the behaviour we decided against (and `co-work-loop.md`
   §2/§3 plus `server.py`'s `edit_cell` note say the same thing);
4. probe the blast radius: `get_cell_map` is the documented "start here", so the
   first patch of every session now owes one `get_cell_data` before an edit —
   check the live co-work tests and the demo runbook for a flow that silently
   depended on the old blessing.

*Closure:* a live test that a preview read no longer blesses a never-read cell
(`edit_cell` → `needs_read` after `get_cell_map`, `ok` after `get_cell_data`) —
it must **fail against today's code**, which is the easy direction for once.

### H8 — `get_variables` reports the evaluating template's namespace as session variables
**misleads.** `templates/variables.py:79-82` enumerates `globals()` when no names
are given, which includes the template's own `import json`, `import
marimo._code_mode as cm` and `get_variables` itself — so every "all variables"
call deterministically returns names no cell defines (cross-checked against
`get_dependency_graph.variable_owners`). A caller iterating the map sees five
foreign names. *Fix:* exclude the template's scaffolding names (or derive the
set from the notebook's own definitions) while keeping the documented
"empty = all" semantics for real session names.

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
*Ordering note:* H9 changes which reads write the baseline, so write H10's
invariant test after H9, against the final baseline semantics.

### H11 — argument-less calls do not work on a session-per-request client
**misleads (capability gap, split out of H1 on 2026-09-11).** Making H1's
*claim* truthful left the capability behind it broken for a whole client class:
the advertised optional-arguments flow works only where the client keeps one MCP
session across calls. fastmcp's own `Client` does not — it starts a fresh MCP
session per request on the pinned fastmcp 4.0.3 (stdio *and* HTTP) — so every
argument-less call fails there immediately after a successful bind. That is the
client shape of the hunt's own subagents and of the codex / dsh / DeepSeek
harnesses, which all launch `marimo-inspect --transport stdio`.
*Evidence so far:*
`tests/marimo_inspect/test_session_binding.py::test_fastmcp_client_starts_a_new_mcp_session_per_request`
pins the failure; `.hermes/probes/state_prefix_*.py` measures the rotating
session id, and the same probe through the `mcp` SDK client is stable.
*Fix shape (decision gated, not a one-liner):* a **process-global fallback
binding**, consulted only when the MCP-session state has nothing bound.
**The isolation analysis must come first:** over stdio one server process serves
exactly one client, so a process-global binding is equivalent to a
connection-global one — no leakage; over `--transport http` one process serves
many clients, and an unscoped fallback would let a client that never bound pick
up another client's session and mutate the wrong notebook. So the fallback must
be scoped (by transport, or by "only one client has ever connected to this
process"), and the scope is the deliverable — not just the store.
*Closure:* one test that an argument-less call after a bind succeeds through the
fastmcp-`Client` path (fails today), plus one test that two concurrent HTTP
clients never see each other's binding. Any wording change in
`co-work-loop.md` §1 / `fallbacks-and-limits.md` lands in the same change.

---

## Resolved log

One entry per closed item, with the evidence pointer that holds the detail. Ids
stay stable (`H<n>` = hunt finding `F<n>`); an item leaves §Open items the moment
it lands here.

- **H2** ✅ *`get_dependency_graph` accepted `cell_id`/`depth` and ignored both* —
  **resolved 2026-09-11** (`ad72710`). The template computed `center_cell` /
  `target_depth` and never used them, so centring on a real cell at depth 1 and 2
  — and on a nonexistent id — all returned the byte-identical full graph with no
  error, while `tools/dependency.py` documented the centring behaviour.
  *Fix:* the tool refuses both arguments up front (`status: error`, `reason:
  unsupported_argument`, `unsupported_arguments`, and `nothing was read`), and
  the dead plumbing is gone from the tool and from
  `build_dependency_graph_template`, which now takes no arguments at all.
  *Evidence:* `tests/marimo_inspect/test_tools.py::TestGetDependencyGraph`
  (`test_cell_id_is_refused_not_ignored`, `test_depth_is_refused_not_ignored` —
  which also pins that `depth=0` stays valid, `test_both_arguments_reported_together`)
  and `tests/marimo_inspect/test_templates.py::TestDependencyGraphTemplate::test_takes_no_centring_arguments`;
  all fail pre-fix. `fallbacks-and-limits.md` and `co-work-loop.md` §7 state the
  whole-notebook contract.

- **H3** ✅ *`get_dependency_graph.cell_name` was a hardcoded empty string* —
  **resolved 2026-09-11** (`ad72710`). *Fix:* the template derives the name from
  the same source `get_cell_map` reads (`ctx.cells` → `NotebookCell.name`, with
  the graph's `CellImpl.name` as fallback), so the two tools agree.
  *Evidence:* `TestDependencyGraphTemplate::test_names_cells_from_the_notebook_cells`
  and `TestGetDependencyGraph::test_cell_names_pass_through` (the latter pins
  tool-layer passthrough, so it does not fail pre-fix).

- **H4** ✅ *stale or absent cell ids were silently dropped by the read tools* —
  **resolved 2026-09-11** (`ad72710`). `templates/cell_data.py` skipped any id it
  could not resolve and returned `{"data": []}` with happy-path `next_steps`;
  `get_cell_outputs` behaved the same, so a deleted or mistyped id was
  indistinguishable from "nothing matched" while the sibling write tools refuse
  the same id.
  *Fix:* both templates collect the unresolved ids and return
  `missing_cell_ids`; the tool layer surfaces the field only when it is
  non-empty (the happy path is unchanged) and names the ids in the first
  `next_step`.
  *Evidence:* `TestCellDataTemplate::test_missing_ids_are_reported`,
  `TestCellOutputsTemplate::test_missing_ids_are_reported` (fail pre-fix) plus
  `test_all_cells_reports_nothing_missing` as the guard for the all-cells path.

- **H5** ✅ *`set_ui_value.did_you_mean` contradicted its own `accepted_shape`* —
  **resolved 2026-09-11** (`ad72710`). The refusal wrapped whatever scalar was
  submitted, so a multiselect whose keys are `'4'`/`'5'` and whose declared shape
  is `list[str]` was told to send `[4]`; following that advice verbatim produced a
  second error (`value_not_applied`, "option name '4' is not a valid option") and
  the working form `["4"]` was never suggested.
  *Fix:* `templates/ui.py` reads the element's own option keys
  (`_option_keys`/`_match_option_key`, exact match then string form) and suggests
  that key, naming it in the message; an element with no options keeps the plain
  one-element form.
  *Evidence:* `tests/marimo_inspect/test_ui.py::TestSetUiValueTemplate::test_scalar_refusal_suggests_the_elements_own_key`
  (fails pre-fix: `[4]` vs `["4"]`) and
  `::test_scalar_refusal_falls_back_without_options` (the no-options pin).
  `co-work-loop.md` §5 now states where the correction comes from.

- **H6** ✅ *`get_errors` reported a console exception for a plain log line* —
  **resolved 2026-09-11** (`ad72710`). `templates/errors.py` matched the bare
  markers `("Traceback (most recent call last)", "Error:", "Exception:")`, so a
  cell that only printed `Error: 3 rows skipped (not an exception)` to stderr and
  returned normally came back with `has_console_exception: true`, counted into
  `total_console_exception_cells`, and carried a `next_step` asserting a
  traceback/UI-handler cause. The template docstring claimed the scan was
  conservative.
  *Fix:* evidence now requires a traceback header or an unindented
  `SomeError:`/`SomeException:` line naming an exception class (a bare
  `Error:`/`Exception:` label is a message, not a type); each flagged cell
  reports `console_exception_evidence` (`"traceback"` / `"exception_line"` /
  `None`), and `tools/errors.py` quotes that evidence in its `next_step` instead
  of asserting a cause.
  *Evidence:* `TestErrorsTemplate::test_error_label_without_a_type_is_not_an_exception`
  (the hunt's exact line; fails pre-fix) and
  `::test_exception_line_is_evidence_without_a_header`, with
  `test_console_exception_visible_when_structured_empty` extended to assert the
  evidence level. `co-work-loop.md` §6 states the rule.

- **H1** ✅ *`list_active_notebooks` / `set_active_session` promise a binding that
  never takes effect* — **claim resolved 2026-09-11; the capability gap behind it
  was split out and is open as H11.** Both tools returned success and told the
  caller `session_id`/`server_url` were "now optional"
  (`tools/session.py:104-110` pre-fix, `server.py:37-43`,
  `resources/co-work-loop.md:10-14`), while every argument-less call raised "No
  session_id provided and no active session bound" — for auto-bind and for an
  explicit `set_active_session`.
  *Mechanism, re-measured 2026-09-11 on fastmcp 4.0.3 / mcp 2.1.1:* session state
  is keyed by the MCP session identity the client negotiates —
  `Context.session_id` caches a state prefix on the SDK connection and
  `_make_state_key` prefixes every key with it
  (`fastmcp/server/context.py:708-791`, `:1106-1108`). So the binding reaches the
  next call only for a client that keeps one MCP session for the connection:
  an `mcp`-SDK stdio client keeps it (the stored value reads back), while
  fastmcp's own `Client` rotates the prefix on **every request, over stdio and
  HTTP alike** (three calls, three different session ids). The pre-fix wording
  ("a client that spawns or reconnects the server per call") named the wrong
  condition — fastmcp's `Client` keeps one subprocess — and the corrected
  mechanism is also why the 2026-09-09 Hermes-gateway verification passed: that
  path is session-stable.
  *Fix (landed, `efac57d`):* the promise is scoped and names its condition —
  `set_active_session` returns `binding_scope` plus a conditional `message`, the
  refusal text explains why an earlier binding can be invisible, `server.py` and
  `list_active_notebooks` carry the same wording, and `co-work-loop.md` §1 +
  `fallbacks-and-limits.md` state the split with the measurement date.
  *Evidence:* four new tests in
  `tests/marimo_inspect/test_session_binding.py` —
  `test_set_active_session_message_is_scoped_to_the_mcp_session` and
  `test_missing_binding_error_names_the_condition` **fail against the pre-fix
  code** (demonstrated by stashing the source change: 2 failed), while
  `test_binding_is_visible_to_a_session_stable_sdk_client` and
  `test_fastmcp_client_starts_a_new_mcp_session_per_request` pin each side of the
  split against the real server binary — the latter is the tripwire that forces
  the docs to be relaxed again if fastmcp starts keeping one session.
  *Left open:* H11 (the capability gap for session-per-request clients).
  Probes kept for reuse: `.hermes/probes/h1_binding_probe.py`,
  `.hermes/probes/state_prefix_driver.py`,
  `.hermes/probes/state_prefix_sdk_client.py`,
  `.hermes/probes/state_prefix_http_driver.py`.

- **H7** ✅ *the `edit_cell` freshness guard is silently disarmed by any unrelated
  write* — **resolved 2026-09-11** (`4660aec`). `_refresh_snapshot`
  (`tools/mutation.py:71-98` pre-fix) read every cell's hash and committed it as a
  **whole-session** baseline after every successful `create_cell`/`edit_cell`/
  `delete_cell`, so a write to *any* cell forged a last-read baseline for *every*
  other cell and defeated both guard branches: a correctly-reported foreign
  `conflict` stopped being reported after one unrelated `create_cell`, and a
  never-read cell became editable with no `needs_read`.
  *Fix:* `_refresh_snapshot` gained `record`/`forget` and now touches only the
  cell the operation mutated — `create_cell` records the new cell's baseline
  (deliberate: the caller authored its source, so the documented create → run →
  edit flow must not force a re-read), `edit_cell` records `[cell_id]`,
  `delete_cell` drops the removed cell via the new `ChangeTracker.forget_cells`
  (`tools/change_tracking.py`; no-op without a snapshot, never erases other
  baselines). The fresh post-write hash read and every `fps is None` warning path
  are unchanged.
  *Evidence:* two live regressions,
  `tests/marimo_inspect/live/test_mutation.py::test_unrelated_write_does_not_disarm_the_guard`
  and `…::test_unrelated_write_does_not_bless_a_never_read_cell` — both **fail
  against the pre-fix code** (with `mutation.py` + `change_tracking.py` stashed
  the first returns `status: ok` instead of `conflict`, the second `ok` instead of
  `needs_read`) and pass after — plus four hermetic tracker cases and the narrowed
  invariant in `resources/live-safety.md` §"Read before edit".
  *Carried debt:* the premise the narrowing rests on (insert/delete leaves
  neighbour `code_hash` values unchanged on marimo 0.24.x) was measured once with
  a throwaway probe and is **not pinned by a test** — tracked as H10, open.
  Plan: `.hermes/plans/2026-09-11_002706-h7-edit-cell-guard-scope.md`.
  *Follow-on:* H9 (a preview read still blesses every cell) is the guard's
  remaining hole and is decided, not yet implemented.

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
  `["5"]` all round-trip; absent ids are now reported (H4) and nothing is
  mangled.
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
