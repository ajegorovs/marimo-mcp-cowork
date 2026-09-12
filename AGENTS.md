# AGENTS.md

Guidance for human and AI contributors working in this repository.

## Scope of this file

`AGENTS.md` is stable contributor guidance, not a work log or scratchpad. Do
not add progress notes, dates, test counts, finding states, release state, or
resolved-item chronology here. Record mutable project state in
[`docs/project-status.md`](docs/project-status.md), with implementation detail
in the agenda or roadmap it links. Keep references from this file static: say
what a document is and when to read it, not whether its current items are open
or closed.

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

### The MCP tool surface

`src/marimo_inspection/server.py` is the registration authority; `README.md`
keeps the consumer-facing inventory. The surface groups notebook/session reads,
cell mutation and execution, widget interaction, linting, and lifecycle tools.

`list_active_notebooks` auto-binds the first discovered session (`session_id`
and `server_url`); every other tool accepts an optional `session_id` and falls
back to the bound session. The binding lives in the MCP session's server-side
state **and** in a **process-global fallback** consulted only when that state
has nothing bound — which is what carries it for a client that starts a fresh
MCP session per request (fastmcp's own `Client`, pinned fastmcp 4.0.3, stdio and
HTTP alike). Over stdio one server process serves exactly one client, so the
fallback is connection-global and omitting the arguments always works; over
`--transport http` it is served only while the process has seen a single client
session, and a second client session makes argument-less calls refuse with
`reason: binding_ambiguous` (fail-closed — never guessed, so one client can
never inherit another's notebook). `create_cell` defaults to `hide_code=False`.
`edit_cell` carries a staleness guard (`check_fresh=True` by default):
never-read → `needs_read`, changed-since-read → `conflict`; recover by
re-reading the cell's full source (`get_cell_data` — a `get_cell_map` preview
does **not** record the read baseline) and retrying (`check_fresh=False` is a
force escape hatch, not the recovery path).

Three static, read-only MCP resources accompany the tools
(`workflow://marimo-inspect/co-work-loop`,
`workflow://marimo-inspect/live-safety`,
`reference://marimo-inspect/fallbacks-and-limits`) — packaged Markdown under
`src/marimo_inspection/resources/`, loaded via `importlib.resources`.

## Consumer installation contract

The MCP resources are the authority for **runtime co-work**, but they are only
available after a client installs and configures the server. The bootstrap
contract therefore lives in `README.md` and the per-harness docs:

- Normal consumers install a **pinned, non-editable release** into their own
  project environment and configure their harness to run that environment's
  `.venv/bin/marimo-inspect --transport stdio`.
- Do not prescribe `uv run` as a long-lived MCP command: it can resync or fail
  on a read-only uv cache.
- An editable sibling source is only a temporary provider-contributor override
  for testing unreleased changes. It is not part of normal consumer onboarding
  and must never be committed into a consumer project's dependency source.
- Once connected, clients list/read the packaged MCP resources for the live
  co-work workflow and safety boundaries.

## Tooling

- Package/venv manager: **uv**. No `venv`/`pip`/`poetry` workflows.
- Python: 3.12 (pinned via `.python-version`).
- Runtime deps: `fastmcp>=4.0.0`, `httpx2>=2.5.0`,
  `marimo[recommended]>=0.24.0,<0.25`, plus `numpy` and `matplotlib`
  (needed by the demo notebooks/runbook; `marimo[recommended]` does not pull
  them in).
- Test extras: `pytest>=9.1.1`, `pytest-asyncio>=1.4.0`,
  `inline-snapshot`, `dirty-equals`.

## Common commands

| Task | Command |
| --- | --- |
| Install / sync | `uv sync --all-extras` (test deps are an optional extra — a bare `uv sync` prunes pytest) |
| Unit tests (no kernel) | `uv run pytest -m "not live"` |
| Live tests (real kernel) | `uv run pytest -m live` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Check notebooks | `uv run marimo check notebooks` |
| Run the MCP server | `uv run marimo-inspect --transport http` (or `stdio`) |

