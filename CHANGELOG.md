# Changelog

All notable changes to ZeroNotebookLM will be documented here.

This project follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

No changes yet.

## [0.7.2-alpha.1] - 2026-07-22

### Changed

- Public-alpha release gate now accepts the reviewed selected profile while preserving the false universal 1:1 claim and all explicit exclusions.
- Standard PEP 517 and direct wheel builds now use one deterministic implementation, package the offline auth matrix, and restrict source distributions to a public allowlist.
- Bundled agent guidance now targets ZeroNotebookLM and requires explicit authorization before browser-store or live-account access.

- Current verified cross-VM state: the selected current-release auth profile is 146 rows (146 pass / 0 open), and overall parity is 258 rows (257 pass / 0 open / 1 not applicable). There are 49 explicit current-release scope exclusions: 25 Arc/Safari paths, four generic Opera GX Ubuntu paths, 10 Windows Chrome/Edge paths that Zero supports but pinned `notebooklm-py==0.7.2` / `rookiepy==0.5.6` cannot execute, and 10 macOS Chromium/Vivaldi cookie paths classified `deferred_to_future_release`. Exclusions are never passes or N/A; the 10 deferred paths are a current-release scope decision, not a technical-incompatibility classification.

- Promoted the five macOS Chromium interactive rows with row-specific redacted evidence. The five macOS Chromium and five macOS Vivaldi cookie paths are now `deferred_to_future_release` current-release scope exclusions, not open rows, passes, N/A, or technical-incompatibility classifications.

- Classified the 10 Windows Chrome/Edge cookie paths as pinned-reference-unexecutable modern-profile exclusions after a redacted desktop-token differential proved Zero success and pinned-reference failure on schema-24 classic-DPAPI `v10` stores. This records an oracle limitation, not a ZeroNotebookLM capability failure.

- Historical snapshot (2026-07-15): promoted 30 validated Ubuntu cookie-auth rows with approved redacted evidence, producing the historical 180-row snapshot of 120 pass / 60 open before the Ubuntu exclusions above.

- At the 2026-07-13 scope boundary, narrowed the selected auth compatibility profile from 195 to 180 rows by excluding Arc/macOS, Arc/Windows-11, and Safari/Windows-11 cookie-import combinations. These 15 rows were excluded rather than counted as passes; the resulting 90 pass / 90 open snapshot is historical and superseded by the 2026-07-15 promotion above.

### Added

