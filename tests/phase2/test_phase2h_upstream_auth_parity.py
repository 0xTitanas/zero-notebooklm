"""Offline parity checks for upstream auth/config module behavior."""

from __future__ import annotations

import asyncio
import json
import contextlib
import os
import sys
import threading
import textwrap
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

import pytest

SECRET_SID = "sidSyntheticSecretValue0123456789abcdef"
SECRET_OSID = "osidSyntheticSecretValue0123456789abcdef"
SECRET_PSIDTS = "psidtsSyntheticSecretValue0123456789abcdef"
SECRET_CSRF = "csrfSyntheticSecretValue0123456789abcdef"
SECRET_SESSION = "sessionSyntheticSecretValue0123456789abcdef"


def _storage_state(*, psidts: str | None = SECRET_PSIDTS, osid: bool = True) -> dict:
    cookies = [
        {
            "name": "SID",
            "value": SECRET_SID,
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
            "expires": 1893456000,
        }
    ]
    if osid:
        cookies.append(
            {
                "name": "OSID",
                "value": SECRET_OSID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            }
        )
    if psidts is not None:
        cookies.append(
            {
                "name": "__Secure-1PSIDTS",
                "value": psidts,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            }
        )
    return {"cookies": cookies, "origins": []}


def _write_storage(path: Path, state: dict, *, age: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    if age:
        mt = time.time() - age
        os.utime(path, (mt, mt))
    return path


def _cookie_value(path: Path, name: str) -> str:
    for cookie in json.loads(path.read_text(encoding="utf-8"))["cookies"]:
        if cookie["name"] == name:
            return cookie["value"]
    raise KeyError(name)


def _wiz_html() -> str:
    return f'<script>{{"SNlM0e":"{SECRET_CSRF}","FdrFJe":"{SECRET_SESSION}"}}</script>'


class FakeResponse:
    def __init__(self, status: int, url: str, headers: dict[str, str] | None = None, body: str = ""):
        self.status = status
        self.url = url
        self.headers = headers or {}
        self.body = body.encode("utf-8")

    def text(self, encoding: str | None = None, errors: str = "replace") -> str:
        return self.body.decode(encoding or "utf-8", errors=errors)


def _make_cookie(name: str, value: str, domain: str = ".google.com", path: str = "/") -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=True,
        expires=1893456000,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""},
    )


def test_public_module_all_matches_upstream_compat(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    surface = json.loads((repo_root / "compat" / "python_api_surface.json").read_text())
    modules = (
        "notebooklm.auth",
        "notebooklm.config",
        "notebooklm.urls",
        "notebooklm.rpc.types",
    )

    import importlib

    for module in modules:
        module_surface = surface["modules"][module]
        imported = importlib.import_module(module)
        if module_surface["has_dunder_all"]:
            names = {member["name"] for member in module_surface["members"]}
            assert set(imported.__all__) == names
        else:
            assert not hasattr(imported, "__all__")


def test_config_base_url_and_language_env_match_upstream(monkeypatch):
    from notebooklm import config

    monkeypatch.delenv("NOTEBOOKLM_BASE_URL", raising=False)
    assert config.get_base_url() == "https://notebooklm.google.com"
    assert config.get_base_host() == "notebooklm.google.com"

    monkeypatch.setenv("NOTEBOOKLM_BASE_URL", " https://notebooklm.cloud.google.com/ ")
    assert config.get_base_url() == "https://notebooklm.cloud.google.com"
    assert config.get_base_host() == "notebooklm.cloud.google.com"

    monkeypatch.setenv("NOTEBOOKLM_HL", " fr ")
    assert config.get_default_language() == "fr"

    monkeypatch.setenv("NOTEBOOKLM_BASE_URL", "https://evil.example.test")
    with pytest.raises(ValueError, match="NOTEBOOKLM_BASE_URL"):
        config.get_base_url()


def test_notebooklm_profile_env_precedes_config_default(tmp_path, monkeypatch):
    from notebooklm import profiles

    store = profiles.ProfileStore(tmp_path)
    store.create_profile("configured")
    store.switch_profile("configured")

    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "env-profile")

    assert store.resolve_profile() == "env-profile"
    assert store.resolve_profile("explicit") == "explicit"


