# Agenda (open issue): redesign how we run live kernel tests

> **Status:** Agenda — issue described, **not yet solved**. This document exists
> to capture the current pain points and the design space so we can plan a
> proper solution; it contains no implementation.

## The issue

The package's behavior depends on **live marimo kernels** — the in-kernel
templates (`templates/*.py`) run inside a real kernel, and the client talks to
a real server's HTTP API. We test against a live kernel via a hand-rolled
`MarimoServerManager` fixture (`tests/marimo_inspect/live/conftest.py`) that
boots `python -m marimo edit --no-token --headless` on `$MARIMO_TEST_PORT`
as a subprocess, runs 21 `-m live` tests, then tears it down.

That works, but it has structural weaknesses that we want to iron out — and
possibly redesign entirely — before the suite grows or supports more than one
marimo version.

## Current pain points

1. **Two version contracts, one test env.** The in-process lint runs against
   the *installed* marimo; the templates run against the *server's* marimo. The
   live suite couples both to the same installed version, so drift between them
   is invisible until production. (See `docs/marimo-version-support.md`.)
2. **No per-version matrix.** The suite only ever exercises the single installed
   marimo; it cannot prove 0.24.x-lineage kernels keep working when the package
   itself runs on a different version.
3. **Subprocess lifecycle is delicate.** Orphan servers on port clashes, slow
   cold starts, and Windows/WSL path quirks make the live suite flaky and
   slow-ish to iterate on.
4. **Live tests are second-class.** They are deselected by default
   (`-m "not live"`), so nothing runs them on every change — regressions in
   kernel-facing code can slip through until someone explicitly opts in.
5. **Coverage is thin relative to effort.** 21 tests, many asserting small
   slices; setup/teardown overhead per run is disproportionate, and the
   scaffold (start_test_server.py, create_session.py) exists in parallel.

## Design space to consider (not decided)

- **Fixture strategy:** one kernel per session (current) vs per test vs a
  lazily-started shared server; automatic retry on port conflict.
- **Version matrix:** run the live suite against N marimo versions (e.g. 0.24.x
  and the next release) via CI matrix or a script that swaps environments;
  which versions are authoritative per `marimo-version-support.md`.
- **Server process management:** subprocess manager (current) vs
  `marimo.mcp`-style in-process kernel vs containerized kernels vs marimo's
  official test helpers if any exist upstream.
- **What exactly needs a live kernel:** many assertions could run against the
  *static* parts (client request building, template string shape) without a
  process; we can split "unit of templates" from "integration against kernel"
  to shrink the truly-live set.
- **Orchestration:** pytest marker/`-m live` (current) vs a separate
  test session (e.g. `uv run pytest tests/live`) vs CI-only job; whether live
  runs stay opt-in or become part of the default path behind a fast flag.
- **Diagnostics:** better failure output (server logs, session dump) so a live
  failure tells you *where*, not just "assertion failed".

## Non-goals

- Removing live coverage entirely (the kernel-facing surface is the point of
  this package).
- Abstracting marimo internals just to make tests easier — that is a separate
  deferred idea (`docs/notebook-backend-protocol.md`).

## Suggested first steps when we pick this up

1. Inventory current live tests: what each asserts, what genuinely needs a
   kernel vs what can be tested statically.
2. Decide the minimal kernel-per-run model and a stable port/lifecycle policy.
3. Add a CI-style script that runs the live suite headlessly and fails loudly
   with server logs on error.
4. Revisit the version matrix only after step 3 makes a single-version run
   reliable.
