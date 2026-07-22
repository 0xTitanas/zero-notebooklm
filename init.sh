#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[init] ZeroNotebookLM harness preflight"

for f in AGENTS.md feature_list.json clean-state-checklist.md evaluator-rubric.md quality-document.md; do
  if [[ ! -f "$f" ]]; then
    echo "missing required harness file: $f" >&2
    exit 1
  fi
done

python3 -m json.tool feature_list.json >/dev/null
echo "[init] feature_list.json valid"

echo "[init] canonical verification commands:"
echo "  - python -m pip install --no-build-isolation --no-deps ."
echo "  - notebooklm --help"
echo "  - python -m notebooklm.self_test --json"
echo "  - python scripts/parity_readiness.py --json"
echo "  - python scripts/cli_api_parity_audit.py --json"
echo "  - python scripts/auth_row_promotion_audit.py --strict --json"
echo "  - python scripts/release_candidate_audit.py --strict-alpha --json"
echo "  - python -m pytest"
echo "  - ruff check ."

if [[ "${RUN_PROJECT_CHECKS:-0}" == "1" ]]; then
  echo "[init] RUN_PROJECT_CHECKS=1; running first canonical check"
  python -m pip install --no-build-isolation --no-deps .
else
  echo "[init] set RUN_PROJECT_CHECKS=1 to run the first canonical project check"
fi

echo "[init] ok"
