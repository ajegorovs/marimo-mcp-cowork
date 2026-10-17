# marimo-inspect

Structured tooling for **co-working on a live marimo notebook**: read cells,
variables and outputs, edit source, run cells, drive widgets, and verify what
happened — over the wire, or from inside another notebook. Two surfaces over one
implementation: a **Python client** (`MarimoClient`, `discover_servers`) that
drives a live marimo session from any notebook or script, and a **FastMCP
server** (`create_server`, and the `marimo-inspect` console script) that exposes
the same tooling to AI agents over the Model Context Protocol.

## Why not just use upstream `marimo pair`?

marimo now ships live-kernel code mode, a core `marimo pair` CLI and a vendor
Agent Skill. That stack and this package drive the **same** live-kernel /
code-mode machinery: they are not different execution engines, and official code
mode can already perform many of the operations these tools expose. Our own
two-arm comparison has not established an overall winner.

What this package adds is *shape* — a structured, inspectable tool vocabulary:

| Dimension | Official `marimo pair` | marimo-inspect |
| --- | --- | --- |
| Agent interface | CLI + agent skill | structured MCP tools |
| Access model | execute Python in the live kernel | named read, write, widget and verification operations |
| Flexibility | general Python and code-mode operations | narrower, predefined tool vocabulary |
| Python integration | native kernel/code-mode workflow | separately importable `MarimoClient` |
| Error contract | native CLI and kernel feedback | structured tool responses with explicit refusal reasons |
| Concurrency protection | depends on the underlying server behaviour | read-before-edit guard and notebook-path-anchored binding — detection, **not** atomic prevention |
| Version evaluated | marimo 0.25.1 | marimo 0.24.x (pinned) |

Two scope notes: the table compares **interfaces**, not feature counts — official
marimo also ships *built-in AI tools* over its own MCP server endpoint, so
structured MCP tooling is not unique to this project (source-inspected in the
now-historical [`docs/marimo-inspection-tools-comparison.md`](docs/marimo-inspection-tools-comparison.md));
and the "concurrency protection" row is not a safety claim.

**Reach for this package** for an agent framework that already speaks MCP, for
refusals as structured payloads rather than tracebacks, for driving a live session
from Python, or for the read-before-edit guard and notebook-path-anchored binding
on the MCP path. **Reach for upstream** for arbitrary code-mode operations,
in-kernel helpers, or a shell-driven workflow. Neither is a lock: the guards here
are detection on a captured snapshot, and `MarimoClient` used directly carries no
notebook-identity precondition. Evidence:
[`docs/interface-positioning.md`](docs/interface-positioning.md).

## Quick start

### 1. As a Python client

Install it into any project and co-work on a running marimo session:

```python
import asyncio
from marimo_inspection import MarimoClient, discover_servers


async def main():
    servers = await discover_servers()
    async with MarimoClient(servers[0].url) as client:
        sessions = await client.list_sessions()
        result = await client.execute(sessions[0].session_id, "1 + 1")
        print(result.status, result.stdout)


asyncio.run(main())
```

Importing `marimo_inspection` does **not** import `fastmcp` — the server is
reached through a lazy `__getattr__`, and only `create_server()` loads it.
`fastmcp` is still an install-time dependency of the package.

### 2. As an MCP server

```python
from marimo_inspection import create_server

server = create_server()
server.run(transport="stdio")
```

`marimo-inspect --transport stdio` runs that same server directly. Add a pinned,
non-editable release to the notebook project's own environment:

```bash
uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v0.3.3"
uv sync
```

Then configure the harness to execute that environment's console script —
`<project-root>/.venv/bin/marimo-inspect --transport stdio` — not `uv run` and not
a provider checkout (verify it with `.venv/bin/marimo-inspect --help`).
Per-harness configuration blocks:
[`docs/harness-integration/README.md`](docs/harness-integration/README.md); local,
explicit-remote and Tailnet topologies:
[`docs/deployment-topologies.md`](docs/deployment-topologies.md). Start a notebook
with `--no-token`, open it in a browser to materialize a session, call
`list_active_notebooks`, then read the packaged resources for the co-work loop and
safety rules. An **editable sibling checkout** is a provider-contributor override
only, never a committed dependency source.

## Release and development boundary

| | Released `v0.3.3` (newest tag) | `main`, documented here |
| --- | --- | --- |
| Tools | 14 — no `restart_kernel` | 15, including `restart_kernel` |
| Binding | process/connection-scoped | MCP-session state **plus** a process-global fallback, anchored to the bound notebook path (an id-only rotation is repaired; a reused id refused `notebook_mismatch`) |