The split is the contract: the default `-m "not live"` tier must stay
kernel-free and fast, and `-m live` boots real kernels. Verified counts and
current status live in `docs/live-tests.md` — do not restate numbers here, they
drift as tests are added.

## Layout

- `src/marimo_inspection/` — `client.py` (HTTP), `discovery.py`
  (registry/network discovery), `server.py` (FastMCP, lazily imported),
  `templates/` (scratchpad code generators), `resources/` (packaged Markdown
  served as three read-only native MCP resources), `tools/` (MCP tool
  handlers, change-tracking, mutation with staleness guard, widget
  interaction in `tools/ui.py`, in-process lint in `tools/lint.py`, session
  binding in `tools/session.py`, list-argument normalization in
  `tools/args.py`), `widgets/` (packaged anywidget components — see
  `docs/custom-widgets.md`), `types.py`.
- `tests/marimo_inspect/` — unit tests (no kernel needed).
- `tests/marimo_inspect/live/` — integration tests that boot a real headless
  marimo server (see "Live tests" below).
- `notebooks/` — marimo fixtures used by live tests and the MCP demo.
- `examples/` — consumer-facing example notebooks (see
  `docs/agenda-example-notebooks.md`).
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
  - A template may enter the code-mode context **twice**: queued writes flush
    on the *first* exit, so `templates/ui.py` re-enters to read back the
    element's value (marimo swallows a rejected widget update, leaving only a
    stderr traceback). Never report success from the flush alone — see
    `tools/ui.py`'s stderr rejection scan.
- **Version contract**: the in-process lint imports marimo private APIs
  (`marimo._ast.parse`, `marimo._lint.rule_engine`), and `templates/ui.py`
  reads the widget declaration off `marimo._plugins.ui._core.ui_element.UIElement`
  (`__orig_bases__`) — pinned to marimo **0.24.x** (`>=0.24.0,<0.25`). Do
  **not** widen the bound without running the live suite; see
  `docs/marimo-version-support.md`.
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

Commands, boot mechanics, verified counts and current status live in
`docs/live-tests.md` — read it before changing the suite, and keep those
numbers there rather than here. Two standing facts about the suite's shape:

- **Instantiation is token-gated.** The `/sse`-created shared session is never
  *instantiated* (its notebook cells do not run), because
  `/api/kernel/instantiate` requires a skew-protection token that isn't exposed
  under `--no-token`. The shared fixture can therefore only ever show
  fresh/empty executed state for outputs, errors and variables. The write-tool
  tests below get real execution by creating and running their own cells.
  Executing the fixture's original cells as a frontend would remains the main
  open bite of live coverage.
- **Write-tool tests are hermetic.** `tests/marimo_inspect/live/test_mutation.py`
  drives the **real MCP handler functions** against a real 0.24 kernel, and
  `test_ui.py` does the same for the widget tool; each boots one isolated
  server per test on a `tmp_path` copy of the fixture and asserts the repo
  fixture stays byte-identical — keep that gate when adding cases. Cells they
  create through the write tools *do* execute, so the widget tool is exercised
  in-kernel with no browser: shape refusal, verified apply (with a reactive
  dependent re-run), verified no-op, kernel rejection as an error, the
  `on_change_failed` callback-failure path (distinguished from a rejected
  conversion by the failure *site*), the cell-private (leading-underscore) name
  rule, and the error-channel split — structured channel silent
  (`has_errors: false`) while the traceback shows in the run payload and the
  cell's `console_stderr`; see `co-work-loop.md` §6.

Frontend *rendering* is **not** CI-covered: the live suite boots kernels, not
frontends.

## Docs map

Mutable workstream state is indexed in [`docs/project-status.md`](docs/project-status.md).
The entries below are stable descriptions and read-before-acting pointers.

