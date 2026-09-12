# Hermes Agent (TUI gateway) — MCP setup findings

> Date: 2026-09-09
> Machine: Arch Linux, repo `~/Repos/marimo-inspect`.
>
> This is a per-harness integration note; sibling docs under
> `docs/harness-integration/` cover other harnesses/IDEs. It records how to
> register the `marimo-inspect` FastMCP server with Hermes Agent's own MCP
> gateway, one real problem hit during setup (a stale config that pointed at
> the pre-extraction consumer path), and the verified working state.

## TL;DR

Set the `marimo-inspect` entry under `mcp_servers:` in the active profile's
`config.yaml` (`hermes config path` prints it — here
`~/.hermes/config.yaml`) to run this repo's **venv binary** over stdio:

```yaml
mcp_servers:
  marimo-inspect:
    command: ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
    args: ["--transport", "stdio"]
    timeout: 60
```

Apply with the CLI, not a hand edit: `hermes config set
mcp_servers.marimo-inspect.command <path>` and `hermes config set
mcp_servers.marimo-inspect.args '["--transport", "stdio"]'`. The gateway
hot-reloads on config change (see Verification) — no restart needed.

## Harness background

- Hermes Agent runs an MCP gateway that reads `mcp_servers:` from the active
  profile config (no global+profile merge — one file wins). `hermes config
  path` / `hermes config env-path` print the exact files.
- Stdio servers are spawned as child processes by the gateway and their tools
  become first-class agent tools, prefixed `mcp__<entry-key>__<tool>` in the
  gateway log and tool catalog (e.g. `mcp__marimo_inspect__edit_cell`).
- A config change is picked up **automatically**: the gateway watches for
  `mcp_servers` changes and reloads connections without a gateway restart
  (verified 2026-09-09 — see Verification). Do NOT add `--reload`/`--reload-dir`
  to the server args to chase live edits; see Finding 2.
- Editing the config by hand is discouraged — use `hermes config set` (the
  CLI knows the file layout and validates the write).

## Minimal working configuration

Applied 2026-09-09 and verified live against the Hermes gateway:

```yaml
mcp_servers:
  marimo-inspect:
    command: ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
    args:
      - --transport
      - stdio
    timeout: 60
```

If your gateway spawns commands without shell expansion, substitute the
expanded absolute path for `~` in the real config (this note keeps the
`~/Repos/…` form per repo privacy convention). The venv binary is
self-contained (shebang points at `.venv/bin/python3`), so it does not need
`uv` on PATH at all.

## Fields

| Field | Value here | Meaning |
|---|---|---|
| `command` | `<repo>/.venv/bin/marimo-inspect` | venv console script (see Finding 1) |
| `args` | `["--transport", "stdio"]` | transport for the gateway child process |
| `timeout` | `60` | per-call timeout (seconds) |
| entry key | `marimo-inspect` | namespace; becomes `mcp__marimo_inspect__*` |

## Findings

**Finding 1 — point at the venv binary, not `uv run … fastmcp run`.**

The pre-extraction Hermes entry used
`uv run --directory <consumer-repo> fastmcp run
src/marimo_inspection/server.py:create_server` with a `--reload-dir` on that
consumer checkout. Two things broke it after the package was extracted into
this standalone repo:

- The consumer repo that used to host the source no longer carries
  `src/marimo_inspection/` — it installs `marimo-inspect` from git. The old
  `--directory`/`server.py` path therefore **no longer exists**, and any
  gateway instance that started before the config fix (or any process still
  holding the old command in memory) logs `MCP server 'marimo-inspect' failed
  initial connection after 3 attempts, parking … MCPError: Connection closed`
  on every reconnect attempt — even after the on-disk config is correct.
- `uv run` also reintroduces the cache/network failure mode documented in the
  DSH note (Finding 2 there): a read-only `~/.cache/uv` or an attempted
  network sync can kill the spawn under constrained filesystems.

