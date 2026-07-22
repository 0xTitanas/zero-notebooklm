# ZeroNotebookLM Clean-State Checklist

Last updated: 2026-06-29 15:36 CDT

Use this before stopping, handing work to another worker, committing, pushing, or claiming completion. Record dated run notes in the work summary or a local ledger when present.

## Baseline

- [ ] `AGENTS.md`, `feature_list.json`, and any local ledger were read before work began.
- [ ] Active task/feature and out-of-scope boundaries were identified.
- [ ] `init.sh` or the documented verification commands were considered before edits.

## Verification Evidence

- [ ] Focused checks for touched files ran, or the reason they were skipped is recorded.
- [ ] Relevant regression/lint/static checks ran when available, or the reason they were skipped is recorded.
- [ ] `feature_list.json` status changes have command output, artifact paths, or review handles as evidence.

## Repository Hygiene

- [ ] `git status --short` was inspected.
- [ ] No private `.ai-bridge/` packets, temp/debug files, raw logs, build artifacts, or unintended generated files are staged.
- [ ] Secret-shaped material was not added to docs, prompts, fixtures, logs, commits, or handoff files.
- [ ] The work summary or local ledger reflects current verified state and next safe action.

## Project Boundary

- [ ] Boundary respected: Public-alpha claims name the explicit exclusions; universal exact 1:1, production readiness, and full live/auth parity remain unclaimed.
- [ ] Boundary respected: Do not add rookiepy or any third-party runtime browser-cookie reader dependency.
- [ ] Boundary respected: Do not read browser stores, keychains, home auth state, raw cookies, OAuth tokens, notebook IDs, source contents, or account emails unless the task explicitly authorizes that exact live/auth lane.

## Stop Conditions

- [ ] Any credential, live account, destructive, public-exposure, service-lifecycle, or public-claim action was explicitly authorized before proceeding.
- [ ] If verification failed after one focused repair loop, the blocker was recorded instead of expanding scope.
