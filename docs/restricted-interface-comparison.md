# Restricted interface comparison — harness

This document describes the **harness** that stages, restricts and grades the
comparison of two agent-facing interfaces on one mission: this repository's
structured MCP wrapper against the pinned native `marimo pair` CLI. The first
complete batch and its per-run evidence are recorded below; every new batch adds
its own record rather than replacing one.

The comparison exists to answer a bounded question — *is the native interface
worth further work?* — not to certify either surface as safe for unattended use.
The upstream server routes by a mutable session id and accepts no caller-side
expected identity, so neither interface can offer atomic target protection at
this layer. Every guard below is **detection on a captured snapshot**, not
prevention.

## Shape of a run

One arm at a time, four steps, each a separate program:

| Step | Program | What it does |
| --- | --- | --- |
| Stage | `benchmark/mission_setup.py` | Copies the mission notebook byte-exactly into a disposable root, boots one loopback server, creates one session, enforces the guards, writes the run manifest plus a control copy of the anchor. |
| Run | `benchmark/arm_run.py` | Prepares an ephemeral configuration home for the arm and launches the agent in one fresh session with a pinned model, provider, reasoning level, turn limit and wall-clock budget. |
| Grade | `benchmark/mission_grade.py` | Independent of the agent's report, nine checks: fixture unchanged; a saved cell that *assigns* `normalized` while reading `processed` and `input_vector` (structural, so a constant fails); fresh process and session; kernel value equal to the oracle at the default gain; **reactivity** — the grader drives `gain` to 4 and requires 2.5, then restores 2 and requires 1.25; zero cell errors with a partial run refused; and, for the native arm, a ledger whose hash chain ends at the saved file. |
| Stop | `benchmark/mission_teardown.py` | Stops that arm's server tree by recorded pid with identity checks, and fails if a confirmed member survives. |

`benchmark/prompts/mission.md` is the shared mission, given verbatim to both
arms. Each arm additionally receives exactly one interface note
(`native-interface.md`, `wrapper-interface.md`); the *difference between those
notes is the variable under test*, so neither is padded to help its arm.

## The restrictions, and why each exists

- **One server, one session, one notebook per arm.** Staged with a byte-exact
  copy (`read_bytes`/`write_bytes`) and refused when the census is anything but
  exactly one session resolving to that copy. A second session, a rotation, or a
  reused id invalidates the run rather than being silently followed.
- **The adapter constrains its own calls, not the shell.** It reads target
  identifiers from the manifest and re-checks the census. Other shell commands
  can bypass it: exclusive adapter use is prompt-constrained, not enforced.
- **Ledger consistency, not tamper resistance.** The shim appends hashes,
  submitted source, observed created cells, UI values and status for calls that
  reach native execution, including failed calls. The grader checks sequence,
  hash continuity, the final saved-file hash and diagnostic-source attribution.
  The native ledger detects unrecorded file changes when the ledger itself
  remains intact. It is not a tamper-resistant audit log or proof that every
  command used the adapter: the agent can modify notebook and ledger together.
  Failure is `interface_ledger_intact: false` with explanatory text, not a
  separate failure code. The wrapper records `ledger_applicable: false`.
- **An anchor the arm is not pointed at.** `mission_setup --control-dir` writes a
  control copy of the identity plus a digest of the manifest file; grading
  refuses unless the two agree, and refuses outright without a control copy
  unless the caller explicitly declares the anchor unverified.
- **Per-arm isolation.** Each arm gets its own ephemeral configuration home and
  exactly the tools its interface needs — a shell for the native arm (to invoke
  the shim), the MCP server for the wrapper arm, and the generic file toolset for
  neither, since the mission forbids editing the notebook with file tools.
- **Equal budgets.** Model, provider, reasoning level, turn limit and wall-clock
  budget are all required with no defaults, and hash into one fingerprint that
  **both arms must print identically**. The toolset list is deliberately not in
  that hash: it is the interface under test, so it is recorded beside the
  fingerprint instead. A difference between the two fingerprints is a harness
  fault, not a finding.
