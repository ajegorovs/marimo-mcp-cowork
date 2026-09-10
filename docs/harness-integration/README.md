# Adding marimo-inspect to another repo — bootstrap and integration index

> **Bootstrap status — normal VCS route verified.** Use the pinned `v0.3.0`
> consumer installation below, configure the harness to invoke the consumer
> environment's console script, then read the MCP resources for runtime work.
> Entries labelled ❓ remain deliberate investigation notes, not setup
> instructions.
>
> - ✅ **verified** — checked against the tree or a command on this machine.
> - 📄 **documented** — copied from another doc in this repo; trust that doc.
> - ❓ **unverified** — plausible, stated so it can be checked. Do not wire a
>   consumer project on an ❓ claim.

This is the index for **consumers**: you have a different repo with live marimo
notebooks, and you want either the MCP tool surface in your agent harness or the
Python client in your own code. It does not replace the per-harness notes — it
tells you which one applies and what to expect.

---

## The two axes

Two independent decisions; pick one from each.

| Axis | Options |
|---|---|
| **A. How the code gets into your project** | git (VCS) · local editable · package index |
| **B. How your agent reaches the tools** | harness spawns stdio · shared HTTP/streamable server · (or skip MCP: Python client) |

The common mistake is treating axis B as "the" install step. The Python client
path (B3) needs no MCP server and no `fastmcp` at all.

---

## Axis A — getting the package in

### A1. Pinned VCS release — standard consumer path ✅

```bash
uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v0.3.0"
```

Pin the tag in a committed `pyproject.toml`; this is a normal, non-editable
installation in the consumer project's environment. 📄 `AGENTS.md` §Remote /
publishing; `README.md` §Install and connect an MCP client.

### A2. Local checkout, editable — provider-contributor override only ✅

```bash
uv add --editable /path/to/marimo-inspect
```

Use this only to test unreleased provider changes against a consumer project.
It is not a normal user or consumer-contributor install mode: restore the
pinned VCS dependency after the test and do not commit the local source. Keep
consumer checkouts as siblings, not children, because a nested checkout causes
uv workspace discovery to rewrite the provider's `pyproject.toml`/`uv.lock`.

```text
~/Repos/marimo-inspect        <- this repo
~/Repos/my-notebooks          <- consumer (fine, sibling)
~/Repos/marimo-inspect/.probe <- nested (dirties this repo's lockfile)
```

### A3. From a package index — not supported today ❓

A package-index release is not currently published. Do not use or document
`uv add marimo-inspect` as a consumer route until a release workflow and an
end-to-end install verification exist. Use A1 for normal consumers.

### What you inherit either way

- **Python 3.12+.** ✅
- **marimo `>=0.24.0,<0.25`** — a hard upper bound, because `tools/lint.py`
  imports private APIs (`marimo._ast.parse`, `marimo._lint.rule_engine`).
  📄 `docs/marimo-version-support.md`. **Read that doc before letting your
  project's lockfile drift marimo past 0.24.x** — it is the single biggest
  cross-repo hazard in this integration.
- Runtime deps pulled in whether you want them or not: `fastmcp>=4.0.0`,
  `httpx2>=2.5.0`, `marimo[recommended]`, plus `numpy` and `matplotlib` (declared
  for the demo notebooks, not needed by the client or server core). ✅
- Importing `marimo_inspection` does **not** import `fastmcp` — the server is
  exposed through a lazy `__getattr__`, so pure-client consumers pay nothing. ✅

Verified resolution on this machine: `fastmcp 4.0.3`, `marimo 0.24.0`,
`marimo-inspect 0.3.0`.

---

## Axis B — how your agent reaches the tools

The console script is `marimo_inspection.server:main`:

```text
usage: marimo-inspect [-h] [--transport {http,stdio,sse,streamable-http}]
                      [--host HOST] [--port PORT]
```

Defaults: `--transport http`, `--host 127.0.0.1`, `--port 8090`. ✅
(`--help` output, checked 2026-09-07.)

### B1. stdio — the harness spawns a server per project

Your harness launches the binary as a child process over stdio. Simplest, fully
isolated per project, no port to manage; one server process per harness.

