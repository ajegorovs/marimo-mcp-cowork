# Agenda (open issue): hosting marimo servers / the MCP server remotely

> **Status:** **Open — thinking task, not yet a plan.** No decision taken.
> **Created:** 2026-09-07
> **Related:** [harness-integration/README.md](harness-integration/README.md)
> (gap #5 "version-skew policy" and gap #7 "remote/containerized servers"),
> `docs/marimo-version-support.md`, `src/marimo_inspection/discovery.py`,
> `docs/live-tests.md`.
>
> This doc *supersedes* the "state it as a non-goal" suggestion in
> harness-integration gap #7: remote hosting is now considered a candidate
> feature, so gap #7 should be re-pointed here rather than closed as a
> non-goal.

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
3. **`--no-token` is also why instantiation is missing.** The known
   live-test coverage gap (see `AGENTS.md` §Live tests) is that
   `/api/kernel/instantiate` needs a skew-protection token not exposed under
   `--no-token`. Remote hosting cannot lean on `--no-token` the way loopback
   does, so remote *forces* the token question that local defers.
4. **The `server_url` parameterism is half-finished.** `server_url` is a
   per-call parameter on the tools, while `session_id` is a per-connection
   auto-bind. For a remote server that is fine, but nothing pins *which* server
   a bound session belongs to — cross-server session ids would be a live footgun
   before long. ❓ unverified whether a stale `session_id` against a different
   `server_url` fails cleanly.
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
  in item 6), the execution-state templates (the session was never instantiated —
  same token-gated gap as the live suite, item 3), or anything over a real
  network (same host, loopback).

Implication: R1 (remote kernel) is less exotic than the list above suggests —
but the *auth* question in item 2 remains the actual blocker, not Python versions.

## Design space (not decided)

- **Transport.** `streamable-http` (`/mcp`) vs `sse` (`/sse`) for R2 — both
  already selectable via `--transport`. Which does each harness speak?
- **Network boundary.** Tailnet-only (WireGuard, no public listener) vs
  reverse-proxy + auth vs SSH tunnel per session. Tailnet-only is by far the
  cheapest honest answer, and we already run Tailscale — but it makes the
  `--no-token` question a policy question about the tailnet ACL rather than a
  technical one.
- **R1 vs R2 vs both.** R2-with-the-kernel-local is a small change (one URL in
  harness config). R1 with a *local* MCP server exercises every "what breaks"
  item above. Decide which one we actually want before designing either.
- **Session identity across hosts.** Do we need a
  `(server_url, session_id)` composite key instead of a bare session id?
- **Discovery for remotes.** An explicit `MARIMO_INSPECT_SERVER_URLS` (or
  config file) list of known remote endpoints, health-checked the same way the
  registry entries are — a small, contained addition to `discovery.py` that
  reuses `_check_server()`.
- **Auth.** If we must stop using `--no-token`: is there a way to pass marimo's
  auth through `MarimoClient` at all today? ❓ unverified — probably the single
  most important question in this doc, because it gates item 3 above.
- **Where the version contract lives.** If R2 is the answer to the pin problem,
  the *host* needs 0.24.x and consumers need nothing — that flips the consumer
  story in `harness-integration/README.md` (they'd configure a URL, not a
  dependency). Worth writing down before it becomes implicit.
- **Failure semantics.** What a tool call should return when the remote kernel is
  unreachable vs authenticated-wrong vs slow-but-alive. Currently `{error,
  stderr}`-ish dicts with no such distinction.
- **Multi-tenancy.** Two humans, one shared server: sessions are per-server, so
  this needs a real answer or an explicit "single-user only" non-goal.

## Non-goals (proposed, needs ratifying)

- No public-internet exposure of a marimo or MCP server, ever. Tailnet or tunnel
  only.
- No multi-user auth model of our own inventing — if we need auth beyond the
  tailnet, that is an upstream marimo question.
- Not a hosted/SaaS product; "remote" here means *a machine we control*.
- No change to the private-API approach for remote's sake — if remote forces a
  compatibility layer, that is the `NotebookBackend` trigger
  (`docs/notebook-backend-protocol.md`) and should be decided there.

## Suggested first steps when we pick this up

1. **Answer the auth question first.** Determine whether a token-protected
   marimo server is usable through `MarimoClient` at all (does it expose the
   skew/session token?), because that single fact decides whether R1 is viable
   or whether only R2 with a local kernel is on the table. Write the answer into
   `docs/live-tests.md` — it also closes the instantiation coverage gap.
2. **Prototype the smallest thing:** `marimo-inspect --transport
   streamable-http --host <tailnet-ip> --port 8090` on one machine, one harness
   `insert:` entry with `transport: streamable-http` + `url` on another. Verify
   the 14 tools surface. No code change, no new discovery. This is a
   half-afternoon and it converts most ❓ items above into facts.
3. **Verify the composed URL shape** (integration draft gap #4) on the way — the
   FastMCP defaults are `/mcp` and `/sse`, but nothing has exercised them
   end-to-end.
4. **Then decide the version-contract question.** If step 2 works, write down
   whether the *server host* is now the thing that must hold 0.24.x, and update
   `docs/marimo-version-support.md` + the integration doc's consumer story to
   match.
5. **Only after 1–4** touch `discovery.py` for multi-endpoint support, and only
   with a live test that boots a second, explicitly-addressed server.

## Open questions for the next working session

- Which of R1/R2 do we actually want, and for which repo? (The udv integration
  is the next consumer — decide its topology *before* we grow the options.)
- Is "one always-on marimo host per machine" a service we'd supervise (systemd
  user unit), or hand-launched?
- What is the minimum we need from upstream marimo to stop using `--no-token`,
  and is it worth filing?
- Does a remote MCP server change what we tell consumers to install at all
  (URL-only, no `uv add`)? That would be the best outcome and deserves its own
  doc if it holds.
