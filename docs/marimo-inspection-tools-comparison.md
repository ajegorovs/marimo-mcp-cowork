# Marimo Inspection Tools vs marimo-pair CM Scripts

## Overview

This document compares two approaches for interacting with a live marimo
notebook: the **marimo-pair CM scripts** (our current tooling) and
**marimo's built-in AI tools** (exposed via the editor's harness and the
`--mcp` server endpoint).

The comparison is based on **source code inspection** of marimo's internal
implementation, not just the published documentation.

## Sources

- **Marimo internal source** — installed at
  `.venv/lib/python3.12/site-packages/marimo/`
  - `marimo/_ai/_tools/tools_registry.py` — tool list
  - `marimo/_ai/_tools/base.py` — `ToolBase`, `ToolContext`
  - `marimo/_ai/_tools/tools/notebooks.py` — `GetActiveNotebooks`
  - `marimo/_ai/_tools/tools/cells.py` — `GetLightweightCellMap`,
    `GetCellRuntimeData`, `GetCellOutputs`
  - `marimo/_ai/_tools/tools/dependency_graph.py` — `GetCellDependencyGraph`
  - `marimo/_ai/_tools/tools/tables_and_variables.py` — `GetTablesAndVariables`
  - `marimo/_ai/_tools/tools/errors.py` — `GetNotebookErrors`
  - `marimo/_ai/_tools/tools/lint.py` — `LintNotebook`
  - `marimo/_ai/_tools/tools/datasource.py` — `GetDatabaseTables`
  - `marimo/_ai/_tools/tools/rules.py` — `GetMarimoRules`
  - `marimo/_mcp/server/main.py` — MCP server setup (uses `ToolContext`,
    `SUPPORTED_BACKEND_AND_MCP_TOOLS`, `edit` scope middleware)
  - `marimo/_mcp/code_server/main.py` — Code server MCP tool
  - `marimo/_mcp/server/_prompts/` — MCP prompts
  - `marimo/_cli/commands/edit.py` — `marimo edit --mcp` flag
- **Marimo-pair scripts** — `.agents/skills/marimo-pair/scripts/`
  - `discover-servers.sh` — server discovery
  - `execute-code.sh` — scratchpad code execution
- **Marimo-pair skill** — `.agents/skills/marimo-pair/`
  - `reference/execution-context.md`
  - `reference/finding-marimo.md`
  - Skill instructions (see `skill` tool)
- **Published marimo docs** — https://docs.marimo.io
  - `guides/editor_features/tools/` — AI tools reference
  - `guides/editor_features/mcp/` — MCP server setup
  - `guides/configuration/llm_providers/` — provider config
  - `guides/editor_features/agents/` — ACP agents panel
  - `guides/editor_features/ai_completion/` — editor's AI assistant

## How the Info Was Found

1. **Searched marimo source** — globbed `marimo/_mcp/` and
   `marimo/_ai/_tools/` directories to find tool definitions.
2. **Read each tool implementation** — `tools_registry.py` lists all
   `SUPPORTED_BACKEND_AND_MCP_TOOLS`; each tool class has a `handle()`
   method that reads marimo's internal state (`session.session_view`,
   `session.session_view.variable_values`, `session.app_file_manager.app`,
   etc.).
3. **Read our scripts** — `execute-code.sh` and `discover-servers.sh`
   directly hit marimo's HTTP API (`/api/sessions`, `/api/kernel/execute`).
4. **Cross-referenced** — matched each built-in tool to the equivalent
   script behavior and to any gaps.

## 1:1 Comparison

### Discovery / Connection

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Server discovery** | `discover-servers.sh` scans `~/.marimo/servers/*.json`, validates liveness via PID + HTTP health check, handles WSL/Windows cross-boundary URL resolution | `GetActiveNotebooks` queries `session_manager.sessions` directly |
| **URL resolution** | Auto-resolves correct URL across WSL ↔ Windows host; handles loopback vs gateway; reports stale/duplicate servers | Returns session ID + file path; user already needs the server URL |
| **Verdict** | **Scripts win** — they give you the connection point immediately and handle platform quirks | Tools require you to already have a session ID |

### Cell Content & Structure

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Cell overview** | None built-in; must query via scratchpad code or parse `.py` file | `GetLightweightCellMap` — lists all cells with preview (N lines), type, line count, runtime state, has_output/has_console_output/has_errors flags |
| **Full cell content** | `cm.get_context()` can read `ctx.cells[cell_id].code` | `GetCellRuntimeData` — full code, errors, metadata, defined variables |
| **Verdict** | **Marimo tools win** — structured, one-shot queries; no custom introspection needed |

### Code Execution

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Endpoint** | POST `/api/kernel/execute` with SSE stream | Same endpoint (code mode uses `execute_code` tool) |
| **Namespace** | Scratchpad (shallow copy of globals; new bindings discarded) | Full kernel access (code mode) |
| **Session targeting** | `--file` (stable across reconnects) or `--session` (ephemeral) | Requires session ID from `get_active_notebooks` |
| **Verdict** | **Tie for execution** — same endpoint; scripts have better session targeting |

### Cell Outputs

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Captured output** | stdout/stderr from SSE events; visual output (HTML/charts) not captured | `GetCellOutputs` — visual output with mimetype + console streams |
| **Verdict** | **Marimo tools win** — capture the full visual output (Altair charts, tables, etc.) |

