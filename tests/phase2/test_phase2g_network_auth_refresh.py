"""Phase 2G network auth refresh/token-fetch foundation tests.

These tests cover only the stdlib-only network-auth slice after Phase 2F-D:

  * stored auth can be exercised through a fake RotateCookies + NotebookLM
    homepage transport, with WIZ tokens parsed and discarded;
  * `auth check --test` adds a redacted token-fetch result;
  * `auth refresh` without `--browser-cookies` performs a one-shot keepalive,
    persists rotated Set-Cookie values, and supports quiet mode;
  * auth redirects and network failures map to stable CLI exit codes without
    leaking cookie values, WIZ tokens, OAuth-looking URLs, or traceback text.

No test touches the real network, a browser store, an OS credential backend, or
`~/.notebooklm`. Every read/write is confined to tmp_path.
"""

from __future__ import annotations

import importlib
import importlib.abc
import json
import types
import urllib.request
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp", "urllib3"}

SECRET_SID = "sidSyntheticSecretValue0123456789abcdef"
SECRET_PSIDTS_OLD = "sidtsOldSyntheticSecretValue0123456789abcdef"
SECRET_PSIDTS_NEW = "sidtsNewSyntheticSecretValue0123456789abcdef"
SECRET_CSRF = "csrfSyntheticSecretValue0123456789abcdef"
SECRET_SESSION = "sessionSyntheticSecretValue0123456789abcdef"
SECRET_URL_MARKER = "urlSyntheticMarkerValue0123456789abcdef"


class _DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    import sys

    finder = _DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        ns = types.SimpleNamespace(
            auth=importlib.import_module("notebooklm.auth"),
            cookies=importlib.import_module("notebooklm.cookies"),
            cli=importlib.import_module("notebooklm.cli"),
            errors=importlib.import_module("notebooklm.errors"),
            http_std=importlib.import_module("notebooklm.http_std"),
        )
    finally:
        sys.meta_path.remove(finder)
    return ns


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


def _storage_state(*, psidts: str = SECRET_PSIDTS_OLD) -> dict:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": SECRET_SID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": psidts,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
        ],
        "origins": [],
    }


def _write_storage(
    mods, home: Path, *, profile: str = "default", state: dict | None = None,
    age_seconds: float = 0,
) -> Path:
    path = home / "profiles" / profile / "storage_state.json"
    mods.cookies.save_storage_state(path, state or _storage_state())
    if age_seconds:
        import os as _os
        import time as _time
        mt = _time.time() - age_seconds
        _os.utime(path, (mt, mt))
    return path


def _read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cookie_value(path: Path, name: str) -> str:
    for cookie in _read_state(path)["cookies"]:
        if cookie["name"] == name:
            return cookie["value"]
    raise KeyError(name)


def _wiz_html(
    csrf: str = SECRET_CSRF, session: str = SECRET_SESSION, *, quote: str = '"'
) -> str:
    if quote == "'":
        return f"<script>var WIZ_global_data = {{'SNlM0e':'{csrf}', 'FdrFJe':'{session}'}};</script>"
    if quote == "html":
        return (
            '<div data-wiz="&quot;SNlM0e&quot;:&quot;'
            + csrf
            + "&quot;,&quot;FdrFJe&quot;:&quot;"
            + session
            + '&quot;"></div>'
        )
    return f'<script>var WIZ_global_data = {{"SNlM0e":"{csrf}", "FdrFJe":"{session}"}};</script>'