- **Grading speaks the arm's language.** The kernel is read with the same
  framework version the arm ran under (MCP tools for the wrapper arm, the native
  CLI for the native arm). A single reader for both arms fails the arm whose
  framework lacks the feature the reader uses, which reads as a failed mission on
  work that actually succeeded.

## Evidence contract for every result

The committed bundles are **summaries of author-generated grades**, not
independently replayable verification packages. They include grade output,
selected metadata, sanitized final agent reports and artifact hashes, but omit
the actual ledger entries, saved notebooks and a protected record of all tool
invocations. A reviewer can inspect reported verdicts but cannot recompute ledger
integrity or establish exclusive adapter use from these bundles. Artifact
reactivity is graded independently of the agent report; this does not make the
agent-writable ledger tamper-resistant. This limitation applies to every batch
and follow-up run below.

## First complete batch — results

Two runs per arm, alternating order (wrapper → native → native → wrapper), model
`deepseek-v4.1-flash` on `opencode-go`, reasoning medium, 30 turns, 600 s wall
clock, identical budget fingerprint on both arms. Every run was graded out of
band by a separate process that never trusted the agent's report.

| Run | Arm | Grade | `normalized` | Oracle | Cell errors | Anchor | Agent wall time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | wrapper (marimo 0.24) | pass | 1.25 | 1.25 | 0 | control_verified | 77 s |
| 2 | native (marimo 0.25.1) | pass | 1.25 | 1.25 | 0 | control_verified | 328 s |
| 3 | native (marimo 0.25.1) | pass | 1.25 | 1.25 | 0 | control_verified | 100 s |
| 4 | wrapper (marimo 0.24) | pass | 1.25 | 1.25 | 0 | control_verified | 122 s |

All six checks passed in all four runs: canonical fixture unchanged, `normalized`
cell saved on disk, reopen rotated both process and session, kernel value equal to
the independent oracle, `gain` back at its default, zero cell errors. One extra
pre-flight wrapper run was used to confirm a harness fix before spending the batch
and is deliberately **not** counted here.

`wrapper` 77–122 s, `native` 100–328 s. **The ranges overlap at two runs per arm,
so no efficiency conclusion is drawn** — more repeats, in both orders, are needed
before any such claim, and no token or cost data was captured.

Author-generated per-run grade summaries: [`benchmark/evidence/restricted_comparison_20261017.json`](../benchmark/evidence/restricted_comparison_20261017.json).

### Interface frictions the agents reported

Self-reported and therefore context, not evidence — but they are the material a
next iteration would act on.

Wrapper arm: no shell or file tool at all, so the agent could not read the
notebook file itself; discovery is the only way to bind, and when it fails there
is no way to name a target (every tool answers `session_required`); `set_ui_value`
certifies only the widget value, so downstream effects had to be re-read; a
materialised session is not a run (cells start `stale` with an empty variable
namespace); the cell-creation payload field is `source`, and a first call was
rejected pre-handler as a schema error.

Native arm: the shim wraps submitted code into one code-mode program, so a created
cell executes immediately and its stdout returns in the same JSON; a cell that
rebound the helper name was refused by the in-kernel graph check with only a
truncated traceback (cut at 400 chars); in-kernel helpers are reachable, so
capabilities beyond the documented subcommands were available.

One native observation is a **framework-version effect, not an interface effect**:
saving under 0.25.1 bumped `__generated_with` from 0.24.0 and rewrote the plot
cell's `return ax, fig` to a bare `return`. It belongs to marimo's serializer, and
it must not be read as a difference between the two agent surfaces.

## Second batch — a stricter grader, and what the ledger caught

The same two-arm, two-run batch re-run under the nine checks. The wrapper arm
passed 2/2 — `metadata.elapsed_s` 98.6 s and 73.3 s (see the correction below).
**Both native runs failed one check**,
`interface_ledger_intact`, and diagnosing that failure found a real defect — in
the harness, not in the arm.

**Correction (2026-10-17).** This paragraph previously reported the two wrapper
runs as 109.6 s and 83.8 s. Those figures are not reproducible from the committed
bundle: each run's metadata records `elapsed_s` of 98.578 and 73.265, equal to
that run's own `started_at` → `ended_at` delta, and no other timing field is
published for this batch. The recorded field is used above; the superseded pair
had no stated measurement boundary. Verdicts, checks and the 129.75 s figure for
the post-fix live native run are unaffected, and no run was repeated.

