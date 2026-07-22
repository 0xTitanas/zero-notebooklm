"""Hermetic coverage for live browser account discovery."""

from __future__ import annotations

import asyncio
import json
from http.cookiejar import Cookie

import pytest


@pytest.fixture(autouse=True)
def _reset_account_discovery_rotation_claim(monkeypatch):
    from notebooklm import auth

    monkeypatch.setattr(auth, "_ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC", 0.0)


def _cookie() -> Cookie:
    return Cookie(
        0,
        "SID",
        "synthetic",
        None,
        False,
        ".google.com",
        True,
        True,
        "/",
        True,
        True,
        1893456000,
        False,
        None,
        None,
        {},
    )


def _response(auth, url: str, email: str | None, *, set_cookie: str | None = None):
    headers = {"set-cookie": set_cookie} if set_cookie else {}
    return auth._http_std.Response(
        200, url, headers, (f'"{email}"' if email else "").encode()
    )


def test_enumerate_accounts_stops_on_default_and_keeps_set_cookie_in_memory(
    monkeypatch,
):
    from notebooklm import auth

    events = []
    calls = []

    def fake_post(url, *, headers, **kwargs):
        events.append("post")
        assert headers["Cookie"] == "SID=synthetic"
        return auth._http_std.Response(
            200,
            url,
            {"set-cookie": "SIDTS=rotated; Domain=.google.com; Path=/; Secure"},
            b"",
        )

    def fake_get(url, *, headers, **kwargs):
        events.append("get")
        calls.append((url, headers))
        authuser = int(url.rsplit("=", 1)[1])
        email = ("default@example.com", "other@example.com", "default@example.com")[
            authuser
        ]
        return _response(auth, url, email, set_cookie="SIDTS=fresh; Path=/; Secure")

    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setattr(auth, "_default_get", fake_get)
    jar = auth.CookieJar()
    jar.set_cookie(_cookie())

    accounts = asyncio.run(auth.enumerate_accounts(jar, max_authuser=5))

    assert [(a.authuser, a.is_default) for a in accounts] == [(0, True), (1, False)]
    assert events == ["post", "get", "get", "get"]
    assert len(calls) == 3
    assert all(
        headers["User-Agent"] == auth._ACCOUNT_DISCOVERY_USER_AGENT
        for _, headers in calls
    )
    assert "SIDTS=rotated" in calls[0][1]["Cookie"]
    assert all("SIDTS=fresh" in headers["Cookie"] for _, headers in calls[1:])
    assert [cookie.name for cookie in jar] == ["SID"]


def test_enumerate_accounts_ignores_rotate_failure(monkeypatch):
    from notebooklm import auth

    def fail_post(*args, **kwargs):
        raise auth.NetworkError("offline")

    monkeypatch.setattr(auth, "_default_post", fail_post)
    monkeypatch.setattr(
        auth,
        "_default_get",
        lambda url, **kwargs: _response(auth, url, "default@example.com"),
    )

    accounts = asyncio.run(auth.enumerate_accounts(auth.CookieJar(), max_authuser=1))

    assert accounts == [auth.Account(0, "default@example.com", True)]