def test_note_auth_json_loads_without_storage_file(monkeypatch):
    from notebooklm import auth

    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", json.dumps(_storage_state()))
    cookies = auth.load_auth_from_storage(None)

    assert cookies["SID"] == SECRET_SID
    assert cookies["__Secure-1PSIDTS"] == SECRET_PSIDTS

    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", "")
    with pytest.raises(ValueError, match="set but empty"):
        auth.load_auth_from_storage(None)

    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", "[]")
    with pytest.raises(ValueError, match="cookies"):
        auth.load_auth_from_storage(None)


def test_load_auth_from_storage_none_uses_upstream_active_profile_resolution(
    tmp_path, monkeypatch
):
    from notebooklm import auth, paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    monkeypatch.delenv("NOTEBOOKLM_AUTH_JSON", raising=False)
    paths.set_active_profile("work")
    try:
        storage = _write_storage(
            tmp_path / "profiles" / "work" / "storage_state.json",
            _storage_state(),
        )

        cookies = auth.load_auth_from_storage(None)

        assert cookies["SID"] == SECRET_SID
        assert paths.get_storage_path() == storage
    finally:
        paths.set_active_profile(None)


def test_refresh_cmd_env_uses_resolved_profile_when_storage_path_is_explicit(
    tmp_path, monkeypatch
):
    from notebooklm import auth, paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path / "home"))
    storage = _write_storage(tmp_path / "storage_state.json", _storage_state())
    env_seen = tmp_path / "env_seen.json"
    refresh_script = tmp_path / "refresh.py"
    refresh_script.write_text(
        textwrap.dedent(
            f"""
            import json, os, pathlib
            pathlib.Path({str(env_seen)!r}).write_text(json.dumps({{
                "profile": os.environ.get("NOTEBOOKLM_REFRESH_PROFILE"),
                "storage": os.environ.get("NOTEBOOKLM_REFRESH_STORAGE_PATH"),
            }}), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD", f"{sys.executable} {refresh_script}")
    paths.set_active_profile("work")
    try:
        auth._run_refresh_cmd(storage)
    finally:
        paths.set_active_profile(None)

    assert json.loads(env_seen.read_text(encoding="utf-8")) == {
        "profile": "work",
        "storage": str(storage),
    }


def test_google_regional_cookie_domains_match_upstream_policy():
    from notebooklm import cookies

    assert cookies.is_allowed_google_domain(".google.co.uk")
    assert cookies.is_allowed_google_domain("accounts.google.co.jp")
    assert not cookies.is_allowed_google_domain(".evilgoogle.co.uk")


def test_rookiepy_cookie_conversion_matches_pinned_storage_shape():
    from notebooklm import auth

    state = auth.convert_rookiepy_cookies_to_storage_state(
        [
            {
                "domain": ".google.com",
                "name": "SID",
                "value": "synthetic",
                "path": "/",
                "secure": True,
                "expires": None,
                "http_only": True,
                "same_site": "Lax",
            },
            {
                "domain": "accounts.google.com",
                "name": "__Secure-1PSIDTS",
                "value": "persistent",
                "expires": 0,
            },
            {
                "domain": ".example.test",
                "name": "SID",
                "value": "filtered",
            },
        ]
    )

    assert state == {
        "cookies": [
            {
                "name": "SID",
                "value": "synthetic",
                "domain": ".google.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "persistent",
                "domain": "accounts.google.com",
                "path": "/",
                "expires": 0,
                "httpOnly": False,
                "secure": False,
                "sameSite": "None",
            }
        ],
        "origins": [],
    }


def test_psidts_inline_recovery_runs_only_when_sid_and_secondary_binding_exist(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(psidts=None), age=120)
    calls: list[str] = []

    def fake_post(url: str, **_kwargs):
        calls.append(url)
        return FakeResponse(
            200,
            url,
            {"set-cookie": "__Secure-1PSIDTS=recovered; Domain=.google.com; Path=/; Secure; HttpOnly"},
            "[]",
        )

    monkeypatch.setattr(auth, "_default_post", fake_post)

    cookies = auth.load_auth_from_storage(storage)

    assert calls == ["https://accounts.google.com/RotateCookies"]
    assert cookies["__Secure-1PSIDTS"] == "recovered"
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "recovered"

    expired = _storage_state(psidts="expired")
    for cookie in expired["cookies"]:
        if cookie["name"] == "__Secure-1PSIDTS":
            cookie["expires"] = int(time.time()) - 10
    expired_storage = _write_storage(tmp_path / "expired.json", expired, age=120)

    cookies = auth.load_auth_from_storage(expired_storage)

    assert calls[-1] == "https://accounts.google.com/RotateCookies"
    assert cookies["__Secure-1PSIDTS"] == "recovered"

    calls.clear()
    no_sid = _write_storage(tmp_path / "no_sid.json", {"cookies": [], "origins": []}, age=120)
    with pytest.raises(ValueError, match="SID"):
        auth.load_auth_from_storage(no_sid)
    assert calls == []


def test_psidts_inline_recovery_none_uses_active_profile_storage(tmp_path, monkeypatch):
    from notebooklm import auth, paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    monkeypatch.delenv("NOTEBOOKLM_AUTH_JSON", raising=False)
    paths.set_active_profile("work")
    storage = _write_storage(
        tmp_path / "profiles" / "work" / "storage_state.json",
        _storage_state(psidts=None),
        age=120,
    )
    calls: list[str] = []

    def fake_post(url: str, **_kwargs):
        calls.append(url)
        return FakeResponse(
            200,
            url,
            {"set-cookie": "__Secure-1PSIDTS=recovered; Domain=.google.com; Path=/; Secure; HttpOnly"},
            "[]",
        )

    monkeypatch.setattr(auth, "_default_post", fake_post)
    try:
        cookies = auth.load_auth_from_storage(None)
    finally:
        paths.set_active_profile(None)

    assert calls == ["https://accounts.google.com/RotateCookies"]
    assert cookies["__Secure-1PSIDTS"] == "recovered"
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "recovered"


def test_psidts_inline_recovery_reports_false_when_cookie_does_not_persist(
    tmp_path, monkeypatch
):
    from notebooklm import auth

    storage = _write_storage(
        tmp_path / "storage_state.json", _storage_state(psidts=None), age=120
    )

    def fake_post(url: str, **_kwargs):
        return FakeResponse(
            200,
            url,
            {"set-cookie": "__Secure-1PSIDTS=recovered; Domain=.google.com; Path=/; Secure; HttpOnly"},
            "[]",
        )

    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setattr(auth._cookies, "save_storage_state", lambda *_args, **_kwargs: None)

    assert auth._recover_psidts_inline(storage) is False
    assert "__Secure-1PSIDTS" not in {
        cookie["name"] for cookie in json.loads(storage.read_text(encoding="utf-8"))["cookies"]
    }


def test_psidts_inline_recovery_rotate_failure_is_best_effort(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(
        tmp_path / "storage_state.json", _storage_state(psidts=None), age=120
    )

    def fake_post(url: str, **_kwargs):
        return FakeResponse(429, url, {}, "")

    monkeypatch.setattr(auth, "_default_post", fake_post)

    assert auth._recover_psidts_inline(storage) is False


def test_psidts_inline_recovery_lock_contention_accepts_sibling_heal(
    tmp_path, monkeypatch
):
    from notebooklm import auth

    storage = _write_storage(
        tmp_path / "storage_state.json", _storage_state(psidts=None), age=120
    )

    @contextlib.contextmanager
    def sibling_holds_lock(_path: Path):
        data = json.loads(storage.read_text(encoding="utf-8"))
        data["cookies"].append(
            {
                "name": "__Secure-1PSIDTS",
                "value": "sibling-healed",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            }
        )
        storage.write_text(json.dumps(data), encoding="utf-8")
        yield False

    monkeypatch.setattr(auth, "_rotation_file_lock", sibling_holds_lock)
    monkeypatch.setattr(
        auth,
        "_default_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("lock contention must not post RotateCookies")
        ),
    )

    assert auth._recover_psidts_inline(storage) is True
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "sibling-healed"


def test_refresh_cmd_retries_auth_fetch_and_scrubs_auth_json(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(psidts="old"), age=120)
    env_seen = tmp_path / "env_seen.json"
    refresh_script = tmp_path / "refresh.py"
    refresh_script.write_text(
        textwrap.dedent(
            f"""
            import json, os, pathlib
            pathlib.Path({str(env_seen)!r}).write_text(json.dumps({{
                "attempted": os.environ.get("_NOTEBOOKLM_REFRESH_ATTEMPTED"),
                "auth_json_present": "NOTEBOOKLM_AUTH_JSON" in os.environ,
                "profile": os.environ.get("NOTEBOOKLM_REFRESH_PROFILE"),
                "storage": os.environ.get("NOTEBOOKLM_REFRESH_STORAGE_PATH"),
            }}), encoding="utf-8")
            path = pathlib.Path(os.environ["NOTEBOOKLM_REFRESH_STORAGE_PATH"])
            data = json.loads(path.read_text(encoding="utf-8"))
            for cookie in data["cookies"]:
                if cookie["name"] == "__Secure-1PSIDTS":
                    cookie["value"] = "fresh"
            path.write_text(json.dumps(data), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, str]] = []

    def fake_post(url: str, **_kwargs):
        calls.append(("POST", url))
        return FakeResponse(200, url, {}, "[]")

    def fake_get(url: str, **kwargs):
        calls.append(("GET", url))
        cookie_header = kwargs.get("headers", {}).get("Cookie", "")
        if "__Secure-1PSIDTS=old" in cookie_header:
            return FakeResponse(200, "https://accounts.google.com/o/oauth2/auth/token", {}, "login")
        return FakeResponse(200, url, {}, _wiz_html())

    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD", f"{sys.executable} {refresh_script}")
    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", json.dumps(_storage_state()))

    summary = auth.fetch_tokens_from_storage(storage, get=fake_get, post=fake_post, persist=False)

    assert summary["ok"] is True
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "fresh"
    assert json.loads(env_seen.read_text(encoding="utf-8")) == {
        "attempted": "1",
        "auth_json_present": False,
        "profile": "default",
        "storage": str(storage),
    }


def test_refresh_cmd_parse_errors_match_upstream_runtime_error(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state())

    def fake_get(url: str, **_kwargs):
        return FakeResponse(
            200, "https://accounts.google.com/o/oauth2/auth/token", {}, "login"
        )

    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD", '"unterminated')

    with pytest.raises(
        RuntimeError,
        match=r"NOTEBOOKLM_REFRESH_CMD could not be parsed: No closing quotation",
    ):
        auth.fetch_tokens_from_storage(storage, get=fake_get, persist=False)


def test_refresh_cmd_windows_split_uses_command_line_to_argv(monkeypatch):
    from notebooklm import auth

    calls: list[str] = []

    def fake_windows_split(cmd: str) -> list[str]:
        calls.append(cmd)
        return [r"C:\Program Files\Python\python.exe", "refresh.py"]

    monkeypatch.setattr(auth.os, "name", "nt", raising=False)
    monkeypatch.setattr(auth, "_windows_split_refresh_cmd", fake_windows_split)

    argv = auth._split_refresh_cmd(
        r'"C:\Program Files\Python\python.exe" refresh.py'
    )

    assert argv == [r"C:\Program Files\Python\python.exe", "refresh.py"]
    assert calls == [r'"C:\Program Files\Python\python.exe" refresh.py']


def test_refresh_cmd_coalesces_parallel_auth_failures_like_upstream(
    tmp_path, monkeypatch
):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state())
    barrier = threading.Barrier(2)
    refreshed = threading.Event()
    refresh_calls = 0
    refresh_calls_lock = threading.Lock()

    with auth._REFRESH_STATE_LOCK:
        auth._REFRESH_GENERATIONS.clear()
        auth._REFRESH_LOCKS.clear()

    def fake_get(url: str, **_kwargs):
        if not refreshed.is_set():
            barrier.wait(timeout=5)
            return FakeResponse(
                200,
                "https://accounts.google.com/o/oauth2/auth/token",
                {},
                "login",
            )
        return FakeResponse(200, url, {}, _wiz_html())

    def fake_refresh(_storage_path: Path | None, _profile: str | None = None):
        nonlocal refresh_calls
        with refresh_calls_lock:
            refresh_calls += 1
        time.sleep(0.05)
        refreshed.set()

    monkeypatch.setenv("NOTEBOOKLM_REFRESH_CMD", "synthetic-refresh")
    monkeypatch.setattr(auth, "_run_refresh_cmd", fake_refresh)

    results: list[dict] = []
    errors: list[BaseException] = []

    def worker():
        try:
            results.append(auth.fetch_tokens_from_storage(storage, get=fake_get, persist=False))
        except BaseException as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert [result["token_fetch_ok"] for result in results] == [True, True]
    assert refresh_calls == 1


