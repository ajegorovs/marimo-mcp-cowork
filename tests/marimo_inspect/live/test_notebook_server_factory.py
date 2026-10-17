"""The ``notebook_server`` factory's byte contract, on a real server.

The benchmark probes assert that their disposable notebook is **byte-identical**
to the committed fixture before they exercise any marimo behaviour. That
assertion is only meaningful if the factory can be handed the exact bytes: a
``read_text()`` round-trip decodes with universal newlines, so on a checkout
whose working tree carries CRLF (git ``core.autocrlf=true`` on Windows) the copy
would come back LF and the test would fail before testing anything.

These cases pin the contract for both payload kinds — bytes pass through
untouched (LF *and* CRLF), text keeps the caller's own line endings — and then
assert the property the probes actually need against the committed fixture, so a
future change to the factory cannot silently reintroduce the round-trip.

A real kernel is required, because the property under test is what the file on
disk contains after a server boots on it, and marimo only boots for a document it
recognises as a notebook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The fixture the benchmark probes copy (their own constant, kept in step).
BENCHMARK_FIXTURE = (
    Path(__file__).parents[3] / "benchmark" / "notebooks" / "mission_clean.py"
)

#: A minimal document marimo recognises, written with LF; the CRLF variant is
#: the same text with translated terminators.
_MARIMO_TEMPLATE = (
    "import marimo\n"
    "\n"
    "app = marimo.App()\n"
    "\n"
    "\n"
    "@app.cell\n"
    "def _():\n"
    "    value = 1\n"
    "    return (value,)\n"
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    app.run()\n"
)


@pytest.mark.live
async def test_bytes_source_is_written_exactly_for_lf_and_crlf(notebook_server):
    """Bytes are written verbatim — no newline translation in either direction."""
    lf = _MARIMO_TEMPLATE.encode("utf-8")
    crlf = _MARIMO_TEMPLATE.replace("\n", "\r\n").encode("utf-8")
    assert b"\r\n" in crlf and b"\r\n" not in lf

    _m1, _u1, _s1, lf_copy = await notebook_server(lf, name="factory_lf.py")
    _m2, _u2, _s2, crlf_copy = await notebook_server(crlf, name="factory_crlf.py")

    assert lf_copy.read_bytes() == lf, "an LF payload did not survive the factory"
    assert crlf_copy.read_bytes() == crlf, (
        "a CRLF payload did not survive the factory — this is the defect that "
        "made the benchmark probes fail on a Windows checkout"
    )


@pytest.mark.live
async def test_text_source_keeps_the_callers_line_endings(notebook_server):
    """A ``str`` payload is written with the caller's endings, not translated.

    This is the behaviour every existing text caller relies on.
    """
    text = _MARIMO_TEMPLATE

    _manager, _url, _sid, copy = await notebook_server(text, name="factory_text.py")

    assert copy.read_bytes() == text.encode("utf-8")


@pytest.mark.live
async def test_committed_fixture_copy_is_byte_identical(notebook_server):
    """The property the benchmark probes assert, independent of checkout policy."""
    original = BENCHMARK_FIXTURE.read_bytes()

    _manager, _url, _sid, copy = await notebook_server(
        original, name=BENCHMARK_FIXTURE.name
    )

    assert copy.read_bytes() == original, "the booted copy is not byte-identical"
    assert BENCHMARK_FIXTURE.read_bytes() == original, (
        "the committed benchmark fixture was modified by this test"
    )
