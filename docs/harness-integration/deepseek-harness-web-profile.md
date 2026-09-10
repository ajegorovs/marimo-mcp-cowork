# DeepSeek Harness (web profile) — MCP setup findings

> Date: 2026-09-06
> Machine: Arch Linux, repo `~/Repos/marimo-inspect`.
>
> This is a per-harness integration note; sibling docs under
> `docs/harness-integration/` cover other harnesses/IDEs. It records how to
> register the `marimo-inspect` FastMCP server with the DeepSeek Harness web UI,
> two real problems hit during setup, and hardening for production.

## TL;DR

Add one entry to the harness profile's **patch layer**
(`~/.dsh/profiles/web/cordis.patch.yml`) — **not** the composed `cordis.yml`
(that file is generated). The entry must use the `- insert:` block form; a bare
`- id:` entry parses fine but is **silently composed away** to an empty list, so
nothing loads. A working start shows the FastMCP logo; the 14 marimo tools then
surface as `mcp__marimo__<tool>`.

## Harness background

- Install (npx): `@deepseek-ai/dsh`; launch `npx @deepseek-ai/dsh web --no-open`
  -> serves `http://127.0.0.1:3080`.
- Version: findings verified against `@deepseek-ai/dsh@0.1.2-rc.1`
  (`dsh-mcp-client`, `dsh-base`, `dsh-web-app` all `0.1.2-rc.1`).
- Profile root: `~/.dsh/profiles/<name>/`; the web profile is
  `~/.dsh/profiles/web/`.
- The profile's `cordis.yml` is **generated** ("the tree is composed as patches").
  Layers apply in order: each bundle's `cordis.patch.yml`, then the user's
  `cordis.patch.yml`, then any `--patch` overlays.
- MCP is a plugin (`@deepseek-ai/dsh-mcp-client`), installed but **not enabled by
  default** — you opt in with a patch entry.
- Reload: the web profile sets `patchReload: "live"`; a new plugin entry is
  most reliably picked up by a full harness restart.

## Minimal working configuration

`~/.dsh/profiles/web/cordis.patch.yml`:

```yaml
- insert:
    - id: mcp-marimo
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: marimo
        transport: stdio
        command: ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
        args: ['--transport', 'stdio']
        cwd: '~/Repos/marimo-inspect'
        failOnStartupError: true
```

This is the **hardened, applied** form (venv binary, not `uv run`; see
Finding 2). The pre-hardening form used `command: uv` +
`args: ['run', 'marimo-inspect', '--transport', 'stdio']` and no
`failOnStartupError`.

After reload the server's tools appear as `mcp__marimo__<name>`, e.g.
`mcp__marimo__list_active_notebooks`, `mcp__marimo__edit_cell`.

### Fields

| Field | Default | Meaning |
|---|---|---|
| `transport` | required | `stdio` or `streamable-http` |
| `serverName` | required | local namespace; `[A-Za-z0-9_-]{1,32}`, unique |
| `command`/`args`/`env`/`cwd` | — | stdio spawn |
| `url`/`headers` | — | streamable-http |
| `toolCallTimeoutMs` | `60000` | per-call timeout |
| `failOnStartupError` | `false` | abort harness if connect/sync fails |
| `reconnect.enabled` | `true` | auto-reconnect + resync |

## Findings

**Finding 1 — bare patch form compiles to `[]` (fails without throwing).**

The MCP-client README shows a minimal stdio entry as a bare `- id … / name … /
config …` list. Wrapping that in a profile patch does **not** load the plugin:

- `loadOptionalPatches` / `loadOverlayPatches` parse it fine (no YAML error)
  — both funnel through `parsePatchList`.
- A bare entry has no `insert`, so `applyEntryPatches` treats it as an
  *override* of an existing entry id. With an empty base tree there is nothing
  to override, so `composeEntries()` **drops the row** and returns `[]`.
- This is non-loud: `composeEntries()`'s default warn sink is literally
  silent, and at boot the loader emits only a single easy-to-miss
  `patch: entry "<id>" not found` warning — the plugin never loads, and
  nothing throws.

The correct shape is the `- insert:` block, exactly like `dsh-base`'s own
`cordis.patch.yml`. Validate against the installed loader:

```js
const boot = await import('.../@deepseek-ai/dsh-app-boot/lib/index.js');
const patches = boot.loadOptionalPatches('dsh', '<profile>/cordis.patch.yml');
const composed = boot.composeEntries(patches); // [] for the bare form
```

**Lesson:** don't copy a minimal README example verbatim into a harness patch
file; verify the entry with the loader's own compile step.

**Finding 2 — prefer a direct venv binary over `uv run` in sandboxed runs.**

`uv run marimo-inspect …` can hit a read-only uv cache under a constrained FS
(`ROFS … at ~/.cache/uv`) and may attempt a network sync. The repo's own
`.venv/bin/marimo-inspect` runs standalone (shebang `.venv/bin/python3`) and
exposes the same 14 tools. Use:

```yaml
command: ~/Repos/marimo-inspect/.venv/bin/marimo-inspect
args: ['--transport', 'stdio']
cwd: '~/Repos/marimo-inspect'
```

## Verification

```bash
cd ~/Repos/marimo-inspect
.venv/bin/marimo-inspect --transport stdio   # or: uv run marimo-inspect --transport stdio
```

A working server prints the FastMCP logo, then `Starting MCP server
'marimo-inspection' with transport 'stdio'`.

## Hardening

- **DONE — venv binary over `uv run`.** Applied: `command` now points at
  `.venv/bin/marimo-inspect`. Dodges the uv-cache ROFS race / network sync.
  Same 14-tool surface.
- **DONE — `failOnStartupError: true`.** Applied while validating; a bad
  spawn now aborts the harness loudly instead of registering zero tools.
  Tradeoff to revisit after validation: with this on, a *transient* startup
  failure kills the whole web harness rather than degrading to "up, but MCP
  tools missing + reconnect later." Flip back to `false` (or omit) once the
  tools are confirmed surfacing, unless the harness is dedicated to
  marimo-inspection.
- **Give each install a distinct `serverName`** so two harnesses never alias
  the same `mcp__<name>` namespace (duplicate `serverName` in one scope
  fails load).
