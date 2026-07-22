"""AuthTokens public behavior parity."""

from __future__ import annotations


def test_auth_tokens_normalizes_cookies_and_exposes_upstream_helpers():
    from notebooklm.auth import AuthTokens

    auth = AuthTokens(
        cookies={
            "SID": "sid-value",
            ("__Secure-1PSIDTS", ".notebooklm.google.com"): "ts-value",
            ("HSID", ".google.com", "/secure"): "hsid-value",
        },
        csrf_token="csrf",
        session_id="session",
        authuser=3,
        account_email="person@example.test",
    )

    assert auth.cookies == {
        ("SID", ".google.com", "/"): "sid-value",
        ("__Secure-1PSIDTS", ".notebooklm.google.com", "/"): "ts-value",
        ("HSID", ".google.com", "/secure"): "hsid-value",
    }
    assert auth.flat_cookies == {
        "SID": "sid-value",
        "__Secure-1PSIDTS": "ts-value",
        "HSID": "hsid-value",
    }
    assert auth.cookie_header == "SID=sid-value; __Secure-1PSIDTS=ts-value; HSID=hsid-value"
    assert auth.account_route == "person@example.test"
    assert auth.cookie_jar is not None
