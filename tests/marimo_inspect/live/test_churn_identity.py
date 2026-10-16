"""Hermetic live regression: real same-server session-id churn vs the anchor.

Issue #7 anchors a bound call to the notebook **path** recorded at bind time,
because a session id is not a durable handle: on ONE marimo server a stale id
can be handed to a *different* notebook's kernel (see
``benchmark/wrapper_churn_probe.py`` and ``docs/native-pair-benchmark-runbook.md``).
The kernel-free ``tests/marimo_inspect/test_notebook_binding.py`` pins the
resolver in isolation; this module is the missing **integrated** regression: it
performs the *real* churn on a live server and then drives the *real* bound
handler path (``create_cell`` / ``restart_kernel`` with ``ctx=``), asserting on
on-disk bytes and kernel process identity — never on a tool's self-report.

The churn (measured, not modelled — see the probe's own contract):

1. two sessions on ONE server: ``materialize_session(id_a, copy_a)`` and
   ``materialize_session(id_b, copy_b)``;
2. a second ``/sse`` on ``copy_a`` ROTATES A's id — the bound id leaves the
   census and ``copy_a`` is served under a new id;
3. ``materialize_session(id_a, copy_b)`` then ALIASES the stale bound id onto
   ``copy_b`` (rotating ``copy_b``'s original id away), so a caller holding the
   stale id now reaches ``copy_b``'s kernel.

Three cases, each on disposable byte-identical copies of
``notebooks/test_marimo.py`` (the mutation suite's fixture) in a ``tmp_path``
workspace served by ONE isolated headless server:

1. **reuse refused** — after the full churn the argument-less bound
   ``create_cell`` and ``restart_kernel`` both refuse ``notebook_mismatch``:
   ``copy_b``'s bytes are unchanged and its kernel process id is the same
   before and after (the restart never ran);
2. **rotation without reuse repaired** — after an id-only rotation the
   argument-less ``create_cell`` succeeds against the bound notebook
   (``notebook_path`` names ``copy_a``), the created cell is genuinely present
   in ``copy_a``'s kernel and file, ``copy_b`` is untouched, and the cell is
   deleted again with an explicit target;
3. **URL alias of the same server** — restating the bound id together with the
   SAME port spelled ``http://localhost:PORT`` (a genuinely different URL string
   for one live server, verified to reach that server first) must still be
   refused ``notebook_mismatch`` rather than dispatched to ``copy_b``. This
   exercises a fix landed concurrently with this test; until it lands the case
   fails with a clear "URL alias bypassed the anchor" message and is not worked
   around here.

Real kernels only — there are no mocks in this file. Teardown stops the server
and re-checks that ``notebooks/test_marimo.py`` is byte-identical to what it was
at boot, so no repo fixture changes. The module skips when marimo is absent and
is deselected by default via the ``live`` marker.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
from conftest import NOTEBOOK_PATH, MarimoServerManager

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.cells import get_cell_data, get_cell_map
from marimo_inspection.tools.lifecycle import restart_kernel
from marimo_inspection.tools.mutation import create_cell, delete_cell
from marimo_inspection.tools.session import bind_active_session, reset_fallback_state

# The live suite's real-kernel runtime: the server is booted as
# `python -m marimo`. When marimo is not installed the whole module is skipped
# (it is also deselected by `-m "not live"` regardless).
pytest.importorskip("marimo")

#: Unique marker per case; none of them may appear in the repo fixture.
_MARKER_1 = "churn_identity_c1_marker"
_MARKER_2 = "churn_identity_c2_marker"
_MARKER_3 = "churn_identity_c3_marker"

#: A scratchpad snippet reporting the *kernel* process id. Two different values
#: across a restart prove a new kernel; an unchanged value proves none ran.
_PID_PROBE = "import os\nprint(f'KERNEL_PID={os.getpid()}')"


def _norm(path: str | os.PathLike[str] | None) -> str:
    """Normalize a notebook path for comparison (case/separator insensitive)."""
    return os.path.normcase(os.path.abspath(str(path))) if path else ""


async def _census(client: MarimoClient) -> dict[str, str]:
    """Map session id -> normalized notebook path, via the client's own view."""
    return {s.session_id: _norm(s.file) for s in await client.list_sessions()}