class FakeTransport:
    def __init__(
        self,
        mods,
        *,
        html: str | None = None,
        fail: Exception | None = None,
        redirect_url: str | None = None,
    ):
        self.mods = mods
        self.calls: list[tuple[str, str, dict]] = []
        self.redirect_limits: list[int] = []
        self.timeouts: list[float | None] = []
        self.html = html if html is not None else _wiz_html()
        self.fail = fail
        self.redirect_url = redirect_url

    def post(
        self,
        url,
        *,
        body=None,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
    ):
        self.redirect_limits.append(max_redirects)
        self.timeouts.append(timeout)
        self.calls.append(("POST", url, dict(headers or {})))
        if self.fail is not None:
            raise self.fail
        return self.mods.http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": (
                    "__Secure-1PSIDTS="
                    + SECRET_PSIDTS_NEW
                    + "; Domain=.google.com; Path=/; Secure; HttpOnly"
                )
            },
            body=b"[]",
        )

    def get(
        self, url, *, headers=None, timeout=None, max_redirects=5, max_body_bytes=None
    ):
        self.redirect_limits.append(max_redirects)
        self.timeouts.append(timeout)
        self.calls.append(("GET", url, dict(headers or {})))
        if self.fail is not None:
            raise self.fail
        final_url = self.redirect_url or url
        return self.mods.http_std.Response(
            status=200,
            url=final_url,
            headers={},
            body=self.html.encode("utf-8"),
        )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def _assert_no_secret_leak(text: str) -> None:
    for secret in (
        SECRET_SID,
        SECRET_PSIDTS_OLD,
        SECRET_PSIDTS_NEW,
        SECRET_CSRF,
        SECRET_SESSION,
        SECRET_URL_MARKER,
    ):
        assert secret not in text
    assert "Traceback" not in text


def test_fetch_tokens_with_domains_none_uses_active_profile_storage(
    mods, home, monkeypatch
):
    import asyncio

    from notebooklm import paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    monkeypatch.delenv("NOTEBOOKLM_AUTH_JSON", raising=False)
    work_storage = _write_storage(mods, home, profile="work")
    seen: dict[str, object] = {}

    def fake_fetch_tokens_from_storage(path, **kwargs):
        seen["path"] = Path(path)
        seen["profile"] = kwargs.get("profile")
        return {"ok": True}, SECRET_CSRF, SECRET_SESSION

    monkeypatch.setattr(
        mods.auth, "fetch_tokens_from_storage", fake_fetch_tokens_from_storage
    )
    paths.set_active_profile("work")
    try:
        csrf, session_id = asyncio.run(mods.auth.fetch_tokens_with_domains(None))
    finally:
        paths.set_active_profile(None)

    assert (csrf, session_id) == (SECRET_CSRF, SECRET_SESSION)
    assert seen == {"path": work_storage, "profile": None}


@pytest.mark.parametrize("argv", [["auth", "refresh"], ["auth", "refresh", "--browser-cookies", "chrome"]])
def test_cli_auth_refresh_rejects_auth_json_before_refresh_paths(
    mods, home, capsys, monkeypatch, argv
):
    def boom(*_args, **_kwargs):
        raise AssertionError("auth refresh must reject NOTEBOOKLM_AUTH_JSON first")

    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", "{}")
    monkeypatch.setattr(mods.auth, "refresh_storage", boom)
    monkeypatch.setattr(mods.cli._bc, "refresh_browser_cookies", boom)

    code, out, err = _run(mods, capsys, ["--storage", str(home), *argv])

    assert code == 1
    assert out == ""
    assert (
        err
        == "Error: 'auth refresh' is incompatible with NOTEBOOKLM_AUTH_JSON. The keepalive needs a writable storage_state.json to persist rotated cookies. Either unset the env var for this process and use a profile-backed storage file, or arrange for the env var to be refreshed externally.\n"
    )


def test_cli_auth_refresh_browser_cookie_unknown_browser_fails_like_upstream(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unknown browser must fail before cookie refresh")

    monkeypatch.setattr(mods.cli._bc, "refresh_browser_cookies", boom)
    expected_message = (
        "Unknown browser: 'not-a-browser'\n"
        "Supported: arc, brave, chrome, chromium, edge, firefox, ie, "
        "librewolf, octo, opera, opera-gx, opera_gx, safari, vivaldi, zen"
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "not-a-browser",
        ],
    )

    assert code == 1
    assert out == expected_message + "\n"
    assert err == ""


