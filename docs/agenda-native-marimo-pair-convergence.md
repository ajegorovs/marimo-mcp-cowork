# Agenda (open issue): native-first marimo convergence — core `marimo pair` vs the MCP wrapper

> **Status:** **Evidence wave in progress; no interface decision.** Task 1's
> Windows baseline ran in an isolated locked environment after test-harness
> portability repairs. Both pinned local arms now passed a bounded live metric,
> slider, saved-copy and fresh-server reopen gate; the broader A4 safety/capability
> matrix and A5 fresh-agent missions remain unrun. Tasks 4–6 are separately
> gated. See the [current agenda](../agenda.md) for the next gate and blockers.
> **Created:** 2026-10-05
> **Related:** [project-status.md](project-status.md) ·
> [marimo-version-support.md](marimo-version-support.md) ·
> [deployment-topologies.md](deployment-topologies.md) ·
> [agenda-remote-marimo-mcp.md](agenda-remote-marimo-mcp.md) ·
> [mcp-upgrade-roadmap.md](mcp-upgrade-roadmap.md) · [live-tests.md](live-tests.md)
>
> Planning does not authorize implementation, commits, pushes, dependency changes
> or service restarts. Each task carries its own authorization boundary below;
> readiness and a reviewer's recommended approval are not execution authorization.

## The question

Upstream marimo now ships live-kernel code mode, runtime-readable documentation
and a core **`marimo pair`** CLI, with a vendor **Agent Skill** for behavioural
guidance. This repo's MCP surface is a constrained typed vocabulary with strong
refusal contracts plus client/orchestration machinery over the *same*
live-kernel/code-mode machinery.

So: what is the smallest useful future for this package — and does upstream native
machinery replace enough of the wrapper to justify moving the default, or does the
wrapper still earn its keep for some consumers? The plan below answers that with
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

## Corrections that must hold before a developer handoff

1. GitHub currently reports marimo-pair **v0.0.20**, not v0.0.18; marimo **0.25.1**
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

## Task 1: establish a reproducible baseline (first approved execution)

**Objective:** prove current baseline health without upgrading or pruning the
existing environment.

**Read first:** `AGENTS.md`, `docs/project-status.md`, `docs/live-tests.md`,
`docs/marimo-version-support.md`, `docs/deployment-topologies.md`,
`docs/bug-hunt-protocol.md`.

**Files:** initially no source changes. Future evidence belongs under managed
scratch storage, with only sanitized summaries landed in this document;
`docs/project-status.md` is updated only after approval.

**Steps:**

1. Record current Git refs, Python, installed marimo/FastMCP versions and uv lock
   identity.
2. Check existing environment dependencies before invocation. Use `uv run --no-sync`
   for baseline checks; if dependencies are missing, **stop** and request an approved
   isolated sync rather than silently prune or upgrade `.venv`.
3. Run collection, fast tests, live tests and checks:
   - `uv run --no-sync pytest --collect-only -q`
   - `uv run --no-sync pytest -m "not live" -q`
   - `uv run --no-sync pytest -m live -q`
   - `uv run --no-sync ruff check .`
   - `uv run --no-sync ruff format --check .`
   - `uv run --no-sync marimo check notebooks examples`
   - `git diff --check`
4. Capture exit codes, actual counts, failures and versions. Do not equate
   tool-handler coverage with MCP wire coverage, nor kernel coverage with frontend
   coverage.
5. Reference the documentation ledger only against a real run: a published claim is
   not new verification. Publishing remains a separate approval, never an experiment
   prerequisite.

**Gate:** baseline is green, or failures are characterized with runnable repros;
original fixtures and the tracked tree are unchanged. Only
[`live-tests.md`](live-tests.md) receives verified counts.

## Task 2: build a small fair comparison experiment

**Objective:** separate version improvements from interface improvements.

**Proposed new files, after approval:** `tests/fixtures/native_pair_comparison.py`
(`tests/fixtures/` does not exist yet); an experiment driver under managed scratch
first, promoted into a repo test only after design review.

**Existing reference files:** `tests/marimo_inspect/live/conftest.py`,
`test_mutation.py`, `test_ui.py`, `test_run_cell_modes.py`, `test_restart.py`,
`test_errors.py`, `test_instantiate.py`. Do not import a consumer repo or alter
existing fixtures.

