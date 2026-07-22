"""Phase 27 auth-row evidence report builder tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TARGET = "notebooklm-py==0.7.2"


def _load_script(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_builder(repo_root: Path):
    return _load_script(
        repo_root / "scripts" / "auth_row_evidence_report_builder.py",
        "_phase27_auth_row_report_builder",
    )


def _load_row_evidence_audit(repo_root: Path):
    return _load_script(
        repo_root / "scripts" / "auth_row_promotion_audit.py",
        "_phase27_auth_row_audit",
    )


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _read_auth_row(root: Path):
    rows = json.loads(
        (root / "compat" / "parity_rows.json").read_text(encoding="utf-8")
    )["rows"]
    return next(row for row in rows if row.get("category") == "auth")


def _read_two_auth_rows(root: Path):
    rows = json.loads(
        (root / "compat" / "parity_rows.json").read_text(encoding="utf-8")
    )["rows"]
    auth_rows = [row for row in rows if row.get("category") == "auth"]
    assert len(auth_rows) >= 2
    return auth_rows[0], auth_rows[1]


def _non_auth_row_id(root: Path) -> str:
    rows = json.loads(
        (root / "compat" / "parity_rows.json").read_text(encoding="utf-8")
    )["rows"]
    return next(row for row in rows if row.get("category") != "auth").get("id", "")


def _base_proof_payload(row_id: str, tokens: list[str]) -> dict:
    return {
        "schema_version": "auth_row_proof_records/1",
        "target": TARGET,
        "generated_at": "2026-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "rows": [
            {
                "row_id": row_id,
                "proofs": [
                    {
                        "token": token,
                        "evidence_id": f"{row_id}::{token}",
                        "evidence_type": (
                            "session_credential"
                            if token == "session_credential_evidence"
                            else "live_differential"
                        ),
                        "status": "pass",
                        "redacted": True,
                    }
                    for token in tokens
                ],
            }
        ],
    }


def _raise_diagnostics(exc: Exception) -> str:
    if hasattr(exc, "violations"):
        return "\n".join(exc.violations)
    return str(exc)


def test_builder_produces_phase26_row_evidence_report_used_by_auth_row_promotion_audit(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    audit = _load_row_evidence_audit(repo_root)

    row = _read_auth_row(repo_root)
    row_id = row["id"]
    required_tokens = list(dict.fromkeys(row.get("required_evidence", [])))

    proof_payload = _base_proof_payload(row_id, required_tokens)
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)
    output_path = tmp_path / "auth_row_evidence_report.json"

    report = builder.build_report(proof_path, repo_root=repo_root, output=output_path)

    assert report["schema_version"] == "auth_row_evidence_report/1"
    assert report["target"] == TARGET
    assert set(report.keys()) == {
        "schema_version",
        "proof_builder",
        "proof_schema_version",
        "target",
        "generated_at",
        "expires_at",
        "rows",
    }
    assert report["proof_builder"] == "auth_row_evidence_report_builder.py"
    assert report["proof_schema_version"] == "auth_row_proof_records/1"
    assert report["rows"][0]["row_id"] == row_id
    assert report["rows"][0]["satisfied_required_evidence"] == sorted(required_tokens)
    assert {
        proof["token"] for proof in report["rows"][0]["proofs"]
    } == set(required_tokens)
    assert output_path.is_file()

    audit_report = audit.build_report(
        repo_root=repo_root,
        row_evidence_report=output_path,
    )

    assert audit_report["auth_row_evidence"]["validated"] is True
    assert audit_report["auth_row_evidence"]["row_evidence_count"] == 1
    assert audit_report["auth_rows_promotable"] == 146
    assert audit_report["auth_rows_blocked"] == 0


def test_builder_missing_required_token_fails_closed_no_report_written(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)

    row = _read_auth_row(repo_root)
    tokens = list(dict.fromkeys(row.get("required_evidence", [])))
    proof_payload = _base_proof_payload(row["id"], tokens[:1])
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)
    output_path = tmp_path / "auth_row_evidence_report.json"

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root, output=output_path)
    diagnostics = _raise_diagnostics(exc.value)

    assert "missing required" in diagnostics
    assert not output_path.exists()
    assert "missing required" in diagnostics.lower()


def test_builder_rejects_reused_evidence_ids_across_auth_rows(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    first, second = _read_two_auth_rows(repo_root)
    tokens = list(dict.fromkeys(first.get("required_evidence", [])))
    proof_payload = _base_proof_payload(first["id"], tokens)
    second_row = json.loads(json.dumps(proof_payload["rows"][0]))
    second_row["row_id"] = second["id"]
    proof_payload["rows"].append(second_row)
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert "row-specific" in diagnostics
    assert "live_differential_result" not in diagnostics
    assert first["id"] not in diagnostics
    assert second["id"] not in diagnostics


def test_builder_rejects_row_agnostic_unique_evidence_ids(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    first, second = _read_two_auth_rows(repo_root)
    tokens = list(dict.fromkeys(first.get("required_evidence", [])))
    proof_payload = _base_proof_payload(first["id"], tokens)
    second_row = json.loads(json.dumps(proof_payload["rows"][0]))
    second_row["row_id"] = second["id"]
    proof_payload["rows"].append(second_row)

    for row_index, row in enumerate(proof_payload["rows"]):
        for proof_index, proof in enumerate(row["proofs"]):
            proof["evidence_id"] = f"redacted-proof-{row_index}-{proof_index}"

    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert "identify its auth row" in diagnostics
    assert "redacted-proof" not in diagnostics
    assert first["id"] not in diagnostics
    assert second["id"] not in diagnostics


def test_builder_rejects_duplicate_proof_tokens_value_free(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    proof_payload = _base_proof_payload(
        row["id"], list(dict.fromkeys(row["required_evidence"]))
    )
    proof_payload["rows"][0]["proofs"].append(
        dict(proof_payload["rows"][0]["proofs"][0])
    )
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert "duplicate" in diagnostics
    assert row["id"] not in diagnostics


def test_builder_rejects_non_redacted_proof_entry(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)

    row = _read_auth_row(repo_root)
    tokens = list(dict.fromkeys(row.get("required_evidence", [])))
    proof_payload = _base_proof_payload(row["id"], tokens)
    proof_payload["rows"][0]["proofs"][0]["redacted"] = False
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)
    diagnostics = _raise_diagnostics(exc.value)

    assert "redacted" in diagnostics.lower()


def test_builder_rejects_future_generated_at(repo_root: Path, tmp_path: Path) -> None:
    builder = _load_builder(repo_root)

    row = _read_auth_row(repo_root)
    tokens = list(dict.fromkeys(row.get("required_evidence", [])))
    proof_payload = _base_proof_payload(row["id"], tokens)
    proof_payload["generated_at"] = "2998-01-01T00:00:00Z"
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)
    diagnostics = _raise_diagnostics(exc.value)

    assert "generated_at" in diagnostics
    assert "future" in diagnostics
    assert "2998" not in diagnostics


@pytest.mark.parametrize(
    "case,modifier",
    [
        (
            "top_level",
            lambda payload: payload.update({"session_cookie": "raw-cookie-value"}),
        ),
        (
            "row_key",
            lambda payload: payload["rows"][0].update(
                {"browser_profile_path": "value"}
            ),
        ),
        (
            "proof_key",
            lambda payload: payload["rows"][0]["proofs"][0].update(
                {"path": "/home/user/data"}
            ),
        ),
    ],
)
def test_builder_unknown_keys_fail_closed_value_free(
    repo_root: Path, tmp_path: Path, case: str, modifier
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    proof_payload = _base_proof_payload(
        row["id"], list(dict.fromkeys(row["required_evidence"]))
    )
    modifier(proof_payload)
    proof_path = _write_json(tmp_path / f"auth_row_proofs_{case}.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert row["id"] not in diagnostics
    assert "session_cookie" not in diagnostics
    assert "browser_profile_path" not in diagnostics
    assert "path" not in diagnostics


@pytest.mark.parametrize(
    "raw_value",
    [
        "/home/example/Library/Application Support/raw/path",
        "ya" + "29.abcdefghijklmnopqrstuvwxyz12345",
        "person@example.com",
    ],
)
def test_builder_rejects_secret_path_cookie_shaped_values_value_free(
    repo_root: Path, tmp_path: Path, raw_value: str
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    proof_payload = _base_proof_payload(
        row["id"], list(dict.fromkeys(row["required_evidence"]))
    )
    proof_payload["rows"][0]["proofs"][0]["evidence_id"] = raw_value
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert raw_value not in diagnostics
    assert "non-redacted" in diagnostics.lower()


def test_builder_rejects_nested_evidence_id_smuggling_value_free(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    proof_payload = _base_proof_payload(
        row["id"], list(dict.fromkeys(row["required_evidence"]))
    )
    proof_payload["rows"][0]["proofs"][0]["evidence_id"] = {
        "raw_token": "raw-token-value"
    }
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert "evidence_id" in diagnostics
    assert "raw_token" not in diagnostics
    assert "raw-token-value" not in diagnostics


@pytest.mark.parametrize(
    "row_kind",
    ["unknown_row", "non_auth_row"],
)
def test_builder_rejects_unknown_and_non_auth_rows_value_free(
    repo_root: Path, tmp_path: Path, row_kind: str
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    tokens = list(dict.fromkeys(row.get("required_evidence", [])))
    row_id = row["id"] if row_kind == "non_auth_row" else "auth.invalid.synthetic"

    if row_kind == "non_auth_row":
        row_id = _non_auth_row_id(repo_root)

    proof_payload = _base_proof_payload(row_id, tokens)
    proof_path = _write_json(
        tmp_path / f"auth_row_proofs_{row_kind}.json", proof_payload
    )

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert row_id not in diagnostics
    assert "unexpected" in diagnostics.lower() or "auth rows" in diagnostics.lower()


def test_builder_rejects_whitespace_padded_row_id_value_free(
    repo_root: Path, tmp_path: Path
) -> None:
    builder = _load_builder(repo_root)
    row = _read_auth_row(repo_root)
    tokens = list(dict.fromkeys(row.get("required_evidence", [])))
    proof_payload = _base_proof_payload(f" {row['id']} ", tokens)
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    with pytest.raises(builder._ValidationError) as exc:
        builder.build_report(proof_path, repo_root=repo_root)

    diagnostics = _raise_diagnostics(exc.value)
    assert row["id"] not in diagnostics
    assert "row_id" in diagnostics


def test_builder_strict_mode_uses_code_77_for_invalid_input(
    repo_root: Path, tmp_path: Path, capsys
) -> None:
    builder = _load_builder(repo_root)

    row = _read_auth_row(repo_root)
    proof_payload = _base_proof_payload(
        row["id"], list(dict.fromkeys(row["required_evidence"]))
    )
    proof_payload["rows"][0]["proofs"][0]["status"] = "fail"
    proof_path = _write_json(tmp_path / "auth_row_proofs.json", proof_payload)

    exit_code = builder.main(["--proofs", str(proof_path), "--json", "--strict"])
    output = capsys.readouterr().out

    assert exit_code == builder.STRICT_BLOCKED_EXIT
    parsed = json.loads(output)
    assert parsed["status"] == "fail"
    assert "status" in parsed and parsed["status"] == "fail"
    assert "rows" not in parsed
    assert "proofs" not in output
