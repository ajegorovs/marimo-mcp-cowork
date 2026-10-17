"""Pure, stdlib-only restricted-session checkers for the comparison benchmark.

The restricted comparison (issue #5) needs a small, always-on set of *pure*
guards that a caller can evaluate without a live kernel, a server, or the
package itself. This module is that guard set. It is deliberately narrow:

* **stdlib only** and no package import — nothing here touches ``marimo``,
  ``fastmcp`` or the ``marimo_inspection`` package.
* **Pure and side-effect free** — the functions read files only where the
  contract requires it (:func:`validate_disposable`) and never write, mutate,
  launch a process, or open a network connection.
* **Fail-closed** — every violation raises :class:`ValueError` instead of
  returning a permissive default. A caller that does not catch it treats the
  session as unusable.

Scope honesty: this is **detection of a restricted precondition, not atomic
prevention**. Each check inspects one already-captured snapshot; it cannot make
the census read and a later mutating request the same operation, so a rebind
that happens *after* a passing check is unaddressed. The guard proves the
snapshot it was handed satisfies the anchor; it does not pin the live server to
that anchor.

Three independent snapshots are checked:

* :func:`validate_census` — a single-session census still maps the anchored
  session id to the intended notebook.
* :func:`validate_reopen` — a "reopen" observation rotated both the process id
  and the session id while keeping the same notebook path.
* :func:`validate_disposable` — the canonical notebook and its disposable
  copies are byte-identical, disjoint and non-aliased, with the canonical kept
  outside the disposable root.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "SessionAnchor",
    "validate_census",
    "validate_disposable",
    "validate_reopen",
]


@dataclass(frozen=True)
class SessionAnchor:
    """A caller's recorded expectation for one live session.

    Attributes:
        session_id: The session id the caller anchored on.
        notebook_path: The notebook path the anchor intends (any spelling —
            compared after :meth:`pathlib.Path.resolve`).
        process_id: The server process id observed when the anchor was taken.
    """

    session_id: str
    notebook_path: str
    process_id: int


def _resolved(path: str | os.PathLike[str]) -> Path:
    """Resolve *path* without requiring it to exist."""
    return Path(path).resolve()


def _resolved_entry_path(entry: object) -> Path:
    """Extract a resolved notebook path from one census entry.

    Accepts a mapping carrying either ``path`` or ``filename`` (the two
    spellings the native census and the wrapper census use).
    """
    if not isinstance(entry, dict):
        raise ValueError(  # noqa: TRY004 - contract mandates ValueError, not TypeError
            "census entry must be a mapping"
        )
    raw = entry.get("path") or entry.get("filename")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("census entry must carry a non-empty path or filename")
    return _resolved(raw)


def validate_census(census: object, anchor: SessionAnchor) -> None:
    """Require a one-session census still anchored to the intended notebook.

    The census must hold **exactly one** session, its id must be exactly
    ``anchor.session_id``, and its entry must carry a non-empty ``path`` or
    ``filename`` whose resolved form equals the resolved
    ``anchor.notebook_path``. Any deviation raises :class:`ValueError` — a
    reused id that now denotes a different notebook is the failure this
    catches.

    Args:
        census: Mapping of session id -> entry mapping, as read from the
            server's session census.
        anchor: The caller's recorded session expectation.

    Raises:
        ValueError: If the census is not a single-session mapping, the id does
            not match the anchor, the entry has no non-empty path/filename, or
            the resolved entry path differs from the anchored path.
    """
    if not isinstance(census, dict):
        raise ValueError(  # noqa: TRY004 - contract mandates ValueError, not TypeError
            "census must be a mapping of session id -> entry"
        )
    if len(census) != 1:
        raise ValueError(f"census must hold exactly one session, got {len(census)}")
    ((session_id, entry),) = census.items()
    if session_id != anchor.session_id:
        raise ValueError("census session id does not match the anchored session id")
    entry_path = _resolved_entry_path(entry)
    if entry_path != _resolved(anchor.notebook_path):
        raise ValueError("census entry path does not resolve to the anchored notebook")


def validate_reopen(previous: SessionAnchor, current: SessionAnchor) -> None:
    """Require a reopen to rotate process and session but keep the notebook.

    A legitimate reopen is served by a **different process** under a
    **different session id** yet must resolve to the **same notebook path**.
    Reusing either id is the silent-rebind hazard; targeting a different
    notebook is a different failure. Both raise :class:`ValueError`.

    Args:
        previous: The session anchor observed before the reopen.
        current: The session anchor observed after the reopen.

    Raises:
        ValueError: If the process id or session id is unchanged, or if the
            resolved notebook paths differ.
    """
    if previous.process_id == current.process_id:
        raise ValueError("reopen must run under a different process id")
    if previous.session_id == current.session_id:
        raise ValueError("reopen must use a different session id")
    if _resolved(previous.notebook_path) != _resolved(current.notebook_path):
        raise ValueError("reopen must target the same resolved notebook path")


def _within(path: Path, root: Path) -> bool:
    """Whether *path* equals *root* or lies beneath it (both pre-resolved)."""
    return path == root or path.is_relative_to(root)


def validate_disposable(canonical: Path, copies: list[Path], root: Path) -> str:
    """Require byte-identical, non-aliased disposable copies of the canonical.

    The canonical notebook must live **outside** ``root``; every copy must live
    **inside** ``root``. All files must exist, the copies must use distinct
    resolved paths, no two files may alias the same on-disk file (a hard link
    or symlink target shares an inode), and every copy's bytes must equal the
    canonical's. On success the canonical's SHA-256 hex digest is returned.

    Args:
        canonical: The pristine canonical notebook (kept outside ``root``).
        copies: Disposable notebook copies (kept inside ``root``).
        root: The disposable workspace directory.

    Returns:
        The SHA-256 hex digest of the canonical notebook's bytes.

    Raises:
        ValueError: If a file is missing, ``root`` is violated, paths collide,
            files alias one another, or any copy's bytes differ.
    """
    canonical = Path(canonical)
    root = Path(root)
    copies = [Path(copy) for copy in copies]

    if not copies:
        raise ValueError("at least one disposable copy is required")
    for path in (canonical, *copies):
        if not path.is_file():
            raise ValueError(f"notebook file does not exist: {path}")

    resolved_root = root.resolve()
    if _within(canonical.resolve(), resolved_root):
        raise ValueError("canonical notebook must live outside the disposable root")
    for copy in copies:
        if not _within(copy.resolve(), resolved_root):
            raise ValueError(f"disposable copy escapes the disposable root: {copy}")

    resolved_copies = [copy.resolve() for copy in copies]
    if len(set(resolved_copies)) != len(resolved_copies):
        raise ValueError("disposable copies must use distinct paths")

    # samefile catches hard links and symlinks: distinct names, one inode.
    targets = [canonical, *copies]
    for index, first in enumerate(targets):
        for second in targets[index + 1 :]:
            if os.path.samefile(first, second):
                raise ValueError(
                    f"disposable files alias the same file: {first} <-> {second}"
                )

    canonical_bytes = canonical.read_bytes()
    for copy in copies:
        if copy.read_bytes() != canonical_bytes:
            raise ValueError(
                f"disposable copy differs from the canonical notebook: {copy}"
            )

    return hashlib.sha256(canonical_bytes).hexdigest()
