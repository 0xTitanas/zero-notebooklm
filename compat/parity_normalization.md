# ZeroNotebookLM — Parity Normalization / Equivalence Specification

**Schema version:** `parity_normalization/1`
**Target oracle:** `notebooklm-py==0.7.2`
**Effective from:** Phase 14A row-level parity harness hardening

---

## Purpose

This document defines the allowed normalization and equivalence rules that a row promoter
may apply when comparing upstream (`notebooklm-py==0.7.2`) and bare (`zero-notebooklm`)
output before claiming row-level parity. Any comparison that applies a transformation not
listed here is considered **unapproved**. Unapproved transformations must be added to this
document — and reviewed by the promotion authority (`jmc_nlb_011_row_review`) — before they
can be used to promote a row to `pass`.

> **Default rule:** If a normalization is not listed here, it is disallowed until explicitly
> added by a reviewer with authority over JMC-NLB-011.

---

## Rule families

### 1. Timestamp normalization (`timestamp`)

**Applies to:** Any field, log line, or JSON key whose value is a wall-clock timestamp,
ISO 8601 datetime string, Unix epoch integer/float, or relative elapsed-time indicator
(e.g. `"2 seconds ago"`).

**Allowed transformation:** Replace the literal timestamp value with a placeholder token
`<TIMESTAMP>` in both upstream and bare outputs before comparison. All timestamps within
a single comparison pair must be replaced consistently.

**Not allowed:** Replacing timestamps only in one side of the comparison, or using
timestamp proximity rather than exact-match after normalization.

**Row scope:** `cli`, `api`, `auth`, `rpc`, `self_test`.

---

### 2. ID / resource identifier normalization (`id_redaction`)

**Applies to:** Notebook IDs, source IDs, artifact IDs, note IDs, session tokens,
request IDs, trace IDs, correlation IDs, and any UUID/ULID that is generated at
runtime or differs between upstream and bare runs by design.

**Allowed transformation:** Replace the literal ID value with a placeholder token
`<ID>` in both upstream and bare outputs before comparison. ID normalization must
be applied structurally (by key name pattern) rather than by regex over the full
output blob, to avoid accidentally masking real content differences.

**Not allowed:** Normalizing IDs that are stable across runs (e.g. pinned fixture
IDs like `fake-notebook-0001`) unless both sides consistently emit the same stable
value.

**Row scope:** `cli`, `api`, `auth`, `rpc`.

---

### 3. Ordering normalization (`ordering`)

**Applies to:** Collections (lists, sets) whose order is not contractually guaranteed
by the API specification. Applies only where the upstream documentation or source code
explicitly states the order is unspecified or implementation-defined.

**Allowed transformation:** Sort both upstream and bare output collections by a
stable key (e.g. `id`, `name`, or `created_at`) before comparison. The sort key
must be specified in the row's `comparator` field or the row-level evidence bundle.

**Not allowed:** Silently ignoring order differences for collections where order is
specified by the API contract (e.g. ordered history, paginated results).

**Row scope:** `api`, `rpc`.

---

### 4. Whitespace and help formatting normalization (`whitespace`, `help_formatting`)

**Applies to:** CLI `--help` output, error message text, and any text where leading/
trailing whitespace or internal indentation varies due to terminal width or Click version
differences between upstream and bare.

**Allowed transformation:** Collapse internal runs of whitespace to a single space,
strip leading and trailing whitespace from each line, and strip trailing blank lines
from the output block. Option descriptions and usage lines may be re-wrapped to a
canonical 80-column width before comparison.

**Not allowed:** Removing or reordering option flags, changing option names, or
normalizing error messages that differ in content (not just formatting).

**Row scope:** `cli`.

---

### 5. Generated text / artifact semantics normalization (`generated_text`)

**Applies to:** AI-generated content fields — notebook summaries, note content,
mind-map text, quiz question bodies, audio script text, slide deck content — where
the upstream and bare outputs call the same Google NotebookLM generative API and
receive nondeterministic generated text.

**Allowed transformation:** For generative fields, verify only structural equivalence
(field presence, type, and non-empty content) rather than byte-level text match.
The row must declare `"nondeterministic_live"` in its `allowed_normalizations` to
use this rule.

**Not allowed:** Using this rule to mask differences in non-generative fields (IDs,
timestamps, state enums, error codes).

**Row scope:** `api`, `self_test`.

---

### 6. Cookie / token redaction (`cookie_redaction`, `token_redaction`)

**Applies to:** HTTP `Cookie` headers, `Set-Cookie` values, OAuth tokens, XSRF/CSRF
tokens, `__Secure-*` cookie values, `at=` batchexecute XSRF field values, and any
credential material that must not appear in comparison artifacts.

**Allowed transformation:** Replace the literal credential value with `<REDACTED>`
in both upstream and bare outputs before comparison. Redaction must occur in the
comparison artifact only — the live test must never log raw credential values.

**Not allowed:** Comparing redacted tokens (both sides `<REDACTED>`) as evidence of
equivalence for the token value itself. Presence/structure is all that can be compared.

**Row scope:** `auth`, `rpc`.

---

### 7. Platform path normalization (`platform_path`)

**Applies to:** Filesystem paths that appear in error messages, `--help` output, or
diagnostic text, where the path is OS-specific (e.g. `~/.config/notebooklm` on macOS
vs `%APPDATA%\notebooklm` on Windows).

**Allowed transformation:** Replace the OS-specific path prefix with a canonical
placeholder `<CONFIG_DIR>` before comparison. Only the path prefix should be replaced;
filename and extension differences are not covered by this rule.

