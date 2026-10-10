"""Bounded hermetic probe: an explicit session id must not reach another server.

Companion to ``test_benchmark_fixture.py`` and ``test_benchmark_error_probe.py``.
Two disposable servers (A and B) are booted on distinct copies with distinct
sources, each bound to its own single session. The probe then calls the REAL
``edit_cell`` handler with **B's session id and A's server URL** — an explicit,
deliberately mismatched target.

The wrapper resolves both arguments verbatim (no guessing), builds a client for
A, and resolves B's id against A's live census. A legitimately does not report
B's session, so the call must be a structured ``session_not_found`` refusal that
neither ran an operation nor changed state — and neither disposable copy may be
touched. The two bindings are re-verified afterwards so the refusal cannot have
secretly rebound or rewritten either server.

This is the wrong-*server* half of the explicit-target contract. A combined
file/session mismatch (the wrapper exposes no notebook-file argument, so a cell
id that is valid on B but absent on A is never even reached here) is deliberately
**not** characterized: the surface does not expose it.
"""

from pathlib import Path

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.mutation import edit_cell


def _notebook(marker: str) -> str:
    """A minimal, VALID marimo document whose one cell carries ``marker``.

    marimo rejects a bare ``x = 1`` script, so the source must be a real
    ``marimo.App`` document. The distinct marker keeps each disposable copy
    byte-identifiable: a leak from one server into the other shows on disk.
    """
    return (
        "import marimo\n\n"
        '__generated_with = "0.24.0"\n'
        'app = marimo.App(width="full")\n\n\n'
        "@app.cell\n"
        "def _():\n"
        f'    value = "{marker}"\n'
        "    value\n"
        "    return (value,)\n\n\n"
        'if __name__ == "__main__":\n'
        "    app.run()\n"
    )


#: Distinct sources so each disposable copy is byte-identifiable.
SOURCE_A = _notebook("server-a")
SOURCE_B = _notebook("server-b")
INTRUDER = 'value = "INTRUDER"'


async def _assert_bound(server_url: str, session_id: str, notebook: Path) -> None:
    """Assert one server reports exactly this one session, on this file."""
    client = MarimoClient(server_url)
    try:
        sessions = await client.list_sessions()
        assert len(sessions) == 1, sessions
        assert sessions[0].session_id == session_id, sessions
        assert sessions[0].file is not None, sessions
        assert Path(sessions[0].file).resolve() == notebook.resolve(), sessions
    finally:
        await client.close()


@pytest.mark.live
async def test_wrong_server_explicit_session_is_refused_hermetically(notebook_server):
    """B's session id against A's server URL is a no-op ``session_not_found``."""
    _manager_a, url_a, sid_a, copy_a = await notebook_server(
        SOURCE_A, name="probe_a.py"
    )
    _manager_b, url_b, sid_b, copy_b = await notebook_server(
        SOURCE_B, name="probe_b.py"
    )

    # Precondition: two distinct servers, two distinct sessions, each bound to
    # its own file. Without this the mismatch probe proves nothing.
    assert url_a != url_b, (url_a, url_b)
    assert sid_a != sid_b, (sid_a, sid_b)
    await _assert_bound(url_a, sid_a, copy_a)
    await _assert_bound(url_b, sid_b, copy_b)

    disk_a = copy_a.read_bytes()
    disk_b = copy_b.read_bytes()
    assert disk_a != disk_b, "disposable copies must be byte-distinct"

    # Pick a REAL cell on B: the refusal must come from the wrong *server*, not
    # from an unknown cell id.
    b_data = await get_cell_data(session_id=sid_b, server_url=url_b)
    b_cell_id = b_data["data"][0]["cell_id"]

    # B's session id, A's server URL — the deliberate cross-wire.
    result = await edit_cell(b_cell_id, INTRUDER, session_id=sid_b, server_url=url_a)

    # --- Structured, fail-closed refusal: nothing ran, nothing changed ---------
    assert result["status"] == "error", result
    assert result["reason"] == "session_not_found", result
    assert result["target_resolved"] is False, result
    assert result["operation_ran"] is False, result
    assert result["state_changed"] is False, result
    # The request is echoed verbatim — the wrapper never silently repairs it.
    assert result["session_id"] == sid_b, result
    assert result["server_url"] == url_a, result
    # A's census WAS read (it is the server that was resolved), so the absence is
    # a real miss and A's true single session is reported.
    assert result["available_sessions_readable"] is True, result
    assert result["available_sessions"] == [
        {"server_url": url_a, "session_id": sid_a}
    ], result

    # --- Neither disposable copy changed on disk -------------------------------
    assert copy_a.read_bytes() == disk_a, "wrong-server refusal altered copy A"
    assert copy_b.read_bytes() == disk_b, "wrong-server refusal altered copy B"
    assert copy_a.read_bytes() == SOURCE_A.encode(), copy_a.read_text()
    assert copy_b.read_bytes() == SOURCE_B.encode(), copy_b.read_text()

    # --- B's live cell is untouched (no cross-server or phantom write) ----------
    b_after = await get_cell_data(session_id=sid_b, server_url=url_b)
    b_row = next(r for r in b_after["data"] if r["cell_id"] == b_cell_id)
    assert "server-b" in b_row["code"], b_row
    assert INTRUDER not in b_row["code"], b_row

    # --- Both bindings re-verified: no secret rebind, both servers intact ------
    await _assert_bound(url_a, sid_a, copy_a)
    await _assert_bound(url_b, sid_b, copy_b)
