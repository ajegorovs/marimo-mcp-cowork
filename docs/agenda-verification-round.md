# Agenda (open): post-hunt verification round

> **Status:** **Open — 1 task (`T-V1`).** Opened 2026-09-11, right after
> `docs/agenda-bug-hunt-1.md` closed with all 11 findings resolved.
> Its one carried item, the `T13` widget residual, was fixed on 2026-09-11
> (see §T13 below) — the check round is now the only open work here.
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
- `assets/presentation/*.png` (untracked) and the ten un-pushed commits.
