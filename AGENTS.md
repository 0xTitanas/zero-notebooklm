# Repository Guidelines

## Project Boundary

ZeroNotebookLM is a stdlib-only, drop-in-compatible NotebookLM client and CLI targeting a pinned notebooklm-py release. The project goal is dependency-free installability and row-by-row parity evidence, not an offline NotebookLM claim.

Do not claim exact 1:1 functionality, production readiness, security audit completion, or full live/auth parity while auth rows remain open. MCP stays deferred until CLI/API parity and auth evidence justify it.

## Progress Ledger

Use the local `progress.md` as the state ledger when it is present. It is not part
of the public distribution.

- Read `progress.md` before starting implementation or live/auth work when present.
- Update local ledgers at phase boundaries and before handing off when present.
- Keep it redacted: no cookies, tokens, auth headers, account emails, notebook IDs, source contents, browser profile paths, or raw storage state.
- A higher-level external project ledger may be backed up separately, but workers should be able to recover current state from this repo-local file.


## Harness Workflow

- Treat tracked repository files as the public system of record. At the start of nontrivial work, read `AGENTS.md`, `feature_list.json`, any local ledger present, and the relevant product/architecture docs before editing.
- Use `init.sh` or the documented verification commands as the baseline preflight. Record intentionally skipped checks in the work summary or a local ledger.
- Keep one active task/feature at a time. Do not mark `feature_list.json` entries as passing without command output, artifact evidence, or a clear review handle.
- Before stopping or handing work to another worker, update any local ledger and run or explicitly review `clean-state-checklist.md`.
- Keep private one-off worker packets, raw logs, credentials, browser/session data, and local-only handoff material out of public commits.
- For GitHub operations, use repository-scoped credentials through a non-logging helper. Never put tokens in remotes, commands, logs, prompts, or tracked files.

## Development Commands

Run from the repository root:

```sh
python -m pip install --no-build-isolation --no-deps .
notebooklm --help
python -m notebooklm.self_test --json
python scripts/parity_readiness.py --json
python scripts/cli_api_parity_audit.py --json
python scripts/release_candidate_audit.py --json
```

Use strict variants only when the task specifically targets release/readiness gates; expected blocked status is not automatically a failure when live/auth gates are intentionally closed.

## Coding Rules

- Preserve zero third-party Python-package runtime dependencies.
- Keep CLI, Python API, single-file artifact, and future MCP adapter on one shared core.
- Treat notebooklm-py==0.7.2 as the pinned oracle until explicitly changed.
- Use committed fixtures/fake servers for ordinary checks; live NotebookLM access is opt-in only.
- Do not read browser stores, keychains, home auth state, raw cookies, OAuth tokens, notebook IDs, source contents, or account emails unless the task explicitly authorizes that exact live/auth lane.
- Redact live evidence artifacts and never put secrets or private NotebookLM contents in prompts, logs, fixtures, examples, commits, or docs.

## Safety Gates

Ask before live NotebookLM probes, auth-row promotion, live mutation/export tests, MCP scope movement, public release language, credential handling changes, or any destructive notebook/source/profile operation.