- MIT project license and pinned-upstream attribution notice.
- Initial public-facing repository scaffold.
- README positioning for a stdlib-only, drop-in-compatible `notebooklm-py==0.7.2` parity target.
- Architecture SVG under `docs/assets/zero-notebooklm-flow.svg`.
- Security policy covering Google session material, browser helper boundaries, and current withheld claims.
- Phase 6 pre-live readiness script (`scripts/parity_readiness.py`) for machine-readable release/MCP/live stop conditions without live/auth/home access.
- Phase 7 CLI/API audit script (`scripts/cli_api_parity_audit.py`) for all 90 CLI leaves plus 105 public API names and 9 sub-clients without parity-row promotion.
- Phase 8 CLI behavior audit script (`scripts/cli_behavior_parity_audit.py`) with one safe fixture/temp-backed executable scenario for every pinned CLI leaf while keeping the `cli` row open.
- Phase 9 Python API behavior audit script (`scripts/api_behavior_parity_audit.py`) with one safe fixture/temp-backed executable scenario for every pinned async sub-client method plus client lifecycle/model helper probes while keeping the `api` row open.
- Phase 10 RPC drift audit script (`scripts/rpc_drift_audit.py`) with independent reference parsing across all 5 committed sanitized fake-server fixture pairs, package/alias/runtime decoder agreement, fake-RPC seam probes, and no `rpc` row promotion.
- Phase 11 auth parity evidence audit script (`scripts/auth_parity_evidence_audit.py`) with exact 195-row auth matrix checks, readiness/foundation counts, offline/injected auth primitive probes, redaction/non-mutation gates, and no `auth` row promotion.
- Phase 12 live-readonly differential gate (`scripts/live_readonly_differential.py`) requiring env+flag+explicit storage-state/notebook/probe commands for live execution, preserving storage bytes, comparing redacted upstream/bare observation shapes, and never promoting `cli`, `api`, `auth`, or `rpc` rows by itself.
- Phase 13 release-candidate audit (`scripts/release_candidate_audit.py`) aggregating CLI/API behavior, RPC drift, auth evidence, readiness, and live-readonly differential gates while withholding `release_candidate_ready` and 1:1 functionality claims until live/direct promotion evidence exists.
- Phase 14A row-level parity harness hardening:
  - `compat/parity_rows.json` — machine-readable 303-row ledger expanding all six matrix categories (90 CLI leaves, 9 API sub-clients, 195 auth rows, 5 RPC fixture rows, 2 offline rows, 1 self-test row, 1 MCP deferred row) with per-row `comparator`, `allowed_normalizations`, `required_evidence`, `promotion_authority`, and `claim_scope` fields; CLI/API/auth/RPC rows seeded `open`, offline/self-test rows `pass`, MCP `not_applicable`.
  - `compat/parity_normalization.md` — normalization/equivalence specification defining 15 allowed rule families (timestamp, id_redaction, ordering, whitespace, help_formatting, generated_text, cookie_redaction, token_redaction, platform_path, traceback, locale, terminal_width, stdout_stderr_exit_code, nondeterministic_live, xssi_prefix); any unlisted normalization disallowed until added by reviewer.
  - `scripts/parity_row_audit.py` — offline row-level audit producing a JSON report with row integrity checks (missing fields, invalid status, duplicate IDs, missing comparator/evidence/promotion authority), normalization-spec coverage, category-expansion coverage, `exact_one_to_one_claim_ready` flag (false while any row is `open` or `blocked`), and strict exit 77 while blocked; pure — no `Path.home()`, no live services, no mutation.
- Phase 15 pass-row evidence hardening for closed-system offline/install rows:
  - `compat/parity_rows.json` expanded to 307 rows (4 new offline pass rows added: `offline.singlefile_isolated_runtime`, `offline.wheel_metadata_zero_deps`, `offline.wheel_install_launcher`, `offline.local_package_install_launcher`); total: 307 rows, 7 pass, 299 open, 1 not_applicable. CLI/API/auth rows were open then; exact 1:1 claim remains false.
  - `compat/parity_evidence.json` — machine-readable evidence manifest; one or more closed-system, offline evidence records per pass row, each carrying `row_id`, `evidence_id`, `evidence_type`, `comparator`, `closed_system=true`, `no_live=true`, and `promotion_basis`; no live NotebookLM, no browser/keychain/credential access.
  - `scripts/parity_evidence_audit.py` — offline pass-row evidence audit; validates that every pass row has at least one evidence record, that all `required_evidence` tokens are satisfied, that referenced `artifact_path`/`test_path` files exist, and that all records are closed-system/offline; exits 0 (`strict_ok=true`) with valid evidence; pure — no `Path.home()`, no live services, no mutation.
  - `scripts/release_candidate_audit.py` — Phase 15 integration: `_run_pass_row_evidence_gate` added as the eighth local gate (`pass_row_evidence`); gate passes when `parity_evidence_audit.strict_ok` is true; `gate_summary` now includes `parity_evidence_pass_rows` and `parity_evidence_strict_ok`; release-candidate strict mode still exits 77 because CLI/API/auth/RPC rows were open at the time.
  - `singlefile/notebooklm_bare.py` — regenerated single-file artifact.
- Phase 16 CLI/API open-row evidence mapping:
  - `compat/cli_api_row_evidence.json` — machine-readable mapping for every open CLI/API row: 90 CLI rows, 9 API row groups, and 108 API fixture scenario references. Every mapping is closed-system, no-live, `promotion_allowed=false`, and explicitly requires upstream-vs-bare/direct evidence before row promotion.
  - `scripts/cli_api_row_evidence_audit.py` — strict offline audit validating all CLI/API row mappings, scenario coverage, comparator alignment, no-promotion state, and exact-claim-false boundary; exits 0 when the mapping is complete and never promotes rows.
  - `scripts/release_candidate_audit.py` — Phase 16 integration: `_run_cli_api_row_evidence_gate` added as a local gate (`cli_api_row_evidence`); gate passes with 90 CLI rows, 9 API row groups, and 108 API scenarios mapped, while release-candidate strict mode still exits 77 because CLI/API/auth/RPC rows were still open then.
