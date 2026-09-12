# Agenda (open): post-hunt verification round

> **Status:** **Open — 2 tasks (`T-V1` check round, `T-V2` doc precision).**
> Opened 2026-09-11, right after
> `docs/agenda-bug-hunt-1.md` closed with all 11 findings resolved.
> Its one carried item, the `T13` widget residual, was fixed on 2026-09-11
> (see §T13 below). A **consumer-side run of the whole check list on
> 2026-09-11** (see §Consumer run below) passed every check and surfaced
> `T-V2`; the zero-context round `T-V1` itself still owes its boxes.
> **Trigger:** a session restart, so the checks run in a **fresh, zero-context
> agent** — the standing regression gate for doc/tool work here: the closed items
> are re-verified through the surface a consumer actually uses, not by re-running
> the tests that already pass.

## Why a separate round

The 11 fixes were each verified against their own repro (every repro fails
pre-fix), which proves the *mechanism* but not that the **documented co-work
flow** now reads correctly end to end. Five behaviours changed shape on
2026-09-11, and each is also a claim a consumer-facing doc makes:

1. `session_id`/`server_url` are omittable over stdio for every client, and over
   HTTP/SSE only while one client session is on the process
   (`binding_ambiguous` after a second appears);
2. `get_cell_map` no longer arms `edit_cell` — a first edit owes a
   `get_cell_data`;
3. `get_dependency_graph` refuses `cell_id`/`depth` instead of ignoring them;
4. the read tools report `missing_cell_ids` instead of an empty happy path;
5. `get_errors` flags a cell only on real exception evidence.

So the question this round answers is: **does the surface plus the packaged
resources teach what the code now does?**

## T-V1 — independent check round (fresh session, zero context)

**Method** — the point is *not* to trust this doc, the closed agenda or the commit
messages:

1. Start from the packaged resources only
   (`workflow://marimo-inspect/co-work-loop`,
   `workflow://marimo-inspect/live-safety`,
   `reference://marimo-inspect/fallbacks-and-limits`) plus `README.md`. Do **not**
   read `docs/agenda-bug-hunt-1.md` first — it is the claim under test.
2. Drive the surface through a real harness against one live notebook and follow
   each doc verbatim. Record PASS/FAIL per check with the tool payload.
3. **Restart the harness's MCP server first** (`/reload-mcp` or a fresh session).
   Every changed docstring, payload field and resource is served by the server
   process the harness started at launch, so a stale process makes the checks
   report the *old* behaviour.

**Checks** (one line each: PASS/FAIL + the evidence payload):

- [ ] §1 binding over stdio: `list_active_notebooks`, then an argument-less
      `get_cell_map` → served, no "no active session bound". Include the
      session-per-request client shape — that is the case H11 fixed.
- [ ] §1 binding over HTTP (`--transport http`, two clients): the second
      argument-less call is refused with `binding_ambiguous` and is never handed
      the first client's notebook.
- [ ] §2/§3 + `live-safety`: after `get_cell_map` **only**, `edit_cell` returns
      `needs_read`; after `get_cell_data`, `ok`. No resource may still offer
      `get_cell_map` as a read or recovery step.
- [ ] §3: a bogus `cell_id` is reported in `missing_cell_ids` for both
      `get_cell_data` and `get_cell_outputs` — never an empty happy path.
- [ ] §7 / limits: `get_dependency_graph` with `cell_id` or non-zero `depth`
      refuses (`reason: unsupported_argument`, nothing read); `cell_name` is
      populated and agrees with `get_cell_map`'s name.
- [ ] §6: a cell that merely prints `Error: 3 rows skipped` is **not** reported as
      a console exception; a real traceback is, and `console_exception_evidence`
      says which marker matched.
- [ ] §5: a scalar sent to a list-shaped element returns `did_you_mean` in the
      element's own key type (`["4"]`, not `[4]`), and applying the correction
      succeeds; a repeat of a value the element already holds whose `on_change`
      handler raises is `on_change_failed` + `applied: false` + `no_change: true`
      — never `value_not_applied`, and no `next_steps` entry tells you to
      re-send it.
- [ ] `get_variables` with no names returns notebook names only — no `json`,
      `cm`, `get_variables`, `_is_ui`, `_serialize`.
- [ ] Doc-vs-surface sweep: nothing in the three resources or `README.md`
      contradicts an observed payload. Report contradictions as findings; fix
      them in a separate change, not mid-check.

**Output:** one line per FAIL — item, observed payload, expected, and the doc
sentence that misled (or the code that lies). A doc-only mismatch is still a
finding for this round.

## T13 — carried no longer: the residual is fixed

From `docs/agenda-udv-consumer-findings.md` §T13 (closed agenda, id stable): a
repeat of a value the element already holds whose `on_change` handler then raises
was reported `value_not_applied` instead of `on_change_failed`. **Fixed
2026-09-11** (plan `.hermes/plans/2026-09-11_000750-t13-residual-ui-rejection-site.md`,
now executed): the failure *site* is read from the kernel traceback's own call
site (`tools/ui.py::_rejection_site`) and combined with the read-back, so
`on_change_failed` + `applied: false` + `no_change: true` + `handler_ran: true` is
the third combination. Real stderr for all three cases is preserved at
`.hermes/probes/t13_transcripts.json`; the resolution + evidence pointers are in
the T13 entry. The `T-V1` §5 check below now covers the third combination too.

