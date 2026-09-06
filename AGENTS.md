# AGENTS.md

Guidance for human and AI contributors working in this repository.

## Project purpose

This repo is the **marimo inspection / co-work toolkit**: a standalone,
installable package that inspects and edits **live** marimo notebooks — over the
wire (HTTP) or from inside another notebook. It is consumed by other repos
(e.g. the image-processing notebook repo, signal-processing next) via
`uv add`; it is **not** a copy of any consumer's code.

Two surfaces over the same kernel:

1. **Python client** (`MarimoClient`, `discover_servers`) — drive a live
   marimo session from any notebook/script. Importing the package must **not**
   require `fastmcp` (the server is lazily imported via `__getattr__`).
2. **FastMCP server** (`create_server`, the `marimo-inspect` console script)
   — exposes the same tooling to AI agents over MCP
   (`list_active_notebooks`, `get_cell_map`, `get_cell_data`,
   `get_errors`, `lint_notebook`, `create_cell`/`edit_cell`/`run_cell`/
   `delete_cell`, ...).

## Tooling

- Package/venv manager: **uv**. No `venv`/`pip`/`poetry` workflows.
- Python: 3.12 (pinned via `.python-version`).
- Runtime deps: `fastmcp>=4.0.0`, `httpx2>=2.5.0`,
  `marimo[recommended]>=0.24.0,<0.25`.
- Test extras: `pytest`, `pytest-asyncio`, `inline-snapshot`,
  `dirty-equals`.

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

## Layout

- `src/marimo_inspection/` — `client.py` (HTTP), `discovery.py`
  (registry/network discovery), `server.py` (FastMCP, lazily imported),
  `templates/` (scratchpad code generators), `tools/` (MCP tool handlers,
  change-tracking, mutation with staleness guard), `types.py`.
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
- **Templates are strings**: every `templates/*.py` *generates* scratchpad
  code that runs **inside the live kernel** (`import marimo._code_mode as cm`,
  `async with cm.get_context() as ctx:`). They are bound to the marimo version
  of the *running server*, not the installed one.
- **Version contract**: `tools/lint.py` imports marimo private APIs
  (`marimo._ast.parse`, `marimo._lint.rule_engine`) in-process — pinned to
  marimo **0.24.x** (`>=0.24.0,<0.25`). Do **not** widen the bound without
  running the live suite; see `docs/marimo-version-support.md`.
- **Module hygiene**: pure functions, type hints, docstrings; one concern per
  module; unit-testable. Keep the FastMCP server out of import-time paths where
  it isn't needed.
- **Commit the lockfile** (`uv.lock`). Never commit `.venv/`, caches, or
  secrets.

## Live tests

`-m live` tests boot their own headless marimo server
(`MarimoServerManager` in `tests/marimo_inspect/live/conftest.py`) — no
manual server needed. Port is configurable via `$MARIMO_TEST_PORT`
(default `2718`). They are **excluded by default**: the fast path is
`uv run pytest -m "not live"`. They couple the in-kernel and in-process
version contracts to the same installed marimo, so a version bump must be
validated by running them (see `docs/marimo-version-support.md`).

## Docs map

- `docs/marimo-version-support.md` — the pinned `0.24.x` marimo range and
  the upgrade validation procedure. **Read before touching marimo deps.**
- `docs/notebook-backend-protocol.md` — deferred idea (status + trigger to
  revisit): a `NotebookBackend` seam if the marimo-touching surface ever
  needs isolating. Do not implement pre-emptively.
- `docs/agenda-live-test-redesign.md` — open agenda: how to improve/redesign
  the live-kernel test strategy (fixture model, version matrix, orchestration).
- `docs/agent-onboarding-demo-mcp.md`, `docs/mcp-tools-demo-scenario.md` —
  the MCP demo scenario (uses `notebooks/agent_demo.py` etc. in this repo).
- `docs/refactor-session-creation.md`, `docs/live-test-architecture.md`,
  etc. — design history; read for context, not as current truth.

## Remote / publishing

- Remote: `origin` = `https://github.com/ajegorovs/marimo-mcp-cowork`
  (**private**). Push to `main` after committing.
- Consumers install via:
  `uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork"`.
- Don't leak the private remote URL into public docs.
