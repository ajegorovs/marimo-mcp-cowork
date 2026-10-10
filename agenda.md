# Current agenda

> **Status:** evidence wave in progress. This is the dashboard-facing current-work pointer, not a history log. Update it when a gate changes; keep measured detail in the linked agenda and PRs.

| Workstream | Status | Next gate | Detail |
| --- | --- | --- | --- |
| Native-first marimo comparison | A1 baseline and A2 source audit complete; A3 static fixture built. No interface decision. | Verify the pinned 0.25.1 CLI in an isolated environment, review the external oracle and persistence route, then run the equal-condition A4 protocol. | [Convergence agenda](docs/agenda-native-marimo-pair-convergence.md) |
| Windows baseline harness | Draft PR: test-only portability repair; non-live and live tests verified in bounded groups on Windows. | Review/merge the PR; repeat the gate after any change. | PR linked here when created |

## Open blockers and boundaries

- The native 0.25.1 CLI has been source-audited, **not installed or exercised**. No A4/A5 comparative result exists.
- The A3 static oracle passed, but its live/durability grader and saved-file reopening have **not** been verified. A runtime-only pass must not be reported as persistence.
- The Windows test baseline used an isolated, lock-exact environment because the checkout's existing `.venv` lacks pytest. The fixture fixes in the draft PR do not change product code or the dependency lock.
- Remote services, auth/network exposure, implementation, migration, release and merge remain separate decision gates. See the canonical agenda for scope and safety constraints.

## Working convention

Use one draft PR per reviewable slice. Put its feature description, bounded plan, evidence and blockers in the PR body; update this file only when its status or next gate changes. Historical test counts and resolved findings belong in their owning documents, not here. Do not infer completion from an unchecked task or an agent's self-report.
