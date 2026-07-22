# Auth Row Remaining Blockers

Current verified cross-VM state: all 146 selected current-release auth rows pass; zero selected auth rows remain open. Overall parity is 258 rows: 257 pass, zero open, and one not applicable. There are 49 explicit current-release scope exclusions: 25 Arc/Safari paths, four generic Opera GX Ubuntu paths, 10 Windows Chrome/Edge cookie paths that Zero supports but pinned `notebooklm-py==0.7.2` / `rookiepy==0.5.6` cannot execute, and 10 macOS Chromium/Vivaldi cookie paths classified `deferred_to_future_release`. Exclusions are never passes or N/A; the 10 deferred paths are a current-release scope decision, not a technical-incompatibility classification.

## Promoted here

- `auth.interactive.chrome.macos.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Chrome/macOS interactive CLI rows carry row-specific redacted proof.
- `auth.interactive.msedge.macos.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Microsoft Edge/macOS interactive CLI rows carry row-specific redacted proof from the temporary signed-in Edge DevTools session and live auth check.
- `auth.interactive.chromium.macos.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Chromium/macOS interactive CLI rows carry row-specific redacted proof from the temporary signed-in Chromium DevTools session and live auth checks.
- `auth.cookie_import.chrome.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Chrome/macOS browser-cookie import rows carry row-specific redacted proof from the signed-in normal Chrome Default profile and live auth checks.
- `auth.cookie_import.firefox.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Firefox/macOS browser-cookie import rows carry row-specific redacted proof from the signed-in local Firefox session and live auth checks.
- `auth.cookie_import.safari.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Safari/macOS browser-cookie import rows carry row-specific redacted proof from the signed-in local Safari session and live auth checks.
- `auth.cookie_import.brave.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Brave/macOS browser-cookie import rows carry row-specific redacted proof from the temporary signed-in Brave session and live auth checks.
- `auth.cookie_import.edge.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Edge/macOS browser-cookie import rows carry row-specific redacted proof from the temporary signed-in Edge session and live auth checks.
- `auth.cookie_import.opera.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Opera/macOS browser-cookie import rows carry row-specific redacted proof from the temporary signed-in Opera session and live auth checks.
- `auth.cookie_import.opera_gx.macos.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Opera GX/macOS browser-cookie import rows carry row-specific redacted proof from the temporary signed-in Opera GX session and live auth checks.
- `auth.cookie_import.firefox.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Firefox/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Firefox session and live auth checks.
- `auth.cookie_import.brave.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Brave/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Brave session and live auth checks.
- `auth.cookie_import.chromium.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Chromium/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Chromium session and live auth checks.
- `auth.cookie_import.vivaldi.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Vivaldi/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Vivaldi session and live auth checks.
- `auth.cookie_import.opera.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Opera/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Opera session and live auth checks.
- `auth.cookie_import.opera_gx.windows11.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Opera GX/Windows browser-cookie import rows carry row-specific redacted proof from the signed-in Windows VM Opera GX session and live auth checks.

