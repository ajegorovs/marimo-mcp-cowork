# MCP Tool Upgrade Roadmap

> Generated from analysis session. Evaluates current interaction surface and proposes upgrades.

## Current State

### What Works

- **Read/state MCP tools** (9 tools): `list_active_notebooks`,
  `set_active_session`, `get_cell_map`, `get_cell_data`, `get_cell_outputs`,
  `get_variables`, `get_dependency_graph`, `get_errors`, `lint_notebook`
- **Write MCP tools** (4): `create_cell`, `edit_cell`, `run_cell`, `delete_cell`
  — with the `edit_cell` staleness guard (Phase 3 ✓; freshness repair 2026-09)
- **Widget interaction** (1): `set_ui_value` — set a live UI element's value by
  kernel-global name; accepts no source code, JSON value shape preserved
- **Native MCP resources** (3): `workflow://marimo-inspect/co-work-loop`,
  `workflow://marimo-inspect/live-safety`,
  `reference://marimo-inspect/fallbacks-and-limits`
- **Stateful session binding** — `list_active_notebooks` auto-binds the first
  discovered session; all tools accept optional `session_id` falling back to
  bound state; `set_active_session` for explicit switching. (Phase 1 ✓)
- **Change detection** — `get_cell_map` reports `changes_since_last`. (Phase 2 ✓)
- **Terminal scripts are fallback only**: `execute-code.sh` for arbitrary probes
  and complex multi-op `cm` blocks; `discover-servers.sh` is human/debug only
- **266 unit tests** pass on this tree (measured 2026-09-10;
  `uv run pytest -m "not live" -q`)

### Where Friction Lives

**Phase 1 (session binding) is resolved** — `session_id` is now optional
after `list_active_notebooks`. Remaining friction:

**Typical agent loop (6 calls, 0 repeat `session_id`):**

```
1. list_active_notebooks()  →  discovers + auto-binds session
2. get_cell_map()           →  finds cell of interest (no session_id needed)
3. get_cell_data(cell_ids=...)  →  reads cell content
4. [write via create_cell/edit_cell/run_cell/delete_cell]
5. get_cell_data(cell_ids=...)  →  verify
6. get_errors()             →  check for problems
```

## Proposed Upgrades

### Phase 1: Default Session Binding (Complete ✓)

**Problem (resolved):** `session_id` was ceremonial — repeated on every call after discovery
but conveying no new information.

**Solution (implemented):** `list_active_notebooks()` auto-binds the first discovered session.
Subsequent tools accept `session_id` as optional, falling back to the stored active session.

**Implementation:** FastMCP v4 `Context.set_state()/get_state()` stores the active
session_id server-side.

```python
# Current behavior:
list_active_notebooks()  →  discovers + auto-binds session
get_cell_map()           →  uses bound session_id (no argument needed)
get_cell_data(cell_ids=["c_0"])  →  uses bound session_id
get_cell_map("def456")  →  explicit override still works
```

**`set_active_session` tool** for explicit switching when needed.

**Impact:** Zero repeated `session_id` parameters after the initial discovery call.

### Phase 2: Composite Read Tools

**Problem:** `get_cell_data()` returns cell code; `get_errors()` returns errors separately.
Agents often need both to decide next action.

**Solution:** Add `include_errors` flag to `get_cell_data()`, or create `read_cell_with_status()`
that returns code + error state + staleness in one call.

**Impact:** Reduces round trips for the read-verify loop. One call instead of two.

### Phase 3: Write Tools (Done — direct tools + staleness guard)

**Problem:** Mutation requires terminal scripts (`execute-code.sh`) with inline Python
using `cm.get_context()`. Agents must write async context managers in prompts.

**Implemented (2026-08; freshness repair 2026-09):** Four first-class MCP write
tools — `create_cell`, `edit_cell`, `run_cell`, `delete_cell`. Each wraps
`cm.get_context()` server-side and returns structured JSON. These are MCP-first
for the normal loop; `execute-code.sh` remains the intentional fallback for
arbitrary probes and complex multi-op blocks. The staleness guard's
read-baseline bug (a re-read that returned source without recording the
baseline) was fixed in 2026-09, so the documented `conflict` → re-read → retry
recovery actually clears.

**Design decision — direct calls (not `begin/queue/commit`):** The original plan
proposed a transactional `begin/queue/commit` worker to preserve cm's
context-exit atomicity and avoid silent stale writes. That was superseded by a
simpler approach:

- **Per-call atomicity** is good enough — a single mutation opens one context,
  applies, and exits, so each call is internally atomic.
- **The stale-write risk is handled by a staleness guard, not a transaction.**
  `edit_cell` compares the cell's live source hash against the change-tracker
  snapshot from the agent's last read; if the cell changed since, it refuses
  (`status: "conflict"`/`"needs_read"`) until the agent re-reads. This directly
  answers the "silent inconsistency between read and write" concern the roadmap
  raised, without worker lifecycle or transaction handles.
- `begin/queue/commit` remains worthwhile only if multi-cell atomic batches are
  ever required (currently a single mutation per tool call is the norm).

**Trade-offs vs. terminal scripts:**