def test_cli_auth_refresh_accepts_browser_cookie_alias_like_upstream(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unknown browser alias must fail before cookie refresh")

    monkeypatch.setattr(mods.cli._bc, "refresh_browser_cookies", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookie",
            "not-a-browser",
        ],
    )

    assert code == 1
    assert out.startswith("Unknown browser: 'not-a-browser'\n")
    assert err == ""


def test_cli_auth_refresh_browser_cookie_upstream_rookiepy_alias_fails_before_refresh(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unsupported stdlib alias must fail before cookie refresh")

    monkeypatch.setattr(mods.cli._bc, "refresh_browser_cookies", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "librewolf",
        ],
    )

    assert code == 1
    assert out.startswith("rookiepy is not installed.\n")
    assert err == ""


def test_wiz_token_extraction_accepts_double_single_and_html_escaped(mods):
    for quote in ('"', "'", "html"):
        csrf, session = mods.auth.extract_auth_tokens_from_html(_wiz_html(quote=quote))
        assert csrf == SECRET_CSRF
        assert session == SECRET_SESSION


def test_cli_global_storage_accepts_storage_state_file_path(
    mods, capsys, tmp_path
):
    storage = tmp_path / "storage_state.json"
    link_dir = tmp_path / "link"
    link_dir.symlink_to(tmp_path, target_is_directory=True)
    mods.cookies.save_storage_state(storage, _storage_state())

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(link_dir / "storage_state.json"), "auth", "check", "--json"],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["checks"]["storage_exists"] is True
    assert payload["checks"]["json_valid"] is True
    assert payload["checks"]["cookies_present"] is True
    assert payload["checks"]["sid_cookie"] is True
    assert payload["checks"]["token_fetch"] is None
    assert payload["details"]["storage_path"] == str(storage.resolve())
    assert payload["details"]["auth_source"] == f"file ({storage.resolve()})"
    assert not (storage / "config.json").exists()


def test_cli_auth_check_json_uses_env_auth_without_storage_file(
    mods, capsys, tmp_path, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", json.dumps(_storage_state()))

    code, out, err = _run(mods, capsys, ["auth", "check", "--json"])

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["checks"]["storage_exists"] is True
    assert payload["checks"]["json_valid"] is True
    assert payload["checks"]["cookies_present"] is True
    assert payload["details"]["auth_source"] == "NOTEBOOKLM_AUTH_JSON"
    assert not (tmp_path / "home").exists()


def test_cli_auth_check_json_reports_notebooklm_home_auth_source(
    mods, capsys, tmp_path, monkeypatch
):
    home = tmp_path / "home"
    storage = home / "profiles" / "default" / "storage_state.json"
    storage.parent.mkdir(parents=True)
    mods.cookies.save_storage_state(storage, _storage_state())
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))

    code, out, err = _run(mods, capsys, ["auth", "check", "--json"])

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["details"]["auth_source"] == f"$NOTEBOOKLM_HOME ({storage.resolve()})"


def test_cli_auth_check_json_cookie_error_matches_upstream_without_extra_advice(
    mods, capsys, tmp_path
):
    storage = tmp_path / "storage_state.json"
    state = _storage_state()
    state["cookies"] = [c for c in state["cookies"] if c["name"] != "__Secure-1PSIDTS"]
    mods.cookies.save_storage_state(storage, state)

    code, out, err = _run(
        mods, capsys, ["--storage", str(storage), "auth", "check", "--json"]
    )

    assert code == 1
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "error"
    assert payload["checks"]["cookies_present"] is False
    assert "Missing required cookies" in payload["details"]["error"]
    assert "--browser-cookies extraction" not in payload["details"]["error"]


