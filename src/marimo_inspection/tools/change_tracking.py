"""Per-session notebook change tracking for the read tools.

The MCP read tools are a single choke point for observing notebook state, so
we can cheaply tell the agent *what changed since its last observation* without
it having to re-poll and eyeball stale flags.

A ``ChangeTracker`` keeps, per session, two independent fingerprint maps
(``cell_id -> code_hash``):

* the **change-detection snapshot** — what ``get_cell_map`` last observed, so a
  later map call can report ``changes_since_last``;
* the **read baseline** — the last *full-source* read of each cell, which the
  ``edit_cell`` staleness guard compares against.

They are kept separate because the observations differ (hunt finding H9): a
3-line preview is a valid change-detection observation but is NOT "I read the
source". ``commit`` (the map path) therefore writes only the snapshot, while
``record_cells`` (the full-source read path) writes both.

When a read tool returns the current cell map, the server diffs it against the
stored snapshot and reports:

* ``new_cells``     — cell_ids present now but absent from the snapshot.
* ``edited_cells``  — cell_ids whose code hash changed since the snapshot.
* ``removed_cells`` — cell_ids absent now but present in the snapshot.
* ``state_changed`` — cell_ids whose runtime state changed (e.g. idle -> stale).

Tracking is opt-in and cheap: the snapshot is updated whenever a caller tells
``ChangeTracker`` to ``commit`` the latest fingerprint map. If a session has no
snapshot yet, the first observation is treated as the baseline (no diff). The
read baseline is written only by ``record_cells``: a cell without one reports
``needs_read`` (the safe direction).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
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

    Holds the two dimensions separately (see the module docstring): the
    change-detection snapshot (``_snapshots``) and the read baseline
    (``_baselines``). Never let one stand in for the other — that conflation
    was hunt finding H9 (a preview read blessing a full-source baseline for
    every cell).

    Thread-safe via a single lock. Keyed by session_id so independent notebooks
    don't collide. Stateless-model note: the tracker lives in the MCP server
    process; a server restart (e.g. hot-reload) clears it, which simply means
    the next observation becomes a fresh baseline again.
    """

    def __init__(self) -> None:
        # Change detection: what get_cell_map last observed (previews are fine).
        self._snapshots: dict[str, dict[str, CellFingerprint]] = {}
        # Read baseline: the last full-source read, for the edit_cell guard.
        self._baselines: dict[str, dict[str, CellFingerprint]] = {}
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

    def record_cells(
        self,
        session_id: str,
        fingerprints: dict[str, CellFingerprint],
    ) -> None:
        """Record the cells whose FULL SOURCE the agent just observed.

        This is the read-baseline write path (``get_cell_data``, and the
        post-mutation refresh for the cell a tool just wrote): the agent read
        the exact source, so the ``edit_cell`` guard may trust it. Unlike
        :meth:`commit` — which replaces the whole snapshot — this updates only
        the fingerprints for exactly the supplied cell_ids and never erases
        fingerprints for cells not supplied; it creates the store if none
        exists yet.

        The same observation also merges into the change-detection snapshot, so
        a cell the agent just read (or wrote) does not reappear as an external
        change on the next ``get_cell_map``.
        """
        with self._lock:
            base = self._baselines.get(session_id)
            if base is None:
                self._baselines[session_id] = dict(fingerprints)
            else:
                base.update(fingerprints)

            snap = self._snapshots.get(session_id)
            if snap is None:
                self._snapshots[session_id] = dict(fingerprints)
            else:
                snap.update(fingerprints)

    def forget_cells(self, session_id: str, cell_ids: Iterable[str]) -> None:
        """Drop fingerprints for ``cell_ids``; never touch other baselines.

        Used by the delete path: the removed cell has no live hash to record,
        and leaving its fingerprint behind would misreport it as a still-known
        cell. Drops from both stores (a removed cell has neither a snapshot
        entry nor a read baseline). A no-op when the session has no entry.
        """
        with self._lock:
            for store in (self._baselines, self._snapshots):
                snap = store.get(session_id)
                if snap is None:
                    continue
                for cid in cell_ids:
                    snap.pop(cid, None)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._snapshots.pop(session_id, None)
            self._baselines.pop(session_id, None)

    def has_snapshot(self, session_id: str) -> bool:
        """True when a change-detection snapshot exists for the session."""
        with self._lock:
            return session_id in self._snapshots

    def get_cell_fingerprint(
        self, session_id: str, cell_id: str
    ) -> CellFingerprint | None:
        """Return the last-recorded READ BASELINE for a cell, or None.

        This is the ``edit_cell`` staleness guard's view, written only by a
        full-source read (``record_cells``). ``None`` means the agent has no
        prior read of that cell (either the session has no baseline or this
        cell wasn't in it) — including after a ``get_cell_map`` preview.
        """
        with self._lock:
            snap = self._baselines.get(session_id)
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
