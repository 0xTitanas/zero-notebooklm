# ZeroNotebookLM — Parity Matrix

**Target oracle:** `notebooklm-py==0.7.2`  
**Wheel SHA-256:** `d850cfea2494732bc5f153406a9637c3ee5fe931d87901d101c026df2a6ecf65`  
**Source commit:** `915b5321e1c1f411e23bd8265517be8740749e56`  
**Generated:** 2026-06-23T01:52:41+00:00

## Closure states (pass-only, JMC-NLB-011)

- `pass` — differential upstream-vs-bare result matches for the same sanitized fixture or disposable live account.
- `open` — not yet proven. **Not** a success state.
- `blocked` — cannot currently be proven (tier/quota/platform). **Not** a success state.

No row is recorded `pass` without a real differential result or a row-specific closed-system proof. The dedicated `self-test` category is backed by a package-contained offline fixture run; the `offline` category is backed only by isolated `python -I -S` import-origin/denylist and fixture-runtime probes. The Phase 3B17 direct-comparison runtime (`notebooklm._parity_runtime` plus `notebooklm_bare.rpc`) now runs offline CLI/API/Auth/RPC artifact probes against the frozen 0.7.2 compat files and sanitized fake-server fixtures. Phase 19 promotes CLI/API rows from committed direct evidence; auth remains open while RPC has now been promoted from committed fake-server fixture evidence.

## Category matrix

| Category | Scope (from pinned upstream) | Differential basis | State |
| --- | --- | --- | --- |
| cli | 90 leaf commands across 103 Click-tree nodes (13 groups) | upstream Click `--help`/error goldens vs bare CLI | pass |
| api | 105 public names, 9 sub-clients, 44 exceptions, 29 enums | upstream import/signature/enum/exception goldens vs bare | pass |
| auth | interactive login (45 rows) + browser-cookie import (101 in-profile rows; 49 explicit exclusions) | upstream auth matrix vs bare | open |
| rpc | batchexecute encode/decode + streaming parse (6 upstream rpc modules) | fake-server fixture contract | pass |
| offline | `python -I -S` import-origin audit + denylist | isolated source/single-file runtime audit with denylisted imports blocked | pass |
| self-test | bundled offline self-test against sanitized fixtures | package-contained offline fixture run (`python -m notebooklm.self_test --json`) | pass |

All 146 selected current-release auth rows pass. The broader auth category remains
open because 49 explicit exclusions are outside that row set; exclusions are never
promoted to pass or not-applicable.

## CLI leaf-command rows (seeded `open` by generator)

| Leaf command | State |
| --- | --- |
| `notebooklm agent show` | pass |
| `notebooklm artifact delete` | pass |
| `notebooklm artifact export` | pass |
| `notebooklm artifact get` | pass |
| `notebooklm artifact list` | pass |
| `notebooklm artifact poll` | pass |
| `notebooklm artifact rename` | pass |
| `notebooklm artifact retry` | pass |
| `notebooklm artifact suggestions` | pass |
| `notebooklm artifact wait` | pass |
| `notebooklm ask` | pass |
| `notebooklm auth check` | pass |
| `notebooklm auth inspect` | pass |
| `notebooklm auth logout` | pass |
| `notebooklm auth refresh` | pass |
| `notebooklm clear` | pass |
| `notebooklm completion` | pass |
| `notebooklm configure` | pass |
| `notebooklm create` | pass |
| `notebooklm delete` | pass |
| `notebooklm doctor` | pass |
| `notebooklm download audio` | pass |
| `notebooklm download cinematic-video` | pass |
| `notebooklm download data-table` | pass |
| `notebooklm download flashcards` | pass |
| `notebooklm download infographic` | pass |
| `notebooklm download mind-map` | pass |
| `notebooklm download quiz` | pass |
| `notebooklm download report` | pass |
| `notebooklm download slide-deck` | pass |
| `notebooklm download video` | pass |
| `notebooklm generate audio` | pass |
| `notebooklm generate cinematic-video` | pass |
| `notebooklm generate data-table` | pass |
| `notebooklm generate flashcards` | pass |
| `notebooklm generate infographic` | pass |
| `notebooklm generate mind-map` | pass |
| `notebooklm generate quiz` | pass |
| `notebooklm generate report` | pass |
| `notebooklm generate revise-slide` | pass |
| `notebooklm generate slide-deck` | pass |
| `notebooklm generate video` | pass |
| `notebooklm history` | pass |
| `notebooklm language get` | pass |
| `notebooklm language list` | pass |
| `notebooklm language set` | pass |
| `notebooklm list` | pass |
| `notebooklm login` | pass |
| `notebooklm metadata` | pass |
| `notebooklm note create` | pass |
| `notebooklm note delete` | pass |
| `notebooklm note get` | pass |
| `notebooklm note list` | pass |
| `notebooklm note rename` | pass |
| `notebooklm note save` | pass |
| `notebooklm profile create` | pass |
| `notebooklm profile delete` | pass |
| `notebooklm profile list` | pass |
| `notebooklm profile rename` | pass |
| `notebooklm profile switch` | pass |
| `notebooklm rename` | pass |
| `notebooklm research status` | pass |
| `notebooklm research wait` | pass |
| `notebooklm share add` | pass |
| `notebooklm share public` | pass |
| `notebooklm share remove` | pass |
| `notebooklm share status` | pass |
| `notebooklm share update` | pass |
| `notebooklm share view-level` | pass |
| `notebooklm skill install` | pass |
| `notebooklm skill show` | pass |
| `notebooklm skill status` | pass |
| `notebooklm skill uninstall` | pass |
| `notebooklm source add` | pass |
| `notebooklm source add-drive` | pass |
| `notebooklm source add-research` | pass |
| `notebooklm source clean` | pass |
| `notebooklm source delete` | pass |
| `notebooklm source delete-by-title` | pass |
| `notebooklm source fulltext` | pass |
| `notebooklm source get` | pass |
| `notebooklm source guide` | pass |
| `notebooklm source list` | pass |
| `notebooklm source refresh` | pass |
| `notebooklm source rename` | pass |
| `notebooklm source stale` | pass |
| `notebooklm source wait` | pass |
| `notebooklm status` | pass |
| `notebooklm summary` | pass |
| `notebooklm use` | pass |

## Python API sub-client rows

| Sub-client | Class | State |
| --- | --- | --- |
| `client.artifacts` | `ArtifactsAPI` | pass |
| `client.chat` | `ChatAPI` | pass |
| `client.mind_maps` | `MindMapsAPI` | pass |
| `client.notebooks` | `NotebooksAPI` | pass |
| `client.notes` | `NotesAPI` | pass |
| `client.research` | `ResearchAPI` | pass |
| `client.settings` | `SettingsAPI` | pass |
| `client.sharing` | `SharingAPI` | pass |
| `client.sources` | `SourcesAPI` | pass |
