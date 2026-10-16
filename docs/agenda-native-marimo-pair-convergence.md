# Native-first comparison — design and provenance

[Issue #5](https://github.com/ajegorovs/marimo-mcp-cowork/issues/5) owns the live plan, checkpoint, authorization and next action. This document retains comparison rationale and historical planning provenance, not an execution plan. Measurements and reproduction commands live in the [benchmark runbook](native-pair-benchmark-runbook.md); work discovery starts at [project status](project-status.md).

## The question

Upstream marimo now ships live-kernel code mode, runtime-readable documentation
and a core **`marimo pair`** CLI, with a vendor **Agent Skill** for behavioural
guidance. This repo's MCP surface is a constrained typed vocabulary with strong
refusal contracts plus client/orchestration machinery over the *same*
live-kernel/code-mode machinery.

So: what is the smallest useful future for this package — and does upstream native
machinery replace enough of the wrapper to justify moving the default, or does the
wrapper still earn its keep for some consumers? The comparison is intended to answer that with
reproducible evidence rather than architectural assertion, while preserving working
consumers and valuable regression coverage.

## Goal

Decide and implement the smallest useful future for this package through
reproducible native-versus-wrapper evidence, preserving working consumers and
valuable regression coverage.

## Architecture (the candidate, not the decision)

Treat upstream live-kernel Python and code mode as the **candidate** default, not a
proven winner. Keep the current client/MCP implementation unchanged as the baseline;
evaluate the native pair in an isolated environment. Implement only gaps that
survive comparison **and have an identified consumer**.

**Tech stack:** uv, Python 3.12 baseline, marimo 0.24.x baseline, candidate marimo
0.25.1 core `marimo pair` CLI, pinned marimo-pair v0.0.20 Agent Skill for
behavioural guidance, FastMCP, and the existing pytest unit/live tiers. The bundled
pair shell scripts are a secondary diagnostic control, never the canonical native
transport.

## What the benchmark is not

It is **not** "MCP versus code mode": both paths already use live-kernel/code-mode
machinery. It compares a constrained typed vocabulary with strong refusal contracts
and client/orchestration machinery against general live-kernel Python, native
code-mode operations, runtime-readable documentation and core pair
transport/CLI. Keep that asymmetry in view when reading any result.

## Historical planning caveats

1. The planning pass recorded marimo-pair **v0.0.20**, not v0.0.18; marimo **0.25.1**
   is confirmed. Pin exact upstream commits/artifacts for experiments rather than
   following main.
2. The upstream blog critiques read-only MCP plus disk edits/watch. This project
   already mutates through live code-mode templates. The critique therefore supports
   reducing *predetermined views*, but does not establish that this project's
   observation/action split is identical, nor that all MCP is obsolete.
3. `help(cm)` reduces agent-side schema guessing; it does not guarantee HTTP
   transport, auth, SSE or private-API compatibility. Our client is pure HTTP but
   still relies on private wire behaviour.
4. Code-mode structural transaction rejection is not rollback of arbitrary Python,
   widget side effects, package installation or runtime failures. Test each boundary
   separately.
5. Core `marimo pair` CLI reportedly landed through marimo PR #10777 and shipped as a
   preview in marimo 0.25.0. Inspect the pinned 0.25.1 CLI implementation, help, tests
   and JSON/error contracts **before** benchmarking; verify the reported
   execution/listing/recovery and `MARIMO_TOKEN`/token-file capabilities rather than
   assuming their exact syntax. Issue #68 remains historical context: the move into
   the core CLI narrows the Python-client contribution opportunity — do not start
   extracting our client for upstream.
6. The client is an asset, not yet a minimal portable distribution: `pyproject.toml`
   still installs FastMCP, marimo, plotting and widget dependencies. Lazy imports do
   not equal dependency-light installation.
7. Runtime introspection, credentials, Windows/WSL reliability, rendered widgets and
   concurrency superiority remain **empirical claims to measure**, not architectural
   assumptions.

## Comparison arms

- **A (primary baseline):** current repo, marimo 0.24.x, existing MCP interface.
- **B (primary native):** marimo 0.25.1 core `marimo pair` CLI plus marimo-pair
  v0.0.20 Agent Skill.
- **C (optional diagnostic):** native code-mode/raw execution on marimo 0.24.x,
  where supported, to separate version from interface effects.
- **D (optional diagnostic):** marimo-pair v0.0.20 shell scripts, only for useful
  portability/compatibility checks, never substituted for arm B.
Run identical raw code-mode probes across versions where useful, and explicitly
label remaining confounding. Do not widen our package pin to force a comparison.

## Design non-goals

No lifecycle MCP tools, no multi-cell transaction wrapper, no richer output API, no
binding expansion, no auto-discovery of remote endpoints, no blanket 0.25 migration,
no repository rename, no wholesale client extraction, no service deployment.

## Standing notes carried from planning

- The original planning pass was **read-only**; its historical ledger claims
  were not new verification. Subsequent Windows baseline work exercised the
  locked 0.24.x environment in bounded file groups after test-harness fixes.
  This is **not** a single all-live invocation or a 0.25.x compatibility result;
  platform-scoped counts belong in [live-tests.md](live-tests.md).
- **Release staleness is separate from unpushed work.** A release tag marks the last
  release, not the head of `main`; a large gap between them is expected during
  development and is not itself a defect.
- The repo-local skill `.agents/skills/marimo-widgets/` is tracked and already in
  published history. A repo-local skill's presence in a checkout is not a hidden
  local implementation feature — but note it requires a per-machine trust opt-in
  before it loads (see `README.md`).
- Ignored local harness/research assets (scratch plans, hunts, probes, repro
  scripts, logs) are **local research assets, not automatically pending product
  changes.** Preserve them; publish raw evidence only after privacy review.

## Sources checked during planning

- Strategic reassessment (2026-10-05) — user direction behind this agenda.
- <https://marimo.io/blog/notebooks-as-a-tool-for-agents>
- <https://github.com/marimo-team/marimo-pair/releases/tag/v0.0.20>
- <https://github.com/marimo-team/marimo/releases/tag/0.25.1>
- <https://github.com/marimo-team/marimo-pair/issues/68> (open; the author offers an
  existing Python fork).
- Pinned v0.0.20 skill and execution-context reference under
  <https://github.com/marimo-team/marimo-pair/tree/v0.0.20/skills/marimo-pair>
- Reviewer-provided reference for the core CLI update (not independently reverified
  during planning): <https://github.com/marimo-team/marimo/pull/10777>. Source/CLI
  validation was an explicit comparison prerequisite.