**Operational:**

- `docs/marimo-version-support.md` — supported marimo range and dependency
  upgrade validation. Read before changing marimo dependencies.
- `docs/live-tests.md` — live-kernel test mechanics, commands, and canonical
  current counts. Read before changing or running the live suite.
- `docs/bug-hunt-protocol.md` — evidence and closure protocol for MCP surface
  bug hunts. Read before hunting or triaging a hunt report.

**Design and decisions:**

- `docs/notebook-backend-protocol.md` — criteria for revisiting a backend seam.
- `docs/live-test-redesign-plan.md` and `docs/agenda-live-test-redesign.md` —
  live-suite mechanics and their design trail.
- `docs/agenda-remote-marimo-mcp.md` — remote kernel/server topology questions.
- `docs/agenda-bug-hunt-1.md` — source-verified mutation, binding, and payload
  decisions from the first bug hunt.
- `docs/agenda-verification-round.md` — zero-context co-work verification method
  and evidence expectations.
- `docs/agenda-udv-consumer-findings.md` — consumer-pass findings and ownership.
- `docs/agenda-example-notebooks.md` — contract and verification policy for
  consumer-facing examples.
- `docs/custom-widgets.md` — packaged anywidget behavior and usage.
- `docs/mcp-upgrade-roadmap.md` — MCP capability roadmap.

**Bootstrap and demos:**

- `README.md` and `docs/harness-integration/README.md` — canonical installation
  path and harness index.
- `docs/harness-integration/deepseek-harness-web-profile.md` and
  `docs/harness-integration/hermes-agent.md` — harness-specific configuration.
- `docs/agent-onboarding-demo-mcp.md` — canonical interactive demo runbook.

Historical reports under `docs/session-report-*.md` and the named progress,
comparison, and scout reports are evidence snapshots, not current guidance.

## Privacy — do not overexpose

Everything committed here must be publishable: treat the whole tree as public,
whether or not the remote currently is.

**Allowed:** the author name/contact email (`pyproject.toml`, `LICENSE`) and
the GitHub username in the remote/install URL. That is the only personal data.

**Never commit:**
- Absolute local paths that name the user or machine layout — write
  `~/Repos/marimo-inspect`, never `/home/<user>/Repos/marimo-inspect`.
- Sibling/consumer repo names or checkout paths that are not meant to be
  public — describe them generically ("a consumer repo").
- Credentials (tokens, api keys, passwords, skew/session tokens). Docs may
  *discuss* marimo auth, but never paste a real value.
- Tailnet / RFC1918 IPs, hostnames, or concrete vLLM/tailscale endpoint URLs
  — write "tailnet-only URL", not the address.

Test fixtures use `/home/user/…` placeholders; keep them that way.

Before a public flip, or when adding docs/config, scan:
`git grep -nE '/home/[a-z]+|api[_-]?key|password|secret|BEGIN .*PRIVATE|tailscale|vllm' -- . ':!uv.lock'`
(loopback `127.0.0.1` in tests is fine; flag any other IP.)

## Remote / publishing

- Remote: `origin` = `https://github.com/ajegorovs/marimo-mcp-cowork`
  (**public**). Push to `main` after committing.
- Consumers install via:
  `uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork"`.

### Git tags — pin releases, don't float HEAD

Consumers should reference a **tag**, not a moving branch, so a future bump
can't silently change what they resolve.

- Cut a release tag at the current package version before asking any consumer
  to depend on this repo: `git tag v<version> && git push origin --tags`.
- Bump `version` in `pyproject.toml` **and** `__version__` in
  `src/marimo_inspection/__init__.py` together (they are duplicated on
  purpose); cut the matching tag in the same change.
- Consumer form:
  `uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v<version>"`
  (or a `[tool.uv.sources]` entry with the matching `tag`).
- Never move a tag that a consumer already pinned — cut a new one instead.