def test_cli_auth_check_text_failure_exits_zero_like_upstream(mods, capsys, tmp_path):
    code, out, err = _run(
        mods, capsys, ["--storage", str(tmp_path / "missing.json"), "auth", "check"]
    )

    assert code == 0
    assert "Storage file not found" in out
    assert err == ""


def test_cli_global_storage_file_path_logout_removes_that_file_only(
    mods, capsys, tmp_path
):
    storage = tmp_path / "storage_state.json"
    context = storage.with_suffix(storage.suffix + ".context.json")
    legacy_context = tmp_path / "context.json"
    mods.cookies.save_storage_state(storage, _storage_state())
    context.write_text('{"notebook_id": "explicit"}\n', encoding="utf-8")
    legacy_context.write_text('{"notebook_id": "legacy"}\n', encoding="utf-8")

    code, out, err = _run(
        mods, capsys, ["--storage", str(storage), "auth", "logout", "--json"]
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["storage_removed"] is True
    assert payload["context_removed"] is True
    assert not storage.exists()
    assert not context.exists()
    assert legacy_context.exists()
    assert not (storage / "config.json").exists()


def test_cli_global_storage_file_path_uses_sibling_context(
    mods, capsys, tmp_path
):
    storage = tmp_path / "storage_state.json"
    context = storage.with_suffix(storage.suffix + ".context.json")

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(storage), "use", "nb-explicit", "--force", "--json"],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out)["notebook_id"] == "nb-explicit"
    assert context.exists()
    assert not (tmp_path / "context.json").exists()

    code, out, err = _run(
        mods, capsys, ["--storage", str(storage), "status", "--paths", "--json"]
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert set(payload) == {"paths"}
    assert payload["paths"]["storage_path"] == str(storage.resolve())
    assert payload["paths"]["context_path"] == str(context)


def test_cli_global_storage_file_path_clear_removes_sibling_context_only(
    mods, capsys, tmp_path
):
    storage = tmp_path / "storage_state.json"
    context = storage.with_suffix(storage.suffix + ".context.json")
    legacy_context = tmp_path / "context.json"
    legacy_context.write_text('{"notebook_id": "legacy"}\n', encoding="utf-8")
    _run(
        mods,
        capsys,
        ["--storage", str(storage), "use", "nb-explicit", "--force", "--json"],
    )

    code, out, err = _run(
        mods, capsys, ["--storage", str(storage), "clear"]
    )

    assert code == 0
    assert err == ""
    assert out == "Context cleared\n"
    assert not context.exists()
    assert legacy_context.exists()


def test_fetch_tokens_rotates_before_homepage_and_persists_set_cookie(mods, home):
    storage = _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)

    summary = mods.auth.fetch_tokens_from_storage(
        storage, get=fake.get, post=fake.post, persist=True
    )

    assert [(method, url) for method, url, _headers in fake.calls] == [
        ("POST", "https://accounts.google.com/RotateCookies"),
        ("GET", "https://notebooklm.google.com/"),
    ]
    assert fake.redirect_limits == [20, 20]
    assert fake.timeouts == [15.0, 30.0]
    assert summary["ok"] is True
    assert summary["token_fetch_ok"] is True
    assert summary["csrf_token_present"] is True
    assert summary["session_id_present"] is True
    assert summary["rotated_cookie_names"] == ["__Secure-1PSIDTS"]
    assert _cookie_value(storage, "__Secure-1PSIDTS") == SECRET_PSIDTS_NEW
    assert SECRET_CSRF not in json.dumps(summary)
    assert SECRET_SESSION not in json.dumps(summary)