**Not allowed:** Using this rule to mask path differences that indicate a real
behavioral divergence (e.g. bare writing to a different config directory than upstream).

**Row scope:** `cli`, `api`.

---

### 8. Traceback normalization (`traceback`)

**Applies to:** Python tracebacks emitted to stderr or captured in error output,
where file paths, line numbers, or internal frame sequences differ between upstream
and bare due to differences in internal implementation structure.

**Allowed transformation:** Mask internal file paths and line numbers in tracebacks
with `<PATH>:<LINE>`. The exception class name and message must still match
(after timestamp and ID normalization) — only the frame location metadata may be
masked.

**Not allowed:** Masking the exception class name, exception message content, or the
outermost error cause visible to the caller.

**Row scope:** `cli`, `api`.

---

### 9. Locale / timezone / terminal width normalization (`locale`, `terminal_width`)

**Applies to:** Output fields that vary by system locale (date format, number format),
timezone (datetime offset in display strings), or terminal width (wrapped help text,
table column widths in human-readable output).

**Allowed transformation:** For locale: normalize date/number display to a canonical
format (ISO 8601 / C locale) before comparison. For timezone: convert to UTC before
comparison. For terminal width: use a fixed 80-column canonical width for output
generation in both upstream and bare test harnesses.

**Not allowed:** Normalizing locale differences in user-visible content that is
not purely presentational (e.g. locale-dependent string values returned by the API).

**Row scope:** `cli`, `api`.

---

### 10. stdout / stderr / exit code normalization (`stdout_stderr_exit_code`)

**Applies to:** CLI commands where diagnostic text (progress spinners, timing info,
deprecation warnings) appears on stderr rather than stdout, or where exit codes are
defined only as "success / non-zero" rather than a specific non-zero value.

**Allowed transformation:** Compare stdout and stderr separately as specified by the
row's comparator. For exit codes, compare only success/failure polarity unless the
row's `required_evidence` specifies exact exit code matching.

**Not allowed:** Discarding stderr content that contains error messages or stack
traces relevant to the comparison.

**Row scope:** `cli`.

---

### 11. Nondeterministic live / generative operation normalization (`nondeterministic_live`)

**Applies to:** Any operation that is inherently nondeterministic when run against
live NotebookLM — including generative AI calls, real-time streaming chunk timing,
live session state, and concurrent-mutation scenarios.

**Allowed transformation:** For these rows, the comparison verifies **structural**
equivalence (schema shape, field set, type correctness, non-error exit) rather than
value equivalence. The row must explicitly declare `"nondeterministic_live"` in
`allowed_normalizations`.

**Not allowed:** Using structural-only comparison for rows that have deterministic
outputs (e.g. fixed fixture roundtrips, static API surface checks).

**Row scope:** `auth`, `api` (generative sub-paths), `rpc` (streaming timing only).

---

### 12. XSSI prefix normalization (`xssi_prefix`)

**Applies to:** batchexecute RPC responses that begin with the `)]}'\n` XSSI guard
prefix, which must be stripped before JSON parsing.

**Allowed transformation:** Strip the leading `)]}'\n` prefix from both upstream and
bare RPC responses before comparison. Both sides must emit the prefix (its presence
is part of the contract) before it is stripped for data comparison.

**Not allowed:** Stripping the prefix from only one side, or treating its absence
as acceptable.

**Row scope:** `rpc`.

### 13. Type annotation format normalization (`type_annotation_format`)

**Applies to:** Python type annotation strings in API surface comparisons — parameter
annotations, return type annotations, and attribute type hints — where upstream and bare
may emit equivalent but syntactically different annotation representations (e.g.
`Optional[str]` vs `str | None`, `Union[int, str]` vs `int | str`).

**Allowed transformation:** Normalize equivalent type annotation representations to a
canonical form before comparison. Specifically: `Optional[X]` ↔ `X | None`,
`Union[X, Y]` ↔ `X | Y` for two-argument unions. Both sides must be normalized
symmetrically.

**Not allowed:** Using this normalization to mask non-equivalent type annotations
(e.g. `str` vs `int`), or to ignore annotation presence vs absence differences.

**Row scope:** `api`.

---

## Review and update process

1. Any normalization not listed in a named rule family above is **disallowed** until
   explicitly added by a reviewer with authority over JMC-NLB-011.
2. To add a new normalization rule: open a change to this file, name the rule family,
   describe the allowed transformation, state what is not allowed, and list the affected
   row scopes. The change must be approved before any row uses the rule to claim `pass`.
3. Row-level `allowed_normalizations` values must reference rule family names from this
   document. Any value not matching a rule family name here is an audit error.

---

## Summary table

| Rule family | Key name | Row scopes |
| --- | --- | --- |
| Timestamp normalization | `timestamp` | cli, api, auth, rpc, self_test |
| ID / resource identifier normalization | `id_redaction` | cli, api, auth, rpc |
| Ordering normalization | `ordering` | api, rpc |
| Whitespace normalization | `whitespace` | cli |
| Help formatting normalization | `help_formatting` | cli |
| Generated text normalization | `generated_text` | api, self_test |
| Cookie redaction | `cookie_redaction` | auth, rpc |
| Token redaction | `token_redaction` | auth, rpc |
| Platform path normalization | `platform_path` | cli, api |
| Traceback normalization | `traceback` | cli, api |
| Locale normalization | `locale` | cli, api |
| Terminal width normalization | `terminal_width` | cli |
| Nondeterministic live normalization | `nondeterministic_live` | auth, api, rpc |
| XSSI prefix normalization | `xssi_prefix` | rpc |
| Type annotation format normalization | `type_annotation_format` | api |
