# Agenda (closed): example notebooks — contract, and how they are verified

> **Status:** **Closed 2026-09-13.** The `examples/` contract, its docs wiring and
> its automated gate are settled: `examples/` holds consumer-facing marimo usage
> patterns that run on `marimo` alone, are never imported by tests, are covered
> statically by `uv run marimo check notebooks examples`, and are booted and
> driven live by `tests/marimo_inspect/live/test_examples.py`.
> **Created:** 2026-09-12 · **Closed:** 2026-09-13
> **Related:** [`examples/README.md`](../examples/README.md),
> [`project-status.md`](project-status.md), [`live-tests.md`](live-tests.md),
> `notebooks/`, `docs/marimo-version-support.md`.

## The question

`notebooks/` is defined in `AGENTS.md` as *"marimo fixtures used by live tests
and the MCP demo"*. Consumer-facing **usage patterns** — the answers to "how do
I make X behave like this in marimo?" — have no home in that definition: they are
not fixtures, no test boots them, and they are exactly the artifact a consumer
would want to read.

Two examples landed under `examples/patterns/` while the wiring was open, so the
question was structural rather than "should we have examples at all":

1. What is the contract for `examples/` versus `notebooks/`?
2. Where does `examples/` get referenced so it is discoverable and checked?

## What made this worth a doc rather than a commit

- **Unchecked trees rot.** `AGENTS.md` §Common commands and the README ran
  `uv run marimo check notebooks`. A new `examples/` tree was outside every
  existing check, so nothing would notice when a marimo bump invalidated an
  example.
- **The examples carry claims, not just code.** They assert marimo behaviour
  (single-assignment rule, the creating-cell `.value` refusal, multi-`mo.sidebar`
  stacking, `full_width` on a sidebar control, multiple renders of one element
  sharing state, and the state-setter cell-skip rule). Claims about a
  version-pinned dependency belong under the same scrutiny as the rest of the
  tree — see `docs/marimo-version-support.md`.
- **Frontends are still not CI-covered here.** The live suite boots kernels, not
  browsers, so "the example renders correctly" is a manual check no matter where
  the example lives. That is a known repo-wide gap, not something `examples/`
  introduces.

## Items — all closed

| id | item | state | resolution |
| --- | --- | --- | --- |
| T-E1 | Decide the `examples/` vs `notebooks/` contract and write it in `examples/README.md` and the consumer-facing README. | **closed** | `examples/` = consumer-facing usage patterns that run on `marimo` alone, carry no consumer deps, are **not fixtures**, and are never imported by tests or the package; `notebooks/` = fixtures by *purpose*. Written in `examples/README.md` and in the new `README.md` §Examples. |
| T-E2 | Extend the check command to `uv run marimo check notebooks examples` in the README Development section and canonical test documentation; decide whether CI runs it. | **closed** | The combined command is `uv run marimo check notebooks examples`, documented in `README.md` §Examples, `examples/README.md` and `docs/live-tests.md`. **No CI is added** — this repo has no CI at all (no workflow files), and adding one is a separate workstream, not part of the examples contract; the command is what CI would run when it exists. `AGENTS.md` retains its narrower, valid fixture-only `marimo check notebooks` command. |
| T-E3 | Keep `examples/` discoverable from `docs/project-status.md` and the README without adding mutable status to `AGENTS.md`. | **closed** | `README.md` §Examples (stable, consumer-facing), the `examples/` entry in `docs/project-status.md`, and this agenda. No example state, count or date was added to `AGENTS.md`. |
| T-E4 | Review `patterns/cascading_sidebar_controls.py` for placement and naming. | **closed** | `examples/patterns/` is the right shape and stays the **only** bucket; both names read as patterns (`cascading_sidebar_controls`, `slider_with_step_buttons`). `examples/` stays flat: a new bucket is justified only by a genuinely different *kind* of artifact, not by a second pattern. |
| T-E5 | Decide whether examples are executed anywhere or lint-only. | **closed** | **Executed.** Static `uv run marimo check notebooks examples` plus a live smoke that discovers the tracked `examples/**/*.py` tree, boots each example as a notebook on its own headless server (tmp copy via the existing `notebook_server` factory) and runs the whole document through the real `run_cell(mode="all")` handler — `status: ok` with no failed/not-run/unverified target. Frontend *rendering* remains manual/out of scope (kernels, not browsers). |
| T-E6 | Decide the fate of the per-parent memory variant. | **closed** | **No third example.** The variant is one control's `value=`/`on_change=`, not a different pattern, so it ships as a concise recipe in `examples/README.md` §Per-parent memory — and, because it is still a claim, it is verified live: `tests/marimo_inspect/live/test_examples.py` boots an implementation of that recipe (the same cell structure, renamed locals) as a test-local notebook and asserts the child is restored per parent (with the no-memory-yet fallback). |
| T-E7 | Settle the ruff/format policy for `examples/`. | **closed (confirmed)** | The blanket `examples/` (and `notebooks/`) exclusion in `[tool.ruff]` is kept — it is preferred over per-file suppressions, because marimo's generated cell shape (bare trailing expression, closing `return`) trips `B018`/`PLR1711` by design in *every* notebook file. `pyproject.toml` needed no change. **Correction to the earlier record:** the repo-wide `uv run ruff format --check .` was claimed clean; it is **not** — two unrelated, pre-existing files would be reformatted (`tests/marimo_inspect/live/test_dependency.py`, `tests/marimo_inspect/test_lifecycle.py`). `uv run ruff check .` *is* clean. Neither file belongs to this agenda and neither was touched. |