def test_fetch_tokens_persist_preserves_sibling_cookie_rotation(mods, home):
    storage = _write_storage(mods, home, age_seconds=120)

    class RacingTransport(FakeTransport):
        def post(self, *args, **kwargs):
            state = _read_state(storage)
            for cookie in state["cookies"]:
                if cookie["name"] == "__Secure-1PSIDTS":
                    cookie["value"] = "siblingFreshSyntheticValue0123456789abcdef"
            storage.write_text(json.dumps(state), encoding="utf-8")
            return super().post(*args, **kwargs)

    fake = RacingTransport(mods)

    mods.auth.fetch_tokens_from_storage(storage, get=fake.get, post=fake.post, persist=True)

    assert _cookie_value(storage, "__Secure-1PSIDTS") == (
        "siblingFreshSyntheticValue0123456789abcdef"
    )


def _cookie_header_names(header: str) -> set[str]:
    names = set()
    for part in header.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        names.add(item.split("=", 1)[0])
    return names


def _cookiejar_header(mods, storage: Path, url: str) -> str:
    request = urllib.request.Request(url)
    mods.auth.build_cookie_jar(storage_path=storage).add_cookie_header(request)
    return request.get_header("Cookie") or ""


def test_fetch_tokens_scopes_cookie_headers_to_target_hosts(mods, home):
    state = _storage_state()
    state["cookies"].extend(
        [
            {
                "name": "ACCOUNT_HOST_ONLY",
                "value": "accountHostOnlySyntheticValue",
                "domain": "accounts.google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            },
            {
                "name": "NOTEBOOKLM_HOST_ONLY",
                "value": "notebooklmHostOnlySyntheticValue",
                "domain": "notebooklm.google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            },
        ]
    )
    storage = _write_storage(mods, home, state=state, age_seconds=120)
    fake = FakeTransport(mods)

    mods.auth.fetch_tokens_from_storage(
        storage, get=fake.get, post=fake.post, persist=False
    )

    rotate_names = _cookie_header_names(fake.calls[0][2]["Cookie"])
    homepage_names = _cookie_header_names(fake.calls[1][2]["Cookie"])
    assert "ACCOUNT_HOST_ONLY" in rotate_names
    assert "ACCOUNT_HOST_ONLY" not in homepage_names
    assert "NOTEBOOKLM_HOST_ONLY" not in rotate_names
    assert "NOTEBOOKLM_HOST_ONLY" in homepage_names
    assert {"SID", "__Secure-1PSIDTS"} <= rotate_names
    assert {"SID", "__Secure-1PSIDTS"} <= homepage_names


def test_fetch_tokens_rescopes_cookies_across_auth_redirects(mods, home):
    state = _storage_state()
    state["cookies"].extend(
        [
            {
                "name": "ACCOUNT_HOST_ONLY",
                "value": "accountHostOnlySyntheticValue",
                "domain": "accounts.google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            },
            {
                "name": "NOTEBOOKLM_HOST_ONLY",
                "value": "notebooklmHostOnlySyntheticValue",
                "domain": "notebooklm.google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            },
        ]
    )
    storage = _write_storage(mods, home, state=state)
    calls = []

    def get(
        url,
        *,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
        follow_redirects=True,
    ):
        del timeout, max_body_bytes
        calls.append((url, dict(headers or {}), max_redirects, follow_redirects))
        if len(calls) == 1:
            return mods.http_std.Response(
                status=302,
                url=url,
                headers={
                    "location": "https://accounts.google.com/Login?continue=synthetic"
                },
                body=b"",
            )
        if len(calls) == 2:
            return mods.http_std.Response(
                status=302,
                url=url,
                headers={"location": "https://notebooklm.google.com/"},
                body=b"",
            )
        return mods.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=_wiz_html().encode("utf-8"),
        )

    def post_must_not_run(*_args, **_kwargs):
        raise AssertionError("recent storage should skip RotateCookies")

    summary = mods.auth.fetch_tokens_from_storage(
        storage, get=get, post=post_must_not_run, persist=False
    )

    assert summary["token_fetch_ok"] is True
    assert [url for url, _headers, _limit, _follow in calls] == [
        "https://notebooklm.google.com/",
        "https://accounts.google.com/Login?continue=synthetic",
        "https://notebooklm.google.com/",
    ]
    assert [follow for _url, _headers, _limit, follow in calls] == [
        False,
        False,
        False,
    ]
    assert calls[0][1]["Cookie"] == _cookiejar_header(
        mods, storage, "https://notebooklm.google.com/"
    )
    assert calls[1][1]["Cookie"] == _cookiejar_header(
        mods, storage, "https://accounts.google.com/Login?continue=synthetic"
    )
    first_names = _cookie_header_names(calls[0][1]["Cookie"])
    accounts_names = _cookie_header_names(calls[1][1]["Cookie"])
    final_names = _cookie_header_names(calls[2][1]["Cookie"])
    assert "NOTEBOOKLM_HOST_ONLY" in first_names
    assert "ACCOUNT_HOST_ONLY" not in first_names
    assert "ACCOUNT_HOST_ONLY" in accounts_names
    assert "NOTEBOOKLM_HOST_ONLY" not in accounts_names
    assert "NOTEBOOKLM_HOST_ONLY" in final_names
    assert "ACCOUNT_HOST_ONLY" not in final_names


def test_fetch_tokens_allows_signed_in_html_with_incidental_accounts_links(mods, home):
    storage = _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(
        mods,
        html=(
            _wiz_html()
            + '<script src="https://accounts.google.com/gsi/client"></script>'
        ),
    )

    summary = mods.auth.fetch_tokens_from_storage(
        storage, get=fake.get, post=fake.post, persist=False
    )

    assert summary["token_fetch_ok"] is True
    assert summary["csrf_token_present"] is True
    assert summary["session_id_present"] is True


def test_fetch_tokens_treats_rotatecookies_429_as_best_effort_like_upstream(
    mods, home
):
    storage = _write_storage(mods, home, age_seconds=120)
    calls: list[str] = []

    def post_429(url, **_kwargs):
        calls.append(url)
        return mods.http_std.Response(status=429, url=url, headers={}, body=b"")

    def get_homepage(url, **_kwargs):
        calls.append(url)
        return mods.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=_wiz_html().encode(),
        )

    summary = mods.auth.fetch_tokens_from_storage(
        storage, get=get_homepage, post=post_429, persist=False
    )

    assert calls == [
        "https://accounts.google.com/RotateCookies",
        "https://notebooklm.google.com/",
    ]
    assert summary["token_fetch_ok"] is True


def test_fetch_tokens_host_only_rotation_updates_existing_google_cookie_and_preserves_attrs(
    mods, home
):
    storage = _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)

    def host_only_post(url, **_kwargs):
        fake.calls.append(("POST", url, {}))
        return mods.http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": "__Secure-1PSIDTS=" + SECRET_PSIDTS_NEW + "; Path=/"
            },
            body=b"[]",
        )

    summary = mods.auth.fetch_tokens_from_storage(
        storage, get=fake.get, post=host_only_post, persist=True
    )
    state = _read_state(storage)
    matching = [c for c in state["cookies"] if c["name"] == "__Secure-1PSIDTS"]

    assert summary["rotated_cookie_names"] == ["__Secure-1PSIDTS"]
    assert len(matching) == 1
    assert matching[0]["domain"] == ".google.com"
    assert matching[0]["value"] == SECRET_PSIDTS_NEW
    # Attributes omitted by the Set-Cookie line preserve the existing stored
    # cookie's security metadata instead of being downgraded to defaults.
    assert matching[0]["secure"] is True
    assert matching[0]["httpOnly"] is True