`main` carries substantial unreleased changes beyond `v0.3.3`, and the package
version in `pyproject.toml` has not yet been advanced — so **there is no release
for the newer surface yet**: a consumer pinning `v0.3.3` gets the older, smaller
interface, which is a normal development gap rather than a defect in the tag.
Read the tag, not the branch, when you need to know what a pin actually does.

## The MCP tool surface

15 tools over the same live kernel:

- **Session** — `list_active_notebooks` (discovers sessions and auto-binds the
  first, setting `session_id` **and** `server_url`), `set_active_session`.
- **Reads** — `get_cell_map`, `get_cell_data`, `get_cell_outputs`,
  `get_variables`, `get_dependency_graph`, `get_errors`, `lint_notebook`.
- **Writes** — `create_cell`, `edit_cell`, `run_cell`, `delete_cell`.
- **Widget interaction** — `set_ui_value`.
- **Lifecycle** — `restart_kernel` (closes the kernel and re-materializes a
  fresh one; the server process survives, and the confirmed session id is
  point-in-time, not durable).

`edit_cell` carries a read-before-edit staleness guard (`check_fresh=True` by
default): a never-read cell answers `needs_read`, a changed-since-read cell
`conflict`, and only `get_cell_data` records the read baseline. Every
cell/session-targeting tool resolves its target through one shared step, so a
target problem is a structured payload (`status: error` with `target_resolved` /
`operation_ran` / `state_changed` all `false`) rather than an exception, and **no
mutation is dispatched on a refusal**. The full contract, written for a reader who
has not connected a client yet: [`docs/mcp-contract.md`](docs/mcp-contract.md).

## MCP resources

Three **static, read-only** documentation resources ship in the wheel
(`text/markdown`, loaded via `importlib.resources`), readable on demand by a
connected client with `list_resources()` / `read_resource(uri)`:

| URI | Content |
| --- | --- |
| `workflow://marimo-inspect/co-work-loop` | the MCP-first co-work loop, step by step |
| `workflow://marimo-inspect/live-safety` | read-before-edit and live-kernel safety rules |
| `reference://marimo-inspect/fallbacks-and-limits` | what MCP does not cover, and its intentional fallbacks |

The same three documents are readable without a connected client: in this
repository at `src/marimo_inspection/resources/`, or in-process with
`Client(transport=create_server())`.

## Requirements

- Python 3.12+
- A running marimo **edit** server (`--no-token` for registry-based discovery; a
  `marimo run` server registers but is not discoverable, because its session
  census requires edit scope).
- marimo **0.24.x**: private APIs are version-bound — see
  [`docs/marimo-version-support.md`](docs/marimo-version-support.md).

## Documentation map

| Read this | For |
| --- | --- |
| [`docs/mcp-contract.md`](docs/mcp-contract.md) | Orientation to the MCP contract, and which document owns which topic |
| [`docs/interface-positioning.md`](docs/interface-positioning.md) | The comparison with upstream `marimo pair`, with its evidence and limits |
| The packaged MCP resources | Authoritative runtime semantics for an agent that is already connected |
| [`docs/harness-integration/README.md`](docs/harness-integration/README.md) | Consumer installation and per-harness configuration |
| [`docs/deployment-topologies.md`](docs/deployment-topologies.md) | Local, remote and Tailnet deployment shapes, URL resolution and the operator boundary |
| [`examples/`](examples/) | Consumer-facing marimo usage patterns to copy, and how they are checked |
| [`docs/live-tests.md`](docs/live-tests.md) | Live-kernel test mechanics and the current counts |
| [`docs/project-status.md`](docs/project-status.md) | Current work, effort issues and durable design trails |

## Development

```bash
uv sync --all-extras           # test deps are an optional extra; a bare sync prunes pytest
uv run ruff check .
uv run pytest -m "not live"   # unit tests (fast, no kernel)
uv run pytest -m live         # live kernel tests (boots its own headless server)
```

The `live` tier deselects by default; read
[`docs/live-tests.md`](docs/live-tests.md) before changing or running it.

## Agent skills

Repo-local agent skills live in `.agents/skills/` — one Markdown `SKILL.md` per
skill. Hermes Agent does not auto-load skills from a cloned repo, so they need an
explicit opt-in, once per repo per machine: `hermes skills trust`, which takes
effect in your next session. `hermes skills list` shows what is loaded.
