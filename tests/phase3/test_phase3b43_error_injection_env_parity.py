"""Test-only synthetic error env guard parity with pinned upstream."""

from __future__ import annotations

import pytest

from notebooklm.auth import AuthTokens
from notebooklm.client import NotebookLMClient


def test_vcr_error_env_mode_normalization_matches_upstream(monkeypatch):
    import notebooklm.client as client_mod

    assert client_mod._ERROR_INJECT_ENV_VAR == "NOTEBOOKLM_VCR_RECORD_ERRORS"

    for raw, expected in [
        (None, None),
        ("", None),
        ("typo", None),
        ("429", "429"),
        ("5XX", "5xx"),
        (" expired_csrf ", "expired_csrf"),
    ]:
        if raw is None:
            monkeypatch.delenv(client_mod._ERROR_INJECT_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(client_mod._ERROR_INJECT_ENV_VAR, raw)
        assert client_mod._get_error_injection_mode() == expected


def test_client_refuses_valid_vcr_error_env_outside_pytest_context(monkeypatch):
    import notebooklm.client as client_mod

    monkeypatch.setenv(client_mod._ERROR_INJECT_ENV_VAR, "5XX")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    message = str(excinfo.value)
    assert "NOTEBOOKLM_VCR_RECORD_ERRORS='5xx'" in message
    assert "PYTEST_CURRENT_TEST unset" in message


def test_client_allows_vcr_error_env_inside_pytest_context(monkeypatch):
    import notebooklm.client as client_mod

    monkeypatch.setenv(client_mod._ERROR_INJECT_ENV_VAR, "429")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic test context")

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))
    assert client.is_connected is True