def test_cli_auth_check_test_json_reports_redacted_success(
    mods, home, capsys, monkeypatch
):
    storage = _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "check", "--test", "--json"]
    )

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["checks"]["token_fetch"] is True
    assert data["details"]["csrf_length"] > 0
    assert data["details"]["session_id_length"] > 0
    # Upstream `auth check --test` routes through fetch_tokens_with_domains(),
    # which persists RotateCookies deltas observed during the token-fetch probe.
    assert _cookie_value(storage, "__Secure-1PSIDTS") == SECRET_PSIDTS_NEW
    _assert_no_secret_leak(out + err)


def test_cli_auth_refresh_json_persists_rotation_and_redacts_output(
    mods, home, capsys, monkeypatch
):
    storage = _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["profile"] == "default"
    assert data["ok"] is True
    assert data["token_fetch_ok"] is True
    assert _cookie_value(storage, "__Secure-1PSIDTS") == SECRET_PSIDTS_NEW
    _assert_no_secret_leak(out + err)


def test_cli_auth_refresh_quiet_emits_no_success_stdout(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--quiet"]
    )

    assert code == 0
    assert out == ""
    assert err == ""


def test_google_auth_redirect_maps_to_authentication_error_and_redacts(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home)
    fake = FakeTransport(
        mods,
        html="<html>accounts.google.com login required</html>",
        redirect_url="https://accounts.google.com/o/oauth2/auth/"
        + SECRET_URL_MARKER
        + "?continue="
        + SECRET_URL_MARKER,
    )
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "check", "--test", "--json"]
    )

    assert code == 1
    assert out
    data = json.loads(out)
    assert data["status"] == "error"
    assert data["checks"]["token_fetch"] is False
    error = data["details"]["error"]
    assert "Authentication" in error or "auth" in error.lower()
    assert "accounts.google.com" in error
    assert "/o/oauth2/auth/" not in error
    assert "?continue=" not in error
    assert err == ""
    _assert_no_secret_leak(out + err)

    code, out, err = _run(mods, capsys, ["--storage", str(home), "auth", "refresh"])
    assert code == 77
    assert out == ""
    assert "accounts.google.com" in err
    assert "/o/oauth2/auth/" not in err
    assert "?continue=" not in err


