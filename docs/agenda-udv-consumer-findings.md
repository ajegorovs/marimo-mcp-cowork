# Agenda (open issue): first-consumer integration findings (udv-echo-process)

> **Status:** Open — first real consumer of this stack outside the provider and
> the origin repo. Filed from the consumer repo on **2026-09-07** after running
> "Phase 0" of `udv-echo-process/docs/marimo-integration-plan.md`.
> Evidence labels: ✅ observed on the consumer machine (Linux, Python **3.14.7**
> kernel, marimo **0.24.0**, this repo's MCP server registered in the DSH
> harness) · 📄 from provider docs · ❓ unconfirmed.
> **Full raw journal:** `udv-echo-process/docs/marimo-integration-log.md`
> (entries F1–F13, S11–S14, O15–O24). This doc is the distilled *provider-side
> work list* from it.

## Consumer context

`udv-echo-process` (public GitHub repo, sibling checkout, **Python ≥ 3.14**,
uv-managed) is integrating live marimo notebooks + this MCP stack. Phase 0
passed: full agent loop (`list_active_notebooks` → `get_cell_map` →
`edit_cell` → `run_cell` → `get_variables` → `get_cell_outputs` →
`get_errors` → `marimo check`) worked end-to-end against a 3.14 kernel over an
ephemeral `uv run --with 'marimo[recommended]>=0.24.0,<0.25'` overlay.

## T1 — Headless `--no-token` servers are invisible to discovery ✅

- Launch: `marimo edit --headless --no-token --port <p> nb.py` (session created
  via the documented `/sse` handshake — that part works, 📄
  `agent-onboarding-demo-mcp.md` §Prerequisites).
- `~/.local/state/marimo/servers/` stayed **empty** for the whole live session;
  `list_active_notebooks()` with no `server_url` found 0 servers. Explicit
  `server_url` addressing worked perfectly.
- Cause (from marimo 0.24.0 source, ✅): `marimo/_server/server_registry.py`
  is invoked from `_server/api/lifespans.py` and `_server/start.py` —
  registration is skipped in headless mode (exact condition not yet isolated;
  could be `--headless` itself, or `--port`/`--no-browser` interaction).
- Why it matters: the harness-integration README's premise (axis B: the stdio
  server "sees every locally-discovered session") silently breaks for
  **browser-less** consumer setups — CI, remote boxes, headless agents. Every
  consumer will hit this.
- To do here:
  1. Reproduce: headless vs browser-connected `marimo edit --no-token`; diff
     registry entries.
  2. If by design → document it in `harness-integration/README.md` (the
     auto-bind story needs a "headless requires explicit server_url" caveat)
     and in the demo runbook; if not → upstream marimo issue.

## T2 — `list_active_notebooks` auto-bind semantics are murkier than documented ✅

- After `list_active_notebooks(server_url=…)`, the result says
  "session_id is now optional" — but the *next* call without `server_url`
  still failed with `Error: server_url is required`. The binding apparently
  holds only across **omitted**-arg calls of the discovery call itself, not
  reliably across tools/turns.
- To do here: clarify wording or make the bind sticky per session; consumer
  guidance meanwhile: always pass `server_url` explicitly. (Consumer plan/DoD
  already updated accordingly.)

## T3 — DSH harness MCP bridge mangles list-typed arguments ❌ consumer-side repro ✅

- `get_variables(variable_names=[...])` and `get_cell_outputs(cell_ids=[...])`
  fail with pydantic `list_type` errors — the harness delivers the JSON array
  as a **string**. Reproduced through the registered `marimo-inspect` stdio
  server (provider is pure HTTP client-side: `client.py` doesn't touch these,
  so the fault is the DSH MCP layer, not this repo).
- Workaround works (empty filter = all). To do here: nothing mandatory — maybe
  accept `str | list[str]` defensively (cheap). File against the harness.

## T4 — Private-API drift check: marimo `_code_mode` on 3.14 ✅

