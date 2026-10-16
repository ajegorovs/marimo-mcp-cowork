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