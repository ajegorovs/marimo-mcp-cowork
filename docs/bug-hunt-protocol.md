# Bug-hunt protocol

> **Status:** validated once — hunt #1 (2026-09-11) produced 8 findings, every one
> independently re-verified against the source before triage. Refine from use, not
> from theory: this document describes what that run actually needed.
>
> **Related:** `AGENTS.md` §Live tests (why the suite cannot see these),
> `docs/live-tests.md` (boot mechanics), `docs/agenda-udv-consumer-findings.md`
> (the historical findings this protocol exists to keep finding).

## The failure mode this targets

**Silent wrongness:** a tool returns a *plausible* payload that is wrong — an
empty list where an error was warranted, a success status for a write that did
not take effect, a hint that contradicts the shape named in the same payload, a
`has_*: false` that is hardcoded, a documented parameter that is accepted and
ignored. Every high-value defect this repo has caught was in this family:
the false `edit_cell` conflict, console-only UI-handler exceptions being
invisible, the `set_ui_value` mislabel (T13), the JSON-list silent misread
(T14), `has_output: false`.

The automated suite **cannot** find them, by construction:

- it asserts invariants someone already thought of — a bug nobody imagined is
  invisible to it;
- it mocks the state layer (so a broken binding passes) and always passes
  `session_id`/`server_url` explicitly (so the argument-less path is never
  exercised);
- its live tier reaches executed-cell state only through cells created by the
  write tools (the shared `/sse` fixture is never instantiated), and it boots
  its own kernel rather than a real consumer notebook.

So discovery has to be agentic: a real task, in a real notebook, through the
real tool surface, with evidence the reader can rerun. Everything *around*
discovery can and should be automated.

## Division of labour

| Part | Nature | Why |
| --- | --- | --- |
| Finding the bug (judgement: "this payload is plausible but wrong") | **agentic, on request** | requires intent to break things and the ability to notice |
| Standing up a realistic lab | scripted/manual, repeatable | the same recipe every time; see §Lab |
| Evidence format (schema + runnable repro) | fixed by this protocol | makes a finding convertible into a regression |
| Repro → regression test | mechanical | the repro already exists; only the harness changes |
| Documented claim → executable assertion | semi-mechanical | the packaged resources are the spec; each claim is testable |
| Everything else (a scripted "hunt suite") | **non-goal** | a script only checks invariants someone wrote down, so its discovery value for novel bugs is ~zero — it is a regression tool wearing a discovery costume |

## The protocol

### 1. Target and lab

Pick **one live session** and say exactly what it is in the charter: server URL,
session id, notebook path, whether it is instantiated, and which provider tree
is under test.

What hunt #1 used, and why it worked:

- a **disposable copy** of a real consumer notebook (`/tmp/…/echo_explorer.py`)
  so the hunt may create/edit/delete cells freely and never dirties a repo;
- a kernel launched from the **consumer project's own venv**, so the notebook's
  real imports and `data/` work;
- an **instantiated** session (cells have outputs). This is not optional for the
  execution-state reads (`get_errors`, `get_cell_outputs`, and unfiltered
  `get_variables`, which reports executed notebook-defined public names only):
  they show nothing useful on a fresh non-instantiated session — the known
  token-gated gap. `get_dependency_graph` is the exception: it inventories every
  live cell from the notebook structure even without execution and only its
  graph-derived `defs`/`refs`/edges are empty for the unexecuted ones. The lab
  can be materialized in a browser, or the session may already be running from
  an earlier run.

**Lab pitfall — session binding does not survive every client.** With a
harness-shaped FastMCP `Client` over stdio, `list_active_notebooks` and
`set_active_session` report success while every argument-less call then fails
(hunt #1 F1; root cause in FastMCP's per-request state prefix, not in this
package). Write explicit `session_id` + `server_url` into the charter so the
hunt does not spend its budget rediscovering this, and let it corroborate the
finding in one line instead.

### 2. Charter (the delegation brief)

The hunt runs as a fresh zero-context subagent. Its brief must contain:

- **the target and lab** (§1), including the exact MCP client recipe;
- **a real mission, stated as work** — hunt #1's was "add a mean-RPM readout for
  the selected recording to the live notebook, using only the MCP tools". Real
  work is what makes an agent traverse the whole surface and hit real state;
- **rules of engagement**: disposable lab, never touch a repo checkout, never
  start/stop servers or kill processes, one client process for the run, prefer
  raw payloads over paraphrase;
- **what counts as a finding**: a payload that is provably wrong, a status/reason
  code contradicting the payload's own fields, a claim in the shipped resources
  or tool descriptions that does not hold, argument handling that silently drops
  or mangles input. **Not** a finding: notebook-domain difficulty (a file that
  will not load, an import that fails for lack of data). Without this line the
  run fills with noise;
- **an adversarial coverage checklist** (see §Checklist), framed as "make a tool
  return a plausible payload that is wrong — do not test that things work";
- **the findings schema and deliverable paths** (§3).

### 3. Evidence schema (fixed)

