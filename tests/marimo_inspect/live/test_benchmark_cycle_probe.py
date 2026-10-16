"""Bounded live CYCLE characterization on the disposable A/B fixture.

The native-pair benchmark's A4 safety matrix left "cycle validation" open
(``docs/native-pair-benchmark-runbook.md``). This probe answers the *wrapper*
(0.24.x) half of that question on a disposable byte-identical copy of
``benchmark/notebooks/mission_clean.py``:

* boot the fixture via ``notebook_server`` and run its originals, so the
  dependency graph is real (a duplicate/cycle can only be judged after the
  original definitions register — see the runbook's duplicate-definition row);
* attempt a two-cell dependency **cycle** — ``a4_cycle_left`` depends on
  ``a4_cycle_right`` and vice versa — in **one** ``cm.get_context()``
  transaction through the raw ``client.execute`` scratchpad, which is the only
  atomic shape available on this surface (the public ``create_cell`` tool
  creates a single cell per call);
* characterize the observed result and assert the safety invariants.

The bounded claim is *atomicity*, not tool behaviour: a one-context transaction
must never leave a *partial* cyclic pair live, and a refused/failing
transaction must be a no-op on both the live census and the disposable copy.
The committed fixture is asserted byte-identical throughout.

Scope note: if a single-context two-cell transaction is not available through
raw ``client.execute`` (the execute fails or yields no queued pair), the probe
does NOT fall back to a non-atomic two-call cycle; it instead characterizes the
existing one-``create_cell`` refusal (the documented duplicate-definition
refusal) atomically and prints ``CYCLE_PROBE_SCOPE_GAP``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marimo_inspection.client import MarimoClient
from marimo_inspection.tools.cells import get_cell_data
from marimo_inspection.tools.errors import get_errors
from marimo_inspection.tools.mutation import create_cell, run_cell

FIXTURE = Path(__file__).parents[3] / "benchmark" / "notebooks" / "mission_clean.py"

CYCLE_LEFT = "a4_cycle_left = a4_cycle_right + 1"
CYCLE_RIGHT = "a4_cycle_right = a4_cycle_left + 1"

# The existing one-cell refusal this probe falls back to when no single-context
# transaction is available: a redefinition of the fixture's own ``input_vector``
# is refused by the kernel (documented in the runbook's A4 checkpoint table).
DUP_SOURCE = "input_vector = 999"


def _parse_json_line(stdout: list[str]) -> dict | None:
    """Return the last JSON object line in ``stdout`` (mirrors mutation.py)."""
    for line in reversed("\n".join(stdout or []).split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _cycle_snippet() -> str:
    """A raw scratchpad snippet queuing BOTH cyclic cells in ONE context.

    The single ``async with cm.get_context()`` block is the atomic unit: both
    ``create_cell`` calls are queued inside it and flushed together on exit, so
    the pair is applied (or refused/rolled back) as one transaction.
    """
    return (
        "import json\n"
        "import marimo._code_mode as cm\n"
        "\n"
        "async def _run():\n"
        "    async with cm.get_context() as ctx:\n"
        f"        left = ctx.create_cell({json.dumps(CYCLE_LEFT)})\n"
        f"        right = ctx.create_cell({json.dumps(CYCLE_RIGHT)})\n"
        '        return json.dumps({"status": "queued",'
        ' "left": str(left), "right": str(right)})\n'
        "\n"
        "print(await _run())\n"
    )


def _code_by_id(data: dict) -> dict[str, str]:
    return {row["cell_id"]: (row.get("code") or "") for row in data["data"]}


@pytest.mark.live
async def test_wrapper_cycle_creation_is_atomic_on_disposable_fixture(notebook_server):
    """A one-context cyclic pair is all-or-nothing; a refusal touches nothing."""
    original = FIXTURE.read_bytes()
    _manager, url, sid, copy = await notebook_server(
        FIXTURE.read_text(encoding="utf-8"), name="mission_cycle_probe.py"
    )
    assert copy.read_bytes() == original, "booted copy is not byte-identical"

    boot = await run_cell(mode="all", session_id=sid, server_url=url)
    assert boot["status"] == "ok", boot
    assert boot["counts"]["requested"] == 5, boot

    before_data = await get_cell_data(session_id=sid, server_url=url)
    before_code = _code_by_id(before_data)
    disk_before = copy.read_bytes()

    client = MarimoClient(url)
    try:
        result = await client.execute(sid, _cycle_snippet())
    finally:
        await client.close()

    payload = _parse_json_line(result.stdout)
    stderr_text = "\n".join(result.stderr or [])
    print("CYCLE_TRANSPORT status=", result.status)
    print("CYCLE_STDOUT=", repr("\n".join(result.stdout or [])[-600:]))
    print("CYCLE_STDERR=", repr(stderr_text[-600:]))
    print("CYCLE_PAYLOAD=", payload)

    # A cyclic pair is refused by marimo's OWN context-exit dry-run compile,
    # after both creates were queued in one context — so the transaction WAS
    # available; the refusal, not a missing mechanism, is the observation.
    cycle_refused_by_validation = (
        result.status == "error" and "Cycles detected" in stderr_text
    )
    print("CYCLE_REFUSED_BY_VALIDATION=", cycle_refused_by_validation)

    after_data = await get_cell_data(session_id=sid, server_url=url)
    after_code = _code_by_id(after_data)
    left_live = [cid for cid, code in after_code.items() if code.strip() == CYCLE_LEFT]
    right_live = [
        cid for cid, code in after_code.items() if code.strip() == CYCLE_RIGHT
    ]
    left_present = bool(left_live)
    right_present = bool(right_live)
    print(
        "CYCLE_LIVE left_present=",
        left_present,
        "right_present=",
        right_present,
        "left_ids=",
        left_live,
        "right_ids=",
        right_live,
    )

    errors = await get_errors(session_id=sid, server_url=url)
    print(
        "CYCLE_ERRORS has_errors=",
        errors.get("has_errors"),
        "total=",
        errors.get("total_errors"),
    )

    # --- Atomicity: never a PARTIAL cyclic pair, whatever the outcome. --------
    assert (left_present, right_present) in {(False, False), (True, True)}, (
        "single-context create produced a partial cyclic pair: "
        f"left_present={left_present} right_present={right_present} "
        f"payload={payload}"
    )

    # The single-context transaction is available iff the execute queued BOTH
    # creates in one context — either it flushed a parseable pair, or marimo's
    # own context-exit validation refused the cyclic pair (which proves the
    # mechanism accepted both creates before refusing).
    single_transaction_available = (
        payload is not None
        and payload.get("status") == "queued"
        and bool(payload.get("left"))
        and bool(payload.get("right"))
    ) or cycle_refused_by_validation
    print("CYCLE_SINGLE_TRANSACTION_AVAILABLE=", single_transaction_available)

    if not (left_present or right_present):
        # Refused / rolled back: the failing transaction must be a no-op on the
        # live census AND on the disposable copy's bytes.
        assert after_code == before_code, (
            "failing transaction changed the live census: "
            f"{(set(before_code) ^ set(after_code))}"
        )
        assert copy.read_bytes() == disk_before, (
            "failing transaction altered the disposable copy on disk"
        )
        print("CYCLE_ATOMICITY refused=atomic_live_and_disk")

    if not single_transaction_available:
        # No usable single-context transaction: characterise the existing
        # one-cell create refusal instead of an unsafe two-call cycle.
        dup = await create_cell(DUP_SOURCE, session_id=sid, server_url=url)
        print("CYCLE_PROBE_SCOPE_GAP dup_refusal=", dup.get("status"), dup.get("error"))
        assert dup.get("status") != "ok", dup
        gap_data = await get_cell_data(session_id=sid, server_url=url)
        assert _code_by_id(gap_data) == before_code, (
            "one-cell refusal changed the live census"
        )
        assert copy.read_bytes() == disk_before, (
            "one-cell refusal altered the disposable copy on disk"
        )
        assert DUP_SOURCE not in copy.read_text(encoding="utf-8"), (
            "a refused one-cell create leaked its source to disk"
        )

    assert cycle_refused_by_validation, (result.status, stderr_text)
    assert not left_present and not right_present, after_code
    assert after_code == before_code
    assert copy.read_bytes() == disk_before
    # The committed user fixture is never the target.
    assert FIXTURE.read_bytes() == original, "repo fixture was modified"
