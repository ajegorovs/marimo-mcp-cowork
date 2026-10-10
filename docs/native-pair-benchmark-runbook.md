# Native-pair capability benchmark — execution record and gates

> **Status:** draft protocol, transport smoke only. No A/B capability result or interface decision. Canonical scope: [native-first convergence agenda](agenda-native-marimo-pair-convergence.md); current gate: [root agenda](../agenda.md).

## Feature and bounded plan

Compare the existing structured MCP facade (marimo 0.24.x) with **core `marimo pair`** (marimo 0.25.1, paired with the marimo-pair v0.0.20 guidance) against byte-identical disposable notebooks. This compares two interfaces and two framework versions, not MCP against code mode. Keep both environments and notebook copies separate; all listeners are loopback, and neither arm may directly edit the notebook file as a shortcut.

| Stage | Acceptance evidence | State |
| --- | --- | --- |
| Baseline | Locked 0.24.x suite, isolated environment, fixture integrity | Complete on Windows in bounded groups; see [live-tests.md](live-tests.md). The test-harness repair is in a separate draft PR. |
| Native transport smoke | Installed pinned CLI, help, listing, explicit file selection, real `help(cm)`, missing-target refusal | Complete on Windows, using a one-cell disposable notebook; details below. |
| Fixture/oracle review | Identical initial bytes, independent metric, executable grader for reactive state **and saved artifact in a distinct fresh kernel** | Static clean fixture and stdlib oracle published under `benchmark/` and tested; fault/slow fixtures and live save/reopen grader remain blocked. |
| A4 capability protocol | [Equal-condition checks](agenda-native-marimo-pair-convergence.md#task-2-build-a-small-fair-comparison-experiment) on both arms; separate version-confound results; raw sanitized payloads and scorecard | Not started. |
| A5 fresh-agent missions | Two independent zero-context agents with identical starting state and external scoring | Not started; requires A4 go decision. |
| Remote/product/release | Separate owner decisions based on comparison | Not authorized by this benchmark. |

## Windows native transport smoke (local, disposable)

An isolated Python 3.12.10 environment outside the checkout installed **marimo 0.25.1** (`uv pip install marimo==0.25.1`). The repo's `.venv`, `pyproject.toml` and `uv.lock` were unchanged. Installed `marimo pair --help` listed `docs`, `execute`, `notebook`, `prompt`; `pair execute --help` exposed `--url`, `--session`, `--file`, `--token-file`, `-c`, `--code-file` and `--stream`.

A generated one-cell notebook was served with `marimo edit <disposable.py> --headless --no-token --no-skew-protection --skip-update-check --host 127.0.0.1 --port <ephemeral>`. The server used an isolated Windows `USERPROFILE`; an `/sse` handshake materialized the session. The **installed CLI**, not the skill's Bash scripts, returned:

| Operation | Observed | Verdict |
| --- | --- | --- |
| `marimo pair notebook list --url <loopback>` | Exit 0; one notebook, one session, no warnings | PASS (transport smoke) |
| `marimo pair execute --url <loopback> --file <disposable.py> -c 'import marimo._code_mode as cm; help(cm)'` | Exit 0, `success: true`, help output present | PASS (runtime introspection) |
| Same `execute` targeting the notebook, with `async with cm.get_context()` and `print(len(ctx.cells))` | Exit 0, `success: true`, stdout `1` | PASS (live structure read) |
| Same `execute` with a nonexistent `--file` | Exit 2, `success: false`, explicit no-running-session error; no code run | PASS (missing-target refusal) |

The probe terminated only its own server process tree. No credentials, tailnet listener or committed fixture was involved. These four results **do not** prove cell mutation, widget reactivity, save/reopen, auth, stale-write safety or native superiority. The v0.0.20 guidance was inspected at a pinned source ref but was **not installed or driven by a fresh agent**.

## Blocking checks before A4

1. Review the published `benchmark/notebooks/mission_clean.py` and `benchmark/oracle.py`: the stdlib oracle computes the gain-2/gain-4 reference independently and refuses aliased or byte-different arm copies. `tests/marimo_inspect/test_benchmark_oracle.py` passed (2 cases), and `marimo check benchmark/notebooks` passed in the isolated 0.24.x environment. The fault/slow variants remain ignored local research, not published benchmark inputs; add them only when their role is decided. The static pass is **not** a runtime or durability claim.
2. Make the live grader verify the **actual notebook path bound to each session**, distinguish runtime and saved-file state, and require a newly started server/kernel for reopen. The current scratch grader cannot establish that a session was freshly reopened just because a filename argument exists.
3. Verify the documented save route for each arm independently. A headless live edit may not autosave; inability to persist is a scored limitation, not permission to directly patch the `.py` notebook.
4. Run the identical A4 probe sequence against A and B; classify `PASS`, `FAIL`, `UNSUPPORTED`, `UNVERIFIED`, or `SETUP_BLOCKED`. A wrong-target write or false-success payload blocks the next stage regardless of speed.
5. Only after those checks, decide whether separate fresh-agent missions are justified. Remote topology and any product changes require their own approval.

## Review contract

Keep this page to the current experiment gate and measured smoke. Put raw private transcripts under ignored scratch storage and publish only sanitized, reproducible evidence in the PR. Move a completed gate out of the root agenda instead of accumulating historical updates there. No dependency upgrade, migration, release, or merge is implied by this draft.