def _id_for_path(census: dict[str, str], path: str | Path) -> str | None:
    """Current session id serving *path*, or None (ids move across the churn)."""
    wanted = _norm(path)
    for session_id, served in census.items():
        if served == wanted:
            return session_id
    return None


async def _wait_for(client: MarimoClient, predicate, timeout: float = 45.0):
    """Poll the census until ``predicate`` holds; return the last census read."""
    deadline = time.monotonic() + timeout
    latest: dict[str, str] = {}
    while time.monotonic() < deadline:
        latest = await _census(client)
        if predicate(latest):
            return latest
        await asyncio.sleep(0.25)
    return latest


async def _kernel_pid(client: MarimoClient, session_id: str) -> int:
    """The kernel process id, read back through the live scratchpad."""
    probe = await client.execute(session_id, _PID_PROBE)
    assert probe.status == "ok", probe.stderr
    text = "".join(probe.stdout)
    assert "KERNEL_PID=" in text, text
    return int(text.split("KERNEL_PID=", 1)[1].split()[0])


async def _churn_reuse_stale_id(
    client: MarimoClient, copy_a: Path, copy_b: Path, bound_id: str
) -> dict[str, str]:
    """Perform the real churn so ``bound_id`` (bound to ``copy_a``) serves ``copy_b``.

    Rotates ``copy_a``'s id through a second ``/sse``, then aliases the stale
    ``bound_id`` onto ``copy_b``. Returns the census after the alias. Raises
    (with the exact census) rather than fabricating a pass when marimo does not
    reproduce the churn in-process.
    """
    rotated_id = str(uuid.uuid4())
    assert await client.materialize_session(rotated_id, str(copy_a)), (
        "rotation /sse did not reach kernel-ready"
    )
    census = await _wait_for(client, lambda c: c.get(rotated_id) == _norm(copy_a))
    assert bound_id not in census, f"rotation left the bound id in the census: {census}"
    assert census.get(rotated_id) == _norm(copy_a), (
        f"rotation did not re-key copy_a to the new id: {census}"
    )

    assert await client.materialize_session(bound_id, str(copy_b)), (
        "cross /sse did not reach kernel-ready"
    )
    census = await _wait_for(client, lambda c: c.get(bound_id) == _norm(copy_b))
    assert census.get(bound_id) == _norm(copy_b), (
        f"stale bound id was not aliased onto copy_b: {census}"
    )
    return census


class _BoundCtx:
    """A minimal issue-#7 binding context for an argument-less live call.

    Stands in for fastmcp's server-side state so the real handler can be driven
    with a bound id + url + notebook path.
    """

    def __init__(self) -> None:
        self._state: dict[str, object] = {}
        self.infos: list[str] = []
        self.transport = "stdio"
        self.session_id = "mcp-churn-live-1"

    async def set_state(self, key, value, serializable=True) -> None:
        self._state[key] = value

    async def get_state(self, key):
        return self._state.get(key)

    async def info(self, message) -> None:
        self.infos.append(message)


@pytest.fixture(autouse=True)
def _isolated_fallback():
    """Keep the process-global fallback from leaking between live tests."""
    reset_fallback_state()
    yield
    reset_fallback_state()


@pytest.fixture(scope="function")
async def churn_server(tmp_path):
    """ONE isolated server on a ``tmp_path`` workspace of two byte-identical copies.

    marimo serves a *directory* with multiple notebooks, which is what the
    churn needs: two disposable copies
    (:attr:`NOTEBOOK_PATH` == ``notebooks/test_marimo.py``, the mutation
    suite's fixture) under one server. Teardown stops the server and re-checks
    the repo fixture is byte-identical, so nothing committed is touched.
    """
    original_bytes = NOTEBOOK_PATH.read_bytes()
    workspace = tmp_path / "churn-ws"
    workspace.mkdir()
    copy_a = workspace / "churn_identity_a.py"
    copy_b = workspace / "churn_identity_b.py"
    copy_a.write_bytes(original_bytes)
    copy_b.write_bytes(original_bytes)

    manager = MarimoServerManager()
    try:
        # Booted on the *directory*: sessions are materialized per file below.
        await manager.start(str(workspace))
        yield manager, manager.server_url, copy_a, copy_b
    except Exception:
        manager.dump_logs()
        raise
    finally:
        await manager.stop()

    # Only reached when setup + the test body succeeded (see the other factories).
    assert NOTEBOOK_PATH.read_bytes() == original_bytes, (
        "Hermeticity violation: notebooks/test_marimo.py changed under the "
        "churn-identity suite. Run `git status` and restore it."
    )


