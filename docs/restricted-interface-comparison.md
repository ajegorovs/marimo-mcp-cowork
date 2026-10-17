# Restricted interface comparison — harness

This document describes the **harness** that stages, restricts and grades the
comparison of two agent-facing interfaces on one mission: this repository's
structured MCP wrapper against the pinned native `marimo pair` CLI. It does not
contain results; a run produces its own evidence.

The comparison exists to answer a bounded question — *is the native interface
worth further work?* — not to certify either surface as safe for unattended use.
The upstream server routes by a mutable session id and accepts no caller-side
expected identity, so neither interface can offer atomic target protection at
this layer. Every guard below is **detection on a captured snapshot**, not
prevention.

## Shape of a run

One arm at a time, four steps, each a separate program:

| Step | Program | What it does |
| --- | --- | --- |
| Stage | `benchmark/mission_setup.py` | Copies the mission notebook byte-exactly into a disposable root, boots one loopback server, creates one session, enforces the guards, writes the run manifest plus a control copy of the anchor. |
| Run | `benchmark/arm_run.py` | Prepares an ephemeral configuration home for the arm and launches the agent in one fresh session with a pinned model, provider, reasoning level, turn limit and wall-clock budget. |
| Grade | `benchmark/mission_grade.py` | Independent of the agent's report: fixture unchanged, `normalized` cell saved on disk, fresh process and session, kernel values compared with the oracle, zero cell errors. |
| Stop | `benchmark/mission_teardown.py` | Stops that arm's server tree by recorded pid with identity checks, and fails if a confirmed member survives. |

`benchmark/prompts/mission.md` is the shared mission, given verbatim to both
arms. Each arm additionally receives exactly one interface note
(`native-interface.md`, `wrapper-interface.md`); the *difference between those
notes is the variable under test*, so neither is padded to help its arm.

## The restrictions, and why each exists

- **One server, one session, one notebook per arm.** Staged with a byte-exact
  copy (`read_bytes`/`write_bytes`) and refused when the census is anything but
  exactly one session resolving to that copy. A second session, a rotation, or a
  reused id invalidates the run rather than being silently followed.
- **The adapter takes no target argument.** The native arm reaches the notebook
  only through `benchmark/pair_cli.py`, which reads server, session and notebook
  from the run manifest and re-checks the census before every subcommand. The
  agent cannot name a different target even if it tries.
- **An anchor the arm is not pointed at.** `mission_setup --control-dir` writes a
  control copy of the identity plus a digest of the manifest file; grading
  refuses unless the two agree, and refuses outright without a control copy
  unless the caller explicitly declares the anchor unverified.
- **Per-arm isolation.** Each arm gets its own ephemeral configuration home and
  exactly the tools its interface needs — a shell for the native arm (to invoke
  the shim), the MCP server for the wrapper arm, and the generic file toolset for
  neither, since the mission forbids editing the notebook with file tools.
- **Equal budgets.** Model, provider, reasoning level, turn limit and wall-clock
  budget are all required with no defaults, and hash into one fingerprint that
  **both arms must print identically**. The toolset list is deliberately not in
  that hash: it is the interface under test, so it is recorded beside the
  fingerprint instead. A difference between the two fingerprints is a harness
  fault, not a finding.
- **Grading speaks the arm's language.** The kernel is read with the same
  framework version the arm ran under (MCP tools for the wrapper arm, the native
  CLI for the native arm). A single reader for both arms fails the arm whose
  framework lacks the feature the reader uses, which reads as a failed mission on
  work that actually succeeded.

## What it does and does not establish

Establishes: whether one interface let an agent complete a fixed mission on a
disposable notebook, at what turn/time cost, and whether the resulting artifact
reopens correctly — under one machine, one framework version per arm and a stated
number of repeats.

Does not establish: that either interface is safe for unattended use (the guards
are snapshot checks, and both arms necessarily have an in-kernel code channel);
that results transfer across versions, platforms or remote topologies; anything
about token cost unless usage was actually captured; or any migration decision.

## Reproducing a run

Paths come from arguments; nothing here is machine-specific. Substitute the two
interpreters (one per arm's framework version) and the wrapper's
`marimo-inspect` executable:

```text
mission_setup.py --canonical benchmark/notebooks/mission_clean.py --root <ROOT> \
  --python <ARM_PYTHON> --arm {native,wrapper} --state-dir <STATE> \
  --manifest <MANIFEST> --control-dir <CONTROL>

arm_run.py --arm {native,wrapper} --home <HOME> --workdir <WORK> \
  --state-dir <STATE> \
  --mission benchmark/prompts/mission.md --interface benchmark/prompts/<ARM>-interface.md \
  --transcript <TRANSCRIPT> --model <MODEL> --provider <PROVIDER> --reasoning <LEVEL> \
  --max-turns <TURNS> --run-budget <SECONDS> \
  [--manifest <MANIFEST> --shim benchmark/pair_cli.py | --mcp-command <MARIMO_INSPECT>]

mission_grade.py --manifest <MANIFEST> --python <ARM_PYTHON> --state-dir <GRADE_STATE> \
  --control-dir <CONTROL> --reader {native,wrapper}

mission_teardown.py --manifest <MANIFEST>
```

`arm_run.py --dry-run` writes the configuration, prompt and launcher, prints the
exact child command line and the budget fingerprint, and launches nothing — the
cheapest way to confirm both arms are comparable before spending any inference.
