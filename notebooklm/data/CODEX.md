# Repository Guidelines

ZeroNotebookLM is a stdlib-only Gemini Notebook client and CLI targeting the
public surface of `notebooklm-py==0.7.2`.

## Verify

```sh
python -m pip install --no-build-isolation --no-deps .
python -m notebooklm.self_test --json
python scripts/cli_api_parity_audit.py --json
python scripts/release_candidate_audit.py --json
```

Use fixture-backed and fake-server checks by default. Do not access live
NotebookLM, browser stores, keychains, credentials, account metadata, notebook
IDs, or source contents without explicit authorization for that exact lane.

Preserve zero third-party runtime dependencies, keep CLI and Python API behavior
on the shared core, and keep diffs small. Explicit current-release exclusions
are documented scope boundaries, not passes or universal compatibility claims.