The hardened form — exec this repo's `.venv/bin/marimo-inspect --transport
stdio` directly — dodges both. ✅ Verified: 13 marimo tools registered at the
date of the fix; the surface is 15 today — see Finding 3.

**Finding 2 — no `--reload`/`--reload-dir` on a long-lived gateway.**

FastMCP's reload supervisor respawns the server child on the same stdio fd;
a persistent gateway connection does not self-heal after a save — the client
gets stuck on `MCPError: Invalid request parameters` and needs a full
`/reload-mcp` or Hermes restart to recover. Keep the args minimal
(`--transport stdio`) and restart the server process yourself when developing
server code.

**Finding 3 — the gateway registers 4 resource/prompt wrappers on top of the marimo surface.**

On 2026-09-09 the marimo surface was 13 tools, and the gateway adds 4
resource/prompt tools of its own on top: `list_resources`, `read_resource`,
`list_prompts`, `get_prompt` (this server's own catalog contains none of them —
probed 2026-09-10). The marimo surface is now 15 tools, so a re-registered
gateway is expected to list 19 names (15 marimo + 4 gateway wrappers). Gateway
log line (verified 2026-09-09, when the surface was 13):

```
MCP server 'marimo-inspect' (stdio): registered 17 tool(s):
mcp__marimo_inspect__list_active_notebooks, mcp__marimo_inspect__get_cell_map,
… mcp__marimo_inspect__delete_cell, mcp__marimo_inspect__list_resources, …
```

If you count 19 in a catalog dump, that is expected — the marimo surface is the
15 documented tools and the other 4 are the gateway's own wrappers.

**Finding 4 — Hermes tool naming is `mcp__<entry-key>__<tool>`.**

Unlike the DSH web harness's `mcp__marimo__*` (serverName-derived), Hermes
derives the prefix from the config entry key, so this entry surfaces as
`mcp__marimo_inspect__*`. If you rename the key, every tool reference
changes with it.

## Verification

```bash
# 1. Point at the repo's venv binary and confirm it starts standalone
#    (needs `uv sync` once in the repo to produce .venv — see the
#    harness-integration README, Axis A, for install routes):
cd ~/Repos/marimo-inspect
timeout 6 .venv/bin/marimo-inspect --transport stdio < /dev/null   # prints FastMCP logo + "Starting MCP server 'marimo-inspection' with transport 'stdio'"; timeout ends the blocking stdio wait

# 2. Set the gateway config via the CLI (never hand-edit):
hermes config set mcp_servers.marimo-inspect.command ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
hermes config set mcp_servers.marimo-inspect.args '["--transport", "stdio"]'
hermes config set mcp_servers.marimo-inspect.timeout 60

# 3. Confirm registration (hot-reload is automatic; watch the log):
grep "MCP server 'marimo-inspect'" ~/.hermes/logs/agent.log | grep "registered" | tail -1
# expect: registered 19 tool(s): mcp__marimo_inspect__list_active_notebooks, …
#         (15 marimo tools + the gateway's 4 resource/prompt wrappers)
# NOTE: grep for "registered" specifically — a *second* long-lived gateway
# process (e.g. a `gateway run` daemon started before the config fix) may
# still hold the old command in memory and keep logging
# "failed initial connection ... parking" against the dead path. Filtering
# for "registered" skips that noise. Restarting that stale daemon clears it
# (see Hardening).
```

Then exercise it against a real session: launch a marimo server
(`marimo edit notebooks/<nb>.py --no-token`, or the `/sse` handshake — a bare
`--headless` launch discovers nothing), call `list_active_notebooks` with an
explicit `server_url`, then `get_cell_map`.

## Hardening

- **DONE — venv binary over `uv run`.** Applied 2026-09-09; the stale
  consumer-path entry is gone and the 15 marimo tools register cleanly (the
  gateway's own 4 resource/prompt wrappers sit on top — see Finding 3).
- **DONE — minimal args, no `--reload`.** Applied; keep it that way
  (Finding 2).
- **DONE — CLI-applied config.** Use `hermes config set`, not a text edit.
- **PENDING — restart stale long-lived gateway daemons after the fix.**
  A `gateway run` daemon started *before* the config fix (here pid 1024,
  running since the previous day for cron) keeps the old in-memory command
  and re-spawns the dead path every reconnect cycle, logging repeated
  "parking … MCPError: Connection closed" lines. It does not affect the
  interactive TUI gateway (which hot-reloaded and registered correctly),
  but its log noise is confusing during verification. Restart it when
  convenient to clear the stale state.
- **Live-session caveat:** the auto-bind session (server_url/session_id) is
  in-memory per gateway server process; a gateway restart or server respawn
  wipes it — re-run `list_active_notebooks(server_url=…)` to rebind.
