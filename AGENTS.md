# AGENTS.md

Guidance for human and AI contributors working in this repository.

## Project purpose

This repo is the **marimo inspection / co-work toolkit**: a standalone,
installable package that inspects and edits **live** marimo notebooks — over
the wire (HTTP) or from inside another notebook. It is consumed by other repos
(e.g. the image-processing notebook repo, signal-processing next) via
`uv add`; it is **not** a copy of any consumer's code.

Two surfaces over the same kernel:

1. **Python client** (`MarimoClient`, `discover_servers`) — drive a live
   marimo session from any notebook/script. Importing the package must **not**
   require `fastmcp` (the server is lazily imported via `__getattr__`).
2. **FastMCP server** (`create_server`, the `marimo-inspect` console
   script) — exposes the same tooling to AI agents over MCP.

### The MCP tool surface (13 tools)

Reads / state: `list_active_notebooks`, `set_active_session`,
`get_cell_map`, `get_cell_data`, `get_cell_outputs`, `get_variables`,
`get_dependency_graph`, `get_errors`, `lint_notebook`.
Writes: `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.

`list_active_notebooks` auto-binds the first discovered session; every other
tool accepts an optional `session_id` and falls back to the bound session.
`edit_cell` carries a staleness guard that refuses to overwrite a cell the
agent has not freshly read.

## Tooling

- Package/venv manager: **uv**. No `venv`/`pip`/`poetry` workflows.
- Python: 3.12 (pinned via `.python-version`).
- Runtime deps: `fastmcp>=4.0.0`, `httpx2>=2.5.0`,
  `marimo[recommended]>=0.24.0,<0.25`.
- Test extras: `pytest>=9.1.1`, `pytest-asyncio>=1.4.0`,
  `inline-snapshot`, `dirty-equals`.

## Common commands

| Task | Command |
| --- | --- |
| Install / sync | `uv sync` |
| Unit tests (no kernel) | `uv run pytest -m "not live"` |
| Live tests (real kernel) | `uv run pytest -m live` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Check notebooks | `uv run marimo check notebooks` |
| Run the MCP server | `uv run marimo-inspect --transport http` (or `stdio`) |

Verified on this tree (2026-09-06): `-m "not live"` → **175 passed**, `-m
live` → **19 passed** (~3s). Counts drift as tests are added; treat the split,
not the exact numbers, as the contract.

## Layout

- `src/marimo_inspection/` — `client.py` (HTTP), `discovery.py`
  (registry/network discovery), `server.py` (FastMCP, lazily imported),
  `templates/` (scratchpad code generators), `tools/` (MCP tool handlers,
  change-tracking, mutation with staleness guard, in-process lint in
  `tools/lint.py`, session binding in `tools/session.py`), `types.py`.
- `tests/marimo_inspect/` — unit tests (no kernel needed).
- `tests/marimo_inspect/live/` — integration tests that boot a real headless
  marimo server (see "Live tests" below).
- `notebooks/` — marimo fixtures used by live tests and the MCP demo.
- `docs/` — design/rationale docs (see "Docs map" below).

## Conventions

- **Public API**: `__init__.py` exports `MarimoClient`, `discover_servers`,
  `SessionInfo`, `DiscoveredServer`, `ExecuteResult`; `create_server` is
  exposed via a lazy `__getattr__` so the client path pays no `fastmcp`
  import cost. Keep it that way.
- **Templates are strings**: `templates/*.py` *generates* scratchpad code
  that runs **inside the live kernel** (`import marimo._code_mode as cm`,
  `async with cm.get_context() as ctx:`). They are bound to the marimo
  version of the *running server*, not the installed one.
  - **Exception — lint does not use a template.** `lint_notebook` runs
    in-process via `tools/lint.py` → `_lint_source()`, which imports
    `marimo._ast.parse` and `marimo._lint.rule_engine` directly (the
    kernel scratchpad exposes no notebook IR, so the old scratchpad approach
    could not work). `templates/lint.py` is **dead code**, retained only for
    the template-shape unit tests (`test_templates.py`); do not add new
    logic there.
- **Version contract**: the in-process lint imports marimo private APIs
  (`marimo._ast.parse`, `marimo._lint.rule_engine`) — pinned to marimo
  **0.24.x** (`>=0.24.0,<0.25`). Do **not** widen the bound without running
  the live suite; see `docs/marimo-version-support.md`.
- **Module hygiene**: pure functions, type hints, docstrings; one concern per
  module; unit-testable. Keep the FastMCP server out of import-time paths where
  it isn't needed.
- **Commit the lockfile** (`uv.lock`). Never commit `.venv/`, caches, or
  secrets.

## Live tests

`-m live` tests boot their own headless marimo server
(`MarimoServerManager` in `tests/marimo_inspect/live/conftest.py`) — no
manual server needed. The server is started on a free port (or
`$MARIMO_TEST_PORT`), and its single session is created via the `/sse`
handshake (see `docs/live-tests.md`). They are **excluded by default**: the
fast path is `uv run pytest -m "not live"`. They couple the in-kernel and
in-process version contracts to the same installed marimo, so a version bump
must be validated by running them (see `docs/marimo-version-support.md`).

**Known coverage gap — instantiation is token-gated.** The `/sse`-created
session is never *instantiated*: notebook cells do not run, because
`/api/kernel/instantiate` requires a skew-protection token that isn't exposed
under `--no-token`. This splits the live suite into two tiers:

- **Fully exercised** (read cell *source/structure*, independent of execution):
  `cell_map`, `cell_data`.
- **Structure/consistency only** (read *executed* state, which is empty for a
  fresh non-instantiated session): `dependency` (graph), `errors` (error
  records), `cell_outputs` (outputs), `variables` (kernel globals — sees
  the scratchpad's own imports but **not** notebook-defined names, which only
  exist after cells run).

So a future marimo bump is made visible by the behavioral assertions for
`cell_map`/`cell_data`, but **not yet** for the execution-state templates.
Getting instantiation working (isolate the skew token) is the main remaining
bite of live-test coverage.

## Docs map

**Operational — read before acting:**

- `docs/marimo-version-support.md` — the pinned `0.24.x` range and the
  upgrade validation procedure. **Read before touching marimo deps.**
- `docs/live-tests.md` — canonical "how we run live kernel tests" doc
  (commands, boot mechanics, verified current status). **Read before running
  the live suite.**

**Design / decision records (current):**

- `docs/notebook-backend-protocol.md` — deferred idea (status + trigger to
  revisit): a `NotebookBackend` seam. Do not implement pre-emptively.
- `docs/live-test-redesign-plan.md` — the executed redesign plan (verified
  marimo internals, session-creation handshake, DoD). Read for mechanics.
- `docs/agenda-live-test-redesign.md` — resolved agenda (rationale + design
  space).

**Demo runbooks:**

- `docs/agent-onboarding-demo-mcp.md` — the canonical demo runbook (uses
  `notebooks/agent_demo.py` etc. in this repo).
- `docs/mcp-tools-demo-scenario.md` — older read-tools-only demo scenario.
- `docs/mcp-tools-stateful-demo-linux-findings.md` — findings from a live
  stateful-demo run on Linux.

**Stale history (superseded; marked STALE in-file):**

- `docs/live-test-architecture.md`, `docs/live-test-redesign.md`,
  `docs/kernel-launch-progress.md`, `docs/integration-test-plan.md` — pre-
  redesign design history; read for context, not as current truth.

**One-off reports / analyses (historical — do not treat as current truth):**

- `docs/fastmcp-v4-scout-report.md` — initial architecture scout.
- `docs/marimo-inspection-tools-comparison.md` — source-inspection
  comparison vs marimo-pair.
- `docs/marimo-inspect-progress-report.md` — 2026-08-25 snapshot
  (pre-dates the write tools and the 13-tool surface; numbers are stale).
- `docs/mcp-tools-test-findings.md` — 2025-01 bug findings (shows old buggy
  code; historical).
- `docs/mcp-upgrade-roadmap.md` — upgrade roadmap (partially executed).

**Broken:**

- `docs/refactor-session-creation.md` — corrupted in migration (a single
  line of literal `\n` escapes). Historical; safe to delete or rewrite.

## Remote / publishing

- Remote: `origin` = `https://github.com/ajegorovs/marimo-mcp-cowork`
  (**private**). Push to `main` after committing.
- Consumers install via:
  `uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork"`.
- Don't leak the private remote URL into public docs.