```
id: F<n>
tool: <tool name>
exact_args: <the literal arguments sent>
observed: <raw payload excerpt, verbatim>
expected: <what the docs / resources / tool description promise>
why_wrong: <1-3 sentences: why this is wrong or misleading, not merely surprising>
repro: <path to a runnable script + the exact invocation>
severity: blocks-work | misleads | cosmetic
status: confirmed | unconfirmed (say what you could not establish)
doc_claim_ref: <file:line of the contradicted claim, if any>
```

Non-negotiable rules: **no finding without a runnable repro**; `unconfirmed` is a
respected status, not a failure; never invent a payload, an error or a behaviour
that was not observed; a finding that could not be reproduced is reported as
such rather than dropped.

### 4. Verification before triage

**A hunt report is a self-report.** Re-verify every finding against the source
before acting on it — hunt #1 was checked this way, and the check is what makes
the batch trustworthy (`tools/mutation.py` for the guard, `templates/*.py` for
the payload shapes). A finding whose mechanism cannot be located in the code is
downgraded or sent back as `unconfirmed`.

### 5. Triage and closure

- Record the accepted findings durably in the repo (an agenda doc with stable
  item ids — the `.hermes/` hunt directory is gitignored and ephemeral).
- Priority by severity first, then by fix shape: a one-line payload fix beats a
  design question.
- **Closure rule: a finding is not closed until its repro is a test that fails
  against the pre-fix code and passes after it.** Hunt #1's findings come with
  runnable repros precisely so this is a mechanical step. State the pre-fix
  failure in the closing commit/record.
- If the finding contradicts a documented claim, the claim is part of the fix —
  a fixed tool with an unchanged doc leaves the bug half-closed.
- If the finding's root cause is outside this package (hunt #1 F1 lives in
  FastMCP's state scoping), the fix is the claim plus a decision — say so
  explicitly rather than silently degrading the contract.

## Adversarial checklist

Coverage, not a list of known bugs — hunting yesterday's defects finds
yesterday's defects.

1. **List-typed arguments** in every shape: native array, single string,
   JSON-encoded string, `[]`, `""`, `"[]"`, whitespace-padded JSON, malformed
   JSON, numeric-looking id (`"5"`), and a name that does not exist.
2. **Widget values** via `set_ui_value`: scalar to a list-shaped element and the
   reverse, unknown option key, a value the element already holds. Cross-check
   every reason code against the read-back fields in the same payload.
3. **Cell identity**: stale, deleted, absent and never-read cell ids across all
   read and write tools; `edit_cell` against a never-read cell.
4. **Execution-state tools** against an instantiated notebook — the tier the
   shared fixture cannot exercise (the suite reaches executed cells only through
   write-tool-created cells).
5. **Claim cross-check**: every sentence in `resources/*.md` and every tool
   description, against observed payloads.
6. **`lint_notebook`** on a cell that trips a rule, then on the repaired state.
7. **Concurrent writers** (needs ≥2 client processes): the staleness guard, and
   whether an unrelated write changes what it reports.

## Calibration: hunt #1 (2026-09-11)

- Cost: ~9 minutes wall clock, 57 API calls, one subagent, 1/6 checklist sections
  partial (two `fallbacks-and-limits.md` claims needed lab features that did not
  exist — a multi-output cell, and a restartable kernel).
- Yield: 8 findings — 1 `blocks-work` (the `edit_cell` guard is silently disarmed
  by any unrelated write: `_refresh_snapshot` commits a whole-session hash map
  after every successful mutation, forging a read-baseline for every other cell),
  5 `misleads` (binding promise, ignored `cell_id`/`depth`, silent drop of
  stale ids, self-contradicting `did_you_mean`, `Error:` log text flagged as an
  exception), 1 cosmetic (hardcoded `cell_name: ""`), 1 leak (the template's own
  namespace reported as session variables).
- It also recorded **5 claims that held** (lint freshness, loud failures for
  absent ids, T14 list-arg normalization, the cell-private-name rule, the
  same-cell `.value` rule) — a "checked and clean" section saves the next run
  from re-deriving them.
- All 8 were re-verified against the source before triage: every one had a
  locatable mechanism.

## Limitations (honest)

- **One lab, one hunt.** The lab is a single live session and the hunt writes to
  it; concurrent hunts would interleave writes and invent findings.
- **Instantiation is the coverage boundary.** Without an executed session,
  checklist item 4 degrades to structure-only: the suite now covers executed
  `dependency`/`variables`/widget state only for cells created through the write
  tools, while a browser-instantiated consumer notebook still has to be hunted by
  hand. Closing the token-gated instantiation gap is what would convert most of
  this from a manual hunt into suite coverage.
- **Some claims need purpose-built lab features** (multi-output cells, a
  restartable kernel). Mark them unexercised rather than inferring a verdict.
- **Unconfirmed findings need a second, targeted pass** with a lab built for the
  question; do not let them sit unlabelled.

## Cadence

Run a hunt before cutting a release tag, after any marimo/FastMCP bump, and after
any change to a payload shape in `tools/*.py` or to a claim in
`resources/*.md`.