**Steps:**

1. Prepare byte-identical disposable copies of one synthetic notebook: array,
   dependent transform, plot, visible `mo.ui.slider` element, intentional runtime
   error, bounded slow cell, and a two-cell edit task. Provide a clean mission state
   after deliberate-error recovery.
2. Add independent numerical and artifact assertions; derive expected results
   programmatically, not from agent self-report.
3. Preserve the existing 0.24 environment. Build an isolated marimo 0.25.1
   environment **outside the checkout** using uv only, with exact version/commit
   pins, isolated config/state directories, loopback listeners and disposable ports.
   Pair it with the marimo-pair v0.0.20 Agent Skill. First inspect the core CLI
   help/source and resolve how the skill's script-based examples map to the CLI;
   document an explicit invocation adapter without changing behavioural guidance or
   silently falling back to scripts.
4. Define comparison arms:
   - **A (primary baseline):** current repo, marimo 0.24.x, existing MCP interface.
   - **B (primary native):** marimo 0.25.1 core `marimo pair` CLI plus marimo-pair
     v0.0.20 Agent Skill.
   - **C (optional diagnostic):** native code-mode/raw execution on marimo 0.24.x,
     where supported, to separate version from interface effects.
   - **D (optional diagnostic):** marimo-pair v0.0.20 shell scripts, only for useful
     portability/compatibility checks, never substituted for arm B.
   Run identical raw code-mode probes across versions where useful, and explicitly
   label remaining confounding. Do not widen our package pin to force a comparison.
5. Measure discovery and exact file/session targeting, live values/source/errors,
   safe durable edits, selected/dependent execution, slider reactivity, structural
   transaction rejection, reconnect behaviour, stale writes, and bounded
   long-running execution.
6. Verify runtime-only versus saved-file state explicitly. Save and reopen a copied
   artifact in a fresh kernel and recheck the numerical result and errors. Existing
   headless tests document that live edits may not autosave; do not rely solely on a
   file diff, nor solely on live readback.
7. Record pass/fail/unsupported/unverified, raw payload paths, versions, commands,
   latency and failure conditions. Exclude Windows/WSL from local verified results
   unless those machines are actually available.

**Gate:** the core workflow has external assertions and a repeatable rerun;
limitations are separated from setup errors; no custom wrapper feature has been built
to make its own score better.

## Task 3: fresh-agent mission comparison

**Objective:** test usefulness, not only endpoint availability.

**Mission:** inspect the live notebook; identify the processed signal; add a visible
normalized diagnostic metric dependent on the existing parameter; execute; change the
parameter; verify the reactive result and both structured/console error channels;
save and demonstrate fresh-kernel rerunnability.

**Steps:**

1. Give separate zero-context agents equivalent disposable initial state, the same
   model and budget, and only their stack's self-sufficient instructions.
2. The native agent starts with a successful `help(cm)` through the core marimo
   0.25.1 `marimo pair` CLI, using the v0.0.20 Agent Skill and the documented CLI
   invocation adapter from Task 2. Bundled scripts belong only to optional arm D. The
   MCP agent uses the packaged MCP workflow resources. Neither edits the live notebook
   file directly.
3. Start with one run each to identify setup failures; repeat completed missions
   before drawing efficiency conclusions. Alternate order and report variance, not
   just the best run.
4. Measure calls, turns, elapsed time, retries, manual interventions, wrong-target
   writes, stale overwrites, numerical correctness and reopened artifact. Token use
   only if actually available.
5. Parent/validator independently reads notebook state and the saved artifact; grade
   PASS/FAIL for each mission requirement.

**Gate:** choose the default interaction only after verified results. **Any
wrong-target write or false-success result is a blocker regardless of speed.**

## Task 4: remote and automation decision gates

**Objective:** spend only where local results show remaining value.

