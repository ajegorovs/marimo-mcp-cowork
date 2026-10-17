# Interface positioning — marimo-inspect beside upstream `marimo pair`

Why this package exists next to marimo's own agent-facing machinery, what the
current comparison evidence does and does not support, and which interface to
reach for.

Measurement record: [`restricted-interface-comparison.md`](restricted-interface-comparison.md).
Design and provenance: [`agenda-native-marimo-pair-convergence.md`](agenda-native-marimo-pair-convergence.md).
Effort plan, checkpoints and open obligations:
[issue #5](https://github.com/ajegorovs/marimo-mcp-cowork/issues/5).

## The question

Upstream marimo now ships live-kernel **code mode**, a core **`marimo pair`**
CLI and a vendor **Agent Skill**. Both that stack and this package drive the
same live-kernel/code-mode machinery: they are not different execution engines,
and the official path can already perform many of the operations this package's
tools expose.

So the question is not which is more powerful. It is whether a *structured tool
vocabulary* — named read, write, widget and verification operations with typed
refusals — plus a separately importable Python client earns its place beside a
general-purpose CLI for agent frameworks and Python consumers.

The current answer is **complementary, and not yet proven better**. Nothing here
establishes an overall winner, and no adoption, migration or release decision
follows from this document.

## The two surfaces

| Dimension | Official `marimo pair` | marimo-inspect |
| --- | --- | --- |
| Agent interface | CLI + agent skill | structured MCP tools |
| Access model | execute Python in the live kernel | named read, write, widget and verification operations |
| Flexibility | general Python and code-mode operations | narrower, predefined tool vocabulary |
| Python integration | native kernel/code-mode workflow | separately importable `MarimoClient` |
| Error contract | native CLI and kernel feedback | structured tool responses with explicit refusal reasons |
| Concurrency protection | depends on the underlying server behaviour | read-before-edit guard and notebook-path-anchored binding — detection on a captured snapshot, **not** atomic prevention |
| Version evaluated | marimo 0.25.1, with the marimo-pair v0.0.20 Agent Skill | marimo 0.24.x (pinned: private APIs are version-bound) |

This compares **interfaces, not feature counts**. Official marimo also ships
*built-in AI tools* exposed over its own MCP server endpoint, so structured MCP
tooling is not unique to this project — those tools were source-inspected in the
now-historical
[`marimo-inspection-tools-comparison.md`](marimo-inspection-tools-comparison.md).
What is claimed as distinctive here is the *shape*: a named operation vocabulary,
typed refusals, an importable client — not the transport.

The protection row is the one most easily over-claimed. The upstream server
routes by a mutable session id and accepts no caller-side expected identity, so
**neither** interface can offer atomic target protection at this layer. Our bind
narrows the exposure — a call resolved through it is re-resolved against the
bound notebook path and refuses `notebook_mismatch` rather than retargeting —
and that is detection on a snapshot, not prevention. The source evidence for why
a server-enforced precondition cannot be built here without an upstream change
is in § "Target binding — feasibility of a server-enforced precondition" of
[`agenda-native-marimo-pair-convergence.md`](agenda-native-marimo-pair-convergence.md);
the call shapes the anchor does **not** cover are tabulated in
`reference://marimo-inspect/fallbacks-and-limits`.

`MarimoClient` used directly has no notebook-identity precondition at all. That
is a stated design limit, not an oversight.

## What the comparison evidence establishes

One mission, two arms, one arm at a time. The mission notebook is copied
byte-exactly into a disposable root; each arm runs in a fresh process and
session with a pinned model, provider, reasoning level, turn limit and
wall-clock budget that both arms must print identically. Both arms receive the
same mission prompt verbatim, plus exactly one interface note each — *the
difference between those two notes is the variable under test*. Grading is a
separate out-of-band process that never trusts the agent's report, and it drives
reactivity itself (`gain` 2 → 4 → 2) rather than reading the agent's claim.

First complete batch — two runs per arm, alternating order:

| Run | Arm | Grade | `normalized` | Oracle | Cell errors | Anchor | Time (`agent_seconds`) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | wrapper (marimo 0.24) | pass | 1.25 | 1.25 | 0 | control_verified | 77 s |
| 2 | native (marimo 0.25.1) | pass | 1.25 | 1.25 | 0 | control_verified | 328 s |
| 3 | native (marimo 0.25.1) | pass | 1.25 | 1.25 | 0 | control_verified | 100 s |
| 4 | wrapper (marimo 0.24) | pass | 1.25 | 1.25 | 0 | control_verified | 122 s |

Times are the harness's recorded `agent_seconds` for that run, rounded to the
second; the per-run detail, hashes and verdicts are authored and owned by
[`restricted-interface-comparison.md`](restricted-interface-comparison.md).

All six checks passed in all four runs. **No efficiency conclusion is drawn**:
wrapper 77–122 s against native 100–328 s, with the ranges overlapping at two
runs per arm.

Second batch, under a stricter nine-check grader: the wrapper arm passed 2/2
(98.6 s and 73.3 s — that batch's recorded `elapsed_s`, a different field from the
first batch's `agent_seconds`); **both** native runs failed
`interface_ledger_intact`. The cause was a defect in the harness, not in the arm:
the shim recorded only successful calls, while a `pair create_cell` that reported
failure had already created and saved the cell. After the fix, a deterministic
replay of that sequence and one live native run both grade pass on all nine
checks (`elapsed_s` 129.75 s for that live run).

Two observations from that batch are findings rather than noise:

- **A failed call is not an ineffective one.** The shim now records a call it
  reports as failed, and the grader attributes an on-disk cell to the code the
  ledger recorded whichever status that call carried.
- **`marimo pair`'s own shape:** `create_cell` creates *and runs* in one
  program, so a kernel error in a newly created cell is reported as a failed
  *creation*. Both agents recovered by running `pair run_all` afterwards.

### Confounds, stated rather than smoothed over

- The arms ran **different framework versions** (wrapper on 0.24, native on
  0.25.1), so version and interface effects are not separated by this batch; the
  optional same-version diagnostic arm was not run.
- One native observation was a **serializer** effect of 0.25.1 — saving bumped
  `__generated_with` from 0.24.0 and rewrote a cell's `return ax, fig` to a bare
  `return`. That must not be read as a difference between the two agent
  surfaces.
- The committed evidence bundles are **summaries of author-generated grades**,
  not independently replayable packages: they omit the ledger entries, the saved
  notebooks and a protected record of all tool invocations. A reader can inspect
  the reported verdicts but cannot recompute ledger integrity or establish
  exclusive adapter use — and the native arm's ledger is agent-writable.
- Everything holds for one machine, one framework version per arm and the stated
  number of repeats. No token or cost data was captured.

## What the two arms reported about themselves

Self-reported, therefore context rather than evidence — but it is the material a
next iteration would act on.

**Wrapper arm:** no shell and no file tool, so it could not read the notebook
file itself; discovery is the only way to bind, and when discovery fails there
is no way to name a target (every tool answers `session_required`);
`set_ui_value` certifies only the widget value, so downstream effects had to be
re-read explicitly; a materialized session is not a run (cells start `stale` and
the variable namespace is empty); the cell-creation payload field is `source`,
and a first call was rejected pre-handler as a schema error.

**Native arm:** the shim wraps submitted code into one code-mode program, so a
created cell executes immediately and its stdout returns in the same JSON;
in-kernel helpers are reachable, so capabilities beyond the documented
subcommands were available; a cell that rebound a helper name was refused by the
in-kernel graph check with only a truncated traceback.

That list is what the trade-off actually looks like: a predefined vocabulary is
narrower than general Python, and predictable to call and to grade; a CLI is
broader and leans on the caller's discipline.

## What is not established

- That either interface is safe for unattended use: the guards are snapshot
  checks, and both arms necessarily have an in-kernel code channel.
- That results transfer across marimo versions, platforms or remote topologies.
  The supported local and Tailnet shapes are described separately in
  [`deployment-topologies.md`](deployment-topologies.md).
- Anything about token cost, or about efficiency beyond the overlapping ranges
  above.
- Rendered-notebook behaviour: the live suite boots kernels, not frontends (see
  [`live-tests.md`](live-tests.md)).
- Atomic target protection at this layer, which needs the upstream contract
  sketched in the convergence agenda.

## Where the other comparison documents stand

| Document | Status |
| --- | --- |
| [`restricted-interface-comparison.md`](restricted-interface-comparison.md) | Measurement record for the current two-arm comparison; authoritative for its grades, frictions and limitations |
| [`native-pair-benchmark-runbook.md`](native-pair-benchmark-runbook.md) | Historical measurements and reproduction commands for the capability benchmark and the churn/identity probes |
| [`marimo-inspection-tools-comparison.md`](marimo-inspection-tools-comparison.md) | **Historical.** A source-inspection comparison of marimo's built-in AI tools against the marimo-pair CM scripts — a different comparison, against earlier machinery. Kept for provenance, not current guidance |

## Sources

- Harness, batches, confounds and limitations:
  [`restricted-interface-comparison.md`](restricted-interface-comparison.md) and
  [`benchmark/evidence/`](../benchmark/evidence/)
- Design, arms, the A4 feasibility gate and planning caveats:
  [`agenda-native-marimo-pair-convergence.md`](agenda-native-marimo-pair-convergence.md)
- Upstream: <https://marimo.io/blog/notebooks-as-a-tool-for-agents>, marimo
  0.25.1, marimo-pair v0.0.20
- This package's own runtime semantics: the packaged MCP resources — see
  [`mcp-contract.md`](mcp-contract.md) for the navigation and ownership map
