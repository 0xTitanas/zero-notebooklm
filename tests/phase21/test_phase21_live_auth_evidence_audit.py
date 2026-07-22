"""Phase 21 live auth evidence artifact audits.

This phase validates redacted live-readonly differential artifacts and confirms no
auth row promotion is inferred from them.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCHEMA_VERSION = "live_auth_evidence_audit/1"
TARGET = "notebooklm-py==0.7.2"
SYNTHETIC_HOME = "/".join(("", "Users", "example"))


def _load_live_auth_module(repo_root: Path):
    path = repo_root / "scripts" / "live_auth_evidence_audit.py"
    spec = importlib.util.spec_from_file_location("_live_auth_audit", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_release_candidate_module(repo_root: Path):
    path = repo_root / "scripts" / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_rc_audit", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_category_states(repo_root: Path) -> dict[str, str]:
    path = repo_root / "compat" / "parity_matrix.md"
    states: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in {
            "cli",
            "api",
            "auth",
            "rpc",
            "offline",
            "self-test",
        }:
            states[cells[0]] = cells[3]
    return {
        "cli": states.get("cli", "open"),
        "api": states.get("api", "open"),
        "auth": states.get("auth", "open"),
        "rpc": states.get("rpc", "open"),
    }


def _base_live_report(repo_root: Path) -> dict:
    states = _parse_category_states(repo_root)
    return {
        "schema_version": "live_readonly_differential/1",
        "target": TARGET,
        "status": "pass",
        "strict_exit_code": 0,
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        "storage_state": "set",
        "notebook_id": "set",
        "read_only_operations": [
            "list_notebooks",
            "get_notebook",
            "list_sources",
            "get_source",
            "list_notes",
            "get_note",
            "list_artifacts",
            "get_artifact",
            "get_status",
            "check_auth",
            "inspect_auth",
        ],
        "smoke": {
            "status": "passed",
            "exit_code": 0,
        },
        "storage_preserved": True,
        "shape_match": True,
        "blockers": [],
        "upstream_probe": {"ok": True, "error": ""},
        "bare_probe": {"ok": True, "error": ""},
        "observations": {
            "upstream_shape": {
                "type": "dict",
                "size": 1,
                "entries": [
                    {
                        "key": {"type": "str", "length": 7, "empty": False},
                        "value": [
                            {
                                "type": "dict",
                                "size": 2,
                                "entries": [
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
            "bare_shape": {
                "type": "dict",
                "size": 1,
                "entries": [
                    {
                        "key": {"type": "str", "length": 7, "empty": False},
                        "value": [
                            {
                                "type": "dict",
                                "size": 2,
                                "entries": [
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        },
        "category_promotion": {
            "cli": False,
            "api": False,
            "auth": False,
            "rpc": False,
        },
        "category_states": {
            "cli": states["cli"],
            "api": states["api"],
            "auth": states["auth"],
            "rpc": states["rpc"],
        },
    }


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir(exist_ok=True)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def test_default_no_args_is_blocked_expected_and_closed_repo_local(
    repo_root: Path, monkeypatch
) -> None:
    mod = _load_live_auth_module(repo_root)
    parity = repo_root / "compat" / "parity_matrix.md"
    before = parity.read_bytes()

    def _forbidden_home() -> Path:
        raise AssertionError(
            "live auth audit must not call Path.home() in default mode"
        )

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    report = mod.build_report()

    assert report["status"] == "blocked_expected"
    assert report["strict_exit_code"] == 77
    assert report["evidence_validated"] is False
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["target"] == TARGET
    assert parity.read_bytes() == before
    assert report["category_promotion"] == {
        "cli": False,
        "api": False,
        "auth": False,
        "rpc": False,
    }
    assert report["category_states"]["auth"] == "open"
    assert report["category_states"]["cli"] == "pass"
    assert report["category_states"]["api"] == "pass"
    assert report["category_states"]["rpc"] == "pass"


def test_synthetic_live_readonly_report_validates_pass_and_non_promotional(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)

    report_path = tmp_path / "live_diff_pass.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "pass"
    assert report["strict_exit_code"] == 0
    assert report["evidence_validated"] is True
    assert report["category_promotion"] == {
        "cli": False,
        "api": False,
        "auth": False,
        "rpc": False,
    }
    assert report["category_states"]["auth"] == "open"
    assert report["category_states"]["cli"] == "pass"
    assert report["category_states"]["api"] == "pass"
    assert report["category_states"]["rpc"] == "pass"
    assert report["validation"]["source_status"] == "pass"


def test_unreadable_report_path_and_basename_are_not_echoed(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    secretish_report = tmp_path / "nb-SHOULD-NOT-LEAK-ya29-LOOKALIKE.json"

    report = mod.build_report(argv=["--report", str(secretish_report)])
    report_json = json.dumps(report)

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 64
    assert "report_path not readable" in report["validation"]["violations"]
    assert str(secretish_report) not in report_json
    assert secretish_report.name not in report_json
    assert str(tmp_path) not in report_json


def test_binary_report_fails_closed_without_traceback_or_path_echo(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    binary_report = tmp_path / "nb-SHOULD-NOT-LEAK-binary.png"
    binary_report.write_bytes(b"\x89PNG\r\n\x1a\n\x00")

    report = mod.build_report(argv=["--report", str(binary_report)])
    report_json = json.dumps(report)

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 64
    assert "report_path not readable" in report["validation"]["violations"]
    assert str(binary_report) not in report_json
    assert binary_report.name not in report_json
    assert str(tmp_path) not in report_json


@pytest.mark.parametrize(
    "leak_name, value",
    [
        ("cookie", "SID=SYNTHSIDVALUE1234567890"),
        ("path", f"{SYNTHETIC_HOME}/scratch/synctest.json"),
        ("email", "auth-check@example.com"),
        ("token", "ya" + "29." + "a0AfH6SMB_verylongtokenvalue1234567890"),
        ("notebook", "nb-SYNTH-ID-OPEN"),
        ("secret", "-".join(("sk", "1234567890abcdef12345678"))),
    ],
)
def test_report_leakage_is_rejected(
    repo_root: Path, tmp_path: Path, leak_name: str, value: str
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["notes"] = {leak_name: value}

    report_path = tmp_path / "live_diff_leaky.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert "redaction" in " ".join(report["validation"]["violations"]).lower()


def test_structured_cookie_object_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["raw_cookie_probe"] = {
        "cookies": [{"name": "SID", "value": "SYNTHCOOKIEVALUE1234567890"}]
    }

    report_path = tmp_path / "live_diff_structured_cookie.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "structured_cookie_value" in violation
        for violation in report["validation"]["violations"]
    )


def test_nested_structured_cookie_value_is_rejected(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["raw_cookie_probe"] = {
        "cookies": [{"name": "SID", "value": ["SYNTHCOOKIEVALUE1234567890"]}]
    }

    report_path = tmp_path / "live_diff_nested_structured_cookie.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "structured_cookie_value" in violation
        for violation in report["validation"]["violations"]
    )


def test_malformed_structured_cookie_name_does_not_crash(repo_root: Path) -> None:
    mod = _load_live_auth_module(repo_root)

    list_name_hits = mod._find_structural_redaction_hits(
        {"name": [], "value": "SYNTHCOOKIEVALUE1234567890"}
    )
    dict_name_hits = mod._find_structural_redaction_hits(
        {"name": {"nested": "SID"}, "value": "SYNTHCOOKIEVALUE1234567890"}
    )

    assert "structured_sensitive_field" in list_name_hits
    assert "structured_sensitive_field" in dict_name_hits


def test_untrusted_report_values_are_not_echoed_in_violations(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    secretish = f"{SYNTHETIC_HOME}/secret/nb-SHOULD-NOT-LEAK-ya29-LOOKALIKE"
    payload["read_only_operations"] = [secretish + "-delete_notebook"]
    payload["category_promotion"][secretish] = True

    report_path = tmp_path / "live_diff_untrusted_values.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])
    report_json = json.dumps(report)

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert secretish not in report_json
    assert "SHOULD-NOT-LEAK" not in report_json
    assert (
        "read_only_operations contains forbidden mutation term"
        in report["validation"]["violations"]
    )
    assert (
        "category_promotion keys must match cli/api/auth/rpc"
        in report["validation"]["violations"]
    )


def test_duplicate_json_keys_cannot_hide_raw_secret_values(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    raw = json.dumps(payload).replace(
        '"storage_state": "set"',
        f'"storage_state": "{SYNTHETIC_HOME}/secret-auth.json", "storage_state": "set"',
        1,
    )

    report_path = tmp_path / "live_diff_duplicate_keys.json"
    report_path.write_text(raw, encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])
    report_json = json.dumps(report)

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert "report contains duplicate JSON keys" in report["validation"]["violations"]
    assert f"{SYNTHETIC_HOME}/secret-auth.json" not in report_json


@pytest.mark.parametrize(
    "modifier",
    [
        lambda payload: payload.__setitem__("mutation_allowed", True),
        lambda payload: payload.update({"status": "fail", "strict_exit_code": 77}),
        lambda payload: payload.__setitem__("shape_match", False),
        lambda payload: payload["category_promotion"].__setitem__("cli", True),
        lambda payload: payload["category_promotion"].__setitem__("mcp", True),
        lambda payload: payload["category_states"].__setitem__("auth", "pass"),
        lambda payload: payload["read_only_operations"].append("delete_notebook"),
        lambda payload: payload.__setitem__("upstream_probe", "bad"),
        lambda payload: payload.__setitem__("bare_probe", "bad"),
        lambda payload: payload.__setitem__("smoke", "bad"),
        lambda payload: payload.__setitem__("smoke", {"status": [], "exit_code": 0}),
        lambda payload: payload["observations"].__setitem__(
            "upstream_shape", {"type": []}
        ),
        lambda payload: payload["observations"].__setitem__(
            "bare_shape", {"type": {"nested": "str"}}
        ),
        lambda payload: payload["observations"].__setitem__(
            "upstream_shape", {"sources": [{"title": "raw observation"}]}
        ),
    ],
)
def test_invalid_live_auth_report_shapes_rejected(
    repo_root: Path, tmp_path: Path, modifier
) -> None:
    mod = _load_live_auth_module(repo_root)
    payload = _base_live_report(repo_root)
    modifier(payload)

    report_path = tmp_path / "live_diff_invalid.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["validation"]["violations"]


def test_release_candidate_includes_live_auth_evidence_gate_summary(
    repo_root: Path,
) -> None:
    rc = _load_release_candidate_module(repo_root)
    report = rc.build_report(repo_root=repo_root)

    assert "local_gates" in report
    assert "live_auth_evidence" in report["local_gates"]
    assert report["local_gates"]["live_auth_evidence"]["status"] == "blocked_expected"
    assert (
        report["local_gates"]["live_auth_evidence"]["summary"]["evidence_validated"]
        is False
    )
    assert report["local_gate_status"] == "pass"
    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False

    blockers = set(report["remaining_blockers"])
    assert "auth_category_open" in blockers
    assert "live_readonly_differential_not_authorized" in blockers
    assert "live_mutation_smoke_not_authorized" in blockers


def test_cli_json_strict_blocked_by_default_and_pass_with_valid_report(
    repo_root: Path, tmp_path: Path
) -> None:
    scripts_dir = repo_root / "scripts"
    script = scripts_dir / "live_auth_evidence_audit.py"
    payload = _base_live_report(repo_root)

    valid_path = tmp_path / "live_diff_valid.json"
    valid_path.write_text(json.dumps(payload), encoding="utf-8")

    default_proc = subprocess.run(
        [sys.executable, str(script), "--json", "--strict"],
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert default_proc.returncode == 77
    default_data = json.loads(default_proc.stdout)
    assert default_data["status"] == "blocked_expected"

    pass_proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--report",
            str(valid_path),
            "--json",
            "--strict",
        ],
        env=_clean_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert pass_proc.returncode == 0
    pass_data = json.loads(pass_proc.stdout)
    assert pass_data["status"] == "pass"
    assert pass_data["evidence_validated"] is True
