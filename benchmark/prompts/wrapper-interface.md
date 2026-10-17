# Wrapper arm — interface note

Your only interface to the notebook is the MCP server `marimo-inspect`, already
configured and already bound to the single notebook and session you may use. Its
tools take no file, path, URL, or session arguments on your behalf — the binding
is already established, and it refuses calls that name a different target.

Start with the server's own documentation:

- list the MCP resources it publishes, and
- read the co-work loop and live-safety resources in full before acting.

They describe the intended order of work, the read-before-edit rule, and the
result fields that tell you what actually happened.

The tool surface covers notebook and session inspection, cell creation and
running, cell reads, variables, errors, and UI element values. Every tool returns
a structured payload; read `status` first, and treat a `reason` field as the
explanation of a refusal.

If a tool refuses because the notebook or session identity no longer matches,
stop and report the refusal verbatim. Do not try to work around it.
