# Project status and work index

This is the single repository work-discovery index; root [`agenda.md`](../agenda.md) is a compatibility pointer, not another tracker. Effort issues own plans, execution checkpoints, decisions and unfinished obligations. This index routes readers to those issues; evidence documents retain their measurements, not competing live plans. Update navigation here, not mutable progress in `AGENTS.md`.

`AGENTS.md` is stable contributor guidance. It should contain only durable
architecture, conventions, safety rules, and a static link to this index.
Exact test counts remain in [`live-tests.md`](live-tests.md), their canonical
ledger.

## Current work

| Area | Current state | Canonical detail |
| --- | --- | --- |
| Native-first comparison | **Merged** (`8820b8e`, PR #4; stacked on PR #3 `24fedd6`). Owner declined A4 option (a); **A4 remains no-go** and the native path is unvalidated for unattended use. Option (b) selected for the restricted comparison; unrestricted A4 remains no-go. The evidence hardening, churn diagnosis and wrapper-arm exposure probe now live on `main`, with the benchmark probes in `benchmark/`. | [Effort issue #5](https://github.com/ajegorovs/marimo-mcp-cowork/issues/5) · [PR #4](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4) · [historical measurements](native-pair-benchmark-runbook.md) |
| Session identity reuse | **Merged** (`9776f60`, PR #8, accepted after three review rounds). Items 1–3 are on `main`: the binding anchors calls on the bound id by **session identity, not URL string**, a reused id refuses `notebook_mismatch`, rotations and cross-notebook rebinds forget the id's read baselines (one `adopt_notebook` operation), `restart_kernel` is covered, and the integrated live churn regression plus the protected/unprotected boundary and the unsaved-notebook limit are recorded. No task selected here next; the direct `MarimoClient` remains unprotected by design | [Issue #7](https://github.com/ajegorovs/marimo-mcp-cowork/issues/7) · [PR #8](https://github.com/ajegorovs/marimo-mcp-cowork/pull/8) |
| Windows baseline harness | **Merged** (`24fedd6`, PR #3). Its review residue remains open in #6. The Windows benchmark byte-copy defect is fixed by merged PR #9 (`a95e31e`); the full Windows live gate is green — see the ledger | [Effort issue #6](https://github.com/ajegorovs/marimo-mcp-cowork/issues/6) · [PR #3](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3) |
| MCP upgrade roadmap | Secondary feature backlog while the native-first comparison is open. Composite cell reads and structured target refusals are implemented — including the evidence-based auth/edit-scope denial taxonomy (`edit_scope_required` / `auth_required` / `session_census_denied`, classified by a read-scope probe); `restart_kernel` is implemented; notebook-server start/stop/switch remain operator-owned | [`mcp-upgrade-roadmap.md`](mcp-upgrade-roadmap.md) |
| Consumer findings | Round 2 fully closed — T18 resolved: server discovery is not session discovery (a launch creates no session; a `marimo run` server registers but is never discoverable, its census answering 401), on top of the browser-first recipe (marimo 0.24 edit mode without `--session-ttl`) and truthful `provenance`/`owner: "unknown"` with scoped counts | [`agenda-udv-consumer-findings.md`](agenda-udv-consumer-findings.md) |
| Remote topology | Tailnet-only R2 (remote MCP co-located with loopback marimo) is the supported shared co-work mode; R1 (local client/MCP targeting an explicit non-loopback marimo server) is supported but not preferred. Both controlled two-machine paths prove discovery, reads, safe scratchpad execution, and structured unreachable refusals. Protected marimo is detected and refused as `auth_required`; lifecycle supervision, timeout/version policy, remote UI elements, and multi-checkout sharing remain open | [`deployment-topologies.md`](deployment-topologies.md) · [`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md) |
| Restricted interface comparison | **Merged** (PR #10 `ffc3436`; reviewed head `c6ae2af` merged unchanged). Merged-tree gate: live 91 passed, Ruff/format/notebook clean; the non-live gate found a fresh-checkout prompt line-ending defect (2 failed, 1153 passed) which PR #11 fixes — the non-live gate stays red on a fresh checkout until it lands. Ledger checks detect unrecorded file changes only while the record is intact; the native shell is prompt-constrained, not sandboxed; bundles are summaries of author-generated grades, not replay packages. A4 remains no-go; no adoption, migration or release. | [Issue #5](https://github.com/ajegorovs/marimo-mcp-cowork/issues/5) · [PR #10](https://github.com/ajegorovs/marimo-mcp-cowork/pull/10) · [PR #11](https://github.com/ajegorovs/marimo-mcp-cowork/pull/11) · [`restricted-interface-comparison.md`](restricted-interface-comparison.md) |
| Documentation positioning | **In review** (PR #12). The README leads with why this package sits beside official `marimo pair` and links out (534 → 186 lines); the comparison, its evidence and its limits are in [`interface-positioning.md`](interface-positioning.md); [`mcp-contract.md`](mcp-contract.md) is the human-facing contract entry point that links the packaged resources instead of copying them; [`marimo-inspection-tools-comparison.md`](marimo-inspection-tools-comparison.md) is banner-marked historical. Remaining: the wider internal link/dedup sweep and the deferred thin co-work agent skill | [PR #12](https://github.com/ajegorovs/marimo-mcp-cowork/pull/12) · [`interface-positioning.md`](interface-positioning.md) · [`mcp-contract.md`](mcp-contract.md) |
| Live tests | Current commands, mechanics, and exact counts | [`live-tests.md`](live-tests.md) |


## Closed work and decision trails

- [`agenda-bug-hunt-1.md`](agenda-bug-hunt-1.md) — first MCP-surface bug hunt.
- [`agenda-bug-hunt-2.md`](agenda-bug-hunt-2.md) — second MCP-surface bug hunt
  (findings F1–F6 with their raw repro evidence) and its independently validated
  remediation record.
- [`agenda-verification-round.md`](agenda-verification-round.md) — post-hunt
  zero-context verification.
- [`agenda-live-test-redesign.md`](agenda-live-test-redesign.md) — live-suite
  redesign decisions.
- [`agenda-example-notebooks.md`](agenda-example-notebooks.md) — the
  `examples/` contract (consumer-facing patterns, not fixtures), the full
  notebook check `uv run marimo check notebooks examples`, and the live smoke
  plus interaction gate in `tests/marimo_inspect/live/test_examples.py`.

## Update rules

- Keep one navigation row per workstream; link the effort issue for current execution state and existing references for durable background.
- Maintain effort decisions, checkpoints and remaining work in the owning issue; link durable evidence without duplicating the plan.
- Put exact test counts only in `live-tests.md`.
- Move completed work to the closed section; do not copy its chronology into
  `AGENTS.md`.
- Keep identifiers and links stable so old reports remain traceable.
