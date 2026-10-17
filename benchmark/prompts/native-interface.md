# Native arm — interface note

Your only interface to the notebook is a command named `pair`, already on your
`PATH` and already bound to the single notebook and session you may use. It
takes subcommands, never file, path, URL, or session arguments — passing one is
an error, by design.

Start with its own documentation, and trust it over anything else you read about
the tool:

```
pair --help
pair <subcommand> --help
```

The command covers notebook and session inspection, cell creation and running,
cell reads, variables, errors, and UI element values — but the authoritative list
is what `--help` prints. Each run prints a single JSON object; read `status`
first, and treat the `code` and `error` fields as the explanation of a refusal.

The code you pass when creating a cell runs inside the notebook's kernel, where
marimo's in-kernel code-mode helpers are available. Ask that environment about
them rather than guessing; it is the same API the command itself uses.

If `pair` refuses because the notebook or session identity no longer matches,
stop and report the refusal verbatim. Do not try to work around it.