def test_authuser_routing_is_added_to_homepage_when_explicit(tmp_path):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(), age=120)
    urls: list[str] = []

    def fake_post(url: str, **_kwargs):
        return FakeResponse(200, url, {}, "[]")

    def fake_get(url: str, **_kwargs):
        urls.append(url)
        return FakeResponse(200, url, {}, _wiz_html())

    auth.fetch_tokens_from_storage(storage, get=fake_get, post=fake_post, persist=False, authuser=1)
    auth.fetch_tokens_from_storage(storage, get=fake_get, post=fake_post, persist=False, authuser=0)

    assert urls == ["https://notebooklm.google.com/?authuser=1", "https://notebooklm.google.com/?authuser=0"]


def test_cookie_snapshot_delta_save_preserves_sibling_rotation(tmp_path):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(psidts="old"))
    jar = CookieJar()
    jar.set_cookie(_make_cookie("SID", SECRET_SID))
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "old"))
    snapshot = auth.snapshot_cookie_jar(jar)

    # Sibling process rotated the same cookie after our snapshot.
    data = json.loads(storage.read_text(encoding="utf-8"))
    for cookie in data["cookies"]:
        if cookie["name"] == "__Secure-1PSIDTS":
            cookie["value"] = "sibling-fresh"
    storage.write_text(json.dumps(data), encoding="utf-8")

    jar.clear(domain=".google.com", path="/", name="__Secure-1PSIDTS")
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "stale-local"))

    result = auth.save_cookies_to_storage(jar, storage, original_snapshot=snapshot, return_result=True)

    assert result.ok is False
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "sibling-fresh"