def test_rotatecookies_auth_rejection_is_best_effort_like_upstream(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home, age_seconds=120)
    calls: list[tuple[str, str]] = []

    def reject_post(url, **_kwargs):
        calls.append(("POST", url))
        return mods.http_std.Response(status=401, url=url, headers={}, body=b"")

    def accept_homepage(url, **_kwargs):
        calls.append(("GET", url))
        return mods.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=_wiz_html().encode(),
        )

    monkeypatch.setattr(mods.auth, "_default_get", accept_homepage)
    monkeypatch.setattr(mods.auth, "_default_post", reject_post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "check", "--test", "--json"]
    )

    assert code == 0
    assert out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["checks"]["token_fetch"] is True
    assert calls[:2] == [
        ("POST", "https://accounts.google.com/RotateCookies"),
        ("GET", "https://notebooklm.google.com/"),
    ]
    assert err == ""
    _assert_no_secret_leak(out + err)

    calls.clear()
    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )
    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["ok"] is True
    assert data["token_fetch_ok"] is True
    assert calls[:2] == [
        ("POST", "https://accounts.google.com/RotateCookies"),
        ("GET", "https://notebooklm.google.com/"),
    ]
    _assert_no_secret_leak(out + err)


def test_homepage_auth_rejection_maps_to_authentication_error(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home, age_seconds=120)
    fake = FakeTransport(mods)

    def reject_get(url, **_kwargs):
        fake.calls.append(("GET", url, {}))
        return mods.http_std.Response(status=403, url=url, headers={}, body=b"")

    monkeypatch.setattr(mods.auth, "_default_get", reject_get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(mods, capsys, ["--storage", str(home), "auth", "refresh"])
    assert code == 77
    assert out == ""
    assert "HTTP 403" in err
    assert "Traceback" not in err


def test_transport_redirect_errors_redact_query_fragment_and_path(mods, monkeypatch):
    raw = (
        "https://notebooklm.google.com/accounts/SetOSID?authuser=0&osidt="
        + SECRET_URL_MARKER
        + "#"
        + SECRET_URL_MARKER
    )
    redacted = mods.http_std._redact_url_for_error(raw)

    assert redacted == "https://notebooklm.google.com/<redacted>"
    assert SECRET_URL_MARKER not in redacted
    assert "?" not in redacted
    assert "#" not in redacted
    assert "/accounts/SetOSID" not in redacted
    assert (
        mods.http_std._redact_url_for_error(
            "https://notebooklm.google.com:bad/path?secret=1"
        )
        == "<redacted-url>"
    )
    assert (
        mods.auth._safe_url("https://accounts.google.com:bad/path?secret=1")
        == "<redacted-url>"
    )

    class FailingHTTPSConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            raise OSError("synthetic reset while following redirect")

        def close(self):
            pass

    monkeypatch.setattr(mods.http_std, "HTTPSConnection", FailingHTTPSConnection)
    try:
        mods.http_std.get(raw, timeout=1)
    except mods.errors.HTTPTransportError as wrapped:
        message = str(wrapped)
    else:  # pragma: no cover - defensive test guard
        raise AssertionError("expected HTTPTransportError")
    assert "https://notebooklm.google.com/<redacted>" in message
    assert SECRET_URL_MARKER not in message
    assert "?" not in message
    assert "#" not in message


def test_network_failure_maps_to_network_error_without_traceback(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home)
    fake = FakeTransport(
        mods, fail=mods.errors.HTTPTransportError("synthetic transport failed")
    )
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )

    assert code == 69
    assert out == ""
    assert "synthetic transport failed" in err
    _assert_no_secret_leak(out + err)


