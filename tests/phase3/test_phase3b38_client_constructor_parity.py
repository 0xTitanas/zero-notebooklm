from pathlib import Path

import pytest


def _auth(storage_path: Path | None = None):
    from notebooklm import AuthTokens

    return AuthTokens(cookies={}, csrf_token="", session_id="", storage_path=storage_path)


def test_client_rejects_non_positive_rpc_concurrency(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import NotebookLMClient

    with pytest.raises(ValueError, match="max_concurrent_rpcs must be >= 1"):
        NotebookLMClient(_auth(), max_concurrent_rpcs=0)


def test_client_rejects_rpc_concurrency_above_connection_limit(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import ConnectionLimits, NotebookLMClient

    with pytest.raises(ValueError, match="max_concurrent_rpcs must be <= limits.max_connections"):
        NotebookLMClient(
            _auth(),
            limits=ConnectionLimits(max_connections=2),
            max_concurrent_rpcs=3,
        )


@pytest.mark.parametrize(
    "kwarg",
    ["rate_limit_max_retries", "server_error_max_retries"],
)
def test_client_rejects_negative_retry_budgets(repo_root, monkeypatch, kwarg):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import NotebookLMClient

    with pytest.raises(ValueError, match=f"{kwarg} must be >= 0"):
        NotebookLMClient(_auth(), **{kwarg: -1})


def test_client_storage_path_override_rebinds_public_auth_without_mutating_original(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import NotebookLMClient

    original_path = tmp_path / "original.json"
    override_path = tmp_path / "override.json"
    original_auth = _auth(original_path)

    client = NotebookLMClient(original_auth, storage_path=override_path)

    assert client.auth is not original_auth
    assert client.auth.storage_path == override_path
    assert original_auth.storage_path == original_path