## Decisions

- **The boundary is by purpose, not by capability.** A notebook that a live
  test *can* boot is still an example if its purpose is to show a consumer a
  pattern; bootability does not make it a fixture. The smoke gate boots examples
  *as notebooks from a tmp copy* and never imports them — so `examples/` stays
  out of the package's import graph while no longer being outside every gate.
- **Audience: both.** The examples serve a consumer who read the README and
  wants a working starting point, and an agent that needs a canonical snippet.
  Both audiences read the same files; the notes stay in the notebook markdown
  and `examples/README.md`.
- **Growth: stay flat.** `patterns/` remains the only bucket. A second bucket is
  added only when the artifact is a different kind (e.g. an end-to-end recipe
  that needs data), never per pattern.
- **Static is the full notebook check, live is the gate.** `uv run marimo check
  notebooks examples` is the command a contributor runs; the live smoke is what
  actually proves the examples still execute on the pinned marimo. Neither
  covers frontend rendering, which stays a manual browser check.
- **CI is out of scope and absent.** This repo has no CI; the examples gate is
  a local command pair (documented above), not a pipeline. Adding CI — and with
  it wiring `marimo check` and the live tier — is a separate workstream.

## Verification (the closure evidence)

Run on the final tree, 2026-09-13:

```text
uv run marimo check notebooks examples                      -> exit 0
uv run pytest tests/marimo_inspect/live/test_examples.py -m live -> 6 passed
uv run pytest -m live                                       -> 68 passed, 486 deselected
uv run pytest -m "not live"                                 -> 486 passed, 68 deselected
uv run ruff check .                                         -> All checks passed!
uv run ruff format --check .  -> 2 files would be reformatted, 94 files already formatted
                                 (both pre-existing and unrelated to examples/)
```

`tests/marimo_inspect/live/test_examples.py` holds: a discovery guard (an empty
`examples/` tree fails rather than making the smoke vacuous), the parametrized
smoke over the discovered tracked `examples/**/*.py` tree, one interaction
regression per shipped example (step buttons → the one shared `mo.state` index
under repeated advancing counters, backward and clamped, with the slider
re-seeded from the state; cascade parent change → the child rebuilt with the new
options and the result re-derived), and the live verification of the T-E6
per-parent-memory recipe. Each test copies its notebook to `tmp_path` and
asserts the repo file is byte-identical afterwards, so `examples/` is never the
mounted document.

## Resolved log

- 2026-09-12 — `examples/patterns/cascading_sidebar_controls.py` +
  `examples/README.md` created (generic, consumer-free; runs on `marimo` alone).
  Behaviour asserted in its notes was verified against the pinned marimo 0.24.x
  by running the notebook headless and driving the live controls in a browser
  (cascade re-derives the child's options; parent change rebuilds the child;
  the creating-cell `.value` access raises; two `mo.sidebar` calls stack).
- 2026-09-12 — **T-E7 applied.** Adding `examples/` turned the repo-wide
  `uv run ruff check .` red: marimo's generated cell shape (a bare trailing
  expression used as the cell's output, plus the closing `return`) trips `B018`
  and `PLR1711` *by design*, and the hand-aligned trailing comment is not
  `ruff format`-stable. `examples/` is therefore excluded in `[tool.ruff]`,
  matching the existing `notebooks/` rationale. Verified after the change:
  `uv run ruff check .` → all checks passed, `uv run marimo check examples` →
  exit 0.
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
  `step_slider.value` read back tracking every step.
- 2026-09-13 — **T-E1…T-E7 closed.** The contract landed in
  `examples/README.md` and `README.md` §Examples; the full notebook check
  extended to `uv run marimo check notebooks examples`; and the examples moved
  from "nothing boots them" to a real gate. Added
  `tests/marimo_inspect/live/test_examples.py` (6 live tests): discovery guard,
  parametrized smoke over the tracked `examples/**/*.py` tree (tmp copy →
  `notebook_server` → real `run_cell(mode="all")`, `status: ok`, no
  failed/not-run/unverified target), a slider step-button interaction regression
  (the shared index walks 0→1→2→3, back to 0, clamps there, then the coarse
  quarter-axis steps to 11/22/33/44 and clamp at 47, with `step_slider` read
  back equal to `index` at every step), a cascade regression (parent → child
  options/selection/result re-derived; a stale option is rejected by the kernel
  as `value_not_applied`; switching back resets the child, i.e. the example
  ships no per-parent memory), and the T-E6 recipe verified as a test-local
  notebook. Counts and the full command list are in §Verification.
