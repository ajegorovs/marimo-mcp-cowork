"""Opt-in observation hooks for a DISPOSABLE native SSE server only.

Add this directory to ``PYTHONPATH`` when running
``benchmark/native_sse_disconnect_probe.py`` so the server subprocess imports it
at startup. Only ``marimo edit`` subprocesses are instrumented, and every hook
delegates to the original implementation: this adds observation, it does not
replace or repair upstream teardown.

Diagnostic output carries disposable session IDs and object identities. Treat it
as private run output, not publishable evidence. This is not a product patch and
must not be shipped to a real deployment.
"""

from __future__ import annotations

import asyncio
import json
import sys

if "edit" in sys.argv:
    from marimo._server.api.endpoints.ws.session_handler import SessionHandler
    from starlette.responses import StreamingResponse

    def _trace(event: str, **fields: object) -> None:
        print("CHURN_TRACE", json.dumps({"event": event, **fields}), flush=True)

    _original_disconnect = SessionHandler._on_disconnect
    _original_attach = SessionHandler.on_attach
    _original_response = StreamingResponse.__call__

    def _on_disconnect(self) -> None:
        _trace(
            "disconnect",
            sid=str(self.params.session_id),
            attached=self._session is not None,
            status=str(self.status),
        )
        return _original_disconnect(self)

    def _on_attach(self, session, event_bus) -> None:
        _trace("attach", sid=str(self.params.session_id), handler=id(self))
        return _original_attach(self, session, event_bus)

    async def _response(self, scope, receive, send):
        if scope.get("path") != "/sse":
            return await _original_response(self, scope, receive, send)
        _trace("response_start", asgi=scope.get("asgi"))
        try:
            return await _original_response(self, scope, receive, send)
        except BaseException as error:
            _trace("response_error", kind=type(error).__name__)
            raise
        finally:
            _trace("response_finished")
            iterator = self.body_iterator
            frame = getattr(iterator, "ag_frame", None)
            handler = frame.f_locals.get("self") if frame else None
            _trace(
                "iterator_after_response",
                frame_line=frame.f_lineno if frame else None,
                running=getattr(iterator, "ag_running", None),
                stream_finished=getattr(handler, "_stream_finished", None),
                attached=getattr(handler, "_session", None) is not None,
            )
            for task in asyncio.all_tasks():
                name = getattr(task.get_coro(), "__qualname__", "")
                if any(term in name for term in ("SSE", "Session", "disconnect")):
                    _trace("task", name=name, done=task.done())

    SessionHandler._on_disconnect = _on_disconnect
    SessionHandler.on_attach = _on_attach
    StreamingResponse.__call__ = _response
