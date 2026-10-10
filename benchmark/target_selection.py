"""Fail-closed single-selector adapter for the native ``marimo pair`` caller.

Motivation (see ``docs/native-pair-benchmark-runbook.md`` §"Mitigation
decision"): the native CLI documents ``--session`` precedence, so a mismatched
pair such as ``--file B --session A`` silently resolves to ``A`` and a mutating
call lands on a notebook the ``--file`` selector did not name. The benchmark's
chosen mitigation is a **single-selector adapter**: refuse any call that
supplies *both* selectors (and any call that supplies *neither*), so the
mismatched-pair path cannot be reached.

This module is deliberately narrow and pure:

* It is **stdlib-only**, imports nothing from the package, and never calls the
  CLI, opens a file, or performs any I/O.
* It **preserves the caller's literal values** verbatim — no path
  normalization, no ``resolve()``, no filesystem lookup, and no session
  registry lookup.
* It **never** encodes, reads or emits credentials or tokens.
* It has no product integration; the native benchmark caller builds its argv
  from the returned selector fragment.

Scope honesty: a single selector removes the mismatched-pair hazard, but
file-only resolution can still race a later session reload and session-only
resolution cannot detect that the named session moved. This adapter makes no
guarantee against such server-side churn — that is a separate gate.

``file-only`` expresses *file intent* (the caller means a notebook path);
``session-only`` expresses *session intent* (the caller means a live session
id). The two are not interchangeable and the adapter does not translate one
into the other.
"""

from __future__ import annotations

__all__ = ["SelectorRefusal", "select_native_target"]


class SelectorRefusal(ValueError):
    """A selector request is fail-closed invalid (both, neither, or blank).

    Raised instead of guessing a target: the caller must present exactly one
    non-blank selector.
    """


def select_native_target(
    *,
    file_path: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Return the native CLI selector argv for exactly one selector.

    Exactly one of ``file_path`` or ``session_id`` must be supplied and must be
    non-blank; otherwise :class:`SelectorRefusal` is raised and no argv is
    produced (fail-closed). The returned list is a selector *fragment* — e.g.
    ``["--file", "<value>"]`` or ``["--session", "<value>"]`` — to be spliced
    into the caller's argv. The value is passed through verbatim; this function
    never normalizes it, looks it up, or touches the filesystem or CLI.

    Args:
        file_path: Notebook path value for ``--file`` (file intent).
        session_id: Live session id value for ``--session`` (session intent).

    Returns:
        A two-element argv fragment naming exactly one selector.

    Raises:
        SelectorRefusal: If both selectors, neither selector, or a blank
            selector value is supplied.
    """
    if file_path is not None and session_id is not None:
        raise SelectorRefusal(
            "refusing combined selectors: supply exactly one of --file or "
            "--session, never both (mismatched pairs resolve silently)"
        )
    if file_path is None and session_id is None:
        raise SelectorRefusal(
            "no selector supplied: exactly one of --file or --session is required"
        )
    value = file_path if file_path is not None else session_id
    if not isinstance(value, str) or not value.strip():
        raise SelectorRefusal("selector value must be a non-blank string")
    if file_path is not None:
        return ["--file", value]
    return ["--session", value]