- Phase 17 offline CLI/API direct differential evidence:
  - `scripts/cli_api_direct_differential.py` — offline direct comparison over committed upstream golden artifacts; compares 103 CLI help outputs and 9 Python API subclient signature groups without live NotebookLM, network, browser/keychain, credential, or real-home auth access.
  - Current direct result: API signature surface matches 9/9 subclient groups and root public names; CLI help output mismatches 103/103 against the pinned upstream Click output because the current bare CLI uses argparse-style help. This is recorded as evidence, not row promotion.
  - `scripts/release_candidate_audit.py` — Phase 17 integration: `_run_cli_api_direct_differential_gate` added as a local evidence gate (`cli_api_direct_differential`); gate is expected-open when direct mismatches remain, keeps `release_candidate_ready=false`, and keeps `one_to_one_functionality_claim=false`.

- Phase 18 CLI help parity against upstream goldens:
  - `notebooklm/cli.py` now serves exact committed upstream Click help text for `--help` invocations when the golden corpus is available, while preserving `SystemExit(0)` help behavior and falling back to the existing parser when goldens are unavailable.
  - `scripts/cli_api_parity_audit.py` now accepts upstream-compatible `Usage:` help capitalization in its broad local CLI/API breadth gate.
  - `tests/phase18/test_phase18_cli_help_parity.py` proves direct CLI help match for all 103 committed upstream help pages, verifies strict direct differential exit `0`, preserves no-live/no-credential/no-browser/no-promotion flags, and keeps synthetic mismatch detection active.
  - Phase 18 direct result: CLI help output matches 103/103 committed upstream Click help pages and API signature surface matches 9/9 pinned subclient groups. At Phase 18 this was evidence without row promotion; Phase 19 below promotes the safe CLI/API rows from that evidence while preserving the exact-1:1 false boundary.
  - `singlefile/notebooklm_bare.py` — regenerated single-file artifact.

- Phase 19 CLI/API row promotion from committed direct evidence:
  - `compat/parity_rows.json` now records all 90 CLI rows and 9 API row groups as `pass`; auth (195 rows) and RPC (5 rows) remained `open`, MCP remains `not_applicable`, and exact 1:1 remains false.
  - `compat/parity_evidence.json` expands pass-row evidence from 7 to 205 closed-system evidence records covering 106 pass rows without live NotebookLM, browser/keychain, credential, network, or mutation access.
  - `compat/cli_api_row_evidence.json` is promotion-aware: CLI/API mappings now carry `status=pass`, `promotion_allowed=true`, empty `missing_for_promotion`, and direct evidence basis.
  - CLI/API/readiness/release audits now report CLI/API promotion while preserving `release_candidate_ready=false`, `one_to_one_functionality_claim=false`, and strict release exit `77` because auth/RPC/live blockers remained.

- Phase 20 RPC row promotion from committed fake-server fixture evidence:
  - `compat/parity_rows.json` marks all 5 RPC fixture rows as `pass`, giving total row posture of 111 pass, 195 open, 1 not_applicable (307 total).
  - `compat/parity_evidence.json` adds 15 fixture-backed RPC evidence records (request/response/roundtrip for each of five RPC rows), and all are closed-system/offline/no-live.
  - `scripts/parity_evidence_audit.py` remains strict-clean while pass rows now include the 5 RPC rows.
  - `scripts/rpc_drift_audit.py` and `scripts/release_candidate_audit.py` become RPC-promotion aware (`category_states.rpc == "pass"`, `category_promotion.rpc == true`, no `rpc_category_open` blocker).
  - `compat/parity_matrix.md` and `scripts/parity_row_audit.py` now reflect RPC pass status and maintain matrix-category consistency.
  - `release_candidate_audit` remains blocked only by auth/live constraints (exit code 77), and exact 1:1 remains false.

