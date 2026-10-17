# Restricted mission — shared statement (both arms)

This text is **identical** for both arms and must not be edited per arm. Each arm
gets it verbatim, plus exactly one interface note (`native-interface.md` or
`wrapper-interface.md`). The mission is the unit of comparison; the interface is
the variable.

---

## Mission

The notebook in front of you is a working signal-processing scratchpad: an input
vector, a processed array derived from it, a plot, and a visible slider called
`gain` that scales the processing.

Your job is to extend it into a notebook that reports its own diagnostic:

1. Add a cell that computes `normalized = float(abs(processed).mean() / abs(input_vector).max())`
   and displays the value visibly (the final expression of the cell counts).
2. Execute the notebook so that the new cell runs inside the live kernel, not
   just on disk.
3. With the slider at its default value, read the diagnostic. Report the number
   you actually observed.
4. Change `gain` to `4` and confirm the diagnostic changes accordingly. Read and
   report that number too.
5. Restore `gain` to `2`.
6. Leave the notebook with no cell errors, and make sure your edit is saved to
   the notebook file on disk — not only present in the running kernel.

## What you must not do

- Do not edit the notebook file with a text editor, a shell redirect, or any
  file-write tool. All changes must go through the interface described in your
  interface note.
- Do not create, open, or target any other notebook, session, or server. There is
  exactly one, and it is already selected for you.
- Do not restart or stop the server.

## When you are done

Reply with a short report: the two diagnostic values you observed, what you
changed, and anything that surprised you about the interface. Your report is not
the grade — the notebook's on-disk state and a fresh kernel run of it are graded
independently — so describe only what you actually did and saw.
