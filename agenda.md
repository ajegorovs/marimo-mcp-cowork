# Current agenda

> **Status:** evidence wave in progress. This is the dashboard-facing current-work pointer, not a history log. Update it when a gate changes; keep measured detail in the linked agenda and PRs.

| Workstream | Status | Next gate | Detail |
| --- | --- | --- | --- |
| Native-first marimo comparison | [Draft PR #4](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4), stacked on #3: both arms pass bounded metric/save/reopen and first missing-target/duplicate-refusal gates. No full A/B verdict. | Complete A4: independent-actor native stale edit, wrong-session binding, error channels, reconnect and bounded slow execution; then assess Task 3. | [Convergence agenda](docs/agenda-native-marimo-pair-convergence.md) · [benchmark runbook](docs/native-pair-benchmark-runbook.md) · [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/4) |
| Windows baseline harness | [Draft PR #3](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3): test-only portability repair; non-live and live tests verified in bounded groups on Windows. | Review/merge the PR; repeat the gate after any change. | [PR plan and blockers](https://github.com/ajegorovs/marimo-mcp-cowork/pull/3) |

## Open blockers and boundaries

- Both arms passed local metric/reactivity/save/fresh-server and missing-target/duplicate-refusal gates on disposable copies. Native independent-actor stale-edit safety, wrong-session/error/reconnect/slow execution and A5 remain unverified; no interface decision follows.
- The Windows test baseline used an isolated, lock-exact environment because the checkout's existing `.venv` lacks pytest. The fixture fixes in the draft PR do not change product code or the dependency lock.
- Remote services, auth/network exposure, implementation, migration, release and merge remain separate decision gates. See the canonical agenda for scope and safety constraints.

## Working convention

Use one draft PR per reviewable slice. Put its feature description, bounded plan, evidence and blockers in the PR body; update this file only when its status or next gate changes. Historical test counts and resolved findings belong in their owning documents, not here. Do not infer completion from an unchecked task or an agent's self-report.
