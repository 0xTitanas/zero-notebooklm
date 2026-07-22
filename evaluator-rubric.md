# ZeroNotebookLM Evaluator Rubric

Last updated: 2026-06-29 15:36 CDT

Use this for no-edit review, human review, or maintainer verification after meaningful changes. Do not let the implementing worker self-approve.

| Dimension | Score 0 | Score 1 | Score 2 | Evidence required |
|---|---|---|---|---|
| Scope discipline | Changed unrelated areas or expanded phase | Mostly scoped with minor drift | Stayed inside active feature/task and stop conditions | Diff/stat, active feature ID, out-of-scope check |
| Correctness | Fails stated behavior or acceptance criteria | Partial behavior with gaps | Meets stated behavior | Focused tests, manual evidence, artifact paths |
| Verification | No real checks run | Narrow checks only or missing evidence | Focused + relevant regression/static checks with results | Command output, CI, logs, screenshots as applicable |
| Safety/privacy | Secrets, live access, public exposure, or destructive risk mishandled | Boundary unclear | Boundary explicit and respected | Secret scan, safety-gate notes, redacted artifacts |
| Maintainability | Overbuilt, duplicated, or brittle | Acceptable but rough | Simple, readable, aligned with repo patterns | Code review notes, architecture/import checks |
| Handoff readiness | No current state or next step | Some notes but ambiguous | `feature_list.json` and work summary current | Updated files and next safe action |

## Verdict

- [ ] Approve
- [ ] Approve with follow-up
- [ ] Block

## Review Notes

- Findings:
- Evidence reviewed:
- Required follow-up:
