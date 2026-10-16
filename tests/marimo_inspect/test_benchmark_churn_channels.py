"""Static, kernel-free gate for the native churn probe's refusal evidence.

The churn probe treats a stale-session refusal as proof the kernel did NOT run
the submitted code. That proof is only valid if the execution sentinel is
absent from EVERY captured channel -- and the subprocess's raw **stderr** is one
of them. A refusal that is otherwise correctly structured (nonzero exit, a
structured ``success: false`` JSON envelope naming a stale session) but whose
raw stderr carries the sentinel MUST fail: the code may have run. An earlier
``run_cli`` discarded stderr, so that leak was unobservable.

Loaded the same way as the other benchmark gates (``test_benchmark_oracle.py``,
``test_benchmark_target_selection.py``): the probe module is executed from disk
with ``importlib`` and only its pure refusal logic is exercised -- no server and
no kernel.
"""

import importlib.util
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).parents[2] / "benchmark" / "native_churn_probe.py"
spec = importlib.util.spec_from_file_location(
    "benchmark_native_churn_probe", MODULE_PATH
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

SENTINEL = probe.STALE_SENTINEL
verify_stale_refusal = probe.verify_stale_refusal
output_channels = probe.output_channels


def _refusal(**overrides) -> tuple[int, dict, str, str]:
    """A correctly structured stale refusal with a clean stderr by default."""
    payload: dict = {
        "success": False,
        "error": "session is stale: no live kernel for this id",
        "stdout": "",
    }
    payload.update(overrides)
    return 1, payload, "", ""


# ---------------------------------------------------------------------------
# The regression: stderr is a captured channel and carries the sentinel.
# ---------------------------------------------------------------------------


def test_structured_stale_refusal_with_sentinel_in_stderr_fails():
    """Nonzero rc + success false + stale wording is NOT enough: a sentinel in
    the raw subprocess stderr must fail the refusal verification."""
    rc, payload, raw_out, _raw_err = _refusal()
    with pytest.raises(AssertionError):
        verify_stale_refusal(rc, payload, raw_out, f"refused\n{SENTINEL}\n")


def test_raw_stderr_is_always_an_observed_channel():
    rc, payload, raw_out, _raw_err = _refusal()
    channels = verify_stale_refusal(rc, payload, raw_out, "refused: stale session")
    assert "raw_stderr" in channels


# ---------------------------------------------------------------------------
# Every channel is swept, raw and payload alike.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        lambda: ("raw stdout", 1, _refusal()[1], SENTINEL, ""),
        lambda: ("raw stderr", 1, _refusal()[1], "", SENTINEL),
        lambda: ("payload.stderr", 1, _refusal(stderr=SENTINEL)[1], "", ""),
        lambda: ("payload.stdout", 1, _refusal(stdout=SENTINEL)[1], "", ""),
        lambda: ("payload.output", 1, _refusal(output=[SENTINEL])[1], "", ""),
    ],
)
def test_sentinel_in_any_channel_fails(leak):
    _name, rc, payload, raw_out, raw_err = leak()
    with pytest.raises(AssertionError):
        verify_stale_refusal(rc, payload, raw_out, raw_err)


def test_output_channels_lists_only_observed_payload_fields():
    channels = output_channels(
        {"success": False, "stdout": "", "stderr": "s"}, "__raw__", "__err__"
    )
    assert set(channels) == {
        "raw_stdout",
        "raw_stderr",
        "payload.stdout",
        "payload.stderr",
    }
    # A field the envelope does not carry is unchecked, never assumed clean.
    assert "payload.output" not in channels


# ---------------------------------------------------------------------------
# Malformed or non-refusing output is never evidence of a valid refusal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rc", "payload"),
    [
        (1, None),  # unparseable / absent JSON envelope
        (1, {}),  # malformed: no structured success flag
        (1, {"detail": "gateway timeout"}),  # malformed: not a refusal record
        (1, {"success": None}),  # malformed: success not boolean false
        (0, {"success": True}),  # accepted: sentinel check would be moot
        (0, {"success": False, "error": "stale session"}),  # exit 0 is success
        (1, {"success": False, "error": "kernel busy"}),  # no stale wording
        (1, {"success": False}),  # missing error/reason
    ],
)
def test_malformed_or_non_refusing_output_is_rejected(rc, payload):
    with pytest.raises(AssertionError):
        verify_stale_refusal(rc, payload, "", "")


def test_clean_structured_stale_refusal_passes():
    rc, payload, raw_out, raw_err = _refusal()
    channels = verify_stale_refusal(rc, payload, raw_out, raw_err)
    assert SENTINEL not in "".join(channels.values())


def test_run_cli_preserves_stderr_and_refusal_check_rejects_it(monkeypatch):
    payload = {"success": False, "error": "session is stale"}
    raw_out = json.dumps(payload)

    def completed(argv, **kwargs):
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(argv, 2, raw_out, SENTINEL)

    monkeypatch.setattr(probe.subprocess, "run", completed)
    result = probe.run_cli(
        "http://127.0.0.1:1",
        session_id="stale",
        file_path=None,
        code="print('not executed by this test')",
        env={},
    )
    assert result == (2, payload, raw_out, SENTINEL)
    with pytest.raises(AssertionError, match="raw_stderr"):
        verify_stale_refusal(*result)


def test_blocker_stops_server_before_temporary_tree_cleanup(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def temporary_directory(**kwargs):
        try:
            yield str(tmp_path)
        finally:
            events.append("cleanup")
            assert events == ["stop", "cleanup"]

    monkeypatch.setattr(probe.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(probe, "start_server", lambda *_: (SimpleNamespace(), "unused"))
    monkeypatch.setattr(probe, "open_session", lambda *_: False)
    monkeypatch.setattr(probe, "stop", lambda _: events.append("stop"))
    assert probe.main() == 3
    assert events == ["stop", "cleanup"]