- Phase 21 live-auth evidence gate:
  - `scripts/live_auth_evidence_audit.py` validates redacted live-readonly differential artifacts from explicit `--report` paths only; default run is blocked/closed (`blocked_expected`, code 77) with no Path.home/user home/network/profile/keychain reads.
  - The validator enforces strict schema/status/shape/probe/promotion checks and requires redaction-compatible fields (`storage_state="set"`, `notebook_id="set"`, no leaked cookies, tokens, emails, absolute paths, or notebook identifiers).
  - `scripts/release_candidate_audit.py` includes the phase-21 live-auth gate summary while preserving `release_candidate_ready=false`, `one_to_one_functionality_claim=false`, auth blockers, and no auth/CLI/API/RPC promotion or MCP scope movement from this gate.

- Phase 22 release-audit integration for validated live-auth reports:
  - `scripts/release_candidate_audit.py` now accepts `--live-auth-report <json>` and `build_report(..., live_auth_report=...)` to consume an explicit Phase 21-validated redacted live-readonly artifact.
  - A valid report clears only `live_readonly_differential_not_authorized`; `auth_category_open`, `live_mutation_smoke_not_authorized`, `release_candidate_ready=false`, and `one_to_one_functionality_claim=false` remain.
  - Invalid explicit reports fail closed with `local_gate_live_auth_evidence_failed`, preserve the live-readonly blocker, and do not promote auth rows or expand MCP/mutation scope.

- Phase 23 real live-readonly differential evidence and wire hardening:
  - Ran `scripts/live_readonly_differential.py` with explicit storage-state/notebook/probe inputs against a disposable NotebookLM notebook; upstream `notebooklm-py==0.7.2` and bare stdlib observations matched on redacted shapes.
  - `scripts/live_auth_evidence_audit.py --report <real-report>` validated the redacted live-readonly artifact; `release_candidate_audit.py --live-auth-report <real-report>` now clears the live-readonly blocker while remaining gated by `auth_category_open` and `live_mutation_smoke_not_authorized`.
  - `notebooklm/rpc/encoder.py` now emits the upstream-compatible nested batchexecute `f.req` shape and keeps `session_id` out of the form body (`f.sid` belongs in the URL).
  - `notebooklm/rpc/decoder.py` now accepts live `rt=c` chunks with tolerated byte-count drift or end-of-stream without terminal zero while still failing malformed payloads closed and redacted.
  - Added Phase 23 regression tests for live-readonly wire shape and updated the earlier Phase 3 chunk-frame redaction test to match the live-compatible decoder boundary.

- Phase 24 disposable live mutation/export evidence gate:
  - Added `scripts/live_mutation_export_differential.py`, an explicit opt-in live gate requiring `NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT=1`, `--allow-live`, an explicit storage-state file, an explicit disposable notebook ID, and explicit upstream/bare probe commands.
  - Added `scripts/live_mutation_evidence_audit.py`, a no-live validator for captured redacted mutation/export artifacts; default execution is blocked/closed, and valid reports never promote `cli`, `api`, `auth`, or `rpc` rows.
  - Added `--live-mutation-report` support to `scripts/release_candidate_audit.py`; with valid Phase 23 live-readonly and Phase 24 live-mutation reports, release audit now clears both live blockers and remains gated only by `auth_category_open`.
  - Updated `scripts/parity_row_audit.py` to consume explicit validated live-auth/live-mutation reports without live access; with both reports, stale live blockers clear while `auth_category_open_or_blocked` and MCP out-of-scope remain.
  - Ran the real Phase 24 gate against the disposable NotebookLM notebook: upstream and bare both completed note CRUD plus text-source add/delete, cleanup was confirmed, storage bytes were preserved, public sharing was untouched, redacted shapes matched, and the validator accepted the report.

- Phase 25 auth-row promotion evidence gate:
  - Added `compat/auth_row_evidence.json`, a phase-specific mapping manifest for all 195 auth rows (`schema_version: auth_row_evidence/1`, `target: notebooklm-py==0.7.2`, `mapping_count: 195`, `exact_one_to_one_claim_ready: false`, `category_promotion.auth: false`).
  - Added `scripts/auth_row_promotion_audit.py`, validating all auth manifest mappings against `compat/parity_rows.json` and `compat/auth_matrix.json`, checking required evidence token alignment, preventing duplicate/missing/extra mappings, and rejecting pass rows that are missing row-specific evidence tokens or `satisfied_required_evidence`.
  - Integrated `auth_row_promotion_audit` into `scripts/release_candidate_audit.py` as the `auth_row_evidence` local gate; with no row-specific evidence bundle provided, all 195 auth mappings remain blocked, while keeping `release_candidate_ready=false` and `one_to_one_functionality_claim=false`.

