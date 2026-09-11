# Agenda (closed): bug-hunt #1 findings

> **Status:** **CLOSED — all 11 findings resolved (2026-09-11).** Every item is
> in §Resolved log with the repro that fails pre-fix (or, for H10, the inverse
> perturbation demonstration). One further open defect lives in another agenda —
> the `T13` residual, id unchanged (§Checkpoint).
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

**No open items.** The priority table is retired with this closure; each entry
in §Resolved log records the mechanism, the fix and the evidence pointer that
holds the detail.

---

## Checkpoint — 2026-09-11

**Landed and committed locally (not pushed):** `4660aec` H7 (+ two live
regressions), `ac07eb7` the protocol + this doc, `8ad4b3a` the H9/H10 checkpoint,
`13acfb6` the H7 close-out, `efac57d` H1's claim, `ad72710` the H2–H6 payload
cluster, `4cbfb37` the H2–H6 close-out and the H9 decision, `a15cc85` H8,
`f6fa732` H9 + H10 + H11. Verified after each: `-m "not live"` 323 passed,
`-m live` 37 passed, ruff check/format clean, `marimo check notebooks` exit 0.

**Nothing in this agenda is pending.** The plan files under `.hermes/plans/`
(gitignored, ephemeral) are executed or superseded —
`2026-09-11_002706-h7-edit-cell-guard-scope.md` (H7) and
`2026-09-11_140500-h1-binding-claim-conditional.md` (H1's claim, H11 split out) —
except `2026-09-11_000750-t13-residual-ui-rejection-site.md`, which is **not
started**: the T13 residual below is the one item still open anywhere. The probes
behind H1/H11's evidence are kept for reuse under `.hermes/probes/`
(`h1_binding_probe.py`, `state_prefix_driver.py`, `state_prefix_sdk_client.py`,
`state_prefix_http_driver.py`).

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

- **H8** ✅ *`get_variables` reported the evaluating template's namespace as
  session variables* — **resolved 2026-09-11** (`a15cc85`). The "all variables"
  path enumerated `globals()` in the scratchpad namespace it shares with the
  notebook, so every call deterministically returned the template's own
  `import json`, `import marimo._code_mode as cm` and `get_variables` — names no
  cell defines (`_is_ui`/`_serialize` were hidden only by the leading-underscore
  filter), cross-checked against `get_dependency_graph.variable_owners`.
  *Fix:* the builder injects a `_SCAFFOLD_NAMES` tuple into the template and the
  all-names path excludes it; "empty = all" keeps its meaning for real session
  names. *Evidence:*
  `TestVariablesTemplate::test_all_variables_excludes_template_scaffolding`
  (fails pre-fix, stash-verified) plus
  `::test_scaffold_names_are_still_bound_by_the_template`, which fails if the
  template ever binds another module-level name — so the list cannot rot silently.
  The live test this broke was itself pinning the defect
  (`test_variables_sees_kernel_injected_global` asserted `"json" in variables` and
  json's datatype); it is now
  `tests/marimo_inspect/live/test_variables.py::test_all_variables_excludes_template_scaffolding`,
  which asserts no scaffolding name leaks **and** that kernel-injected names stay
  visible ("all" is not "nothing"). `tools/variables.py` documents the contract.

- **H9** ✅ *a preview read blessed a full-source baseline for every cell* —
  **resolved 2026-09-11** (`f6fa732`). `tools/cells.py` had `get_cell_map` commit
  every cell's fingerprint to the change tracker, but that single snapshot also
  served as the `edit_cell` read baseline — so a 3-line preview of the notebook
  blessed every cell, and an edit could overwrite a co-worker's newer source with
  `status: ok`. Decision (2026-09-11, user): the read baseline becomes
  **explicit-only**.
  *Fix:* `ChangeTracker` keeps two dimensions — `_snapshots` (what `get_cell_map`
  observed, feeding `changes_since_last`) and `_baselines` (what a full-source
  read observed, which the guard compares against). `commit` writes only the
  snapshot; `record_cells` writes both; `get_cell_fingerprint` reads the baseline;
  `forget_cells`/`clear_session` cover both. Every message and doc line that named
  `get_cell_map` as a read or recovery path now names `get_cell_data` only
  (`live-safety.md` §"Read before edit", `co-work-loop.md` §2/§3, `server.py`'s
  `edit_cell` note, the three guard messages, the `edit_cell`/`get_cell_map`
  docstrings, `README.md`, `docs/agent-onboarding-demo-mcp.md`).
  *Evidence:*
  `tests/marimo_inspect/live/test_mutation.py::test_preview_read_does_not_bless_a_read_baseline`
  (`needs_read` after a map-only read, `ok` after `get_cell_data`) — shown
  **FAILING against pre-fix code** in an isolated copy
  (`assert 'ok' == 'needs_read'`, the preview having blessed the cell), plus
  hermetic cases in `tests/marimo_inspect/test_change_tracking.py` and
  `::test_cell_map_still_reports_changes_since_last`, which pins the dimension
  that must keep working. Blast radius probed: no live co-work test or documented
  flow depended on the old blessing (the demo's create → run → edit still works,
  because `create_cell` records the new cell's baseline), but the first edit of a
  cell in a session now owes one `get_cell_data`.

- **H10** ✅ *the H7 fix's premise was cited from a throwaway probe, not pinned* —
  **resolved 2026-09-11** (`f6fa732`). Narrowing the tracker write is safe only
  because inserting (or deleting) a cell leaves every pre-existing cell's
  `code_hash` unchanged on marimo 0.24.x — measured once with a `/tmp` probe and
  cited in H7's entry, with nothing to fail if a marimo bump invalidated it and
  the guard started returning false `conflict`s.
  *Fix:* `tests/marimo_inspect/live/test_mutation.py::test_insert_keeps_pre_existing_code_hashes_unchanged`
  and `::test_delete_keeps_pre_existing_code_hashes_unchanged`.
  *Closure (inverse — there is no pre-fix code to stash):* with the assertion
  perturbed (`== hash` → `!= hash`) the test fails on an isolated copy; the shared
  tree was never perturbed, and the observation is recorded in the tests. Written
  **after** H9 by design, so it pins the final baseline semantics.

- **H11** ✅ *argument-less calls did not work on a session-per-request client* —
  **resolved 2026-09-11** (`f6fa732`); this is the capability gap H1's claim fix
  left behind. On the pinned fastmcp 4.0.3, fastmcp's own `Client` starts a fresh
  MCP session per request (stdio *and* HTTP), so state keyed by `ctx.session_id`
  was written and read under different keys and every argument-less call refused
  — the client shape of the codex / dsh / DeepSeek harnesses, all of which launch
  `marimo-inspect --transport stdio`.
  *Fix:* a binding is written to the MCP-session state **and** to a
  process-global fallback consulted only when that state has nothing bound, scoped
  by transport. stdio is single-client by construction, so the fallback there is
  connection-global — never scoped, and its rotating per-request session ids are
  deliberately not counted; HTTP/SSE records distinct client sessions and
  withholds the fallback as soon as a second appears, refusing with
  `SessionBindingError(reason="binding_ambiguous")` rather than letting a client
  that never bound inherit another's notebook. An unknown transport is treated as
  multi-client (conservative). The isolation argument lives in `tools/session.py`'s
  module docstring.
  *Evidence:* `tests/marimo_inspect/test_session_binding.py::test_fastmcp_client_starts_a_new_mcp_session_per_request`
  now pins the **fix** (it used to pin the failure) and
  `::test_two_http_clients_do_not_share_the_fallback_binding` spawns a real HTTP
  server plus two independent HTTP client sessions, asserting the positive control
  **and** `_PROBE_URL not in refused_text` — so an unscoped fallback fails it too;
  plus `::test_fallback_scope_serves_stdio_and_withholds_on_a_second_client`
  (hermetic, driving the shipped predicate) and
  `::test_bind_active_session_stores_the_process_global_fallback`. Pre-fix: 5 tests
  fail on an isolated copy built from read-only `HEAD` sources with `PYTHONPATH`
  pinned. Resources carry the same truth (`co-work-loop.md` §1,
  `fallbacks-and-limits.md`, `server.py` instructions, `set_active_session`'s
  `binding_scope`/message, `list_active_notebooks`' `next_steps`).
  *Known bound:* over HTTP a session-per-request client must still pass the
  arguments explicitly (its rotation trips the single-client scope) — documented
  and fail-closed, not silent.

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
