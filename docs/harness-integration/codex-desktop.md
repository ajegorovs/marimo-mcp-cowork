# Codex Desktop — project-local MCP setup

> Status: **partial verification, 2026-09-10.** The current standalone Codex
> CLI completed read-only MCP calls from this project and from a sibling
> project using a global registration. The Codex Desktop-bundled engine also
> completed a read-only MCP call.
> On this Linux Desktop build, project-relative `command` and `cwd` values
> failed at startup; use the local full-path workaround below until a newer
> Desktop build verifies relative paths. A Desktop task still needs to be
> reopened after changing MCP configuration. A fresh Desktop app-server also
> launched the configured stdio server, but its new task did not receive the
> server's tool catalog; see [Finding 4](#findings).

## TL;DR

Install `marimo-inspect` into each notebook project's own environment. The
portable configuration that works with the standalone CLI is:

```toml
[mcp_servers.marimo-inspect]
command = ".venv/bin/marimo-inspect"
args = ["--transport", "stdio"]
cwd = "."
tool_timeout_sec = 60
```

It is intentionally relative: when the CLI opens the project, it runs that
project's `.venv/bin/marimo-inspect`, not a copy from another checkout. On the
tested Linux Codex Desktop build, however, this form failed with `No such file
or directory` during MCP startup. See [Desktop workaround](#desktop-workaround-local-full-path-registration).

## Prerequisites

The project needs an installed package and its virtual environment before
Codex starts the MCP server. For a consumer project, pin a release tag:

```bash
uv add "marimo-inspect @ git+https://github.com/ajegorovs/marimo-mcp-cowork@v0.3.1"
uv sync
```

For this repository itself, `uv sync` creates `.venv/bin/marimo-inspect`.

`marimo-inspect` requires marimo `>=0.24.0,<0.25`; read
[`../marimo-version-support.md`](../marimo-version-support.md) before changing
that project's marimo version.

## Codex Desktop configuration

Codex documents a trusted project's `.codex/config.toml` as part of its shared
MCP configuration model. With a Desktop build that resolves relative paths,
add the TL;DR block to the notebook project, then fully restart Codex Desktop
(or reopen the project in a new session). In the Desktop app, inspect Settings
→ MCP servers and type `/mcp` in the composer to confirm that
`marimo-inspect` is connected.

The settings are deliberately project-local. A sibling project installs its
own dependency and repeats the same relative configuration; it does not
inherit a path to this repository.

### CLI global registration for sibling projects

A global full-path registration is also verified with the standalone Codex CLI
from a sibling project:

```bash
codex mcp add marimo-inspect -- \
  <project-root>/.venv/bin/marimo-inspect --transport stdio
```

This makes one chosen installation available to every project opened by that
CLI. It does not dynamically select the sibling project's own virtual
environment, so use it when a single shared installation is intentional. Keep
the literal path in user-level configuration only; never commit it or replace
the placeholder in this document.

### Desktop workaround: local full-path registration

If the Desktop log reports `No such file or directory` while starting the
server, configure absolute paths **locally**:

```toml
[mcp_servers.marimo-inspect]
command = "<project-root>/.venv/bin/marimo-inspect"
args = ["--transport", "stdio"]
cwd = "<project-root>"
tool_timeout_sec = 60
```

Replace `<project-root>` while editing your own uncommitted local config. Do
not commit those values or put a literal path in shared documentation. The
project-specific entry takes precedence over a global entry of the same name,
so changing only the global configuration will not fix a failing project-local
relative entry. Restart Codex Desktop after making the change.

## Start a notebook session

The MCP server discovers local marimo servers through marimo's user-level
registry, independent of its current working directory. Start the notebook
with `--no-token`:

```bash
uv run marimo edit --no-token notebooks/example.py
```

A marimo server does not have a session until a client opens the notebook.
After the browser opens it, ask Codex to call `list_active_notebooks`. If more
than one notebook is live, select the intended one with `set_active_session`
or explicitly provide its `server_url` and `session_id` to a tool call.

## Verification

From the project root, confirm the installed server can start:

```bash
.venv/bin/marimo-inspect --help
```

Then launch a fresh Codex CLI or Desktop session and inspect the MCP catalog:

```bash
codex mcp list
```

With a live notebook session, `list_active_notebooks` should return it; use
`get_cell_map` as a read-only follow-up check.

## Findings

- The direct venv console script avoids `uv run` during MCP startup, so Codex
  does not need to resolve packages or write to the uv cache while launching
  the stdio child process.
- A single global configuration cannot dynamically resolve its command from
  whichever project happens to be active. A global full-path registration is
  verified to work with standalone Codex CLI from a sibling project, but it
  always uses its chosen installation. The relative project configuration
  above is the portable per-project CLI solution; use the temporary local
  full-path form for the affected Desktop build.
- The MCP server's process directory does not scope notebook discovery. It
  scans marimo's local registry and health-checks discovered servers.
- **Finding 3 — relative paths fail in the tested Desktop build.** On
  2026-09-10, the project was explicitly trusted and both Codex executables
  listed the registration. Standalone CLI `0.153.4` successfully invoked
  `list_active_notebooks` with the relative configuration. The Desktop-bundled
  CLI `0.153.0-alpha.5` reported `MCP startup failed: No such file or
  directory (os error 2)` for that configuration. After changing the local
  project entry to absolute `command` and `cwd` values, that same bundled
  executable completed `list_active_notebooks` successfully. This confirms a
  Desktop path-resolution or process-context issue, not a `marimo-inspect`
  protocol failure. The new-task result is documented in Finding 4.
- **Finding 4 — the Desktop process can start the server without exposing its
  tools to a task.** After a full Desktop restart, the Desktop app-server had
  live `marimo-inspect --transport stdio` child processes using the configured
  project environment. A newly created Desktop task nevertheless reported that
  the MCP tools were unavailable. Thus the remaining issue is not a stale
  configuration, executable path, or failed server startup: it is the Desktop
  task gateway failing to inject the connected server's tools. Treat this
  Desktop build as unsupported for this server until that product issue is
  resolved; the standalone CLI remains verified.

## Official Codex reference

See [Model Context Protocol](https://developers.openai.com/codex/mcp) for
Codex MCP configuration, the `cwd` option for stdio servers, and the Desktop
restart workflow.