## Not in scope

- Re-litigating the closed hunt #1 items' mechanisms — each already has a repro
  that fails pre-fix; this round checks the surface and the docs, not internals.
- `assets/presentation/*.png` (untracked). The commits this round was opened
  alongside are no longer pending: `main` was pushed with the T13 fix and the
  release, tagged `v0.3.3` (2026-09-11).

## Consumer run 2026-09-11 — all checks PASS (not the zero-context round)

Run from the **consumer repo** (`udv-echo-process`), which is the first real
user of this surface, against **v0.3.3**. Target: headless marimo 0.24.0 edit
server on a `/tmp` copy of the consumer's live notebook, kernel session
materialized with the `/sse?session_id=…&file=…` handshake (isolated
`XDG_STATE_HOME`); the surface was driven both by the harness's own MCP client
and by `fastmcp` / `mcp`-SDK clients over stdio plus a second
`--transport http` instance.

**Verdict: PASS on every check** — stdio binding including the
session-per-request client shape; the HTTP `binding_ambiguous` refusal (fails
closed, never hands over another client's notebook) and the session-holding
client served from its own state; `needs_read` after a preview-only read and
success after `get_cell_data`; `missing_cell_ids` on both read tools;
`get_dependency_graph` argument refusals with `cell_name` agreeing with
`get_cell_map`; the `set_ui_value` shape/apply/verify and T13-repeat
classifications; the `get_errors` console split; `get_variables` scaffolding
exclusion. The doc-vs-surface sweep found **no contradiction**: every
`get_cell_map` mention across the three resources and `README.md` frames the map
as orientation only and states the preview does not record the edit baseline.

Two harness-side lessons for anyone re-running it: a crashed pass leaves created
cells in the notebook copy (repeated names then make `create_cell` fail with
`Multiply-defined names`), and `create_cell` legitimately records its own cell's
read baseline, so the faithful preview-vs-read test needs an **untouched** cell.
Full evidence: `udv-echo-process/docs/marimo-integration-log.md`, entry
2026-09-11 (O35–O38).

**This does not tick `T-V1`.** That round exists to be run by a fresh,
zero-context agent — this run had read this very document first, which is
exactly the contamination the method excludes. The boxes below stay open.

## T-V2 — `get_errors` evidence/stderr are per-cell only; the surfaces don't say so

```
id: F1
tool: get_errors
exact_args: {}   (session bound; a UI on_change handler had raised)
observed:
  top level: {"has_errors": false, "total_errors": 0, "total_structured_errors": 0,
              "total_cells_with_errors": 0, "has_console_exception": true,
              "total_console_exception_cells": 2, "next_steps": ["Console stderr
              carries traceback in 2 cell(s) while the structured channel is
              empty — read those cell's console_stderr and inspect the affected cell"]}
  no top-level "console_exception_evidence" or "console_stderr" key exists;
  cells[].console_exception_evidence == "traceback" (1 per flagged cell) and
  cells[].console_stderr carries the traceback + marimo's "An exception was
  raised by a UIElement's on_change handler:" line
expected: a consumer's reading of the check line in §T-V1 ("a real traceback is
  [reported], and `console_exception_evidence` says which marker matched") and
  of this tool's own description — which names `console_stderr` as one of the
  two reported channels without saying it is per cell — is that the evidence is
  findable from the payload the summary describes
why_wrong: nothing is misreported — the payload is correct and `next_steps`
  does point at `cells[].console_stderr`. But a consumer that reads the
  top-level summary (counts, the flag) and then looks for the evidence at the
  same level reads an exception with no evidence, and a consumer-side check had
  to be re-run to discover the field is per cell. The tool description names
  `console_stderr` without scoping it to `cells[]`, and the §T-V1 line does not
  either
repro: boot a headless marimo server, materialize a session via
  `GET /sse?session_id=<uuid>&file=<abs path>` (wait for `kernel-ready`), then
  with a fastmcp client: `create_cell` a cell defining
  `mo.ui.dropdown(options=['a','b'], value='a', on_change=<handler that raises>)`,
  `run_cell` it, `set_ui_value` the dropdown to `["b"]`, then `get_errors` —
  observe the payload above. Working end-to-end recipe (exact commands, plus
  the fresh-copy pitfall) is in the consumer log entry cited above
severity: cosmetic
status: confirmed (observed live 2026-09-11 against v0.3.3, twice)
doc_claim_ref: src/marimo_inspection/tools/errors.py (tool description);
  docs/agenda-verification-round.md §T-V1 §6 check line
```

**Fix shape (one line beat):** scope the wording — the tool description and the
`T-V1` check line should say the evidence marker and the stderr events live on
`cells[]`, while the top level carries the counts/flag. Optionally add
`next_steps`-style symmetry, but no payload change is required. Per the closure
rule the claim is part of the fix: a fixed tool with an unchanged doc leaves this
half-closed.