def test_rotate_redirect_rescopes_cookie_and_merges_each_response(monkeypatch):
    from notebooklm import auth

    merged = []
    original_merge = auth._merge_cookie_updates

    def track_merge(state, updates):
        merged.extend(cookie["name"] for cookie in updates)
        return original_merge(state, updates)

    def fake_post(url, *, headers, follow_redirects, **kwargs):
        assert url == auth.ROTATE_COOKIES_URL
        assert follow_redirects is False
        assert headers["Cookie"] == "SID=synthetic"
        return auth._http_std.Response(
            302,
            url,
            {
                "location": "https://redirect.example.test/rotate",
                "set-cookie": "STEP=one; Domain=.google.com; Path=/; Secure",
            },
            b"",
        )

    def fake_get(url, *, headers, follow_redirects, **kwargs):
        assert follow_redirects is False
        if "redirect.example.test" in url:
            assert "Cookie" not in headers
            return auth._http_std.Response(
                200,
                url,
                {
                    "set-cookie": (
                        "CROSS=two; Path=/; Secure\n"
                        "INJECT=evil; Domain=.google.com; Path=/; Secure"
                    )
                },
                b"",
            )
        assert "STEP=one" in headers["Cookie"]
        assert "CROSS=two" not in headers["Cookie"]
        assert "INJECT=evil" not in headers["Cookie"]
        return _response(auth, url, "default@example.com")

    monkeypatch.setattr(auth, "_merge_cookie_updates", track_merge)
    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setattr(auth, "_default_get", fake_get)
    jar = auth.CookieJar()
    jar.set_cookie(_cookie())

    accounts = asyncio.run(auth.enumerate_accounts(jar, max_authuser=1))

    assert accounts == [auth.Account(0, "default@example.com", True)]
    assert merged[:2] == ["STEP", "CROSS"]
    assert "INJECT" not in merged


@pytest.mark.parametrize(("mode", "expected_posts"), [("missing", 1), ("limit", 2)])
def test_rotate_redirect_failures_are_bounded_and_nonfatal(
    monkeypatch, mode, expected_posts
):
    from notebooklm import auth

    post_calls = []

    def fake_post(url, *, follow_redirects, **kwargs):
        post_calls.append(url)
        headers = {"location": "/again"} if mode == "limit" else {}
        return auth._http_std.Response(307, url, headers, b"")

    monkeypatch.setattr(auth, "_AUTH_MAX_REDIRECTS", 1)
    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setattr(
        auth,
        "_default_get",
        lambda url, **kwargs: _response(auth, url, "default@example.com"),
    )

    accounts = asyncio.run(auth.enumerate_accounts(auth.CookieJar(), max_authuser=1))

    assert accounts == [auth.Account(0, "default@example.com", True)]
    assert len(post_calls) == expected_posts


def test_rotate_malformed_redirect_is_nonfatal(monkeypatch):
    from notebooklm import auth

    monkeypatch.setattr(
        auth,
        "_default_post",
        lambda url, **kwargs: auth._http_std.Response(
            307, url, {"location": "https://[malformed"}, b""
        ),
    )
    monkeypatch.setattr(
        auth,
        "_default_get",
        lambda url, **kwargs: _response(auth, url, "default@example.com"),
    )

    accounts = asyncio.run(auth.enumerate_accounts(auth.CookieJar(), max_authuser=1))

    assert accounts == [auth.Account(0, "default@example.com", True)]


def test_enumerate_accounts_honors_disabled_keepalive(monkeypatch):
    from notebooklm import auth

    def forbidden_post(*args, **kwargs):
        raise AssertionError("disabled keepalive must not post")

    monkeypatch.setenv(auth.NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV, "1")
    monkeypatch.setattr(auth, "_default_post", forbidden_post)
    monkeypatch.setattr(
        auth,
        "_default_get",
        lambda url, **kwargs: _response(auth, url, "default@example.com"),
    )

    accounts = asyncio.run(auth.enumerate_accounts(auth.CookieJar(), max_authuser=1))

    assert accounts == [auth.Account(0, "default@example.com", True)]


def test_enumerate_accounts_rejects_unsigned_default(monkeypatch):
    from notebooklm import auth

    def fake_get(url, **kwargs):
        if "accounts.google.com" in url:
            return auth._http_std.Response(200, url, {}, b"")
        return auth._http_std.Response(
            302,
            url,
            {"location": "https://accounts.google.com/signin"},
            b"",
        )

    monkeypatch.setenv(auth.NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV, "1")
    monkeypatch.setattr(auth, "_default_get", fake_get)

    with pytest.raises(ValueError, match="authuser=0 did not return"):
        asyncio.run(auth.enumerate_accounts(auth.CookieJar()))


