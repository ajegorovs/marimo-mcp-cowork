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

## Target binding — feasibility of a server-enforced precondition

**Question the A4 gate must answer:** can the pinned server be made to refuse a
mutation whose resolved session is not the notebook the caller named, atomically
with the mutation itself?

Source inspection of the pinned environment (marimo **0.25.1**, Windows):

- Routing is by the mutable `Marimo-Session-Id` request header alone.
  `AppState.get_current_session_id()` reads that header and
  `require_current_session()` looks the session up by it
  (`_server/api/deps.py:135-160`). No file, path or identity parameter exists on
  the lookup.
- `/api/kernel/execute` therefore resolves its session from that header and
  queues `ExecuteScratchpadCommand`; the request body carries only `code`
  (`_server/api/endpoints/execution.py:310-413`). There is no field the caller
  could use to assert which notebook it believes it is addressing.
- Session IDs are mutable by design. `SessionRepository.update_session_id_sync`
  moves the *same* session object to a new key and deletes the old
  (`_session/session_repository.py:152-167`), and `maybe_resume_session(new_id,
  file_key)` attaches an existing file's session to a **caller-supplied** id
  (`_server/session_manager.py:508-521`). That is the mechanism by which a stale
  id later denotes a different notebook.
- The session object *does* carry a durable file identity: `app_file_manager.path`
  (absolute path) and `initialization_id` (the file key it was created under),
  compared together in `get_all_by_file_key`
  (`_session/session_repository.py:62-81`).

**Conclusion:** the identity data a precondition needs is present in the same
handler that resolves the session, but nothing in the pinned wire contract lets a
caller state what it expects. A server-enforced atomic precondition therefore
requires an upstream change; it cannot be built in this package without one. The
wrapper is unaffected by this gap — it addresses a server by URL and a session by
id and refuses an unknown session (`session_not_found`) — but it also has no file
selector, so it is not a drop-in substitute for the native file-targeting path.

### Proposed upstream contract (proposal only — not implemented, not authorized)

Add one optional request field or header carrying the caller's expected notebook
identity, and have the executing handler compare it against the resolved session's
`app_file_manager.path` **before** queueing the command, refusing with a typed
error when they differ. Refusing on mismatch, not merely reporting it afterwards,
is what makes it prevention rather than detection. An epoch or generation counter
on the session, bumped by every id rebinding, would additionally close the case
where the same path is reopened as a different session.

### Falsifying test for that contract

The contract is only real if a test can go red. On one loopback server holding
disposable copies A and B: rotate A's id (open a new `/sse` for A's file under a
new id), present the stale A id together with B's file, then attempt a mutating
execute that asserts A — and require a typed refusal with **both** copies and the
committed fixture byte-identical afterwards. The current benchmark already
demonstrates the setup half: the stale id is accepted for B's orphaned kernel and
a read by it reaches B (`benchmark/native_churn_probe.py`,
`benchmark/evidence/native_sse_disconnect_20261016.json` for the disconnect
behaviour that gates the rotation step). What is missing is a wire field to assert
against, so the test cannot yet be written to fail for the right reason.

### Alternative if that change is not pursued

An owner-approved **restricted** comparison: one server, one session per arm, no id
reuse, with the adapter as the sole caller. That measures interface and version
differences honestly but explicitly stops grading the shipped CLI's unconstrained
targeting, so any A5 result could not be cited as evidence about the native CLI's
safety. The trade-off must be recorded in the decision, not implied.

Implementing the contract, or adopting the restricted scope, needs its own
authorization. This section is feasibility evidence and a proposal, not a selected
implementation.

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
