# Deployment topologies and operating modes

> **Supported scope:** local development and Tailnet-only co-work between
> machines we control. The recommended shared topology is a remote
> Streamable-HTTP MCP server co-located with a loopback-only marimo server.
> Detailed evidence and work still in progress live in
> [`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md).

This document is the operating contract for where the agent harness,
`marimo-inspect`, and marimo run, how they connect, and which URLs are resolved
by which process. It is not an installation guide or an experiment log.

## Terms and connection direction

| Term | Meaning |
| --- | --- |
| **Harness / MCP client** | The agent application that invokes MCP tools. |
| **MCP server** | The `marimo-inspect` process that implements the tools. |
| **marimo server** | The live notebook HTTP server and its kernel. |
| **`server_url`** | The base URL of the marimo server. It is always dialled by the **MCP server process**, not by the harness. |
| **`session_id`** | A live marimo session identifier. It is meaningful only together with its marimo server. |
| **Binding** | The retained target pair `(session_id, server_url)` used when later calls omit one or both values. It is not notebook ownership or a lock. |

The direction is always:

```text
Harness / MCP client  →  marimo-inspect MCP server  →  marimo server / kernel
```

Marimo does not connect back to the MCP client. The host that resolves a
`server_url` is the host running `marimo-inspect`.

## Supported modes

| Mode | MCP transport | MCP location | marimo location and bind | Status | Intended use |
| --- | --- | --- | --- | --- | --- |
| **L1 — local co-work** | `stdio` | Harness host | Same host, local marimo | Supported default | One project, one harness, ordinary development |
| **R1 — explicit remote marimo** | `stdio` | Harness host | A Tailnet-addressed marimo server on another controlled host | Supported, not preferred | A bounded, operator-supplied remote target |
| **R2 — shared Tailnet co-work** | `streamable-http` | Notebook / compute host | Same host, marimo bound to `127.0.0.1` | **Recommended shared mode** | Agents or harnesses on other controlled Tailnet hosts drive one live notebook host |
| **H1 — local shared HTTP** | `http` or `streamable-http` | Local host | Local marimo | Supported with shared-client rules | A persistent local service or a harness that cannot launch stdio |

A process can support other mechanically possible arrangements, but they are
not operating modes promised by this project unless listed above.

### L1 — local stdio MCP and local marimo

```text
Harness  ── stdio ──>  marimo-inspect  ── local HTTP ──>  marimo
```

This is the ordinary consumer path. The harness starts a child MCP process for
its project. `list_active_notebooks()` discovers and binds a live session;
argument-less later calls work because one stdio MCP process serves exactly one
client.

Use the normal pinned installation and harness configuration described in
[`harness-integration/README.md`](harness-integration/README.md). Start marimo
in edit mode and open it in a browser before co-working when a human is part of
the session.

### R1 — local stdio MCP and an explicit remote marimo target

```text
Harness  ── stdio ──>  marimo-inspect  ── Tailnet HTTP ──>  marimo
```

Here `marimo-inspect` runs locally and dials an operator-supplied Tailnet
`server_url` directly. For example, `list_active_notebooks(server_url=...)` or
`set_active_session(session_id=..., server_url=...)` makes that remote URL part
of the binding. Local registry discovery is not a fallback for this mode.

R1 is useful for a controlled, explicit target, but it exposes marimo's
control surface to the Tailnet. It is therefore not the standard shared-service
pattern. It does **not** support token-protected marimo: current clients detect
that condition and refuse with `auth_required`; they have no credential channel.

### R2 — remote HTTP MCP and loopback marimo

```text
Harness on host A  ── Tailnet Streamable HTTP ──>  marimo-inspect on host B
                                                       └─ loopback HTTP ──> marimo on host B
```

This is the supported Tailnet sharing topology.

- Bind `marimo-inspect --transport streamable-http` to one explicit Tailnet
  address on host B. Do not bind it to a wildcard address.
- Bind marimo only to `127.0.0.1` on host B.
- The harness on host A connects to the MCP server's `/mcp` endpoint. It never
  connects to marimo directly.
- MCP discovery on host B can return a loopback `server_url`, such as
  `http://127.0.0.1:<port>`. That is correct: the MCP server on B resolves it,
  not the harness on A.
- The Tailnet ACL and the explicit listener bind are the current security
  boundary. This does not create a public-network deployment contract.

A headless marimo launch creates no live notebook session by itself. Materialize
and instantiate a session before expecting MCP notebook tools to find one; see
[`live-tests.md`](live-tests.md) for the measured handshake mechanics. For
human co-work, browser-first edit mode is preferred instead.

### H1 — local shared HTTP MCP

A local persistent HTTP MCP server uses the same multi-client binding rules as
R2, but has no Tailnet hop. Use it only when a shared local service is useful or
a harness requires HTTP. It is not a replacement for the simpler stdio default.

## Binding, URL ownership, and multiple clients

Every target is a pair:

```text
(session_id, server_url)
```

`list_active_notebooks()` auto-binds the first discovered session and its URL.
`set_active_session()` validates an explicit pair against that server before it
stores a binding. A tool call with an explicit pair uses it for that call;
explicit values always override a binding.

The binding is stored in two places:

1. **MCP-session state**, for clients that retain one MCP session across calls.
2. A **process-global fallback**, only when the MCP-session state has no binding.

The fallback has deliberately different behavior by transport:

| MCP transport | Argument-less calls after binding | Reason |
| --- | --- | --- |
| `stdio` | Supported | One MCP process serves one client, so the fallback cannot cross a client boundary. |
| HTTP / Streamable HTTP with one observed client session | May work | The fallback is temporarily safe while the process is single-client. |
| HTTP / Streamable HTTP after a second client session | Refused with `binding_ambiguous` | A client that never bound a notebook must not inherit another client's target. |

Therefore, in shared HTTP/Tailnet mode, clients should use this robust loop:

1. Call `list_active_notebooks()`.
2. Choose the intended returned session row.
3. Retain its `session_id` and `server_url`.
4. Pass both explicitly on later calls.

In R2, passing a returned loopback `server_url` back to the remote MCP server is
intentional. The harness must not attempt to open that loopback URL itself.

Binding is not ownership: marimo does not publish a notebook owner, per-session
client count, or edit lock. A browser page and another MCP client can still be
attached to the same live kernel. Re-read before edits and follow the normal
staleness guard.

## Security and lifecycle boundary

The current contract is intentionally narrow:

- Tailnet-only co-work between machines we control; no public-internet or LAN
  listener is supported.
- In R2, expose only the MCP listener to the Tailnet and keep marimo loopback
  only.
- Do not put tokens, cookies, credentials, session identifiers, or private URLs
  in committed configuration, logs, or chat.
- Authenticated marimo is detected and reported as `auth_required`, but is not
  usable through the current client.
- Server start, stop, service supervision, and host reboot policy are
  operator-owned. MCP provides `restart_kernel`, not notebook-server lifecycle
  tools.
- `restart_kernel` resets kernel globals and widget state; re-read the notebook
  and re-run the needed cells afterwards.

## Capability status and remaining work

| Capability | Current status | Limit or next evidence |
| --- | --- | --- |
| L1 local stdio co-work | Supported | Normal consumer path |
| R1 explicit remote-marimo reads and safe execution | Proven in a controlled two-machine test | Explicit URL only; no automatic remote discovery or auth |
| R2 Tailnet MCP with loopback marimo | Proven in a controlled two-machine test | Recommended shared mode |
| Shared HTTP multi-client target isolation | Implemented | Use explicit target pairs after `binding_ambiguous` |
| Remote marimo `UIElement` mutation | Pending targeted validation | Requires a real marimo UI element and dependent re-run |
| Long remote-cell timeout behavior | Pending | No promised timeout policy yet |
| Cross-marimo-version behavior | Pending | The supported marimo range remains `>=0.24.0,<0.25` |
| One shared MCP process across multiple checkouts | Pending | Do not claim multi-repo support yet |
| Persistent host/service operation | Undecided | Current remote service evidence is prototype-scoped |
| Authenticated remote marimo | Intentionally unsupported | Requires a separate vault-bound or upstream-backed design |
| Configured remote endpoint discovery | Deferred | Consider only after the preceding operational decisions and live regressions |

The evidence, decision record, and detailed next work are maintained in
[`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md). Current project
priorities are indexed in [`project-status.md`](project-status.md).

## Related documents

- [`README.md`](../README.md) — package overview and quick MCP entry point.
- [`harness-integration/README.md`](harness-integration/README.md) — consumer
  installation and harness configuration.
- [`agenda-remote-marimo-mcp.md`](agenda-remote-marimo-mcp.md) — remote
  prototype evidence and unresolved design/validation work.
- [`marimo-version-support.md`](marimo-version-support.md) — pinned framework
  contract and upgrade validation.
- Packaged MCP resources — runtime co-work and safety guidance after a client
  is connected.