def test_cookie_snapshot_delta_ignores_disallowed_domain_changes(tmp_path):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state())
    jar = CookieJar()
    jar.set_cookie(_make_cookie("SID", SECRET_SID))
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", SECRET_PSIDTS))
    jar.set_cookie(_make_cookie("EVIL", "old", domain=".example.test"))
    snapshot = auth.snapshot_cookie_jar(jar)

    jar.clear(domain=".example.test", path="/", name="EVIL")
    jar.set_cookie(_make_cookie("EVIL", "new", domain=".example.test"))

    result = auth.save_cookies_to_storage(
        jar, storage, original_snapshot=snapshot, return_result=True
    )

    names = {cookie["name"] for cookie in json.loads(storage.read_text())["cookies"]}
    assert result.ok is True
    assert "EVIL" not in names


def test_cookie_snapshot_delta_accepts_disk_already_at_new_value(tmp_path):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(psidts="old"))
    jar = CookieJar()
    jar.set_cookie(_make_cookie("SID", SECRET_SID))
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "old"))
    snapshot = auth.snapshot_cookie_jar(jar)
    jar.clear(domain=".google.com", path="/", name="__Secure-1PSIDTS")
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "fresh"))

    data = json.loads(storage.read_text(encoding="utf-8"))
    for cookie in data["cookies"]:
        if cookie["name"] == "__Secure-1PSIDTS":
            cookie["value"] = "fresh"
    storage.write_text(json.dumps(data), encoding="utf-8")

    result = auth.save_cookies_to_storage(
        jar, storage, original_snapshot=snapshot, return_result=True
    )

    assert result.ok is True
    assert result.cas_rejected_keys == frozenset()
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "fresh"