def _patch_live_extraction(monkeypatch, bc):
    extraction = bc.CookieExtraction(
        browser="firefox",
        family=bc.FAMILY_FIREFOX,
        source_path=None,
        source_present=True,
        cookies=[
            {
                "name": "SID",
                "value": "synthetic",
                "domain": ".google.com",
                "path": "/",
                "expires": -1,
                "http_only": True,
                "secure": True,
                "same_site": "Lax",
            }
        ],
    )
    location = bc.CookieStoreLocation("firefox", bc.FAMILY_FIREFOX, "macos", True, True)
    monkeypatch.setattr(
        bc,
        "_extract_live_and_filter",
        lambda *args, **kwargs: (
            "firefox",
            bc.FAMILY_FIREFOX,
            location,
            extraction,
            extraction.cookies,
        ),
    )


def test_live_import_discovers_accounts_and_rejects_missing_selector(
    monkeypatch, tmp_path
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)

    async def discovered(jar):
        return [
            auth.Account(0, "default@example.com", True),
            auth.Account(1, "selected@example.com", False),
        ]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    state_path = tmp_path / "state.json"
    summary = bc.import_live_browser_to_storage_state(
        "firefox", dest_path=state_path, authuser=1
    )
    assert summary["account"] == {
        "count": 2,
        "email_present_count": 2,
        "default_authuser": 0,
        "selected_authuser": 1,
    }
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert "accounts" not in persisted
    assert "account" not in persisted
    assert persisted["notebooklm"] == {
        "version": 1,
        "account": {"authuser": 1, "email": "selected@example.com"},
    }
    assert persisted["cookies"][0]["expires"] == -1
    assert persisted["cookies"][0]["sameSite"] == "None"
    assert "default@example.com" not in state_path.read_text(encoding="utf-8")

    case_path = tmp_path / "case.json"
    bc.import_live_browser_to_storage_state(
        "firefox",
        dest_path=case_path,
        account_email="  SELECTED@example.com  ",
    )
    assert json.loads(case_path.read_text(encoding="utf-8"))["notebooklm"][
        "account"
    ] == {"authuser": 1, "email": "selected@example.com"}

    with pytest.raises(
        bc.ValidationError, match="requested browser account was not found"
    ):
        bc.import_live_browser_to_storage_state(
            "firefox", dest_path=tmp_path / "missing.json", authuser=9
        )


def test_live_import_rejects_implicit_all_accounts(monkeypatch, tmp_path):
    from notebooklm import browser_cookies as bc

    with pytest.raises(
        bc.ValidationError, match="live --all-accounts is not supported"
    ):
        bc.import_live_browser_to_storage_state(
            "firefox", dest_path=tmp_path / "state.json", all_accounts=True
        )


def test_live_import_keeps_explicit_all_accounts_behavior(monkeypatch, tmp_path):
    from notebooklm import auth, browser_cookies as bc

    def forbidden_enumerate(jar):
        raise AssertionError("explicit accounts must skip discovery")

    _patch_live_extraction(monkeypatch, bc)
    monkeypatch.setattr(auth, "enumerate_accounts", forbidden_enumerate)
    summary = bc.import_live_browser_to_storage_state(
        "firefox",
        dest_path=tmp_path / "state.json",
        accounts=[{"authuser": 0, "email": "default@example.com", "is_default": True}],
        all_accounts=True,
    )

    assert summary["account"]["count"] == 1


def test_live_import_rejects_discovery_from_running_event_loop(monkeypatch, tmp_path):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)
    called = False

    def forbidden_enumerate(jar):
        nonlocal called
        called = True
        raise AssertionError("enumeration coroutine must not be created")

    monkeypatch.setattr(auth, "enumerate_accounts", forbidden_enumerate)

    async def invoke():
        with pytest.raises(bc.ValidationError, match="active event loop"):
            bc.import_live_browser_to_storage_state(
                "firefox", dest_path=tmp_path / "state.json", authuser=0
            )

    asyncio.run(invoke())
    assert called is False