- `auth.interactive.chrome.windows11.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Chrome/Windows interactive CLI rows carry row-specific redacted proof from the signed-in loopback DevTools Chrome session and live auth checks.
- `auth.interactive.msedge.windows11.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Edge/Windows interactive CLI rows carry row-specific redacted proof from the signed-in loopback DevTools Edge session and live auth checks.
- `auth.interactive.chromium.windows11.*` (5 rows: `login`, `refresh`, `status`, `logout`, `doctor`) — Chromium/Windows interactive CLI rows carry row-specific redacted proof from the signed-in loopback DevTools Chromium session and live auth checks.
- `auth.cookie_import.firefox.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Firefox/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.
- `auth.cookie_import.brave.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Brave/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.
- `auth.cookie_import.chromium.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Chromium/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.
- `auth.cookie_import.edge.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Edge/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.
- `auth.cookie_import.opera.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Opera/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.
- `auth.cookie_import.vivaldi.ubuntu.*` (5 rows: `import`, `profile_select`, `account_select`, `inspect`, `refresh`) — Vivaldi/Ubuntu browser-cookie import rows carry row-specific redacted proof and live auth checks.

## Latest current-system probe

2026-07-18 CDT Windows desktop-token differential and approved classification: Zero succeeded against current Chrome and Edge schema-24 classic-DPAPI `v10` profiles, while the exact pinned reference failed cookie decryption/profile discovery before authentication. The 10 Windows Chrome/Edge cookie paths are therefore excluded from the strict selected profile as pinned-reference-unexecutable modern-profile paths, never marked pass or N/A. No Windows or Ubuntu selected row remains open.

2026-07-20 CDT macOS Chromium interactive probe and approved promotion: the five Chromium/macOS interactive rows passed row-specific redacted evidence and live auth checks. The five Chromium/macOS cookie rows and five Vivaldi/macOS cookie rows are `deferred_to_future_release` current-release scope exclusions, not open rows, passes, N/A, or technical-incompatibility classifications; universal exact 1:1 and release claims remain false.

2026-07-18 CDT Ubuntu desktop probe and approved promotion: all in-profile Ubuntu rows passed. Promoted Ubuntu interactive (15), Chrome cookie (5), and Opera GX profile-select (1), bringing the canonical in-profile ledger to 141 pass / 25 open. Exact 1:1 and release claims remain false. This supersedes the historical snapshot below.

2026-07-06 10:24 CDT historical Windows VM WSL attempt: Ubuntu WSL1 had no WSLg/display or signed-in Linux browser session, and WSL2 was unavailable. No Ubuntu rows were promoted in that attempt; this blocker was later superseded by the Ubuntu desktop evidence above.

2026-07-06 09:06 CDT redacted Windows VM interactive rerun after the operator signed into the visible isolated DevTools windows: Chrome, Edge, and Chromium Windows interactive login/refresh/status/logout/doctor rows passed live auth checks and were promoted. Redacted artifacts are ignored under `.ai-bridge/windows-interactive-proofs-20260706/`, `.ai-bridge/live-auth-row-probe-windows11-20260706T140615Z/`, `.ai-bridge/live-auth-row-probe-windows11-20260706T140618Z/`, and `.ai-bridge/live-auth-row-probe-windows11-20260706T140621Z/`.

2026-07-06 03:49 CDT redacted Windows VM probe after adding the pinned upstream Windows Opera/Opera GX roaming profile roots: Opera and Opera GX Windows import/profile-select/account-select/inspect/refresh rows passed live auth checks and were promoted. Chrome and Edge stores remain readable and contain required rows, but those rows are app-bound `v20` (`APPB`) and direct user DPAPI unwrap is unavailable; Safari remains Windows-unsupported. Redacted artifacts are ignored under `.ai-bridge/windows-opera-proofs-20260706/` and `.ai-bridge/live-auth-row-probe-windows11-20260706T084816Z/`.

2026-07-06 04:10 CDT redacted Windows VM interactive rerun after enabling attach-only probes for all upstream interactive browser labels: Chrome, Edge, and Chromium DevTools-launched sessions timed out waiting for required NotebookLM auth cookies, so no Windows interactive rows were promoted. These rows need a signed-in loopback DevTools browser session before promotion; normal non-DevTools signed-in windows are not enough proof for the interactive row lane. Redacted artifacts are ignored under `.ai-bridge/live-auth-row-probe-windows11-20260706T085648Z/`, `.ai-bridge/live-auth-row-probe-windows11-20260706T090007Z/`, and `.ai-bridge/live-auth-row-probe-windows11-20260706T090316Z/`.

2026-07-01 22:29 CDT redacted temporary-browser macOS rerun: Brave, Edge, Opera, and Opera GX cookie import/inspect/refresh/profile-select/account-select paths passed live auth checks; Edge interactive login captured required cookies and `auth check --test` passed. Redacted summary/proof artifacts are ignored under `.ai-bridge/temp-macos-cookie-probe-20260701-222524/`, `.ai-bridge/temp-macos-auth-row-promotion-20260701-222611/`, `.ai-bridge/temp-macos-interactive-20260701-222647-msedge/`, and `.ai-bridge/temp-macos-msedge-interactive-promotion-20260701-222907/`.

## Deferred macOS future-release scope exclusions

Per the operator, the remaining Chromium and Vivaldi macOS cookie rows are `deferred_to_future_release` current-release scope exclusions. They are not selected current-release parity rows and are never counted as pass or N/A; this does not support a universal exact 1:1 auth-parity claim.

- `auth.cookie_import.chromium.macos.*` (5 rows): `deferred_to_future_release` scope exclusion. Interactive Chromium is promoted; this cookie-import path is neither pass nor N/A.
- `auth.cookie_import.vivaldi.macos.*` (5 rows): `deferred_to_future_release` scope exclusion. It is neither pass nor N/A, and its deferral is not a technical-incompatibility classification.

## Selected current-release profile

- All 146 selected current-release auth rows pass; none is open.
- Ubuntu-LTS-Linux: all selected rows pass; four generic Opera GX paths remain excluded.
- Windows-11: all selected rows pass. Zero supports current Chrome/Edge cookie import, but those 10 strict-parity paths are excluded because the pinned reference cannot execute modern schema-24 profiles. Arc/Windows and Safari/Windows are also outside the selected current-release profile.
- macOS: selected rows pass; 10 Chromium/Vivaldi cookie paths are `deferred_to_future_release` current-release scope exclusions as listed above.