def test_cookie_snapshot_delta_rejects_new_cookie_sibling_conflict(tmp_path):
    from notebooklm import auth

    state = _storage_state()
    state["cookies"].append(
        {
            "name": "SIDCC",
            "value": "sibling-fresh",
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
            "expires": 1893456000,
        }
    )
    storage = _write_storage(tmp_path / "storage_state.json", state)
    jar = CookieJar()
    jar.set_cookie(_make_cookie("SID", SECRET_SID))
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", SECRET_PSIDTS))
    snapshot = auth.snapshot_cookie_jar(jar)
    jar.set_cookie(_make_cookie("SIDCC", "local-new"))

    result = auth.save_cookies_to_storage(
        jar, storage, original_snapshot=snapshot, return_result=True
    )

    assert result.ok is False
    assert _cookie_value(storage, "SIDCC") == "sibling-fresh"


def test_auth_tokens_from_storage_preserves_snapshot_after_token_save_cas_reject(
    tmp_path, monkeypatch
):
    from notebooklm import auth

    storage = _write_storage(
        tmp_path / "storage_state.json", _storage_state(psidts="old-local-baseline")
    )
    sibling_fresh = "siblingFreshSyntheticValue0123456789abcdef"
    local_rotation = "localRotationSyntheticValue0123456789abcdef"

    def fake_get(url: str, **_kwargs):
        state = json.loads(storage.read_text(encoding="utf-8"))
        for cookie in state["cookies"]:
            if cookie["name"] == "__Secure-1PSIDTS":
                cookie["value"] = sibling_fresh
        storage.write_text(json.dumps(state), encoding="utf-8")
        return FakeResponse(
            200,
            url,
            {
                "set-cookie": (
                    "__Secure-1PSIDTS="
                    + local_rotation
                    + "; Domain=.google.com; Path=/; Secure; HttpOnly"
                )
            },
            _wiz_html(),
        )

    monkeypatch.setenv("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", "1")
    monkeypatch.setattr(auth, "_default_get", fake_get)

    tokens = asyncio.run(auth.AuthTokens.from_storage(storage))
    key = auth.CookieSnapshotKey("__Secure-1PSIDTS", ".google.com", "/")

    assert _cookie_value(storage, "__Secure-1PSIDTS") == sibling_fresh
    assert tokens.cookies[("__Secure-1PSIDTS", ".google.com", "/")] == local_rotation
    assert tokens.cookie_snapshot is not None
    assert tokens.cookie_snapshot[key].value == "old-local-baseline"