def test_live_inspect_enumerates_accounts_without_writing_or_echoing_paths(
    monkeypatch, tmp_path
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)
    resolved_profile = "Profile 1"
    monkeypatch.setattr(
        bc,
        "_extract_live_and_filter",
        lambda *args, **kwargs: (
            "firefox",
            bc.FAMILY_FIREFOX,
            bc.CookieStoreLocation(
                "firefox",
                bc.FAMILY_FIREFOX,
                "macos",
                True,
                True,
                path=str(tmp_path / "private" / "cookies.sqlite"),
                browser_profile=resolved_profile,
            ),
            bc.CookieExtraction("firefox", bc.FAMILY_FIREFOX, None, True, []),
            [{"name": "SID", "value": "synthetic", "domain": ".google.com"}],
        ),
    )

    async def discovered(jar):
        assert [cookie.name for cookie in jar] == ["SID"]
        return [
            auth.Account(0, "default@example.com", True),
            auth.Account(1, "other@example.com", False),
        ]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)

    accounts = bc.enumerate_live_browser_accounts("firefox")

    assert accounts == [
        {"email": "default@example.com", "is_default": True, "browser_profile": None},
        {"email": "other@example.com", "is_default": False, "browser_profile": None},
    ]
    assert list(tmp_path.iterdir()) == []

    accounts = bc.enumerate_live_browser_accounts(
        "firefox", browser_profile=resolved_profile
    )
    assert [account["browser_profile"] for account in accounts] == [
        resolved_profile,
        resolved_profile,
    ]
    assert str(tmp_path) not in json.dumps(accounts)
    assert "synthetic" not in json.dumps(accounts)


def test_live_inspect_rejects_running_event_loop_before_enumeration(monkeypatch):
    from notebooklm import auth, browser_cookies as bc

    called = False

    def forbidden_enumerate(jar):
        nonlocal called
        called = True
        raise AssertionError("enumeration coroutine must not be created")

    monkeypatch.setattr(auth, "enumerate_accounts", forbidden_enumerate)

    async def invoke():
        with pytest.raises(bc.ValidationError, match="active event loop"):
            bc.enumerate_live_browser_accounts("firefox")

    asyncio.run(invoke())
    assert called is False


def test_cli_live_inspect_matches_upstream_account_envelope(
    monkeypatch, tmp_path, capsys
):
    from notebooklm import cli

    home = tmp_path / "home"
    private_path = tmp_path / "private"
    accounts = [
        {"email": "default@example.com", "is_default": True, "browser_profile": None},
        {"email": "other@example.com", "is_default": False, "browser_profile": None},
    ]
    seen = {}

    def fake_enumerate(browser, **kwargs):
        seen.update(browser=browser, **kwargs)
        return accounts

    monkeypatch.setattr(cli._bc, "enumerate_live_browser_accounts", fake_enumerate)

    code = cli.console(
        ["--storage", str(home), "auth", "inspect", "--browser", "firefox", "--json"]
    )
    output = capsys.readouterr()

    assert code == 0, output.err
    assert json.loads(output.out) == {"browser": "firefox", "accounts": accounts}
    assert str(private_path) not in output.out
    assert seen["browser"] == "firefox"

    code = cli.console(
        ["--storage", str(home), "auth", "inspect", "--browser", "firefox"]
    )
    output = capsys.readouterr()
    assert code == 0, output.err
    assert output.out == (
        "Browser: firefox\n"
        "Found 2 signed-in Google account(s):\n"
        "default@example.com (default)\n"
        "other@example.com\n"
    )


def test_cli_live_account_selector_is_forwarded_without_echo(
    monkeypatch, tmp_path, capsys
):
    from notebooklm import cli, profiles

    home = tmp_path / "home"
    profiles.ProfileStore(home).create_profile("default")
    selected = "selected@example.com"
    seen = {}

    def fake_import(browser, **kwargs):
        seen["account_email"] = kwargs["account_email"]
        return {
            "browser": "firefox",
            "family": "firefox",
            "os": "macos",
            "browser_profile": None,
            "source_kind": "live_browser",
            "account": {"selected_authuser": 1},
        }

    monkeypatch.setattr(cli._bc, "import_live_browser_to_storage_state", fake_import)

    code = cli.console(
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "firefox",
            "--account",
            selected,
            "--json",
        ]
    )
    output = capsys.readouterr()

    assert code == 0, output.err
    assert seen == {"account_email": selected}
    assert selected not in output.out