- Every live template ran against a 3.14.7 kernel (marimo 0.24.0): `cell_map`,
  `errors`, `variables`, `cell_outputs`, plus `edit_cell` (which round-trips
  through `_code_mode.get_context()` — its dry-run compile appears in
  tracebacks). All clean, including error paths (a cross-cell redefinition
  surfaced as `kind:"graph"` via `get_errors`).
- 📄 `marimo-version-support.md` says drift evidence so far was provider-side
  only. To do here: record this as consumer evidence; when the live suite gains
  a 3.14 leg (agenda-live-test item), cite
  `udv-echo-process/docs/marimo-integration-log.md` O21.

## T5 — Publishing checklist (repo to be made public — owner decision 2026-09-07) ✅

Pre-publish audit of this tree: MIT LICENSE present; no tailnet/RFC1918 IPs;
no credential strings (`token`/`api_key` hits are docs *discussing* marimo
auth); test fixtures use `/home/user/…` placeholders; only personal identifier
is the `pyproject.toml` author email. **No edits needed before flipping.**

After flipping:
1. `AGENTS.md` §Remote / publishing: drop "(**private**)" and the
   "don't leak" line (line ~186/189) — they become false.
2. `docs/harness-integration/README.md` §A1: remove the "Needs access to a
   **private** git repo — do not paste this URL into public docs" warning;
   A1 becomes the default consumer path, A3 (package index) stays aspirational.
3. Consider tagging `v0.2.0` (current package version) so consumers pin a tag,
   not floating HEAD (`udv-echo-process` will reference the tag in Phase 1).
4. `README.md` §Install: verify no "private repo / needs access" caveats
   remain (AGENTS.md line ~189 and harness-integration §A1 are the known
   spots).

- **2026-09-07-b** ❗ Status: items 1–4 are **NOT done** — this is a checklist
  to execute *after* the owner flips the repo public (Settings → danger zone →
  publish). The flip has not happened yet (repo still `Not Found`
  unauthenticated as of this entry). Verification sequence: flip → re-probe the
  API (`private: false`) → run items 1–4 here → `git tag v0.2.0 && git push
  --tags` → only then update the *consumer* repos: udv Phase 1 gate (sibling
  pattern + tag pin) and IPN L1/L2 (add `<0.25`, close L2, re-pin to
  `@v0.2.0`). *(This line originally claimed items 1–4 were done and cited a
  bogus commit hash — corrected in-session; the "hash" was a mis-copied lockfile
  pin, not a real provider commit. Kept as a self-caught-drift example.)*

- **2026-09-07-c** ✅ Status: flip landed. API re-probe confirmed
  `private: false`; repo now returns HTTP 200 unauthenticated. Items 1–4
  executed: AGENTS.md §Remote / publishing now says **public** (and gained a
  "Git tags" subsection), harness-integration §A1 drops the private-repo
  warning and shows the `@v0.2.0` pin form, README §Install verified clean
  (no private caveats). Tag `v0.2.0` is **not yet cut** — the owner has never
  used tags; AGENTS.md now documents the tag/release practice for the next
  agent to follow.

## T6 — Consumer gotchas worth cross-referencing (no work here) ✅

- **Sandboxed agents:** `uv` fails on read-only `$HOME` (`.cache/uv` lock) and
  matplotlib fails on unwritable `~/.config/matplotlib`. Fix: `UV_CACHE_DIR` +
  `MPLCONFIGDIR` pointed at the workspace. Candidate for
  `harness-integration/deepseek-harness-web-profile.md` (that doc already
  covers the `uv run` vs venv-binary gotcha).
- **`marimo[recommended]` cold install ≈ 340 MB / ~13 min** on a cacheless
  consumer — advise consumers to pre-sync once, not per `--with` probe.
- **marimo 0.24.0 API:** `mo.mpl` exposes only `interactive` (no `mo.plt` /
  `mo.pyplot`) — relevant to consumers wiring matplotlib figures; consumer
  library that saves-and-closes figures must render via `mo.image(path)` until
  it adds a fig-returning mode.