What the agents actually did: each ran `pair create_cell` through the shim. The
shim created the cell, then ran it, and the run raised
`NameError: name 'processed' is not defined` because the fixture's earlier cells
had not been executed in that session yet. The call therefore reported failure
(exit 8, `tool_unsuccessful`) — **but the cell had already been created and
saved**. The first shim version recorded only *successful* calls, so that write
appeared in the ledger nowhere, and the hash chain showed the notebook changing
"outside the adapter".

Two things follow, and both are recorded rather than smoothed over:

- **A failed call is not an ineffective one.** The shim now records a call it
  reports as failed, with its error status and the hashes around it, and the
  grader attributes the cell on disk to the `create_cell` code the ledger
  recorded — whichever status that call carried. Attribution, not just chaining.
  With an intact ledger, a cell written straight into the file answers to no
  recorded call and fails; coordinated ledger edits are not excluded.
- **The native interface's own shape**: `create_cell` creates and runs in one
  program, so a kernel error in the new cell is reported as a failed *creation*.
  Both agents recovered by running `pair run_all` afterwards. That is a
  usability observation about `marimo pair`, not a comparison result.

After the fix: a deterministic driver replaying exactly that sequence (including
the failed `create_cell`) grades **pass on all nine checks with 9 ledger
entries**, and one live native agent run grades **pass on all nine with 14
entries**, exit 0, 129.75 s, with the grader observing 1.25 → 2.5 → 1.25 on the
restored gain. The check did not loosen to make those pass: the ledger became
complete, and the attribution rule is stricter than "an entry said create_cell".

That live re-validation took three attempts, and the first two failed on harness
invocation rather than on the arm: a relative `--shim` path was correctly refused,
and a missing working directory killed the child in 1.8 s (now refused up front,
and recorded as `exit_code 1, terminal_state completed` instead of a silent
success). Failed attempts stay in the record: a harness that hides its own
misfires cannot be trusted with an arm's.

The first batch's verdicts stay labelled as what they were — passes of the six
checks that existed then.

## What it does and does not establish

Establishes: whether one interface let an agent complete a fixed mission on a
disposable notebook, at what turn/time cost, and whether the resulting artifact
reopens correctly — under one machine, one framework version per arm and a stated
number of repeats.

Does not establish: that either interface is safe for unattended use (the guards
are snapshot checks, and both arms necessarily have an in-kernel code channel);
that results transfer across versions, platforms or remote topologies; anything
about token cost unless usage was actually captured; or any migration decision.

## Reproducing a run

Paths come from arguments; nothing here is machine-specific. Substitute the two
interpreters (one per arm's framework version) and the wrapper's
`marimo-inspect` executable:

```text
mission_setup.py --canonical benchmark/notebooks/mission_clean.py --root <ROOT> \
  --python <ARM_PYTHON> --arm {native,wrapper} --state-dir <STATE> \
  --manifest <MANIFEST> --control-dir <CONTROL>

arm_run.py --arm {native,wrapper} --home <HOME> --workdir <WORK> \
  --state-dir <STATE> \
  --mission benchmark/prompts/mission.md --interface benchmark/prompts/<ARM>-interface.md \
  --transcript <TRANSCRIPT> --model <MODEL> --provider <PROVIDER> --reasoning <LEVEL> \
  --max-turns <TURNS> --run-budget <SECONDS> \
  [--manifest <MANIFEST> --shim benchmark/pair_cli.py | --mcp-command <MARIMO_INSPECT>]

mission_grade.py --manifest <MANIFEST> --python <ARM_PYTHON> --state-dir <GRADE_STATE> \
  --control-dir <CONTROL> --reader {native,wrapper}

mission_teardown.py --manifest <MANIFEST>
```

`arm_run.py --dry-run` writes the configuration, prompt and launcher, prints the
exact child command line and the budget fingerprint, and launches nothing — the
cheapest way to confirm both arms are comparable before spending any inference.
