# Agenda (open issue): hosting marimo servers / the MCP server remotely

> **Status:** **Tailnet-only R2 is the supported shared co-work topology; R1 is a supported explicit-target escape hatch. Both controlled prototypes are proven; protected marimo is detected but unsupported.**
>
> The durable operating contract is [`deployment-topologies.md`](deployment-topologies.md).
> This agenda retains prototype evidence and unresolved validation/design work.
> **Created:** 2026-09-07
> **Related:** [harness-integration/README.md](harness-integration/README.md)
> (gap #5 "version-skew policy" and gap #7 "remote/containerized servers"),
> `docs/marimo-version-support.md`, `src/marimo_inspection/discovery.py`,
> `docs/live-tests.md`.
>
> This doc supersedes the earlier "state it as a non-goal" suggestion in
> harness-integration gap #7. Tailnet-only remote hosting is now a supported
> operating scope; the remaining entries below track its unresolved validation
> and operator-policy work.

## The question

Right now everything in the marimo-inspect topology is **same-machine**: the
marimo editor, the registry files `discover_servers()` scans, and the
`marimo-inspect` FastMCP server all live on one host, and each agent harness
spawns its own stdio child. What do we actually gain by putting the marimo
server and/or the MCP server on a **remote** machine — and what does that break?

Two distinct ideas that must not be conflated, because they have different
answers:

- **R1 — remote marimo kernel.** The notebook server (the thing holding live
  state) runs elsewhere; marimo-inspect and the agent stay local.
- **R2 — remote MCP server.** `marimo-inspect` runs elsewhere (e.g. on the
  kernel's host) and the harness connects over `streamable-http`.

## Why now (the concrete hooks)

1. **We already have a tailnet and a GPU box in the picture.** `tailscale` is up
   on this machine, and the image-processing repo's `marimo.toml` already points
   its AI provider at a remote vLLM endpoint (tailnet-only URL; do not paste the
   concrete address into docs — the consumer repo's
   `docs/agenda-config-hygiene.md` tracks scrubbing it). Notably that endpoint
   **did not integrate out-of-the-box — it had to be wired into the notebooks
   manually** (custom-provider config, per the image repo), which is itself a
   data point: multi-host setups are already real but currently hand-held. So
   "work happens on more than one host" is already true in this setup; the
   notebook/MCP layer is the part still assumed local.
2. **The per-repo stdio pattern does not scale linearly.** Each consumer repo
   needs its own venv binary and its own harness `insert:` entry (see
   `harness-integration/README.md` B1). N repos → N server processes → N places
   for the marimo pin to drift.
3. **Version pinning has no answer today.** marimo-inspect pins
   `marimo[recommended]>=0.24.0,<0.25` because lint imports private APIs. A
   consumer repo on a newer marimo has no clean resolution. **R2 is arguably the
   fix**: one host holds the 0.24.x contract, consumers just speak MCP to it.
   Gap #5 in the integration draft is really this question.
4. **Notebook-side resources may not be on the laptop.** Heavy
   signal/image work (long cell runs, big arrays, matplotlib figures, GPUs) is
   exactly what you'd rather have executing on the machine with the compute,
   while the agent drives it from wherever it lives.

## The benefits we think we're buying (to be tested, not assumed)

- **Single version contract.** One host owns the marimo pin; the private-API
  risk surface collapses from N environments to 1.
- **Fewer moving parts per repo.** No per-consumer venv binary, no per-consumer
  harness spawn config beyond a URL.
- **Shared long-lived sessions.** A kernel can outlive an agent restart, a
  laptop sleep, or a harness `--reload` respawn (the demo doc already complains
  that a reload drops the bound session).
- **Compute locality.** Kernels run next to the data / GPU they need instead of
  next to the agent.
- **Multi-agent / multi-client access to one notebook state** — one human's live
  session driven by two different harnesses.

## What breaks (the honest list)

1. **Discovery is a local-filesystem scan.** `_get_registry_dir()` reads
   `$XDG_STATE_HOME/marimo/servers` (Windows: `%USERPROFILE%\.marimo/servers`),
   health-checking each via `GET /api/sessions`. A registry on another host is
   invisible from here. **Existing seam:** `discover_servers(base_url=...)`
   already bypasses the registry for a single explicit URL — so R1 works today if
   the URL is configured by hand. What does *not* exist is "list the notebooks on
   a remote host" without knowing the port.
2. **`--no-token` is a security cliff, and discovery depends on it.**
   Token-less means session-auth is off — harmless on loopback, and on a tailnet
   "harmless" only because the tailnet *is* the ACL. Over any broader interface,
   an unauthenticated marimo server is remote code execution. This is the
   constraint that decides the whole design, and it is why "just expose port
   8090" is not an option.
3. **`--no-token` does not block instantiation, but it is still a security
   boundary.** The skew-protection token required by `/api/kernel/instantiate`
   is exposed in the root-page `<marimo-server-token data-token="…">` marker
   under `--no-token`; Task 03 and the R2 prototype materialized and
   instantiated headless sessions through that flow. A token-authenticated
   marimo server remains a separate unanswered question for `MarimoClient`.
4. **The `server_url` parameterism is half-finished.** `server_url` is a
   per-call parameter on the tools, while `session_id` is a per-connection
   auto-bind. For a remote server that is fine, but nothing pins *which* server
   a bound session belongs to — cross-server session ids would be a live footgun
   before long. ✓ **Sub-question resolved (2026-09-13, Wave A):** the
   *pair-mismatch* half is verified — a stale `session_id` against a different
   `server_url` now fails cleanly: every targeting tool resolves its target
   through one shared step and answers `status: error`,
   `reason: session_not_found` with that server's truthful `available_sessions`
   and `available_sessions_readable: true` (and `operation_ran: false`); no read
   or write runs. An unreadable census is `server_query_failed`, never a
   not-found, and transport failures are `server_unreachable`.
   See `reference://marimo-inspect/fallbacks-and-limits` §"Target errors are
   payloads, not tool exceptions". ✓ **Sub-question resolved (2026-09-13,
   Task 01):** the **auth-versus-edit-scope taxonomy** is decided and
   implemented. A denied census is classified by a read-scope probe
   (`GET /api/version`), never by `WWW-Authenticate` (stripped) or a byte
   count: `edit_scope_required` (readable read-scope endpoint — the run-mode
   case, which authentication cannot fix), `auth_required` (read-scope probe
   denied with the same body), `session_census_denied` (probes undecided,
   conservative). Target resolution, `set_active_session` and `restart_kernel`
   all speak that vocabulary, and a denied census reports
   `available_sessions_readable: false` rather than an empty list. **The remaining
   topology work is operational, not target-pair correctness:** the supported
   R2/R1 contract and its URL-resolution rules are in
   [`deployment-topologies.md`](deployment-topologies.md); remote hosting still
   needs the validation and service-policy decisions tracked below.
5. **Latency and timeouts.** Templates round-trip scratchpad execution over
   `POST /api/kernel/execute` and parse an SSE stream. Every tool call becomes a
   WAN round-trip. The harness default `toolCallTimeoutMs` is 60000 — long kernel
   runs on a remote box will hit it.
6. **The in-process / in-kernel split moves with the deployment.** Lint runs in
   the *MCP server's* marimo; the templates run in the *kernel's* marimo.
   Locally these are the same env, so `-m live` proves both. Remotely they are
   genuinely two versions, and the "both contracts coupled by the live suite"
   safety net stops existing. This is the deepest technical cost — it turns today's
   known-but-deferred drift risk (`marimo-version-support.md`) into the normal
   case, and the live suite has no mode for it.
7. **Writes hit files on the other host.** `create_cell`/`edit_cell`/
   `delete_cell` rewrite the `.py` on disk. Remotely that means the repo checkout
   the agent reasons about and the repo the writes land in are different
   filesystems — a whole class of "why didn't my change appear" confusion.

## Evidence gathered while writing this (2026-09-07)

A throwaway probe answered the "how bad is the drift?" part of item 6 — the
result is worth keeping even though the probe file was deleted:

- Setup: marimo **0.24.0 kernel on Python 3.14.7** (`uv venv --python 3.14`),
  driven by marimo-inspect on **Python 3.12.14** (marimo 0.24.0), session created
  via the `/sse` handshake, scratchpad executed through `MarimoClient`.
- Result: `cell_map` and `errors` templates returned correct JSON
  (`status=ok`, no stderr); in-process `_lint_source` ran fine on the 3.12 side.
  So **a marimo-0.24 kernel tolerates a different Python minor on the kernel
  side**, and the two-version split in item 6 is survivable *for 0.24.x-vs-0.24.x
  kernels*.
- What this does **not** cover: a kernel running marimo **0.25+** (the real risk
  in item 6), or anything over a real network (same host, loopback).

Implication: R1 (remote kernel) is less exotic than the list above suggests —
but the *auth* question in item 2 remains the actual blocker, not Python versions.

## R2 controlled-Tailnet prototype (2026-09-13) — PASS

Two controlled machines were used, with identities and addresses deliberately
scrubbed. Machine B cloned the provider repository at `bdc02d4`, then ran:

```text
marimo edit notebooks/widget_demo.py --headless --no-token \
  --host 127.0.0.1 --port 2718
marimo-inspect --transport streamable-http \
  --host <machine-B-tailnet-ip> --port 8090
```

The processes were transient user-systemd units for the prototype. The marimo
listener was loopback-only; the MCP listener was bound to the explicit Tailnet
IPv4 only. Listener inspection confirmed no wildcard or LAN bind. This is an
appropriate prototype mechanism, not a reboot-persistent service design.

Because `--headless` suppresses a browser but does not create a session, Machine
B used the measured Task 03 sequence: create an `/sse` session, fetch the root
page's skew token, then POST `/api/kernel/instantiate`. The resulting orphan
session had executed fixture cells (`idle` with outputs) and was discoverable by
the MCP server without a browser.

Machine A connected to Machine B's `/mcp` endpoint through a real FastMCP
Streamable HTTP client. It verified the 15 advertised tools and three packaged
resources, discovered one session, then explicitly supplied the returned
`(session_id, server_url)` on every subsequent call. It successfully performed
`get_cell_map`, `get_cell_data(include_errors=True)`, `get_variables`,
`run_cell(mode="cell")`, `get_cell_outputs`, and `get_errors`; the run returned
`status: ok`, its requested cell succeeded, and `has_errors` was false.

The explicit pair is required for this client shape: FastMCP 4.0.3 HTTP calls
may carry fresh MCP client sessions, and an argument-less request after more
than one client session is correctly refused as `binding_ambiguous`. The
`server_url` discovered from the remote MCP server was its local
`http://127.0.0.1:2718`; that is correct because it is consumed by the MCP
process on Machine B, not by Machine A.

The fixture exposes an `anywidget.AnyWidget`, not a marimo `UIElement`, so it
was not a valid safe target for `set_ui_value`; this prototype proves remote
discovery, reads, and execution rather than the marimo-UI-element mutation
contract. No public network exposure, tunnel, or source change was needed.

## R1 explicit remote-marimo prototype (2026-09-13) — PASS

Machine A used its local `MarimoClient` and local marimo-inspect handlers
against an operator-supplied, controlled **non-loopback** marimo endpoint on
Machine B. The endpoint itself, session identifier, and dead-endpoint control
were supplied only through local environment variables and emitted only as
short opaque fingerprints. The client-side evidence therefore proves the R1
protocol path; the probe deliberately records non-loopback scope rather than
claiming to audit Machine B's listener/ACL configuration.

All six probe steps passed without retries:

1. `discover_servers(base_url=...)` made the targeted census request, returned
   one healthy server, and echoed only the supplied target; the local registry
   was not a fallback.
2. The supplied materialized session appeared in that server's census.
3. `get_cell_map`, `get_cell_data(include_errors=True)`, `get_variables`, and
   `get_errors` all succeeded with the explicit `(session_id, server_url)`
   pair. The served fixture had pre-existing error rows; a successful read is
   not a claim that the fixture was error-free.
4. A non-mutating scratchpad execution returned `status: ok`, the expected
   stdout marker, and the expected scalar value. On pinned marimo 0.24 that
   scalar is rendered as an HTML output envelope rather than the text/plain
   shape used in older client mocks; the probe validates the rendered value
   without emitting its MIME type or payload.
5. A deliberately failing scratchpad execution surfaced an error in its normal
   error channel, without mutating notebook source or restarting the kernel.
6. A separately supplied dead endpoint returned no discovered server, raised a
   client transport error, and made `get_cell_map` refuse with
   `reason: server_unreachable`, `operation_ran: false`, and
   `state_changed: false`.

This validates R1's manual explicit-URL path, not automatic remote discovery,
authentication, an operator service model, or a public-network deployment.

## Protected remote-marimo capability spike (2026-09-13) — BLOCKED AS DESIGNED

A second controlled, non-loopback Machine B server ran headlessly in edit mode
with marimo's default token authentication enabled. Machine A held no token,
cookie, credential-bearing URL, or session identifier. The probe emitted only
scrubbed status/result evidence and performed no write, execute, instantiate,
restart, or authentication attempt.

The result is an intentional, safe block rather than a transport failure:

- `MarimoClient`'s source-level contract has only `server_url` as a constructor
  input and no generic credential/header configuration channel.
- `discover_servers(base_url=...)` returned no server because the protected
  census was non-200, which is its documented health-check rule.
- Direct census and the read-scope probe both returned HTTP 401.
- `get_cell_map` and `set_active_session` both returned structured
  `reason: auth_required`; no target was resolved, no operation ran, no state
  changed, and no binding was created.
- The root-page request was not followed: it returned HTTP 303 and was
  semantically classified as a login page rather than an app shell.

Therefore a protected remote marimo server is **detected but unsupported** by
the current client. Do not work around this by putting a token in a URL, a
repository file, an MCP configuration, or chat. Supporting it would require a
separate security design for a vault-bound credential flow or upstream marimo
integration; it is not a discovery, retry, or timeout fix.

## Decisions and deferred design

- **Transport and topology (decided).** R2 uses `streamable-http` at `/mcp` for
  shared Tailnet co-work: the MCP process is co-located with a loopback-only
  marimo server. R1 remains a supported explicit-target escape hatch, not the
  recommended shared-service path. The operating consequences are canonical in
  [`deployment-topologies.md`](deployment-topologies.md).
- **Network boundary (decided).** Tailnet-only operation between machines we
  control is the supported scope. The R2 MCP listener binds one explicit
  Tailnet address; marimo stays loopback-only. No public or LAN listener is a
  supported contract. This makes `--no-token` an explicit policy relying on the
  Tailnet ACL, not an accidental fallback.
- **Session identity (implemented).** Active bindings retain both
  `(server_url, session_id)`, and an explicit pair is validated against the
  selected server before tools operate. Shared HTTP clients use explicit pairs
  after fallback binding becomes ambiguous.
- **Remote discovery (deferred).** A configured list of known remote endpoints
  is not needed for R2, and R1 remains explicit-URL only. Consider it only
  after the remaining operational decisions and a live multi-endpoint
  regression.
- **Authentication (unsupported by design).** The client detects an auth gate
  and returns `auth_required`, but cannot authenticate. Supporting protected
  marimo would require a separate vault-bound or upstream-backed design; do not
  put credentials in URLs, configuration, logs, or tool arguments.
- **Version ownership (open).** R2 centralizes the 0.24.x provider environment,
  but the host-versus-consumer version contract and deliberate version-skew
  behavior still need validation.
- **Failure semantics (partially resolved).** `server_unreachable`,
  `auth_required`, `edit_scope_required`, and conservative
  `session_census_denied` are structured and distinct. Slow-but-alive behavior
  has no dedicated remote-timeout contract yet.
- **Multi-client co-work (open).** Target isolation fails closed, but marimo
  provides neither ownership nor edit locking. Multi-checkout visibility and
  the practical shared-session policy still require evidence.

## Scope boundaries

- No public-internet exposure of marimo or MCP; Tailnet-only co-work is the
  current supported boundary.
- No local multi-user authentication model. Any need beyond the Tailnet boundary
  is an upstream or separately designed credential problem.
- Not a hosted/SaaS product; "remote" means a machine we control.
- No compatibility layer solely for remote deployment. If remote operation
  forces one, use the `NotebookBackend` decision trigger in
  [`notebook-backend-protocol.md`](notebook-backend-protocol.md).

## Remaining prototype and design work

The smallest R2 test, its `/mcp` URL shape, and no-token headless
materialization are now proven above. Do not add remote discovery configuration
or alter `discovery.py` merely because R2 works. The remaining questions are:

1. **Credential design decision.** Decide whether protected remote marimo is a
   supported product goal. If so, design a vault-bound credential flow or seek
   upstream integration; do not add tokens to URLs, config, logs, or MCP tool
   arguments. If not, retain Tailnet-only no-token as an explicit operational
   policy rather than an accidental fallback.
2. **Remote UI-element behaviour.** Use a purpose-built notebook exposing a
   marimo `UIElement` (not the fixture's `anywidget.AnyWidget`) to test
   `set_ui_value` plus an explicit dependent `run_cell` in headless operation.
3. **Version and timeout policy.** Decide whether Machine B is the supported
   owner of the pinned 0.24.x contract, then test the real long-cell timeout
   boundary and an intentional marimo version-skew case before promising either
   behaviour to consumers.
4. **Shared-server and lifecycle policy.** Test multiple notebook checkouts on
   one MCP process before describing it as multi-repo; separately decide whether
   the prototype's transient user unit becomes an operator runbook or a managed
   service. Neither is a reason to add remote process-control tools today.
5. **Only after the above** consider `discovery.py` support for a configured
   list of known remote endpoints, with a live multi-endpoint regression.

## Open questions for the next working session

- Is R2 the supported topology for the next consumer, or does that consumer
  specifically require the now-proven but still operationally distinct R1
  arrangement?
- Is "one always-on marimo host per machine" an operator-supervised service or
  a hand-launched prototype? Do not infer a reboot/restart policy from the
  transient-unit result.
- Can `MarimoClient` support authenticated marimo without exposing credentials
  to an MCP client, and is upstream work required?
- Does an R2 server centralize the 0.24.x version contract enough to offer a
  URL-only consumer path, or must consumers still install the package?
