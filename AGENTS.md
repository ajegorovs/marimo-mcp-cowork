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

### The MCP tool surface (14 tools)

Reads / state: `list_active_notebooks`, `set_active_session`,
`get_cell_map`, `get_cell_data`, `get_cell_outputs`, `get_variables`,
`get_dependency_graph`, `get_errors`, `lint_notebook`.
Writes: `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
Widget interaction: `set_ui_value` (sets a live UI element's value by
kernel-global name; accepts no source code). Value shapes are per widget and
never coerced — the element's own `UIElement[...]` declaration drives a
pre-flight shape guard (scalar to a `dropdown`/`multiselect`/`range_slider` is
refused with `reason: value_shape_mismatch` + `did_you_mean`), and the
element's value is read back afterwards so `status: ok` + `verified: true` means
the read-back succeeded — `applied: true` if the value moved, `no_change: true`
if it already held it. A value marimo swallowed (unknown dropdown key: traceback
on stderr only) returns `status: error`, `reason: value_not_applied`.

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

Verified on this tree (2026-09-11): `-m "not live"` → **323 passed**, `-m
live` → **37 passed** (incl. the 4 mutation regressions — 2 hermetic flow cases
plus the 2 hunt-H7 guard cases, the H9 preview/read-baseline pair, the 2 H10
`code_hash` invariants, 7 widget regressions and 2 console-channel
regressions). The not-live tier also carries the session-binding subprocess
cases that spawn the console script and pin the binding model (H1/H11 — stdio,
and the HTTP single-client scope). Counts drift as tests are added; treat the
split, not the exact numbers, as the contract.

## Layout

- `src/marimo_inspection/` — `client.py` (HTTP), `discovery.py`
  (registry/network discovery), `server.py` (FastMCP, lazily imported),
  `templates/` (scratchpad code generators), `resources/` (packaged Markdown
  served as three read-only native MCP resources), `tools/` (MCP tool
  handlers, change-tracking, mutation with staleness guard, widget
  interaction in `tools/ui.py`, in-process lint in `tools/lint.py`, session
  binding in `tools/session.py`, list-argument normalization in
  `tools/args.py`), `types.py`.
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

Separately, `tests/marimo_inspect/live/test_mutation.py` (2 tests) exercises the
**real MCP handler functions** against a real 0.24 kernel on a `tmp_path` copy
of the fixture: create → read → guarded edit → run → verify → delete, and
external-conflict → re-read → recover. It boots one isolated server per test
and asserts the repo fixture stays byte-identical (the hermeticity gate).
`tests/marimo_inspect/live/test_ui.py` (7 tests) does the same for the widget
tool — cells *created through the write tools* do execute, so a widget can be
materialized in-kernel without a browser: shape refusal, verified apply with a
reactive dependent re-run, verified no-op on a repeat, kernel rejection
surfacing as an error, an `on_change` handler that raises reported as
`on_change_failed` with `applied: true` (the value moved; only the callback
failed), and the cell-private (leading-underscore) name rule — that case also
pins the error-channel split (the structured channel stays silent,
`has_errors: false`, while the traceback is visible in the run payload and in
the cell's `console_stderr`; see `co-work-loop.md` §6). Frontend *rendering* was
verified once by hand against a real browser (agenda T9-b) and is still not
CI-covered: the live suite boots kernels, not frontends.

## Docs map

**Operational — read before acting:**

- `docs/marimo-version-support.md` — the pinned `0.24.x` range and the
  upgrade validation procedure. **Read before touching marimo deps.**
- `docs/live-tests.md` — canonical "how we run live kernel tests" doc
  (commands, boot mechanics, verified current status). **Read before running
  the live suite.**
- `docs/bug-hunt-protocol.md` — how we find the silent-payload bugs the suite
  cannot see (lab recipe, subagent charter, evidence schema, verification and
  closure rules, adversarial checklist). **Read before hunting bugs, or before
  triaging a hunt report.**

**Design / decision records (current):**

- `docs/notebook-backend-protocol.md` — deferred idea (status + trigger to
  revisit): a `NotebookBackend` seam. Do not implement pre-emptively.
- `docs/live-test-redesign-plan.md` — the executed redesign plan (verified
  marimo internals, session-creation handshake, DoD). Read for mechanics.
- `docs/agenda-live-test-redesign.md` — resolved agenda (rationale + design
  space).
- `docs/agenda-remote-marimo-mcp.md` — **OPEN** agenda: what we'd gain (and
  break) by hosting the marimo kernel (R1) and/or the MCP server (R2) on a remote
  machine. Read before choosing a consumer's topology or touching
  `discovery.py`/`--no-token` assumptions.
- `docs/agenda-bug-hunt-1.md` — **CLOSED** (all 11 hunt #1 findings resolved,
  2026-09-11): each carried a source-verified mechanism, a proposed priority and
  the "repro must fail pre-fix" closure rule, and every one now has a test that
  proves it. Read its §Resolved log for the decision trail (H1's binding claim
  and the H11 fallback that replaced the capability gap, H2–H6's payload
  truthfulness, H7's guard scope, H9's explicit-only read baseline, H10's pinned
  hash invariant). Its §Checkpoint still carries the one open cross-agenda item —
  the `T13` widget residual. Read its §Resolved log before touching
  `tools/mutation.py` payload/guard code, `tools/session.py` binding claims, or
  the `templates/*` payload shapes it names.
- `docs/agenda-verification-round.md` — **OPEN** agenda: the post-hunt check round
  (`T-V1`) that re-verifies the eleven closed findings through the tool surface
  and the packaged resources from a fresh zero-context agent, plus the carried
  `T13` widget residual. Read it before re-running tests "to check" — that round
  exists because a green suite cannot see whether the docs still teach the old
  behaviour.
- `docs/agenda-udv-consumer-findings.md` — **CLOSED** (fully resolved) agenda
  for the first-consumer integration, T-ids kept stable. Every item is resolved
  and has a one-line record + evidence pointer in its §Resolved log — read that
  for the decision trail (T1 session-vs-server, T2 auto-bind, T3 harness list
  args, T4 cross-Python evidence, T9/T12/T13 write surface and error channels,
  T14 list-argument normalization), not to find open work.

**Bootstrap / harness integration:**

- `README.md` §Install and connect an MCP client — the canonical pre-MCP
  bootstrap path: pinned consumer install, console-script invocation,
  session-materialization prerequisite, then MCP resources for runtime work.
- `docs/harness-integration/README.md` — consumer integration index: supported
  package routes, marimo 0.24.x pin, runtime prerequisites, per-harness notes,
  and explicitly labelled known gaps. Treat its supported normal route as
  authoritative; do not turn its `❓` sections into consumer instructions.
- `docs/harness-integration/deepseek-harness-web-profile.md` — registering the
  marimo-inspect FastMCP server with the DeepSeek Harness web profile
  (`~/.dsh/profiles/web/cordis.patch.yml`); the two setup gotchas (bare vs
  `insert:` patch form, `uv run` vs venv binary) and hardening.
- `docs/harness-integration/hermes-agent.md` — registering the same server
  with the Hermes Agent MCP gateway (`hermes config set
  mcp_servers.marimo-inspect.*`); the stale pre-extraction consumer-path
  finding, why the venv binary beats `uv run … fastmcp run`, and why
  `--reload` is off.

**Demo runbook (the only one):**

- `docs/agent-onboarding-demo-mcp.md` — the canonical demo runbook (uses
  `notebooks/function_plotting_demo.py` in this repo). Its "Prerequisites" section
  documents the two things an interactive run needs: `uv sync` for the demo's
  `numpy`/`matplotlib` deps, and a real session (browser launch or the `/sse`
  handshake — a bare `--headless` launch discovers nothing).

**One-off reports / analyses (historical — do not treat as current truth):**

- `docs/fastmcp-v4-scout-report.md` — initial architecture scout.
- `docs/marimo-inspection-tools-comparison.md` — source-inspection
  comparison vs marimo-pair.
- `docs/marimo-inspect-progress-report.md` — 2026-08-25 snapshot
  (pre-dates the write tools and the 14-tool surface; numbers are stale).
- `docs/mcp-tools-test-findings.md` — 2025-01 bug findings (shows old buggy
  code; historical).
- `docs/mcp-upgrade-roadmap.md` — upgrade roadmap (partially executed).

## Privacy — do not overexpose

Everything committed here must be publishable; treat the whole tree as public
(and remember it flips public once Settings → danger zone is done).

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
  to depend on this repo: `git tag v0.3.2 && git push origin --tags`.
- Bump `version` in `pyproject.toml` **and** `__version__` in
  `src/marimo_inspection/__init__.py` together (they are duplicated on
  purpose); cut the matching tag in the same change.
- Consumer form: `uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v0.3.2"`
  (or a `[tool.uv.sources]` entry with `tag = "v0.3.2"`).
- Never move a tag that a consumer already pinned — cut a new one instead.
