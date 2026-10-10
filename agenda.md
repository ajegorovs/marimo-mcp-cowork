# Current agenda

> **Status:** evidence wave in progress. This is the dashboard-facing current-work pointer, not a history log. Update it when a gate changes; keep measured detail in the linked agenda and PRs.

| Workstream | Status | Next gate | Detail |
| --- | --- | --- | --- |
| Native-first marimo comparison | [Draft PR #4](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4), stacked on #3: **A4 no-go**. The approved benchmark-only single-selector guard blocked combined selectors under stable A/B sessions, but a same-server reconnect reused stale A's ID for B; native read by that ID then reached B. | Review residual session-binding/churn hazard and design an atomic or otherwise fail-closed target contract before A5. Do not mistake the adapter for an upstream CLI fix. | [Convergence agenda](docs/agenda-native-marimo-pair-convergence.md) · [benchmark runbook](docs/native-pair-benchmark-runbook.md) · [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4) |
| Windows baseline harness | [Draft PR #3](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3): test-only portability repair; non-live and live tests verified in bounded groups on Windows. | Review/merge the PR; repeat the gate after any change. | [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3) |

## Open blockers and boundaries

- **A4 blocker:** native CLI accepts combined selectors by documented session precedence; the benchmark-only guard now refuses them with no CLI call, but session-ID reuse was observed on the same server: after A rotated, its old ID was accepted for orphaned B, then read B's kernel. This is not a wrong-write demonstration through the guard, but the remaining identity/churn gap prevents a fail-closed target guarantee. **A5 stays held**. Authenticated behavior and version-normalized footprint remain unverified.
- The Windows test baseline used an isolated, lock-exact environment because the checkout's existing `.venv` lacks pytest. The fixture fixes in the draft PR do not change product code or the dependency lock.
- Remote services, auth/network exposure, implementation, migration, release and merge remain separate decision gates. See the canonical agenda for scope and safety constraints.

## Working convention

Use one draft PR per reviewable slice. Put its feature description, bounded plan, evidence and blockers in the PR body; update this file only when its status or next gate changes. Historical test counts and resolved findings belong in their owning documents, not here. Do not infer completion from an unchecked task or an agent's self-report.
