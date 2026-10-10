# Current agenda

> **Status:** evidence wave in progress. This is the dashboard-facing current-work pointer, not a history log. Update it when a gate changes; keep measured detail in the linked agenda and PRs.

| Workstream | Status | Next gate | Detail |
| --- | --- | --- | --- |
| Native-first marimo comparison | [Draft PR #4](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4), stacked on #3: **A4 no-go** — native `--file B --session A` accepted a write to A without warning. Both arms passed bounded happy path, basic refusals and fault channels; native independent-actor stale edit refused. | Publish and review the reproduced blocker; design and verify a fail-closed target-selection rule before A5. Other A4 probes remain open. | [Convergence agenda](docs/agenda-native-marimo-pair-convergence.md) · [benchmark runbook](docs/native-pair-benchmark-runbook.md) · [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4) |
| Windows baseline harness | [Draft PR #3](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3): test-only portability repair; non-live and live tests verified in bounded groups on Windows. | Review/merge the PR; repeat the gate after any change. | [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3) |

## Open blockers and boundaries

- **A4 blocker:** native combined selectors are not cross-checked. With B named by `--file` and A by `--session`, the CLI returned success and saved the new cell in A. This matches documented session precedence but fails the benchmark's fail-closed wrong-target criterion; A5 is held pending an explicit safety design and verification. Remaining A4 probes and version-confounded interface comparison are still open.
- The Windows test baseline used an isolated, lock-exact environment because the checkout's existing `.venv` lacks pytest. The fixture fixes in the draft PR do not change product code or the dependency lock.
- Remote services, auth/network exposure, implementation, migration, release and merge remain separate decision gates. See the canonical agenda for scope and safety constraints.

## Working convention

Use one draft PR per reviewable slice. Put its feature description, bounded plan, evidence and blockers in the PR body; update this file only when its status or next gate changes. Historical test counts and resolved findings belong in their owning documents, not here. Do not infer completion from an unchecked task or an agent's self-report.
