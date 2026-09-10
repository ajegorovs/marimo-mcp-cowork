"""Tests for the per-session notebook change tracker."""

from marimo_inspection.tools.change_tracking import (
    CellFingerprint,
    ChangeTracker,
)


def _fp(code: str, state: str | None = "idle") -> CellFingerprint:
    return CellFingerprint(code_hash=code, state=state)


def test_diff_without_snapshot_lists_everything_as_new():
    """Raw diff vs. an empty tracker reports every current cell as new.

    (The callers gate on ``has_snapshot`` to suppress this on the very first
    observation, treating it as the baseline instead.)
    """
    t = ChangeTracker()
    current = {"A": _fp("import numpy"), "B": _fp("x = 1")}
    change = t.diff("s1", current)
    assert change.has_changes
    assert change.new_cells == ["A", "B"]


def test_new_and_removed_cells():
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("a"), "B": _fp("b")})
    change = t.diff("s1", {"A": _fp("a"), "C": _fp("c")})
    assert change.new_cells == ["C"]
    assert change.removed_cells == ["B"]
    assert change.edited_cells == []
    assert change.state_changed == []


def test_edited_cell_detected_by_hash_change():
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("import numpy")})
    change = t.diff("s1", {"A": _fp("import numpy\nx = 1")})
    assert change.edited_cells == ["A"]
    assert change.new_cells == []
    assert change.removed_cells == []


def test_state_change_detected_when_hash_unchanged():
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("x = 1", state="idle")})
    change = t.diff("s1", {"A": _fp("x = 1", state="stale")})
    assert change.state_changed == ["A"]
    assert change.edited_cells == []


def test_hash_change_takes_priority_over_state_change():
    """A code edit supersedes an incidental state change on the same cell."""
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("x = 1", state="idle")})
    change = t.diff("s1", {"A": _fp("x = 2", state="stale")})
    assert change.edited_cells == ["A"]
    assert change.state_changed == []


def test_commit_replaces_snapshot():
    t = ChangeTracker()
    t.commit("s1", {"B": _fp("v2")})
    # After this commit the snapshot is {B}, so A is new relative to it.
    change = t.diff("s1", {"A": _fp("v1"), "B": _fp("v2")})
    assert change.new_cells == ["A"]
    assert change.removed_cells == []
    assert change.edited_cells == []


def test_clear_session_resets_baseline():
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("v1")})
    t.clear_session("s1")
    assert not t.has_snapshot("s1")


def test_record_cells_creates_snapshot_when_none_exists():
    """record_cells on a session without a snapshot establishes one."""
    t = ChangeTracker()
    t.record_cells("s1", {"A": _fp("a")})
    assert t.has_snapshot("s1")
    fp = t.get_cell_fingerprint("s1", "A")
    assert fp is not None
    assert fp.code_hash == "a"


def test_record_cells_merges_and_updates_only_supplied_cells():
    """record_cells is an upsert: supplied cells are updated, others kept."""
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("a"), "B": _fp("b")})
    t.record_cells("s1", {"A": _fp("a2", state="stale")})
    assert t.get_cell_fingerprint("s1", "A").code_hash == "a2"
    assert t.get_cell_fingerprint("s1", "A").state == "stale"
    assert t.get_cell_fingerprint("s1", "B").code_hash == "b"
    # Re-diffing the same state after the merge reports no changes.
    change = t.diff("s1", {"A": _fp("a2", state="stale"), "B": _fp("b")})
    assert not change.has_changes


def test_record_cells_never_erases_unlisted_cells():
    """record_cells must not drop fingerprints for cells it wasn't given."""
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("a"), "B": _fp("b"), "C": _fp("c")})
    t.record_cells("s1", {"C": _fp("c2")})
    assert t.get_cell_fingerprint("s1", "A").code_hash == "a"
    assert t.get_cell_fingerprint("s1", "B").code_hash == "b"
    assert t.get_cell_fingerprint("s1", "C").code_hash == "c2"


def test_record_cells_keeps_other_baselines():
    tracker = ChangeTracker()
    tracker.commit("s1", {"a": CellFingerprint(code_hash="h_a")})
    tracker.record_cells("s1", {"b": CellFingerprint(code_hash="h_b")})
    assert tracker.get_cell_fingerprint("s1", "a") is not None  # NOT dropped
    assert tracker.get_cell_fingerprint("s1", "b").code_hash == "h_b"


def test_record_cells_updates_the_named_cell_only():
    tracker = ChangeTracker()
    tracker.commit("s1", {"a": CellFingerprint(code_hash="h_a")})
    tracker.record_cells("s1", {"a": CellFingerprint(code_hash="h_a2")})
    assert tracker.get_cell_fingerprint("s1", "a").code_hash == "h_a2"


def test_forget_cells_drops_only_the_named_cells():
    tracker = ChangeTracker()
    tracker.commit("s1", {"a": CellFingerprint("h_a"), "b": CellFingerprint("h_b")})
    tracker.forget_cells("s1", ["a"])
    assert tracker.get_cell_fingerprint("s1", "a") is None
    assert tracker.get_cell_fingerprint("s1", "b") is not None


def test_forget_cells_is_a_noop_without_a_snapshot():
    tracker = ChangeTracker()
    tracker.forget_cells("missing", ["a"])  # must not raise, must not create a snapshot
    assert tracker.has_snapshot("missing") is False


def test_to_dict_shape():
    t = ChangeTracker()
    t.commit("s1", {"A": _fp("a"), "B": _fp("b")})
    c = t.diff("s1", {"A": _fp("a"), "C": _fp("c")})
    d = c.to_dict()
    assert d == {
        "new_cells": ["C"],
        "edited_cells": [],
        "removed_cells": ["B"],
        "state_changed": [],
    }
    assert c.has_changes is True