@pytest.mark.live
async def test_reused_bound_id_refuses_create_and_restart(churn_server):
    """Case 1: the bound id now serving copy_b refuses both handlers, nothing runs.

    The argument-less ``create_cell`` and ``restart_kernel`` must both refuse
    ``notebook_mismatch``; ``copy_b``'s bytes are unchanged and its kernel
    process is the SAME before and after (a restart would have produced a new
    pid, so the pid is the identity check the refusals cannot fake).
    """
    _manager, server_url, copy_a, copy_b = churn_server
    fixture_bytes = NOTEBOOK_PATH.read_bytes()
    client = MarimoClient(server_url)
    try:
        # ── two sessions on one server, each on its own copy.
        bound_id, other_id = str(uuid.uuid4()), str(uuid.uuid4())
        assert await client.materialize_session(bound_id, str(copy_a))
        assert await client.materialize_session(other_id, str(copy_b))
        census = await _wait_for(
            client,
            lambda c: (
                len(c) == 2
                and c.get(bound_id) == _norm(copy_a)
                and c.get(other_id) == _norm(copy_b)
            ),
        )
        assert census.get(bound_id) == _norm(copy_a), census
        assert census.get(other_id) == _norm(copy_b), census

        # ── bind to copy_a, then perform the REAL churn.
        ctx = _BoundCtx()
        await bind_active_session(
            bound_id, ctx, server_url=server_url, notebook_path=str(copy_a)
        )
        census = await _churn_reuse_stale_id(client, copy_a, copy_b, bound_id)
        assert census.get(bound_id) == _norm(copy_b), census

        current_b = _id_for_path(census, copy_b)
        assert current_b is not None, census
        # The stale bound id genuinely reaches copy_b's kernel: the same kernel
        # process serves copy_b through the aliased id and its original session.
        pid_before = await _kernel_pid(client, current_b)

        a_before = copy_a.read_bytes()
        b_before = copy_b.read_bytes()

        # ── the argument-less mutation: refuse, do not retarget.
        created = await create_cell(f"{_MARKER_1} = 1", ctx=ctx)
        assert created["status"] == "error", created
        assert created["reason"] == "notebook_mismatch", created
        assert created["target_resolved"] is False, created
        assert created["operation_ran"] is False, created
        assert created["state_changed"] is False, created
        assert _norm(created["bound_notebook"]) == _norm(copy_a), created
        assert _norm(created["resolved_notebook"]) == _norm(copy_b), created

        # ── the argument-less restart: refuse, close nothing.
        restarted = await restart_kernel(ctx=ctx)
        assert restarted["status"] == "error", restarted
        assert restarted["reason"] == "notebook_mismatch", restarted
        assert restarted["state_changed"] is False, restarted
        assert restarted["restarted"] is False, restarted

        # ── on disk: nothing was written to either copy.
        assert copy_b.read_bytes() == b_before == fixture_bytes, (
            "copy_b changed on disk across a refused call"
        )
        assert copy_a.read_bytes() == a_before == fixture_bytes, (
            "copy_a changed on disk across a refused call"
        )
        assert _MARKER_1.encode() not in copy_b.read_bytes()
        assert _MARKER_1.encode() not in copy_a.read_bytes()

        # ── kernel identity: copy_b's kernel was NOT restarted.
        census = await _census(client)
        current_b = _id_for_path(census, copy_b)
        assert current_b is not None, census
        pid_after = await _kernel_pid(client, current_b)
        assert pid_after == pid_before, (
            f"copy_b's kernel pid changed {pid_before} -> {pid_after}: a refused "
            "restart restarted it anyway"
        )

        # ── and the marker is nowhere in copy_b's live cells.
        live = await get_cell_map(session_id=current_b, server_url=server_url)
        assert all(_MARKER_1 not in (c.get("preview") or "") for c in live["cells"]), (
            live
        )
    finally:
        await client.close()