def test_live_default_import_skips_discovery_and_leaves_metadata_absent(
    monkeypatch, tmp_path
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)

    def forbidden_enumerate(jar):
        raise AssertionError("default live import must not enumerate accounts")

    monkeypatch.setattr(auth, "enumerate_accounts", forbidden_enumerate)
    state_path = tmp_path / "state.json"
    bc.import_live_browser_to_storage_state("firefox", dest_path=state_path)

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert "accounts" not in persisted and "account" not in persisted
    assert "notebooklm" not in persisted


def test_live_refresh_selects_default_and_does_not_persist_account_list(
    monkeypatch, tmp_path
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)

    async def discovered(jar):
        return [
            auth.Account(0, "default@example.com", True),
            auth.Account(1, "other@example.com", False),
        ]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    state_path = tmp_path / "state.json"
    meta_path = tmp_path / "auth_source.json"
    bc.write_auth_source(
        meta_path,
        bc.build_auth_source_metadata(
            "firefox", source_kind=bc.SOURCE_KIND_LIVE_BROWSER, os_name="macos"
        ),
    )

    summary = bc.refresh_browser_cookies(
        "firefox", dest_path=state_path, meta_path=meta_path
    )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert summary["account"]["selected_authuser"] == 0
    assert "accounts" not in persisted and "account" not in persisted
    assert persisted["notebooklm"]["account"] == {
        "authuser": 0,
        "email": "default@example.com",
    }
    assert persisted["cookies"][0]["expires"] == -1
    assert persisted["cookies"][0]["sameSite"] == "None"
    assert "other@example.com" not in state_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("stored_email", ["SELECTED@example.com", "  "])
def test_live_refresh_matches_stored_email_or_falls_back_from_blank(
    monkeypatch, tmp_path, stored_email
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)

    async def discovered(jar):
        return [auth.Account(1, "selected@example.com", True)]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [],
                "origins": [],
                "notebooklm": {
                    "version": 1,
                    "account": {
                        "authuser": 1,
                        "email": stored_email,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    meta_path = tmp_path / "auth_source.json"
    bc.write_auth_source(
        meta_path,
        bc.build_auth_source_metadata(
            "firefox", source_kind=bc.SOURCE_KIND_LIVE_BROWSER, os_name="macos"
        ),
    )

    bc.refresh_browser_cookies(
        "firefox", dest_path=state_path, meta_path=meta_path
    )

    assert json.loads(state_path.read_text(encoding="utf-8"))["notebooklm"][
        "account"
    ] == {"authuser": 1, "email": "selected@example.com"}


@pytest.mark.parametrize(
    "stored", [{"authuser": 9}, {"authuser": 0, "email": "stale@example.com"}]
)
def test_live_refresh_rejects_stale_or_missing_stored_selection(
    monkeypatch, tmp_path, stored
):
    from notebooklm import auth, browser_cookies as bc

    _patch_live_extraction(monkeypatch, bc)

    async def discovered(jar):
        return [auth.Account(0, "other@example.com", True)]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [],
                "origins": [],
                "notebooklm": {"version": 1, "account": stored},
            }
        ),
        encoding="utf-8",
    )
    meta_path = tmp_path / "auth_source.json"
    bc.write_auth_source(
        meta_path,
        bc.build_auth_source_metadata(
            "firefox", source_kind=bc.SOURCE_KIND_LIVE_BROWSER, os_name="macos"
        ),
    )

    with pytest.raises(
        bc.ValidationError, match="stored browser account was not found"
    ):
        bc.refresh_browser_cookies("firefox", dest_path=state_path, meta_path=meta_path)