def test_auth_tokens_from_storage_advances_successful_snapshot_keys_after_partial_cas(
    tmp_path, monkeypatch
):
    from notebooklm import auth

    storage = _write_storage(
        tmp_path / "storage_state.json", _storage_state(psidts="old-psidts")
    )
    sid_local = "sidLocalSyntheticValue0123456789abcdef"
    psidts_sibling = "psidtsSiblingSyntheticValue0123456789abcdef"
    psidts_local = "psidtsLocalSyntheticValue0123456789abcdef"

    def fake_get(url: str, **_kwargs):
        state = json.loads(storage.read_text(encoding="utf-8"))
        for cookie in state["cookies"]:
            if cookie["name"] == "__Secure-1PSIDTS":
                cookie["value"] = psidts_sibling
        storage.write_text(json.dumps(state), encoding="utf-8")
        return FakeResponse(
            200,
            url,
            {
                "set-cookie": "\n".join(
                    [
                        f"SID={sid_local}; Domain=.google.com; Path=/; Secure; HttpOnly",
                        (
                            "__Secure-1PSIDTS="
                            + psidts_local
                            + "; Domain=.google.com; Path=/; Secure; HttpOnly"
                        ),
                    ]
                )
            },
            _wiz_html(),
        )

    monkeypatch.setenv("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", "1")
    monkeypatch.setattr(auth, "_default_get", fake_get)

    tokens = asyncio.run(auth.AuthTokens.from_storage(storage))
    sid_key = auth.CookieSnapshotKey("SID", ".google.com", "/")
    psidts_key = auth.CookieSnapshotKey("__Secure-1PSIDTS", ".google.com", "/")

    assert _cookie_value(storage, "SID") == sid_local
    assert _cookie_value(storage, "__Secure-1PSIDTS") == psidts_sibling
    assert tokens.cookie_snapshot is not None
    assert tokens.cookie_snapshot[sid_key].value == sid_local
    assert tokens.cookie_snapshot[psidts_key].value == "old-psidts"


