# Native-pair capability benchmark — historical evidence and reproduction

[Issue #5](https://github.com/ajegorovs/marimo-mcp-cowork/issues/5) owns the current plan, checkpoint, safety gate, remaining work and authorization. This runbook retains bounded historical measurements and reproduction instructions; commands below are not current task selection. See the [comparison design](agenda-native-marimo-pair-convergence.md) for rationale and arms, and [live-test ledger](live-tests.md) for suite results.

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

## Fixture and runtime gate evidence

1. Review the published `benchmark/notebooks/mission_clean.py` and `benchmark/oracle.py`: the stdlib oracle computes the gain-2/gain-4 reference independently and refuses aliased or byte-different arm copies. `tests/marimo_inspect/test_benchmark_oracle.py` passed (2 cases), and `marimo check benchmark/notebooks` passed in the isolated 0.24.x environment. The fault/slow variants remain ignored local research, not published benchmark inputs; add them only when their role is decided. The static pass is **not** a runtime or durability claim.
2. The wrapper-arm live gate (`tests/marimo_inspect/live/test_benchmark_fixture.py`) ran on a disposable notebook copy under the locked 0.24.x environment: the real handlers executed all five original cells; a metric cell yielded 1.25 at gain 2 and 2.5 after a verified slider update; the copy contained the metric; and a **different server PID and session**, with its census path checked against that same copy, reran it at default 1.25 without structured errors. The repo fixture remained byte-identical. This proves one wrapper workflow, not a complete A4 comparison or frontend rendering.
3. Native 0.25.1 runtime probe: after installing the same `numpy==2.5.3` and `matplotlib==3.11.1` as the locked baseline into the isolated native environment, a loopback headless editor opened a byte-identical disposable fixture copy. The **installed core `marimo pair execute` CLI** (no v0.0.20 shell scripts) read five cells, ran them, created a visible metric cell with `hide_code=False`, and observed `normalized` **1.25** at gain 2. `ctx.set_ui_value` changed the slider to 4; separate live readback observed gain 4 and metric **2.5**. The edited copy then contained the metric source; after stopping the first server, a new server PID and session on that same bound path reran the saved copy at gain 2 and metric **1.25**. This first local scratch run exited 0 with `NATIVE_GATE_PASS`; the canonical fixture bytes stayed unchanged. The portable `benchmark/native_gate.py` then repeated it with exact metrics computed by `benchmark/oracle.py`, verified the reopened census path and new server PID/session, and found six reopened cells with zero cell errors (exit 0, `NATIVE_GATE_PASS`). Run with an isolated Python 3.12 environment containing `marimo==0.25.1`, `numpy==2.5.3`, `matplotlib==3.11.1` and `psutil`: `python benchmark/native_gate.py`. The driver uses a temporary directory, loopback listener and only core `marimo pair execute`. No generalized error-channel, stale-write or structural-refusal verdict follows from this gate.
4. Run the identical A4 probe sequence against A and B; classify `PASS`, `FAIL`, `UNSUPPORTED`, `UNVERIFIED`, or `SETUP_BLOCKED`. A wrong-target write or false-success payload blocks the next stage regardless of speed.
5. Only after those checks, decide whether separate fresh-agent missions are justified. Remote topology and any product changes require their own approval.

## A4 first safety checkpoint (Windows, disposable copies)

Run the wrapper side with the isolated locked environment: `uv run --no-sync pytest tests/marimo_inspect/live/test_benchmark_fixture.py tests/marimo_inspect/live/test_mutation.py::test_external_conflict_reread_recover -m live -q` (4 passed). Run native with an isolated marimo 0.25.1 interpreter: `python benchmark/native_safety_gate.py` (`NATIVE_SAFETY_PASS`); run `python benchmark/native_stale_probe.py` separately for the independent-actor stale row. Set `MARIMO_BENCHMARK_SCRATCH` to a private scratch directory when desired. Both scripts boot a loopback editor on a byte-identical copy, check exact session-to-copy binding and shut it down.

| Probe | Wrapper 0.24.x | Native 0.25.1 | Scope of assertion |
| --- | --- | --- | --- |
| Unknown cell edit | Structured `unknown_cell_ids`, `operation_ran=false`, `state_changed=false` | CLI exit 1, `success=false`, `KeyError` | Live cell ID/source census and saved copy unchanged. |
| Duplicate `input_vector` definition | Refused via raw `Execution failed` envelope, not structured `status/reason` | CLI exit 1, `success=false`, compile-time multiply-defined error | Original five-cell census and saved copy unchanged. Wrapper must instantiate/run the original notebook before probing the graph; otherwise a duplicate can be created before the original definitions register. |
| Concurrent stale edit | Existing real-kernel wrapper regression `test_external_conflict_reread_recover` passes; tool reports `conflict` and preserves external source | `benchmark/native_stale_probe.py`: CLI read → independent `POST /api/document/transaction` `set-code` → guarded CLI edit exits 1 with `StaleCellError`; independent actor's source and saved file remain unchanged after refusal | The read tracker is kernel-global, so a second CLI code-mode write cannot model a second actor; a frontend document transaction does. |

This is a safety **checkpoint**, not the complete A4 matrix. The equal-source raising-cell probe populates both structured runtime errors and console traceback on each arm (`test_benchmark_error_probe.py`, `benchmark/native_error_probe.py`). Native combined-selector targeting is an observed blocker: one directory server held sessions A and B on separate disposable copies; `pair execute --file B --session A` returned exit 0 / `success=true`, created `a4_wrong_target_marker` in A's live map and saved A's file, while B's map/file and the committed fixture remained unchanged. `benchmark/native_target_probe.py` tests the read-only precedence; `benchmark/native_wrong_write_probe.py` returns exit **4** with `NATIVE_WRONG_WRITE_FAIL` on this observed unsafe behavior. The CLI documents `--session` precedence, so this is not a claim that the CLI violated its documented selection rule; it is a fail-closed **combined-selector safety** failure for an agent that presents both identifiers and assumes they are cross-checked. Do not run A5, and do not treat a successful CLI envelope as proof the file selector was honored. Mitigation (for example, enforce a single selector or preflight and compare census binding) needs explicit design and a separate verified gate, not a silent benchmark adjustment.

The wrapper's explicit `(server_url, session_id)` wrong-server refusal was separately verified by `test_benchmark_target_probe.py`: two disposable servers, B's real session ID sent to A's URL, structured `session_not_found` and no change in either copy. This is not the native **same-server, two-notebook combined-selector** case; the wrapper has no `file` selector. For a bounded native slow-cell probe, `benchmark/native_timeout_probe.py` killed one `pair execute --stream` after 5 seconds of an 8-second cell, without retry. A separate read-only inspection observed the saved cell, START global set, status `interrupted`, DONE unset (`NATIVE_TIMEOUT_PROBE_STARTED_ABORTED`, about 18 seconds). Thus the client timeout did **not** mean the mutation never ran; other disconnect outcomes remain unverified. Both pinned arms also refused a **two-cell cycle queued in one code-mode context** before applying any graph or disk change (`test_benchmark_cycle_probe.py` and `benchmark/native_cycle_probe.py`). The wrapper raw `client.execute` returned `status=error` and `Cycles detected` in stderr; native CLI returned exit 1 / `success=false` with the same structural refusal. This checks transaction atomicity through raw code mode, **not** a two-call MCP `create_cell` sequence. The wrapper's **separate** 0.24.x timeout probe (`test_benchmark_timeout_probe.py`) ran one 40-second cell via `run_cell` and observed `httpx2.ReadTimeout` at 30.1 seconds, then read back after a 15-second settle: source saved, START set, DONE unset, cell `interrupted` (`STARTED_ABORTED`, one measured case). This agrees with the native warning that timeout is an unknown outcome until readback; it does **not** guarantee interruption for all code or failures. The native reconnect probe (`benchmark/native_reconnect_probe.py`) restarted a disposable notebook in a new PID/session: the old ID was refused with exit 2 / `success=false` / `The session is stale.`, while `--file` alone re-resolved the fresh session and read gain 2 and processed `[4, 8, 12, 16]` without file changes. It covers an unknown old ID, not a reused ID rebound to a different notebook. Authorization has a separate native-gated probe below; the dependency comparison remains version/extras-confounded. The wrapper and native arms also run different marimo versions, so an interface-only conclusion remains confounded.

## A4 authorization and installation inventory (auth-gated native probe; footprint confounded)

The initial benchmark's native loopback servers explicitly disabled authentication and skew protection (`--no-token --no-skew-protection`), while the wrapper live harness disabled auth but retained skew. A separate `benchmark/native_auth_probe.py` used **only generated throwaway credentials** outside the checkout and an auth-enabled native 0.25.1 loopback server with default skew ON. Authenticated `/sse` materialized the disposable session; native execute with no token and with an incorrect `--token-file` each exited 2 / `success=false` with an authentication refusal and no kernel output. A valid `--token-file` plus file-only selection exited 0 / `success=true`, ran read-only code in the expected session, and left both disposable copy and repository fixture unchanged (`NATIVE_AUTH_PROBE_PASS`). The probe does not test token rotation, remote exposure, or wrapper authenticated success (the wrapper has no Authorization-forwarding path).

The wrapper sends `Marimo-Session-Id` on execution and obtains/sends the separate `Marimo-Server-Token` for protected lifecycle POSTs (`src/marimo_inspection/client.py`); it does not forward an `Authorization` header. The installed native 0.25.1 pair client has Bearer-token handling for execute and session listing, loading a token from `--token-file` or `MARIMO_TOKEN`. The native execute POST is exempt from the server's skew middleware; the auth probe measures successful execution with skew ON but not a skew-required lifecycle POST.

The first two benchmark environments contained **142** distributions for the locked wrapper project (marimo 0.24.0, test extras and `marimo[recommended]`) and **35** for native 0.25.1 plus numpy/matplotlib (base install, no recommended or test extras); that comparison remains **invalid as wrapper overhead**. A separate **same-version, same-common-roots installation measurement** used fresh outside-checkout `uv` environments on Windows/Python 3.12.10. F1 installed `marimo[recommended]==0.24.0`, numpy 2.5.3, matplotlib 3.11.1, anywidget 0.11.0, traitlets 5.16.1 and pillow 12.3.0; F2 installed this repo's base package (no test extra) with marimo 0.24.0 and matplotlib 3.11.1 pinned. Parent readback found F1 **97** distributions / **651,377,977** site-packages bytes, F2 **136** / **686,358,416** bytes: **39** additional distributions and **34,980,439** bytes for this exact base-wrapper recipe. The 97 common distributions had identical versions, with none F1-only. This measures installation closure, not import latency, kernel memory or the 0.25.1 native-versus-0.24 wrapper runtime decision. The shipped wrapper's `<0.25` constraint prevents a same-version 0.25.1 wrapper comparison without a hypothetical unverified dependency change. The lock contains 147 cross-platform entries, five absent from the earlier locked Windows materialization.

For reproduction outside the checkout, create scratch venvs with `uv venv --python 3.12 <scratch>/F1` and likewise `F2`; run `uv pip install --python <scratch>/F1/Scripts/python.exe "marimo[recommended]==0.24.0" numpy==2.5.3 matplotlib==3.11.1 anywidget==0.11.0 traitlets==5.16.1 pillow==12.3.0`, then from this repo run `uv pip install --python <scratch>/F2/Scripts/python.exe . "marimo==0.24.0" "matplotlib==3.11.1"`. Count `*.dist-info` directories under each `Lib/site-packages` and sum file sizes recursively there; compare each common distribution's version via `importlib.metadata.distributions(path=[...])`. Do not install into the checkout `.venv`.

## Target-selection mitigation gate (bounded; A5 still held)

The approved **benchmark-only** `benchmark/target_selection.py` refuses *both* selectors (even an agreeing pair), neither selector, blank values and non-string values **before** constructing a CLI call. Its 12 unit cases pass. `benchmark/native_target_guard_probe.py` exercised the adapter on one loopback server with A/B disposable sessions: mismatched B+A, missing-file+A, agreeing A+A and blank+A caused zero CLI subprocesses and no notebook changes. File-only B produced a marker in B's live graph **and saved copy**, not A; session-only A read succeeded, and missing file-only returned exit 2 / `success=false`. This gate proves only calls routed through the adapter under stable bindings, **not** a fix to upstream's CLI or an atomic file/session binding.

`benchmark/native_churn_probe.py` then observed a **remaining same-server hazard** without mutating notebook content. A new `/sse` connection for copy A rotated its old ID to a fresh ID; the old ID initially failed native execution as stale. Later presenting that *old A ID* in an `/sse` connection to orphaned copy B caused the server to bind the ID to **B**; a subsequent native **read-only** `--session <old A ID>` succeeded and read B's kernel. Both disposable copies remained unchanged. This proves session-ID reuse can change the target's meaning in this controlled sequence. It does **not** demonstrate a wrong write through the adapter or a file-only resolve→execute race, but the CLI's two-request file resolution remains non-atomic and the adapter cannot prevent a reused session-only ID from targeting a different notebook. Thus **A4 remains no-go and A5 remains on hold**. A robust target contract must address session binding and churn *before* a mutating call, not merely forbid two selectors; an after-write check detects harm but cannot prevent it. No product or upstream change is authorized by this gate.

### In-kernel expected-file experiment (code-mode only; not A5 clearance)

Marimo 0.25.1 exposes no atomic expected-file field on `/api/kernel/execute`: the server routes by mutable session ID. `benchmark/native_in_kernel_guard_probe.py` tested a **private-API**, same-command precondition instead: before queuing any cell operation inside `async with cm.get_context()`, it compares `ctx._kernel.app_metadata.filename` with the intended disposable path and raises on mismatch. With two sessions, a deliberately misrouted `--session A` expecting B returned exit 1 / `success=false` and left both graphs and files unchanged. Correct `--session B` wrote only B, live and saved. After rebinding old A's ID to B, a guarded command expecting A again refused without an additional B mutation. Parent rerun: `NATIVE_IN_KERNEL_GUARD_PASS`.

This check protects **only the guarded code-mode operation in that command**, not the routing endpoint itself or an agent invoking raw CLI code without the check. Filename is a mutable save-path string (`None` for untitled notebooks), not immutable notebook identity. The guard must be the first operation in the context; queued package operations and UI/cell operations also need deliberate coverage, and HTTP lifecycle operations such as restart cannot be guarded in-kernel. A later filename rename or unguarded request remains outside the tested contract. This is defense-in-depth evidence, **not** a verified general native safety contract or permission to start A5.

## Mitigation decision (partially tested; residual blocker)

- **Single-selector adapter:** refuse calls supplying both `--file` and `--session` before invoking the CLI. This removes the mismatched-pair path; file-only resolution can still race a session reload, so stale-id refusal and post-call binding should be tested.
- **Census preflight:** require the selected session ID to map to the supplied file in `/api/sessions` before executing. The census read and execute are separate requests with no shared epoch/file token; it is **not** an atomic guarantee under session churn.
- **Post-write readback:** detects a wrong destination after mutation; **not prevention** and not sufficient to lift the A5 hold alone.

The owner approved evaluating the single-selector adapter; its stable-session mismatch gate passed, but the observed ID reuse leaves the larger fail-closed target contract unresolved. Do not lift A5 for this partial mitigation. A future binding/churn design needs separate owner review and a verified no-wrong-write gate. No product or upstream CLI change is authorized by this experiment.

## Evidence-hardening rerun (2026-10-16)

The stale probe now uses always-on setup/verdict checks and rejects non-JSON CLI
stdout rather than synthesizing a structured refusal. Real disposable runs in a
fresh isolated Python 3.12.10 / marimo 0.25.1 environment passed both
`python benchmark/native_stale_probe.py` and
`python -O benchmark/native_stale_probe.py`: exit 1 / `success=false` for the
attempted edit, `StaleCellError` present, generic `KeyError` absent, then
`NATIVE_STALE_PROBE_PASS` after live/disk/canonical integrity checks.

Churn capture now retains raw subprocess stderr as well as stdout, and checks
both plus carried payload stdout/stderr/output for the sentinel while preserving
structured stale-refusal requirements. Kernel-free regressions pass, but **both
normal and optimized real churn reruns were blocked before reaching that check**:
rotation did not settle to orphaned within the bounded wait. The first attempts also exposed cleanup running before server teardown on
exception, so a locked marimo log raised Windows `PermissionError`, masking the
structured blocker. A kernel-free failure-path regression reproduced that ordering;
cleanup now stops the server before removing the temporary tree on failure as
well as success. The post-fix normal rerun returned exit 3 with
`NATIVE_CHURN_PROBE_BLOCKER: rotation did not settle to orphaned`, without the
cleanup traceback.
No new `NATIVE_CHURN_PROBE_PASS` or successful live channel-validation result is
claimed. Historical churn observations above remain historical; this rerun does
not invalidate them or establish a product regression's cause.

The fresh native environment pinned marimo 0.25.1, numpy 2.5.3 and matplotlib
3.11.1; transitive dependencies were newly resolved, not proven identical to the
historical environment. The checkout environment, lock and committed fixture were
not changed. Suite counts belong in [live-tests.md](live-tests.md); current work
and the blocked rerun's next action belong in issue #5. A4/A5 are not cleared.

## Churn blocker diagnosis (2026-10-16)

The live churn rerun's `rotation did not settle to orphaned` blocker was diagnosed
with a bounded, diagnostic-only probe (`benchmark/native_sse_disconnect_probe.py`,
scratch instrumentation under `benchmark/diagnostics/sse_lifecycle/`, criteria in
`benchmark/evidence/native_sse_disconnect_20261016.json`).

Finding: closing a **resumed** SSE response immediately after the replayed
`kernel-ready` event leaves the consumer attached, so the bounded orphan wait
cannot settle. Draining one `: keep-alive` event before closing lets teardown
complete. The discriminator is not the rename: a first stream on the file and a
post-orphan new-id stream without a booted kernel both tore down at
`kernel-ready`, and a native file-targeted `pair execute` retains no consumer by
itself. Instrumentation showed the response finishing while the handler's
iterator stayed suspended at a `yield` with its session still attached, its
disconnect hook unrun and its heartbeat task pending; `SSESessionHandler.stream()`
returns only when its queue is signalled, and the disconnect hook runs from the
response generator's `finally`.

This is a **diagnosis, not a clearance**. The canonical churn benchmark is
unchanged and still does not reach its stale-channel check; no live churn PASS,
A4 clearance or A5 mission is claimed. No marimo or product code was patched. Why
the booted-resumed case discriminates from the no-boot case is not explained, no
dependency bisection was performed, and the heartbeat-drain control is a
diagnostic control rather than a substitute transport.

## Review contract

Keep this page to historical measurements, their limits and reproduction instructions; maintain current execution state in issue #5. Put raw private transcripts under ignored scratch storage and publish only sanitized, reproducible evidence in the PR. The root agenda is only a compatibility pointer to the work index. No dependency upgrade, migration, release, or merge is implied by this draft.