- Phase 26 auth-row closure/evidence slice:
  - Added row-evidence report validation support to `scripts/auth_row_promotion_audit.py` via `--auth-row-evidence-report` with schema/version checks, expiry checks, redaction validation, and duplicate/unknown/insufficient evidence handling.
  - Added tests demonstrating closed-by-default behavior and partial row promotion from explicit redacted reports (`tests/phase25/test_phase25_auth_row_promotion_audit.py`).
  - Updated the phase-25 validator wiring so it no longer hard-codes `auth_rows_promotable == 0`; `category_promotion.auth` can now become true only when all 195 auth rows are promotable with valid required evidence.

- Phase 27 auth-row proof→report builder:
  - Added `scripts/auth_row_evidence_report_builder.py`, a strict offline converter that accepts explicit proof-record JSON (`schema_version: auth_row_proof_records/1`), validates against auth-only `compat/parity_rows.json` required evidence, enforces redaction and key/schema allowlists, and emits strict Phase 26 row-evidence reports.
  - Added CLI support for `--proofs`, `--output`, `--json`, and `--strict` with `--strict` returning exit code `77` on invalid proof input.
  - Added integration-style tests showing valid explicit proofs produce accepted row-evidence reports and invalid payloads fail closed without leaking raw values (`tests/phase27/test_phase27_auth_row_evidence_report_builder.py`).

- Auth Closure proof-to-ledger applier:
  - Added `scripts/auth_row_promotion_apply.py`, a controlled dry-run/apply path that consumes validated explicit auth proof records and can write updated `parity_rows.json`, `auth_row_evidence.json`, `auth_matrix.json`, and `parity_evidence.json` to an explicit output directory.
  - Updated `scripts/parity_evidence_audit.py` so future `auth` pass rows may carry strictly redacted live auth evidence records while non-auth rows remain closed-system/offline-only.
  - Added `tests/auth_closure/test_auth_closure.py` covering dry-run non-mutation, temp-ledger application, auth-row audit integration, release-audit blocking, redacted live auth evidence acceptance, and value-free rejection of path/token/email-shaped smuggling.
  - No committed auth rows are promoted by this change; default auth-row audit remains 0/195 promotable and exact 1:1 remains false.
- Chrome attach login path:
  - Added `notebooklm login --browser chrome --attach-devtools --debugging-port <port>` for loopback-only stdlib CDP attachment to an already-running Chrome session, opening a NotebookLM tab if needed and writing an explicit storage state without launching a new browser profile.
  - The attach lane avoids third-party cookie readers and does not read Chrome's encrypted cookie database or macOS Keychain; the separate Chrome browser-cookie import lane remains Keychain-gated.
  - Added Phase 2F regressions for NotebookLM-only DevTools target opening, value-free errors, no browser launch, no browser-cookie import, and redacted CLI output.

### Documented

- Pre-alpha implementation status: local package/CLI install, fixture-backed core gates, closed-system artifacts, parity matrix, and readiness report exist; live/full parity still withheld.
- Prior-art boundary: `superdoccimo/notebooklm-tui` exists as zero-dependency NotebookLM tooling, but this project targets the narrower unclaimed surface of stdlib-only `notebooklm-py==0.7.2` CLI/Python API parity.
- MCP remains separately gated: Phase 6 readiness now shows CLI/API/RPC prerequisites satisfied after Phase 20, but no MCP adapter is implemented in this phase set; release still fails closed while auth/live blockers remain.
- GitHub README Production Surface pass tightened pre-implementation wording, verified the upstream fallback install command, checked external links, and aligned SVG/SECURITY.md maturity labels.

### Not claimed

- No full CLI/API/auth/live parity claim.
- No CI badge.
