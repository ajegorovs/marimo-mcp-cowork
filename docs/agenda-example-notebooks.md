# Agenda (open issue): example notebooks — review, and tie them into the repo

> **Status:** **Open — a first example exists, the wiring does not.** One pattern
> example landed under `examples/patterns/`; what `examples/` *is* (its contract
> against `notebooks/`) and where it gets wired so it cannot rot are undecided.
> **Created:** 2026-09-12
> **Related:** [`examples/README.md`](../examples/README.md),
> [`project-status.md`](project-status.md), `notebooks/`,
> `docs/agent-onboarding-demo-mcp.md`, `docs/marimo-version-support.md`.

## The question

`notebooks/` is defined in `AGENTS.md` as *"marimo fixtures used by live tests
and the MCP demo"*. Consumer-facing **usage patterns** — the answers to "how do
I make X behave like this in marimo?" — have no home in that definition: they are
not fixtures, no test boots them, and they are exactly the artifact a consumer
would want to read.

A first example now exists
(`examples/patterns/cascading_sidebar_controls.py`), so the open question is
structural rather than "should we have examples at all":

1. What is the contract for `examples/` versus `notebooks/`?
2. Where does `examples/` get referenced so it is discoverable and checked?

## What makes this worth a doc rather than a commit

- **Unchecked trees rot.** `AGENTS.md` §Common commands and the README
  Development section both run `uv run marimo check notebooks`. A new
  `examples/` tree is outside every existing check, so nothing notices when a
  marimo bump invalidates an example.
- **The examples carry claims, not just code.** The first one asserts marimo
  behaviour (single-assignment rule, the creating-cell `.value` refusal,
  multi-`mo.sidebar` stacking, `full_width` on a sidebar control, multiple
  renders of one element sharing state). Claims about a version-pinned
  dependency belong under the same scrutiny as the rest of the tree — see
  `docs/marimo-version-support.md`.
- **Frontends are still not CI-covered here.** The live suite boots kernels, not
  browsers, so "the example renders correctly" is a manual check no matter where
  the example lives. That is a known repo-wide gap, not something `examples/`
  introduces.

## Items

| id | item | state |
| --- | --- | --- |
| T-E1 | Decide the `examples/` vs `notebooks/` contract and write it in `examples/README.md` and the consumer-facing README (examples = no consumer deps, not imported by tests, not fixtures). | open |
| T-E2 | Extend the check command to `uv run marimo check notebooks examples` in the README Development section and canonical test documentation; decide whether CI runs it. | open |
| T-E3 | Keep `examples/` discoverable from `docs/project-status.md` and the README without adding mutable status to `AGENTS.md`. | open |
| T-E4 | Review `patterns/cascading_sidebar_controls.py` for placement and naming: is `examples/patterns/` the right shape, and does the pattern name read as a pattern? | open |
| T-E5 | Decide whether examples are *executed* anywhere (a smoke run that proves the cells run) or lint-only (`marimo check`), given the kernel-vs-frontend split above. | open |
| T-E6 | Decide the fate of the per-parent memory variant (a `mo.state`-seeded child control) — currently only described in the notes of T-E4's example, not shipped as its own example. | open |
| T-E7 | Settle the ruff/format policy for `examples/`. Applied 2026-09-12: added `examples/` to `[tool.ruff] exclude` next to `notebooks/` (see §Resolved log). Confirm the exclusion is preferred over per-file suppressions. | applied, confirm |

## Open decisions

- **Fixture boundary.** Does a notebook that a live test happens to be able to
  boot belong in `notebooks/`, even if it reads like an example? The current
  split is by *purpose*, not by capability — worth confirming that is the intent.
- **Audience.** Are these examples for a consumer that read the README and wants
  a working starting point, or for an agent that needs a canonical snippet? The
  first example targets both, which is a decision that has not been made
  explicitly.
- **Growth.** If a second and third example arrive, does `examples/` stay flat
  with `patterns/` as the only bucket, or does it mirror the package layout?

## Resolved log

- 2026-09-12 — `examples/patterns/cascading_sidebar_controls.py` +
  `examples/README.md` created (generic, consumer-free; runs on `marimo` alone).
  Behaviour asserted in its notes was verified against the pinned marimo 0.24.x
  by running the notebook headless and driving the live controls in a browser
  (cascade re-derives the child's options; parent change rebuilds the child;
  the creating-cell `.value` access raises; two `mo.sidebar` calls stack).
  Remaining wiring is T-E1…T-E6.
- 2026-09-12 — **T-E7 applied.** Adding `examples/` turned the repo-wide
  `uv run ruff check .` red: marimo's generated cell shape (a bare trailing
  expression used as the cell's output, plus the closing `return`) trips `B018`
  and `PLR1711` *by design*, and the hand-aligned trailing comment is not
  `ruff format`-stable. `examples/` is therefore excluded in `[tool.ruff]`,
  matching the existing `notebooks/` rationale. Verified after the change:
  `uv run ruff check .` → all checks passed, `uv run ruff format --check .` →
  88 files already formatted, `uv run marimo check examples` → exit 0.
- 2026-09-12 — a second pattern landed:
  `examples/patterns/slider_with_step_buttons.py` (+ a row in
  `examples/README.md`) — a slider paired with `±1` and coarse step buttons over
  one shared `mo.state`, slicing a 2-D `f(x, y)` into 1-D profiles. **Its central
  claim cost a round trip and is worth recording here:** the slider must be
  created in a *different cell* from the buttons. marimo never re-runs the cell
  that called a state setter (`_runtime/runner/cell_runner.py`, rule 3 — *"a
  state update in a given cell will never re-trigger the same cell to run"*), so
  a buttons+slider cell updates the state on a click, is then skipped, and
  nothing visibly happens: the slider is never re-seeded and downstream cells keep
  reading its stale `.value`. That is the failure mode a consumer observed live
  ("i dont see that buttons do anything") in the single-cell version this example
  first shipped as; splitting it is the whole fix. A third cell then composes
  both halves into ONE sidebar entry (`mo.sidebar` calls stack as separate
  blocks, so neither creation cell displays itself). Verified after the split
  through this repo's own tool surface, against the live session the frontend
  held: `+1` → `profile = 1`; `−1` → `0`; a coarse step down from 0 clamped at
  `0`; a coarse step up → `11` (`LAST // 4`, `LAST = 47`), with
  `step_slider.value` read back tracking every step. Static gates: `marimo check
  examples/patterns/slider_with_step_buttons.py` → exit 0, `ruff check .` /
  `ruff format --check .` → clean (88 files, `examples/` excluded as in T-E7).