Verified working for **DeepSeek Harness** →
[deepseek-harness-web-profile.md](deepseek-harness-web-profile.md) and
**Hermes Agent** → [hermes-agent.md](hermes-agent.md). No note exists
yet for Claude Desktop/Code, Cursor, Zed, Windsurf — see [Adding a note for
your harness](#adding-a-note-for-your-harness).

Two pieces of advice from the DSH note that **generalize to any harness config**:

- **Point at the venv binary, not `uv run`.** `uv run marimo-inspect …` can hit a
  read-only uv cache (`ROFS … at ~/.cache/uv`) under a constrained FS and may
  attempt a network sync. `<repo>/.venv/bin/marimo-inspect` runs standalone via
  its own shebang and exposes the same 14 tools. ✅ This failure was hit again
  *while writing this draft* — see [Sandbox notes](#sandbox-notes).
- **Give each install a distinct `serverName`/namespace** so two servers never
  alias the same `mcp__<name>__<tool>` prefix. 📄

### B2. HTTP / streamable-http — one shared server, many clients

```bash
marimo-inspect --transport http --host 127.0.0.1 --port 8090
```

Recommended in `docs/fastmcp-v4-scout-report.md` (a historical scout doc) as the
primary mode: one persistent service, clients connect by URL. Use this when
several repos/harnesses should share one server, or when your harness only speaks
`streamable-http`.

Endpoint paths come from the FastMCP defaults (`fastmcp.settings`) ✅:

| `--transport` | Path |
|---|---|
| `http` / `streamable-http` | `/mcp` (`streamable_http_path`) |
| `sse` | `/sse` (`sse_path`) |

❓ **Unverified:** whether the notebook sessions a shared server exposes are scoped
by *discovery* (registry + `GET /api/sessions`) rather than by its cwd — which is
what would let one HTTP server serve notebooks in several repos. Check before
relying on it.

### B3. No MCP — the Python client (the "plug into any repo" path)

If the consumer is your *code* rather than an *agent*, skip the server entirely:

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

📄 `README.md` §1; public API in `src/marimo_inspection/__init__.py`
(`MarimoClient`, `discover_servers`, `SessionInfo`, `DiscoveredServer`,
`ExecuteResult`). This is the only path that works without `fastmcp`.

---

## Runtime prerequisites (every option above)

The MCP server is a *remote control*; it does nothing without a live marimo
session to point at. Two things bite:

1. **Start marimo with `--no-token`** for registry-based discovery — the flag is
   present on every `marimo` subcommand (`--token/--no-token`). ✅
   `discovery.py` reads the registry and health-checks `GET /api/sessions`.
2. **A server alone is not a session.** A `--headless` launch never creates a
   session, so `list_active_notebooks` returns `total_notebooks: 0` until a client
   connects. Either launch without `--headless` (the browser performs the
   handshake) or do the `/sse` handshake yourself. 📄
   `docs/agent-onboarding-demo-mcp.md` §Prerequisites and `docs/live-tests.md`.

Registry location (from `discovery.py::_get_registry_dir`): ✅

- POSIX: `$XDG_STATE_HOME/marimo/servers`, defaulting to
  `~/.local/state/marimo/servers`
- Windows: `%USERPROFILE%\.marimo\servers`

> `discovery.py`'s module docstring still says `~/.marimo/servers/*.json`
> unqualified — that is the Windows path only. Stale comment, not a bug.

## What the agent sees

14 tools, namespaced `mcp__<serverName>__<tool>` in most harnesses:

`list_active_notebooks` · `set_active_session` · `get_cell_map` ·
`get_cell_data` · `get_cell_outputs` · `get_variables` ·
`get_dependency_graph` · `get_errors` · `lint_notebook` ·
`create_cell` · `edit_cell` · `run_cell` · `delete_cell` ·
`set_ui_value`

`list_active_notebooks` auto-binds the first discovered session's `session_id`
and `server_url` together; every other tool takes an optional `session_id` and
`server_url` and falls back to the bound values. The binding lives in the
server process/connection — a harness that respawns the server per call loses
it. `edit_cell` refuses to overwrite a cell the agent has not freshly read
(staleness guard: `needs_read` → re-read → retry). `set_ui_value` sets a live
widget's value by kernel-global name and accepts no source code. Three
read-only MCP resources (`workflow://marimo-inspect/co-work-loop`,
`workflow://marimo-inspect/live-safety`,
`reference://marimo-inspect/fallbacks-and-limits`) ship with the server.
📄 `AGENTS.md`.

---

## Adding a note for your harness

Create `docs/harness-integration/<harness-slug>.md` mirroring the DSH doc's shape
— it has earned its structure:

1. **TL;DR** — the minimal working config block, copy-pasteable.
2. **Harness background** — profile/config file location, reload semantics,
   whether MCP is on by default.
3. **Minimal working configuration** — the actual applied config, not a sketch.
4. **Fields table** — this harness's config keys and defaults.
5. **Findings** — numbered, each a *real* problem hit, with the failure mode and
   why it failed that way. This section is the whole value of the doc.
6. **Verification** — the exact command that proves the server started.
7. **Hardening** — DONE/PENDING list.

Then add a row to the table below.

### Index of per-harness notes

| Harness / client | Transport | Note | Status |
|---|---|---|---|
| DeepSeek Harness (web profile) | stdio | [deepseek-harness-web-profile.md](deepseek-harness-web-profile.md) | ✅ verified 2026-09-06 |
| Hermes Agent (TUI gateway) | stdio | [hermes-agent.md](hermes-agent.md) | ✅ verified 2026-09-09 |
| Codex Desktop | stdio | [codex-desktop.md](codex-desktop.md) | ❌ Desktop task tools missing; CLI works with both project-local and sibling global setup (2026-09-10) |
| Claude Desktop / Claude Code | — | — | ❓ no note |
| Cursor | — | — | ❓ no note |
| Zed / Windsurf / other | — | — | ❓ no note |
| Shared HTTP gateway (any `streamable-http` client) | http | — | ❓ no note |

## Sandbox notes

Two environment-specific facts that shape consumer config on constrained
filesystems:

- `uv add` / `uv run` need a **writable uv cache**. Under a filesystem sandbox
  with `~/.cache/uv` read-only they fail outright: `Read-only file system (os
  error 30) at path "~/.cache/uv/..."`. ✅ Workarounds: set
  `UV_CACHE_DIR` to a writable path, or — for the server process itself — skip
  `uv run` and exec the venv binary.
- A consumer project **nested inside** this repo makes uv treat it as a workspace
  member and rewrite this repo's `pyproject.toml`/`uv.lock`. ✅ See A2.

## Known gaps / unverified claims

The graduation checklist for this draft, ordered by how likely a consumer is to
hit each.

1. ❓ **No publish route.** Should `uv add marimo-inspect` (A3) work? If yes, needs
   a release workflow; if no, the README line should go.
2. ❓ **Only one harness is actually verified** (B1). Everything said about
   Claude/Cursor/etc. is inference from generic MCP config keys.
3. ❓ **Shared HTTP server across repos** (B2) — untested whether sessions from
   multiple repos are visible, or whether cwd/registry scoping limits it.
4. ❓ **Exact HTTP URL shape** for `--transport http` (`http://127.0.0.1:8090/mcp`
   vs another base path) — paths confirmed in FastMCP settings, the composed URL
   not exercised end-to-end.
5. ❓ **Version-skew policy for consumers.** What happens when the *notebook* repo
   runs marimo 0.25 while marimo-inspect is pinned `<0.25`?
   `docs/marimo-version-support.md` notes the two contracts (in-process installed
   marimo vs in-kernel server marimo) can drift, but nothing tells a consumer
   which side wins or what the error looks like. Partially probed (2026-09-07):
   a marimo 0.24.0 kernel under **Python 3.14**, driven by the 3.12 package, ran
   `cell_map`/`errors` + in-process lint clean — evidence in
   [agenda-remote-marimo-mcp.md](../agenda-remote-marimo-mcp.md) §Evidence.
   marimo **version** drift (0.24 vs 0.25) is still untested.
6. ❓ **`numpy`/`matplotlib` for pure-client consumers** — declared
   unconditionally for the demo. Should they move to an extra
   (`marimo-inspect[demo]`) so client-only repos don't inherit them? Design
   question, not a doc fix.
7. ❓ **Remote or containerized marimo servers** — discovery is local-registry plus
   `--no-token`, so those are out of scope. Worth stating as a non-goal rather
   than leaving implicit.

## Related docs

- `AGENTS.md` §Remote/publishing — the canonical consumer install command.
- `docs/marimo-version-support.md` — **read before touching marimo deps.**
- `docs/agent-onboarding-demo-mcp.md` — the demo runbook; its Prerequisites
  section is the best description of the session-materialization gotcha.
- `docs/live-tests.md` — `/sse` handshake mechanics.
- [deepseek-harness-web-profile.md](deepseek-harness-web-profile.md) — the worked
  stdio example, and the source of the two general config lessons.
