"""Phase 8 CLI behavior parity audit tests.

Phase 8 consolidates scattered historical fixture-backed CLI checks into a single
executable behavior audit over every pinned CLI leaf.  It deliberately keeps the
`cli` parity row open because this is not an upstream-vs-bare live differential
closure.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType


def _load_audit_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "cli_behavior_parity_audit.py"
    )
    spec = importlib.util.spec_from_file_location("cli_behavior_parity_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_audit_module()


def _phase8_env(tmp_path: Path, repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = tmp_path / "home"
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "PYTHONPATH": str(repo_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _cli_state_from_matrix(repo_root: Path) -> str:
    matrix = repo_root / "compat" / "parity_matrix.md"
    for line in matrix.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "cli" and len(cells) >= 4:
            return cells[3]
    raise AssertionError("cli row missing from parity matrix")


def test_phase8_scenario_registry_covers_every_pinned_cli_leaf_exactly_once(
    cli_surface,
):
    leaves = [scenario.leaf for scenario in audit.build_scenarios()]
    counts = Counter(leaves)

    assert len(leaves) == 90
    assert set(leaves) == set(cli_surface["leaf_commands"])
    assert [leaf for leaf, count in counts.items() if count != 1] == []


def test_phase8_strict_json_audit_runs_all_scenarios_and_passes(repo_root, tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "cli_behavior_parity_audit.py"),
            "--strict",
            "--json",
        ],
        cwd=repo_root,
        env=_phase8_env(tmp_path, repo_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "/".join(("", "Users", "example")) not in proc.stdout + proc.stderr
    assert "__Secure-1PSID" not in proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "passed"
    assert payload["pinned_leaf_total"] == 90
    assert payload["scenario_total"] == 90
    assert payload["covered_leaf_total"] == 90
    assert payload["passed_scenario_count"] == 90
    assert payload["failed_scenario_count"] == 0
    assert payload["missing_leaf_commands"] == []
    assert payload["extra_leaf_commands"] == []
    assert payload["duplicate_leaf_commands"] == []
    assert payload["failures"] == []
    assert payload["cli_category_state"] == "pass"
    assert payload["category_promotion"] == {"cli": True}
    assert "Phase 19 direct" in payload["blocked_reason"]
    assert all(result["passed"] for result in payload["scenarios"])


def test_phase8_human_report_declares_non_promotion_boundary(repo_root, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "cli_behavior_parity_audit.py")],
        cwd=repo_root,
        env=_phase8_env(tmp_path, repo_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Phase 8 CLI behavior parity audit" in proc.stdout
    assert "status: passed" in proc.stdout
    assert "pinned leaves: 90" in proc.stdout
    assert "scenarios: 90" in proc.stdout
    assert "category promotion: cli=true" in proc.stdout
    assert "cli category state: pass" in proc.stdout
    assert "Phase 19 direct" in proc.stdout


def test_phase8_cli_parity_matrix_row_can_be_promoted_by_later_phase(repo_root):
    report = audit.run_audit()

    assert report["status"] == "passed"
    assert report["cli_category_state"] == "pass"
    assert report["category_promotion"] == {"cli": True}
    assert _cli_state_from_matrix(repo_root) == "pass"


def test_phase8_audit_report_is_path_redacted_and_uses_relative_temp_outputs():
    report = audit.run_audit()
    rendered = json.dumps(report, sort_keys=True)

    assert "/".join(("", "Users", "example")) not in rendered
    assert "phase8-cli-audit-" not in rendered
    assert "storage_state.json" not in rendered
    output_lists = [result["temp_output_files"] for result in report["scenarios"]]
    assert any("fulltext.md" in outputs for outputs in output_lists)
    assert all(
        not str(item).startswith("/") for outputs in output_lists for item in outputs
    )
