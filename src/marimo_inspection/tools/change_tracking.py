"""Per-session notebook change tracking for the read tools.

The MCP read tools are a single choke point for observing notebook state, so
we can cheaply tell the agent *what changed since its last observation* without
it having to re-poll and eyeball stale flags.

A ``ChangeTracker`` keeps, per session, the last snapshot of cell fingerprints
(``cell_id -> code_hash``). When a read tool returns the current cell map, the
server diffs it against the stored snapshot and reports:

* ``new_cells``     — cell_ids present now but absent from the snapshot.
* ``edited_cells``  — cell_ids whose code hash changed since the snapshot.
* ``removed_cells`` — cell_ids absent now but present in the snapshot.
* ``state_changed`` — cell_ids whose runtime state changed (e.g. idle -> stale).

Tracking is opt-in and cheap: the snapshot is updated whenever a caller tells
``ChangeTracker`` to ``commit`` the latest fingerprint map. If a session has no
snapshot yet, the first observation is treated as the baseline (no diff).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class CellFingerprint:
    code_hash: str
    state: str | None = None


@dataclass
class NotebookChange:
    """What changed in a notebook since the previous observation."""

    new_cells: list[str] = field(default_factory=list)
    edited_cells: list[str] = field(default_factory=list)
    removed_cells: list[str] = field(default_factory=list)
    state_changed: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_cells
            or self.edited_cells
            or self.removed_cells
            or self.state_changed
        )

    def to_dict(self) -> dict:
        return {
            "new_cells": self.new_cells,
            "edited_cells": self.edited_cells,
            "removed_cells": self.removed_cells,
            "state_changed": self.state_changed,
        }


class ChangeTracker:
    """Process-wide per-session notebook change tracker.

    Thread-safe via a single lock. Keyed by session_id so independent notebooks
    don't collide. Stateless-model note: the tracker lives in the MCP server
    process; a server restart (e.g. hot-reload) clears it, which simply means
    the next observation becomes a fresh baseline again.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, CellFingerprint]] = {}
        self._lock = threading.Lock()

    def diff(
        self,
        session_id: str,
        current: dict[str, CellFingerprint],
    ) -> NotebookChange:
        """Diff ``current`` fingerprints against the stored snapshot.

        Does NOT update the stored snapshot — caller decides when to commit.
        """
        with self._lock:
            prev = self._snapshots.get(session_id, {})

        change = NotebookChange()
        cur_ids = set(current.keys())
        prev_ids = set(prev.keys())

        change.new_cells = sorted(cur_ids - prev_ids)
        change.removed_cells = sorted(prev_ids - cur_ids)

        for cid in sorted(cur_ids & prev_ids):
            cur_fp = current[cid]
            prev_fp = prev[cid]
            if cur_fp.code_hash != prev_fp.code_hash:
                change.edited_cells.append(cid)
            elif prev_fp.state is not None and cur_fp.state != prev_fp.state:
                change.state_changed.append(cid)

        return change

    def commit(
        self,
        session_id: str,
        current: dict[str, CellFingerprint],
    ) -> None:
        """Replace the stored snapshot for ``session_id`` with ``current``."""
        with self._lock:
            self._snapshots[session_id] = dict(current)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._snapshots.pop(session_id, None)

    def has_snapshot(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._snapshots

    def get_cell_fingerprint(
        self, session_id: str, cell_id: str
    ) -> CellFingerprint | None:
        """Return the last-recorded fingerprint for a cell, or None.

        ``None`` means the agent has no prior read of that cell (either the
        session has no snapshot or this cell wasn't in it).
        """
        with self._lock:
            snap = self._snapshots.get(session_id)
            if not snap:
                return None
            return snap.get(cell_id)


# Module-level singleton, matching the FileStateRegistry pattern used by the
# Hermes file tools (see ~/.hermes/hermes-agent/tools/file_state.py).
_tracker = ChangeTracker()


def get_tracker() -> ChangeTracker:
    return _tracker


__all__ = [
    "CellFingerprint",
    "ChangeTracker",
    "NotebookChange",
    "get_tracker",
]