@pytest.mark.live
async def test_id_only_rotation_is_repaired_and_creates_in_the_bound_notebook(
    churn_server,
):
    """Case 2: a rotation without reuse is repaired to the bound notebook.

    The bound id is gone (``copy_a`` is served under a new id) and ``copy_b``
    is untouched, so the argument-less ``create_cell`` must target ``copy_a``:
    it succeeds, reports ``notebook_path`` naming ``copy_a``, and the created
    cell is genuinely present in ``copy_a``'s kernel and file. Cleanup deletes
    it with an explicit target.
    """
    _manager, server_url, copy_a, copy_b = churn_server
    fixture_bytes = NOTEBOOK_PATH.read_bytes()
    client = MarimoClient(server_url)
    created_id: str | None = None
    try:
        bound_id, other_id = str(uuid.uuid4()), str(uuid.uuid4())
        assert await client.materialize_session(bound_id, str(copy_a))
        assert await client.materialize_session(other_id, str(copy_b))
        await _wait_for(
            client,
            lambda c: (
                c.get(bound_id) == _norm(copy_a) and c.get(other_id) == _norm(copy_b)
            ),
        )

        ctx = _BoundCtx()
        await bind_active_session(
            bound_id, ctx, server_url=server_url, notebook_path=str(copy_a)
        )

        # ── rotation ONLY: a second /sse on copy_a; the bound id leaves the census.
        rotated_id = str(uuid.uuid4())
        assert await client.materialize_session(rotated_id, str(copy_a))
        census = await _wait_for(
            client,
            lambda c: c.get(rotated_id) == _norm(copy_a) and bound_id not in c,
        )
        assert bound_id not in census, f"bound id still live after rotation: {census}"
        assert census.get(rotated_id) == _norm(copy_a), census
        assert census.get(other_id) == _norm(copy_b), census

        b_before = copy_b.read_bytes()
        a_before = copy_a.read_bytes()

        created = await create_cell(f"{_MARKER_2} = 1", ctx=ctx)
        assert created["status"] == "ok", created
        created_id = created["cell_id"]
        assert created_id, created
        assert created.get("notebook_path"), created
        assert _norm(created["notebook_path"]) == _norm(copy_a), created

        # ── genuinely present: read back through a session serving copy_a.
        a_id = _id_for_path(await _census(client), copy_a)
        assert a_id is not None, "no session serves copy_a after the repair"
        read = await get_cell_data(session_id=a_id, server_url=server_url)
        assert any(_MARKER_2 in (row.get("code") or "") for row in read["data"]), read

        # ── on disk: the cell reached copy_a's file; copy_b is untouched.
        assert _MARKER_2.encode() in copy_a.read_bytes(), (
            "the created cell did not reach copy_a on disk"
        )
        assert copy_a.read_bytes() != a_before, "copy_a file did not change"
        assert copy_b.read_bytes() == b_before == fixture_bytes, (
            "copy_b changed while creating in copy_a"
        )
        assert _MARKER_2.encode() not in copy_b.read_bytes()

        # ── copy_b's kernel holds no such cell either.
        b_id = _id_for_path(await _census(client), copy_b)
        assert b_id is not None
        other_read = await get_cell_data(session_id=b_id, server_url=server_url)
        assert all(
            _MARKER_2 not in (row.get("code") or "") for row in other_read["data"]
        ), other_read
    finally:
        # Cleanup with an EXPLICIT target so the created cell never survives.
        if created_id is not None:
            a_id = _id_for_path(await _census(client), copy_a)
            if a_id is not None:
                await delete_cell(created_id, session_id=a_id, server_url=server_url)
        await client.close()

    # After the explicit-target delete the marker is gone from the copy.
    assert _MARKER_2.encode() not in copy_a.read_bytes(), (
        "the created cell's source survived the explicit-target cleanup"
    )


