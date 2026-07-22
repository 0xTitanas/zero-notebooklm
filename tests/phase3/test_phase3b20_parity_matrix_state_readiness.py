"""Phase 3B20 parity-matrix state readiness.

The matrix should stay conservative, but the oracle harness must no longer assume
that every row remains ``open`` forever after direct comparison and offline gates
exist. This batch makes the validators mixed-state-ready while keeping the
current live matrix rows unchanged unless row-specific evidence is promoted.
"""

from __future__ import annotations


def _mixed_matrix() -> str:
    return """# synthetic mixed parity matrix

| Category | Scope | Differential basis | State |
| --- | --- | --- | --- |
| cli | synthetic | synthetic | pass |
| api | synthetic | synthetic | open |
| auth | synthetic | synthetic | blocked |
| rpc | synthetic | synthetic | pass |
| offline | synthetic | synthetic | pass |
| self-test | synthetic | synthetic | open |

| Leaf command | State |
| --- | --- |
| `notebooklm list` | pass |
| `notebooklm login` | open |

| Sub-client | Class | State |
| --- | --- | --- |
| `client.notebooks` | `NotebooksAPI` | pass |
"""


def test_run_phase0_oracle_accepts_mixed_valid_parity_states(repo_root, monkeypatch):
    import _phase0_constants as constants
    import run_phase0_oracle

    matrix = repo_root / ".pytest_cache" / "phase3b20-mixed-parity-matrix.md"
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text(_mixed_matrix(), encoding="utf-8")
    monkeypatch.setattr(constants, "PARITY_MATRIX_MD", matrix)
    monkeypatch.setattr(run_phase0_oracle.C, "PARITY_MATRIX_MD", matrix)

    results = run_phase0_oracle.validate_all()
    parity_failures = [
        name for name, ok, _ in results if not ok and "parity_matrix" in name
    ]

    assert parity_failures == []


def test_run_phase0_oracle_rejects_invalid_parity_state(repo_root, monkeypatch):
    import _phase0_constants as constants
    import run_phase0_oracle

    matrix = repo_root / ".pytest_cache" / "phase3b20-invalid-parity-matrix.md"
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text(
        _mixed_matrix().replace(
            "| auth | synthetic | synthetic | blocked |",
            "| auth | synthetic | synthetic | bogus |",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(constants, "PARITY_MATRIX_MD", matrix)
    monkeypatch.setattr(run_phase0_oracle.C, "PARITY_MATRIX_MD", matrix)

    results = run_phase0_oracle.validate_all()
    failures = [name for name, ok, _ in results if not ok]

    assert "parity_matrix states subset of {pass,open,blocked}" in failures


def test_pytest_oracle_source_no_longer_requires_all_matrix_rows_open(repo_root):
    source = (repo_root / "tests" / "test_phase0_oracle.py").read_text(encoding="utf-8")

    assert "test_parity_matrix_states_open_only" not in source
    assert "parity rows must all be open" not in source
    assert 'category_state[cat] == "open"' not in source
    assert 'set(states) == {"open"}' not in source


def test_run_phase0_oracle_source_no_longer_requires_all_matrix_rows_open(repo_root):
    source = (repo_root / "scripts" / "run_phase0_oracle.py").read_text(
        encoding="utf-8"
    )

    assert "parity_matrix all rows open" not in source
    assert "set(states) == {C.PHASE0_INITIAL_STATE}" not in source
    assert "starts ``open``" not in source
    assert "every row is `open`" not in source


def test_cli_module_docstring_no_longer_claims_every_other_command_is_future_stub(
    repo_root,
):
    source = (repo_root / "notebooklm" / "cli.py").read_text(encoding="utf-8")
    top_doc = source.split('"""', 2)[1]

    assert "Every other upstream command remains" not in top_doc
    assert "future-phase stub" not in top_doc
