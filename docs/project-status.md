# Project status and work index

This is the mutable index for project progress. Update this file—not
`AGENTS.md`—when an agenda opens or closes, priorities change, a release moves,
or a current-work pointer changes.

`AGENTS.md` is stable contributor guidance. It should contain only durable
architecture, conventions, safety rules, and a static link to this index.
Exact test counts remain in [`live-tests.md`](live-tests.md), their canonical
ledger.

## Current work

| Area | Current state | Canonical detail |
| --- | --- | --- |
| MCP upgrade roadmap | `restart_kernel` is implemented; notebook-server start/stop/switch remain operator-owned | [`mcp-upgrade-roadmap.md`](mcp-upgrade-roadmap.md) |
| Consumer findings | T19/T22 resolved — browser-first session recipe (marimo 0.24 edit mode without `--session-ttl`) and truthful `provenance`/`owner: "unknown"` with scoped counts; Round 2's only open item is T18 (session discovery) | [`agenda-udv-consumer-findings.md`](agenda-udv-consumer-findings.md) |
| Example notebooks | Contract and verification wiring remain open | [`agenda-example-notebooks.md`](agenda-example-notebooks.md) |
| Remote topology | Design investigation remains open | [`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md) |
| Live tests | Current commands, mechanics, and exact counts | [`live-tests.md`](live-tests.md) |

## Closed work and decision trails

- [`agenda-bug-hunt-1.md`](agenda-bug-hunt-1.md) — first MCP-surface bug hunt.
- [`agenda-verification-round.md`](agenda-verification-round.md) — post-hunt
  zero-context verification.
- [`agenda-live-test-redesign.md`](agenda-live-test-redesign.md) — live-suite
  redesign decisions.

## Update rules

- Keep one current row per active workstream; link to the detailed agenda or
  roadmap rather than copying its full item list.
- Put implementation evidence and decisions in the owning agenda/roadmap.
- Put exact test counts only in `live-tests.md`.
- Move completed work to the closed section; do not copy its chronology into
  `AGENTS.md`.
- Keep identifiers and links stable so old reports remain traceable.