def test_live_refresh_rejects_running_event_loop_before_enumeration(
    monkeypatch, tmp_path
):
    from notebooklm import auth, browser_cookies as bc

    called = False

    def forbidden_enumerate(jar):
        nonlocal called
        called = True
        raise AssertionError("enumeration coroutine must not be created")

    monkeypatch.setattr(auth, "enumerate_accounts", forbidden_enumerate)
    meta_path = tmp_path / "auth_source.json"
    bc.write_auth_source(
        meta_path,
        bc.build_auth_source_metadata(
            "firefox", source_kind=bc.SOURCE_KIND_LIVE_BROWSER, os_name="macos"
        ),
    )

    async def invoke():
        with pytest.raises(bc.ValidationError, match="active event loop"):
            bc.refresh_browser_cookies(
                "firefox", dest_path=tmp_path / "state.json", meta_path=meta_path
            )

    asyncio.run(invoke())
    assert called is False


@pytest.mark.parametrize(
    "profile_args",
    [
        ["--browser-cookies", "chrome::Profile 2"],
        ["--browser-cookies", "chrome", "--browser-profile", "Profile 2"],
    ],
)
def test_cli_live_refresh_rejects_profile_mismatching_persisted_source(
    tmp_path, profile_args, capsys
):
    from notebooklm import browser_cookies as bc, cli, profiles

    home = tmp_path / "home"
    store = profiles.ProfileStore(home)
    store.create_profile("default")
    bc.write_auth_source(
        store.auth_source_path("default"),
        bc.build_auth_source_metadata(
            "chrome",
            source_kind=bc.SOURCE_KIND_LIVE_BROWSER,
            os_name="macOS",
            browser_profile="Profile 1",
        ),
    )

    code = cli.console(
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            *profile_args,
            "--json",
        ]
    )
    output = capsys.readouterr()

    assert code == 64
    assert "does not match the persisted browser profile" in output.err


def test_cli_live_inspect_scoped_selector_preserves_json_browser(
    monkeypatch, tmp_path, capsys
):
    from notebooklm import cli

    seen = {}

    def fake_enumerate(browser, **kwargs):
        seen.update(browser=browser, **kwargs)
        return []

    monkeypatch.setattr(cli._bc, "enumerate_live_browser_accounts", fake_enumerate)
    code = cli.console(
        [
            "--storage",
            str(tmp_path / "home"),
            "auth",
            "inspect",
            "--browser",
            "chrome::Profile 1",
            "--json",
        ]
    )
    output = capsys.readouterr()

    assert code == 0, output.err
    assert json.loads(output.out) == {"browser": "chrome::Profile 1", "accounts": []}
    assert seen["browser"] == "chrome"
    assert seen["browser_profile"] == "Profile 1"