### Dependency Graph

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Graph traversal** | `cm.graph.descendants/ancestors` via scratchpad (less structured) | `GetCellDependencyGraph` — full graph traversal with depth limiting, variable ownership, cycle detection, multiply-defined variable detection |
| **Verdict** | **Marimo tools win significantly** — built-in graph; we'd need to replicate this logic |

### Variables & Data

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Variable inspection** | Scratchpad code execution reads globals (e.g., `print(type(df))`) | `GetTablesAndVariables` — tables with columns/rows/types/PKs/indexes + variables with value and datatype |
| **SQL database introspection** | None | `GetDatabaseTables` — schema info from data connectors with fuzzy matching |
| **Verdict** | **Marimo tools win** — structured data + table metadata |

### Errors & Linting

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Runtime errors** | `execute-code.sh` captures stderr from SSE | `GetNotebookErrors` — all errors organized by cell in one call |
| **Static linting** | None | `LintNotebook` — static analysis pass (breaking/runtime/formatting) without execution |
| **Verdict** | **Marimo tools win** — one-shot error aggregation + dedicated linting |

### Editing / Mutation

| Aspect | marimo-pair CM scripts | Marimo built-in tools |
|--------|-----------------------|----------------------|
| **Persistent mutations** | `cm.get_context()` — create/edit/delete cells, run cells, set_ui_value, add/remove packages | `edit_notebook`, `run_stale_cells` — chat panel only (NOT available via MCP) |
| **Access model** | Any scratchpad caller with `cm` import | Restricted to chat panel Agent mode |
| **Verdict** | **Our scripts win** — `cm` API is accessible from scratchpad; marimo's editing tools are gated behind the chat panel |

## Summary Matrix

| Capability | Our Scripts | Marimo Tools | Winner |
|------------|:-----------:|:------------:|--------|
| Server discovery | ✅ Auto-resolve URL | ⚠️ Returns session ID only | Scripts |
| Cell overview (lightweight map) | ❌ | ✅ | Marimo |
| Full cell content | ✅ (cm) | ✅ (tool) | Tie |
| Dependency graph | ❌ | ✅ Full traversal | Marimo |
| Variable inspection | ✅ (scratchpad) | ✅ (structured) | Marimo |
| Table metadata | ❌ | ✅ (columns, PKs, indexes) | Marimo |
| SQL database introspection | ❌ | ✅ | Marimo |
| Code execution | ✅ Scratchpad | ✅ Kernel access | Scripts (targeting) |
| Visual cell outputs | ❌ stdout/stderr only | ✅ Full visual | Marimo |
| Error aggregation | ❌ Per-call | ✅ All cells | Marimo |
| Static linting | ❌ | ✅ | Marimo |
| Persistent mutations | ✅ cm.get_context() | ⚠️ Chat panel only | Scripts |
| MCP exposure | ❌ (custom) | ✅ HTTP endpoint | Marimo |

## Conclusions

### Where we're stronger
- **Discovery and connection** — `discover-servers.sh` auto-resolves the
  correct URL across WSL/Windows boundaries. `--file` targeting is stable
  across browser reconnects.
- **Persistent mutations** — `cm.get_context()` works from scratchpad;
  marimo's editing tools (`edit_notebook`, `run_stale_cells`) are locked
  behind the chat panel's Agent mode.
- **Agent accessibility** — Our scripts are designed for external agents;
  marimo's editing tools are not.

### Where marimo's tools are stronger
- **All read-only inspection** — `get_lightweight_cell_map`,
  `get_cell_dependency_graph`, `get_tables_and_variables`, `get_cell_outputs`,
  `get_notebook_errors`, `lint_notebook` — these are structured,
  one-shot queries that save custom introspection code.
- **Visual output capture** — Full HTML/chart/table output with mimetype.
- **Static linting** — `LintNotebook` is a dedicated pass without execution.
- **SQL introspection** — `GetDatabaseTables` with fuzzy matching.

### Workflow comparison

| Scenario | Our scripts | Marimo tools |
|----------|------------|--------------|
| "List cells in notebook" | Parse .py file or custom code | One tool call |
| "Show all errors" | Run scratchpad per cell | One tool call |
| "Show variable types" | Write introspection code | One tool call |
| "Run this code" | `execute-code.sh` ✅ | `execute_code` (code mode only) |
| "Edit a cell" | `cm.get_context()` ✅ | Chat panel Agent only |
| "Find server URL" | `discover-servers.sh` ✅ | Need session ID first |

The **marimo-pair approach covers the critical path** (execution + mutation)
that matters for agent-driven notebook work. Marimo's tools fill gaps in
the **inspection layer** but don't expose editing capabilities externally.

## Recommendation

The two approaches are **complementary, not competitive**:

1. **Keep using `marimo-pair` scripts** for code execution and persistent
   mutations (our proven workflow).
2. **Optionally expose inspection via MCP** — a standalone MCP server could
   wrap the read-only tools (`get_lightweight_cell_map`,
   `get_cell_dependency_graph`, `get_tables_and_variables`, etc.) and
   expose them alongside `execute-code.sh` for agents that prefer MCP over
   direct scripts.

A standalone MCP server would require implementing the same marimo internal
API calls that marimo's own tools use — no new functionality, just a
different transport layer.