def test_advance_cookie_snapshot_after_save_preserves_rejected_domain_variant():
    from notebooklm import auth

    bare_key = auth.CookieSnapshotKey("OSID", "accounts.google.com", "/")
    dotted_key = auth.CookieSnapshotKey("OSID", ".accounts.google.com", "/")
    original_snapshot = {
        bare_key: auth.CookieSnapshotValue(
            value="old",
            expires=None,
            secure=True,
            http_only=True,
        )
    }
    post_save_snapshot = {
        dotted_key: auth.CookieSnapshotValue(
            value="local",
            expires=None,
            secure=True,
            http_only=True,
        )
    }

    advanced = auth.advance_cookie_snapshot_after_save(
        original_snapshot,
        post_save_snapshot,
        frozenset({dotted_key}),
    )

    assert advanced == original_snapshot


def test_cookie_save_reads_storage_under_shared_lock(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state(psidts="old"))
    jar = CookieJar()
    jar.set_cookie(_make_cookie("SID", SECRET_SID))
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "old"))
    snapshot = auth.snapshot_cookie_jar(jar)
    jar.clear(domain=".google.com", path="/", name="__Secure-1PSIDTS")
    jar.set_cookie(_make_cookie("__Secure-1PSIDTS", "fresh"))
    seen: list[Path] = []

    @contextlib.contextmanager
    def mutate_before_read(path: Path):
        seen.append(path)
        data = json.loads(storage.read_text(encoding="utf-8"))
        data["cookies"].append(
            {
                "name": "SIDCC",
                "value": "sibling-under-lock",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        )
        storage.write_text(json.dumps(data), encoding="utf-8")
        yield

    monkeypatch.setattr(auth, "_storage_state_file_lock", mutate_before_read)

    result = auth.save_cookies_to_storage(
        jar, storage, original_snapshot=snapshot, return_result=True
    )

    assert seen == [storage]
    assert result.ok is True
    assert _cookie_value(storage, "SIDCC") == "sibling-under-lock"
    assert _cookie_value(storage, "__Secure-1PSIDTS") == "fresh"


def test_account_metadata_write_reads_storage_under_shared_lock(tmp_path, monkeypatch):
    from notebooklm import auth

    storage = _write_storage(tmp_path / "storage_state.json", _storage_state())
    seen: list[Path] = []

    @contextlib.contextmanager
    def mutate_before_read(path: Path):
        seen.append(path)
        data = json.loads(storage.read_text(encoding="utf-8"))
        data["cookies"].append(
            {
                "name": "SIDCC",
                "value": "sibling-under-lock",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        )
        storage.write_text(json.dumps(data), encoding="utf-8")
        yield

    monkeypatch.setattr(auth, "_storage_state_file_lock", mutate_before_read)

    auth.write_account_metadata(storage, authuser=2, email="profile-user@example.com")

    state = json.loads(storage.read_text(encoding="utf-8"))
    assert seen == [storage]
    assert _cookie_value(storage, "SIDCC") == "sibling-under-lock"
    assert state["notebooklm"]["account"] == {
        "authuser": 2,
        "email": "profile-user@example.com",
    }


def test_rpc_override_env_matches_upstream_validation(monkeypatch, caplog):
    from notebooklm.rpc import overrides

    monkeypatch.delenv("NOTEBOOKLM_BASE_URL", raising=False)
    overrides._parse_rpc_overrides.cache_clear()
    overrides._logged_override_hashes.clear()

    monkeypatch.setenv(
        "NOTEBOOKLM_RPC_OVERRIDES",
        json.dumps(
            {
                "LIST_NOTEBOOKS": "override-id",
                "LIST_NOTEBOOK": "typo",
                "GET_NOTEBOOK": None,
            }
        ),
    )

    assert overrides.resolve_rpc_id("LIST_NOTEBOOKS", "canonical") == "override-id"
    assert overrides.resolve_rpc_id("GET_NOTEBOOK", "canonical") == "canonical"
    assert "unknown NOTEBOOKLM_RPC_OVERRIDES method names" in caplog.text
    assert "null values" in caplog.text

    overrides._parse_rpc_overrides.cache_clear()
    monkeypatch.setenv("NOTEBOOKLM_RPC_OVERRIDES", "[]")
    assert overrides.resolve_rpc_id("LIST_NOTEBOOKS", "canonical") == "canonical"
    assert "must be a JSON object" in caplog.text