def test_non_browser_auth_refresh_rejects_browser_source_options(
    mods, home, capsys, tmp_path
):
    _write_storage(mods, home)
    flag_cases = [
        ["--include-all-domains"],
        ["--cookie-store", str(tmp_path / "Cookies")],
        ["--fixture-root", str(tmp_path / "fixtures")],
        ["--os", "macOS"],
        ["--browser-profile", "Default"],
    ]
    for flags in flag_cases:
        code, out, err = _run(
            mods, capsys, ["--storage", str(home), "auth", "refresh", *flags]
        )
        assert code == 64
        assert out == ""
        assert "--browser-cookies" in err


def test_non_browser_auth_refresh_include_domains_requires_browser_cookies_like_upstream(
    mods, home, capsys
):
    _write_storage(mods, home)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--include-domains",
            "youtube",
        ],
    )
    assert code == 1
    assert out == ""
    assert (
        err
        == "Error: --include-domains only applies when --browser-cookies is also set (the keepalive-only path does not re-extract cookies).\n"
    )


def test_existing_explicit_browser_cookie_refresh_behavior_unchanged(
    mods, home, capsys, tmp_path
):
    # Existing Phase 2C path still fails deterministically without a persisted
    # explicit source or explicit fixture path; it must not be replaced by live
    # network refresh when --browser-cookies is present.
    _write_storage(mods, home)
    code, _out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--json",
        ],
    )
    assert code == 64
    assert "auth_source" in err or "explicit" in err.lower()


def test_storage_override_avoids_default_home_for_network_auth(
    mods, home, capsys, monkeypatch
):
    _write_storage(mods, home)
    fake = FakeTransport(mods)
    monkeypatch.setattr(mods.auth, "_default_get", fake.get)
    monkeypatch.setattr(mods.auth, "_default_post", fake.post)
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )

    assert code == 0
    assert json.loads(out)["ok"] is True
    assert err == ""


def test_phase2g_runtime_imports_remain_stdlib_only(repo_root):
    import ast

    deny = DENYLIST
    checked = [
        repo_root / "notebooklm" / name for name in ("auth.py", "cli.py", "http_std.py")
    ]
    for path in checked:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                assert root not in deny, f"denylisted runtime import in {path}: {name}"
