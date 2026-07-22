---
name: notebooklm
description: Use the stdlib-only ZeroNotebookLM client and CLI for Gemini Notebook automation.
---

# ZeroNotebookLM

ZeroNotebookLM provides the `notebooklm` package and CLI while targeting the
documented `notebooklm-py==0.7.2` surface. It is an alpha release with explicit
browser/OS exclusions; it does not claim universal 1:1 or production readiness.

## Install from a checkout

```sh
python -m pip install --no-build-isolation --no-deps .
notebooklm --help
python -m notebooklm.self_test --json
```

Do not install `notebooklm-py` into the same environment: it owns the same
`notebooklm` import and CLI names.

## Authentication

The normal interactive path is:

```sh
notebooklm login
notebooklm auth check --test --json
```

Browser-cookie import reads sensitive local browser state. Use it only after the
user explicitly authorizes the exact browser/profile lane. Never print or retain
passwords, cookies, tokens, account emails, notebook IDs, or source contents.

## Safe operating rules

- Ask before login, browser-store access, live NotebookLM calls, mutation,
  export/download, sharing changes, or destructive commands.
- Prefer `--json`, explicit profiles, and full notebook IDs for automation.
- Keep `storage_state.json` and browser profiles out of source control and logs.
- Treat private Gemini Notebook RPCs as drift-prone.
- Do not claim unsupported browsers or universal compatibility; consult the
  repository auth matrix and README for the current release scope.

## Common commands

```sh
notebooklm auth check --json
notebooklm list --json
notebooklm create "Research"
notebooklm source add "https://example.com"
notebooklm ask "Summarize the sources"
```

Use `notebooklm <command> --help` before invoking unfamiliar or destructive
operations.
