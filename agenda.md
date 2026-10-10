# Current agenda

> **Status:** evidence wave in progress. This is the dashboard-facing current-work pointer, not a history log. Update it when a gate changes; keep measured detail in the linked agenda and PRs.

| Workstream | Status | Next gate | Detail |
| --- | --- | --- | --- |
| Native-first marimo comparison | [Draft PR #4](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4), stacked on #3: A1 baseline and A2 installed-CLI transport smoke complete on Windows; A3 static fixture built. No A/B capability result or interface decision. | Review and promote the independent live/saved-artifact oracle, then run the equal-condition A4 protocol. | [Convergence agenda](docs/agenda-native-marimo-pair-convergence.md) · [benchmark runbook](docs/native-pair-benchmark-runbook.md) · [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4) |
| Windows baseline harness | [Draft PR #3](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3): test-only portability repair; non-live and live tests verified in bounded groups on Windows. | Review/merge the PR; repeat the gate after any change. | [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3) |

## Open blockers and boundaries

- The native 0.25.1 CLI passed a **local Windows transport smoke** (listing, explicit file targeting, `help(cm)`, missing-target refusal). Its mutation, UI, persistence and A/B behaviour remain unverified; no A4/A5 comparative result exists.
- The A3 static oracle passed, but its live/durability grader and saved-file reopening have **not** been verified. A runtime-only pass must not be reported as persistence.
- The Windows test baseline used an isolated, lock-exact environment because the checkout's existing `.venv` lacks pytest. The fixture fixes in the draft PR do not change product code or the dependency lock.
- Remote services, auth/network exposure, implementation, migration, release and merge remain separate decision gates. See the canonical agenda for scope and safety constraints.

## Working convention

Use one draft PR per reviewable slice. Put its feature description, bounded plan, evidence and blockers in the PR body; update this file only when its status or next gate changes. Historical test counts and resolved findings belong in their owning documents, not here. Do not infer completion from an unchecked task or an agent's self-report.