@pytest.mark.live
async def test_url_alias_of_the_same_server_does_not_bypass_the_anchor(churn_server):
    """Case 3: the bound id restated with a ``localhost`` alias still refuses.

    ``http://localhost:PORT`` and the bound ``http://127.0.0.1:PORT`` are
    different URL *strings* naming one live server (verified against that
    server before use). Restating the bound id with the alias after the churn
    must keep the notebook anchor, so the call is refused ``notebook_mismatch``
    and never dispatched to ``copy_b``. Until the concurrently-landed fix is
    present this fails with a "URL alias bypassed the anchor" assertion.
    """
    _manager, server_url, copy_a, copy_b = churn_server
    fixture_bytes = NOTEBOOK_PATH.read_bytes()
    assert "127.0.0.1" in server_url, server_url
    alias_url = server_url.replace("127.0.0.1", "localhost", 1)

    client = MarimoClient(server_url)
    created_id: str | None = None
    try:
        bound_id, other_id = str(uuid.uuid4()), str(uuid.uuid4())
        assert await client.materialize_session(bound_id, str(copy_a))
        assert await client.materialize_session(other_id, str(copy_b))
        canonical = await _wait_for(
            client,
            lambda c: (
                c.get(bound_id) == _norm(copy_a) and c.get(other_id) == _norm(copy_b)
            ),
        )

        # ── precondition: the alias is a genuine second spelling of THIS server.
        alias_client = MarimoClient(alias_url)
        try:
            alias_sessions = await alias_client.list_sessions()
        except Exception as exc:  # noqa: BLE001 - reported as a blocked precondition
            pytest.fail(
                f"BLOCKED PRECONDITION: {alias_url} did not reach the live "
                f"server at {server_url}: {exc!r}. The alias case needs a "
                "genuinely resolvable second spelling; it is not faked or "
                "widened here."
            )
        finally:
            await alias_client.close()
        alias_census = {s.session_id: _norm(s.file) for s in alias_sessions}
        assert alias_census == canonical, (
            f"alias census {alias_census} != canonical {canonical}: the alias "
            "does not name the same live server"
        )

        # ── bind to copy_a (canonical URL), then the real churn.
        ctx = _BoundCtx()
        await bind_active_session(
            bound_id, ctx, server_url=server_url, notebook_path=str(copy_a)
        )
        census = await _churn_reuse_stale_id(client, copy_a, copy_b, bound_id)
        assert census.get(bound_id) == _norm(copy_b), census

        b_before = copy_b.read_bytes()
        a_before = copy_a.read_bytes()

        # ── restate the BOUND id with the alias spelling of the same server.
        result = await create_cell(
            f"{_MARKER_3} = 1",
            session_id=bound_id,
            server_url=alias_url,
            ctx=ctx,
        )
        created_id = result.get("cell_id") if result.get("status") == "ok" else None
        assert result.get("reason") == "notebook_mismatch", (
            "URL alias bypassed the anchor: the bound id restated with "
            f"{alias_url!r} (same live server as {server_url!r}) was not refused "
            f"notebook_mismatch (result={result!r})"
        )
        assert result["status"] == "error", result
        assert result.get("operation_ran", False) is False, result
        assert result.get("state_changed", False) is False, result
        # Non-vacuous: the anchor named the bound notebook (copy_a) while the
        # id actually resolved to copy_b — so the refusal is decisive, not an
        # accidental pass.
        assert _norm(result["bound_notebook"]) == _norm(copy_a), result
        assert _norm(result["resolved_notebook"]) == _norm(copy_b), result

        # ── nothing reached copy_b: no bytes, no marker.
        assert copy_b.read_bytes() == b_before == fixture_bytes, (
            "copy_b changed across the alias call"
        )
        assert copy_a.read_bytes() == a_before, "copy_a changed across the alias call"
        assert _MARKER_3.encode() not in copy_b.read_bytes()

        b_id = _id_for_path(census, copy_b)
        assert b_id is not None, census
        live = await get_cell_map(session_id=b_id, server_url=server_url)
        assert all(_MARKER_3 not in (c.get("preview") or "") for c in live["cells"]), (
            live
        )
    finally:
        # If (pre-fix) the alias call WAS dispatched, remove the leaked cell.
        if created_id is not None:
            b_id = _id_for_path(await _census(client), copy_b)
            if b_id is not None:
                await delete_cell(created_id, session_id=b_id, server_url=server_url)
        await client.close()