| Aspect | Terminal Script | MCP direct write tools |
|--------|----------------|------------------------|
| Transactionality | Native (one cm block) | Per-call atomic; guard prevents stale writes |
| Error feedback | Immediate (exception) | Structured (status field) |
| Mental model | "Execute Python block" | Tool per operation |
| Discoverability | Bash scripts (not MCP) | Proper MCP tools with schemas |
| Complexity | Simple (one call) | Simple (no worker lifecycle) |
| Stale-write safety | None | Staleness guard (edit_cell) |

### Phase 4: Session Management Tools

**Status: NOT landed.** No lifecycle tools exist; launching, restarting, or
stopping the notebook server is still a local/operator action (there is no
kernel-restart tool either).

**Problem:** Agents discover servers via terminal scripts (`discover-servers.sh`
— now a human/debug fallback).

**Solution:** Add MCP tools for server lifecycle:

```python
@mcp.tool
async def start_notebook(file_path: str, host: str = "127.0.0.1") -> dict:
    """Start marimo server for a notebook. Returns session_id."""


@mcp.tool
async def stop_notebook(session_id: str = None) -> dict:
    """Stop the default or specified notebook server."""


@mcp.tool
async def switch_notebook(file_path: str) -> dict:
    """Switch default notebook binding. Returns new session_id."""
```

## What NOT to Change

### Scratchpad Transport

The scratchpad (`POST /api/kernel/execute`) is the **transport**, not the storage.
It's how code reaches the marimo kernel via HTTP. There's no alternative path.
Any MCP tool that executes code will use scratchpad internally.

**Decision:** Keep scratchpad as the transport. Abstract it behind MCP tools, don't
eliminate it.

### Direct `edit_cell()` Without Transaction

A naive `edit_cell(cell_id, new_code)` MCP tool would call `cm.get_context()` per-call,
applying mutations immediately. This was originally flagged as breaking cm's
transactional semantics (no rollback, no batching, silent read-write inconsistency).

**Resolution (implemented 2026-08):** We shipped direct `edit_cell()` anyway, but
closed the "silent inconsistency" risk with a **staleness guard** instead of a
transaction: the tool compares the cell's live source hash against the
change-tracker snapshot from the agent's last read and refuses the write if the
cell changed since. Multi-cell atomic batches would need `begin/queue/commit`;
single-cell edits are covered by per-call atomicity + the guard.
`execute-code.sh` remains as a fallback for complex multi-op `cm` blocks.

## FastMCP v4 Statefulness

FastMCP v4 advertises "stateful applications on sessionless protocol." The three
state mechanisms available:

| Mechanism | Use Case | Persistence |
|-----------|----------|-------------|
| `UserSession` | Session binding, transaction handles | Across requests, replicas |
| `RequestStateSecurity` | Multi-round elicitation | Single conversation, signed |
| `task=True` | Long-running background work | Async completion |

**Relevance:** `UserSession` (via `Context.set_state()/get_state()`) enabled Phase 1
(default session binding) without architectural changes — now implemented. It stores small serializable state (session_id, transaction
handle) — not live objects.

**What it doesn't solve:** Cannot store `cm.get_context()` context manager instances.
The worker architecture would still be needed only for multi-cell **atomic
batches** (not landed); single-cell edits are covered by per-call atomicity plus
the staleness guard.

## Recommendation

1. **Phase 1 done ✓** — Default session binding implemented. Parameter noise eliminated.
2. **Phase 2 done ✓** — Change detection in `get_cell_map` (`changes_since_last`);
   composite `include_errors` read remains a small optional win.
3. **Phase 3 done ✓** — Unified write surface (`create_cell`/`edit_cell`/
   `run_cell`/`delete_cell`) with an `edit_cell` staleness guard, plus
   `set_ui_value` for live widget interaction and three read-only MCP
   resources. The guard's read-baseline bug was repaired in 2026-09.
4. **Phase 4 (session lifecycle) NOT landed** — no `start_notebook`/
   `stop_notebook`/`switch_notebook` tools; server launch/restart/stop stays
   local/operator.
5. **Keep terminal scripts as a bounded fallback** — `execute-code.sh` remains
   the deliberate escape hatch for arbitrary kernel probes, complex multi-op
   `cm` blocks, and lifecycle; `discover-servers.sh` is human/debug only. The
   MCP surface is intentionally narrow (no arbitrary-code tool).
6. **Next: validate with a fresh agent** — the docs (this roadmap + the demo
   runbook) should let an agent with no prior context run the full demo
   end-to-end using only MCP tools.

## Open Questions

- Does the agent actually prefer direct write tools over `begin/queue/commit`
  semantics? (Initial signal: yes, direct + guard is simpler — but multi-cell
  atomic edits would reopen the question.)
- Should `UserSession` state require authentication (current FastMCP requirement),
  or can it work in a local-agent deployment without auth?
- How does editing a cell interact with marimo's own cell dependency tracking?
  (e.g., editing cell A triggers re-execution of cell B — should the MCP tool
  report this?)
- Does the kernel-vs-file race (browser edits vs. MCP writes both serializing to
  disk) need marimo-side handling, or is documenting "don't edit server code
  mid-demo" enough?