**Read:** [`deployment-topologies.md`](deployment-topologies.md) and
[`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md).

**Steps:**

1. With operator approval and a disposable remote notebook, compare a native direct
   authenticated URL against the existing R2 remote MCP / loopback marimo topology.
2. Use securely provisioned ephemeral credentials; never place real tokens in
   arguments, committed files, logs or reports. Do not restart established services or
   change network exposure without permission.
3. Test reconnect/session re-resolution, explicit targets, two clients, long
   operations, latency and operational burden. Keep tailnet listeners restricted:
   native auth is not permission to expose a public listener.
4. For fixed-schema value, identify one actual automation consumer and define
   required refusal fields, persistence and retry behaviour. Without one, retain the
   existing facade in maintenance mode rather than expand it speculatively.
5. Inspect the core pair CLI implementation and live-server tests first; use issue
   #68 and its fork/PR history as secondary context. Compare discovery, session
   resolution, auth and SSE behaviour before identifying any remaining client gap. Do
   not extract or propose upstreaming our client without a demonstrated unmet need.
   Distinguish the marimo auth token from the skew-protection token.

**Gate:** the user decides one or a combination of — native-first maintenance;
native-first plus a minimal Python bridge; native-first plus a justified structured
automation facade; a lab-specific remote service layer. Windows/WSL gates need actual
platform execution and may be deferred explicitly.

## Task 5: implement the selected gap only (conditional)

**Objective:** deliver a small validated change rather than recreate upstream.

**Likely existing files if a client gap survives:**
`src/marimo_inspection/client.py`, `discovery.py`, `__init__.py`,
`tests/marimo_inspect/test_client.py`, `test_discovery.py`, `test_access.py`,
`test_target_resolution.py`. Verify exact relevant definitions and tests before
edits.

Possible independently approved slices:

- Native-compatible auth and stable file targeting in the Python transport, with
  unknown/ambiguous targets failing closed.
- Minimal distribution/dependency separation **only** if a real Python-only consumer
  or upstream integration needs it. Changing `pyproject.toml` and `uv.lock` is not
  justified by lazy-import preference alone.
- A structured refusal adapter only where automation requires it; preserve existing
  consumer contracts rather than removing tools wholesale.

For each slice: write focused failing tests; demonstrate fail-before; implement the
minimal change; run focused tests; real-kernel readback; the entire fast/live/check
gate; fresh-agent acceptance. Define exact behaviour and files in a separate approved
slice plan after evidence reveals the gap. Never invent an all-purpose backend seam,
and never promise to upstream the whole package.

## Task 6: documentation and release decision (last)

**Files:** `README.md`, `docs/project-status.md`, `docs/mcp-upgrade-roadmap.md`,
`docs/marimo-version-support.md`, `docs/deployment-topologies.md`, relevant
`docs/harness-integration/` pages and packaged resources. Version files only if
releasing: `pyproject.toml` and `src/marimo_inspection/__init__.py`.

1. Document the measured default, the maintained optional facade, supported versions
   and honest non-goals.
2. Mark superseded backlog as superseded in the current work index; retain historical
   evidence and ignored assets.
3. Run cross-reference and privacy review; remove personal paths, concrete private
   network details and credentials from public deliverables.
4. If a marimo bound changes, the full real-kernel suite must pass on the claimed
   range; a native-only benchmark is not a wrapper compatibility proof.
5. Choose version and release notes from actual consumer compatibility. Do not presume
   0.4.0, and do not claim native superiority before the gates.
6. Commit, push and tag only with explicit authorization. **Never move an existing
   consumer tag.**

## Non-goals now

No lifecycle MCP tools, no multi-cell transaction wrapper, no richer output API, no
binding expansion, no auto-discovery of remote endpoints, no blanket 0.25 migration,
no repository rename, no wholesale client extraction, no service deployment.

## Next execution gate

The Windows 0.24.x baseline is exercised, and the isolated native 0.25.1
CLI passed transport smoke. Both local arms now have bounded, reproducible
metric/reactivity/saved-copy/fresh-server reopen checks on disposable copies;
see the [benchmark runbook](native-pair-benchmark-runbook.md). Next run the
A4 safety/capability matrix, with setup and version confounds reported
separately, before deciding whether to launch Task 3 fresh-agent missions.
Review the evidence before remote work or product implementation;
**no repository-future outcome is selected in advance.**

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
  validation is an explicit Task 2 prerequisite.