def test_cli_live_inspect_unknown_scoped_selector_keeps_structured_error(
    tmp_path, capsys
):
    from notebooklm import cli

    code = cli.console(
        [
            "--storage",
            str(tmp_path / "home"),
            "auth",
            "inspect",
            "--browser",
            "unknown::Profile",
            "--json",
        ]
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert code == 1
    assert output.err == ""
    assert payload["code"] == "UNKNOWN_BROWSER"
    assert payload["browser"] == "unknown::Profile"


def test_cli_live_inspect_selector_conflict_is_rejected(tmp_path):
    from notebooklm import cli

    code = cli.console(
        [
            "--storage",
            str(tmp_path / "home"),
            "auth",
            "inspect",
            "--browser",
            "firefox::work",
            "--browser-profile",
            "other",
        ]
    )
    assert code == 64


def test_cli_explicit_store_inspect_scoped_selector_forwards_profile(
    monkeypatch, tmp_path, capsys
):
    from notebooklm import cli

    seen = {}

    def fake_inspect(browser, **kwargs):
        seen.update(browser=browser, **kwargs)
        return {"browser": browser}

    monkeypatch.setattr(cli._bc, "inspect_cookie_store", fake_inspect)
    code = cli.console(
        [
            "--storage",
            str(tmp_path / "home"),
            "auth",
            "inspect",
            "--browser",
            "chrome::Profile 1",
            "--cookie-store",
            str(tmp_path / "Cookies"),
            "--json",
        ]
    )
    output = capsys.readouterr()

    assert code == 0, output.err
    assert seen["browser"] == "chrome"
    assert seen["browser_profile"] == "Profile 1"


@pytest.mark.parametrize("explicit_profile", ["Profile 1", "Profile 2"])
def test_cli_explicit_store_inspect_selector_conflict_is_rejected(
    tmp_path, explicit_profile
):
    from notebooklm import cli

    assert (
        cli.console(
            [
                "--storage",
                str(tmp_path / "home"),
                "auth",
                "inspect",
                "--browser",
                "chrome::Profile 1",
                "--browser-profile",
                explicit_profile,
                "--cookie-store",
                str(tmp_path / "Cookies"),
            ]
        )
        == 64
    )


def test_live_chromium_fanout_orders_dedupes_and_tags_profiles(monkeypatch):
    from notebooklm import auth, browser_cookies as bc

    monkeypatch.setattr(
        bc, "_live_chromium_profiles", lambda *args: ["Default", "Profile 2"]
    )
    calls = []

    def fake_extract(browser, *, browser_profile, **kwargs):
        calls.append(browser_profile)
        cookie = {"name": "SID", "value": browser_profile, "domain": ".google.com"}
        location = bc.CookieStoreLocation(
            browser,
            bc.FAMILY_CHROMIUM,
            "macos",
            True,
            True,
            browser_profile=browser_profile,
        )
        return browser, bc.FAMILY_CHROMIUM, location, None, [cookie]

    async def discovered(jar):
        profile = next(iter(jar)).value
        return (
            [
                auth.Account(0, "first@example.com", True),
                auth.Account(1, "shared@example.com", False),
            ]
            if profile == "Default"
            else [
                auth.Account(0, "shared@example.com", True),
                auth.Account(1, "second@example.com", True),
            ]
        )

    monkeypatch.setattr(bc, "_extract_live_and_filter", fake_extract)
    monkeypatch.setattr(auth, "enumerate_accounts", discovered)

    assert bc.enumerate_live_browser_accounts("chrome") == [
        {
            "email": "first@example.com",
            "is_default": True,
            "browser_profile": "Default",
        },
        {
            "email": "shared@example.com",
            "is_default": False,
            "browser_profile": "Default",
        },
        {
            "email": "second@example.com",
            "is_default": False,
            "browser_profile": "Profile 2",
        },
    ]
    assert calls == ["Default", "Profile 2"]


def test_live_chromium_single_profile_keeps_legacy_none_profile(monkeypatch):
    from notebooklm import auth, browser_cookies as bc

    monkeypatch.setattr(bc, "_live_chromium_profiles", lambda *args: ["Default"])
    _patch_live_extraction(monkeypatch, bc)

    async def discovered(jar):
        return [auth.Account(0, "default@example.com", True)]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    assert bc.enumerate_live_browser_accounts("chrome") == [
        {"email": "default@example.com", "is_default": True, "browser_profile": None}
    ]


def test_live_chromium_fanout_assigns_default_to_first_signed_in_profile(monkeypatch):
    from notebooklm import auth, browser_cookies as bc

    monkeypatch.setattr(
        bc, "_live_chromium_profiles", lambda *args: ["Default", "Profile 1"]
    )

    def fake_extract(browser, *, browser_profile, **kwargs):
        cookie = {"name": "SID", "value": browser_profile, "domain": ".google.com"}
        location = bc.CookieStoreLocation(
            browser,
            bc.FAMILY_CHROMIUM,
            "macos",
            True,
            True,
            browser_profile=browser_profile,
        )
        return browser, bc.FAMILY_CHROMIUM, location, None, [cookie]

    async def discovered(jar):
        profile = next(iter(jar)).value
        return [] if profile == "Default" else [
            auth.Account(0, "second@example.com", True)
        ]

    monkeypatch.setattr(bc, "_extract_live_and_filter", fake_extract)
    monkeypatch.setattr(auth, "enumerate_accounts", discovered)

    assert bc.enumerate_live_browser_accounts("chrome") == [
        {
            "email": "second@example.com",
            "is_default": True,
            "browser_profile": "Profile 1",
        }
    ]


def test_live_chromium_fanout_skips_stale_profile(monkeypatch):
    from notebooklm import auth, browser_cookies as bc

    monkeypatch.setattr(
        bc, "_live_chromium_profiles", lambda *args: ["Default", "Profile 1"]
    )

    def fake_extract(browser, *, browser_profile, **kwargs):
        if browser_profile == "Default":
            raise bc.ValidationError("synthetic stale profile")
        location = bc.CookieStoreLocation(
            browser,
            bc.FAMILY_CHROMIUM,
            "macos",
            True,
            True,
            browser_profile=browser_profile,
        )
        cookie = {"name": "SID", "value": "synthetic", "domain": ".google.com"}
        return browser, bc.FAMILY_CHROMIUM, location, None, [cookie]

    async def discovered(jar):
        return [auth.Account(0, "second@example.com", True)]

    monkeypatch.setattr(bc, "_extract_live_and_filter", fake_extract)
    monkeypatch.setattr(auth, "enumerate_accounts", discovered)

    assert bc.enumerate_live_browser_accounts("chrome") == [
        {
            "email": "second@example.com",
            "is_default": True,
            "browser_profile": "Profile 1",
        }
    ]


def test_cli_live_scoped_cookie_selector_forwards_profile_to_login_and_refresh(
    monkeypatch, tmp_path, capsys
):
    from notebooklm import browser_cookies as bc, cli, profiles

    home = tmp_path / "home"
    profiles.ProfileStore(home).create_profile("default")
    calls = []

    def fake_import(browser, **kwargs):
        calls.append(("login", browser, kwargs["browser_profile"]))
        return {
            "browser": browser,
            "family": bc.FAMILY_CHROMIUM,
            "os": "macos",
            "browser_profile": kwargs["browser_profile"],
            "source_kind": "live_browser",
            "account": {},
        }

    def fake_refresh(browser, **kwargs):
        calls.append(("refresh", browser, kwargs["browser_profile"]))
        return {"browser": browser, "browser_profile": kwargs["browser_profile"]}

    monkeypatch.setattr(cli._bc, "import_live_browser_to_storage_state", fake_import)
    monkeypatch.setattr(cli._bc, "refresh_browser_cookies", fake_refresh)

    for command in (("login",), ("auth", "refresh")):
        code = cli.console(
            [
                "--storage",
                str(home),
                *command,
                "--browser-cookies",
                "chrome::Profile 1",
                "--os",
                "macOS",
                "--json",
            ]
        )
        output = capsys.readouterr()
        assert code == 0, output.err

    assert calls == [
        ("login", "chrome", "Profile 1"),
        ("refresh", "chrome", "Profile 1"),
    ]


def test_enumerate_accounts_throttles_in_process_rotate_without_skipping_gets(
    monkeypatch,
):
    from notebooklm import auth

    posts = []
    gets = []

    monkeypatch.setattr(auth, "_default_post", lambda *args, **kwargs: posts.append(1) or auth._http_std.Response(200, auth.ROTATE_COOKIES_URL, {}, b""))

    def fake_get(url, **kwargs):
        gets.append(url)
        authuser = int(url.rsplit("=", 1)[1])
        return _response(
            auth, url, "default@example.com" if authuser == 0 else None
        )

    monkeypatch.setattr(auth, "_default_get", fake_get)

    for _ in range(2):
        assert asyncio.run(auth.enumerate_accounts(auth.CookieJar(), max_authuser=1)) == [
            auth.Account(0, "default@example.com", True)
        ]

    assert len(posts) == 1
    assert len(gets) == 4
