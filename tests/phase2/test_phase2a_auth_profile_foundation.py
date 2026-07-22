"""Phase 2A auth/session/profile foundation tests.

These tests target only the Phase 2A offline/fixture-backed slice:

  * profile directory layout, precedence, and lifecycle (create/delete/list/
    rename/switch) using the stdlib only;
  * per-profile session context for ``use``/``status``/``clear``;
  * upstream-compatible Playwright ``storage_state.json`` load/save, Netscape
    cookie import/export, explicit cookie JSON import/export, and deterministic
    redaction helpers that never emit cookie/token values;
  * offline ``auth check``/``auth inspect``/``auth logout`` and ``doctor``.

They never touch the network, a real Google/NotebookLM account, a real browser
cookie store, an OS keychain, or ``~/.notebooklm``. Every filesystem write is
confined to a caller-provided temp ``home`` root.
"""

from __future__ import annotations

import importlib
import importlib.abc
import json
import types
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402  (placed on sys.path by tests/conftest.py)
import import_origin_audit  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp", "urllib3"}

# A handful of realistic-looking secret values used to prove redaction. None of
# these are real credentials; they are synthetic strings shaped like the real
# thing so the "no values leak" assertions are meaningful. They deliberately do
# NOT embed a contiguous real credential *format* literal (e.g. an "ya29." OAuth
# token), so the repo-wide secret scanner stays clean; format-specific detection
# is exercised with tokens assembled at runtime in the test body below.
SECRET_SID = "averysecretsidvalue1234567890ABCDEF"
SECRET_PSIDTS = "sidts-CjEBSecretRotatingTokenValue000111222333"
SECRET_OAUTH = "oauthAccessTokenFAKEvalue0011223344556677"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class _DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401 - import hook protocol
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


@pytest.fixture
def mods(repo_root, monkeypatch):
    """Import the Phase 2A modules from the checkout, guarding against any
    denylisted third-party runtime import sneaking in at import time."""
    monkeypatch.syspath_prepend(str(repo_root))
    import sys

    finder = _DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        ns = types.SimpleNamespace(
            profiles=importlib.import_module("notebooklm.profiles"),
            cookies=importlib.import_module("notebooklm.cookies"),
            auth=importlib.import_module("notebooklm.auth"),
            cli=importlib.import_module("notebooklm.cli"),
            errors=importlib.import_module("notebooklm.errors"),
            output=importlib.import_module("notebooklm.output"),
        )
    finally:
        sys.meta_path.remove(finder)
    return ns


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


def _good_storage_state(mods, *, accounts=None) -> dict:
    """A minimal but valid Playwright storage_state with the required cookies."""
    state = {
        "cookies": [
            {
                "name": "SID",
                "value": SECRET_SID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "expires": 1893456000,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 1893456000,
            },
        ],
        "origins": [
            {
                "origin": "https://notebooklm.google.com",
                "localStorage": [{"name": "tok", "value": SECRET_OAUTH}],
            },
        ],
    }
    if accounts is not None:
        state["accounts"] = accounts
    return state


# --------------------------------------------------------------------------- #
# Profile name validation + storage-root precedence
# --------------------------------------------------------------------------- #


def test_validate_profile_name_accepts_conservative_names(mods):
    for ok in ("default", "work", "alice", "work-old", "a_b.c", "Team2"):
        assert mods.profiles.validate_profile_name(ok) == ok


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "-leads",
        ".hidden",
        "a b",
        "a\tb",
        "a\x00b",
        "x" * 200,
        "con/../etc",
    ],
)
def test_validate_profile_name_rejects_unsafe_names(mods, bad):
    with pytest.raises(mods.errors.ValidationError):
        mods.profiles.validate_profile_name(bad)


def test_resolve_home_precedence_explicit_env_default(mods, tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    env_home = tmp_path / "from-env"

    # 1) explicit beats everything
    got = mods.profiles.resolve_home(
        explicit, environ={"NOTEBOOKLM_HOME": str(env_home)}
    )
    assert Path(got) == explicit

    # 2) env var when no explicit
    got = mods.profiles.resolve_home(None, environ={"NOTEBOOKLM_HOME": str(env_home)})
    assert Path(got) == env_home

    # 3) default ~/.notebooklm when neither; must NOT create it
    got = mods.profiles.resolve_home(None, environ={})
    assert Path(got) == Path.home() / ".notebooklm"
    # resolve_home is pure: it must not have created the default home directory.
    assert not (tmp_path / ".notebooklm").exists()


# --------------------------------------------------------------------------- #
# Profile lifecycle
# --------------------------------------------------------------------------- #


def test_profile_create_list_exists(mods, home):
    store = mods.profiles.ProfileStore(home)
    assert store.list_profiles() == []
    p = store.create_profile("work")
    assert Path(p).is_dir()
    assert Path(p).is_relative_to(home)
    store.create_profile("home2")
    assert store.list_profiles() == ["home2", "work"]
    assert store.profile_exists("work") and not store.profile_exists("nope")


def test_profile_create_existing_raises(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    with pytest.raises(mods.errors.ProfileExistsError):
        store.create_profile("work")


def test_profile_switch_sets_active_and_resolution_precedence(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.create_profile("play")
    assert store.active_profile() is None
    # explicit beats active beats default
    assert store.resolve_profile("play") == "play"
    assert store.resolve_profile() == mods.profiles.DEFAULT_PROFILE_NAME
    store.switch_profile("work")
    assert store.active_profile() == "work"
    assert store.resolve_profile() == "work"
    assert store.resolve_profile("play") == "play"


def test_profile_switch_missing_raises(mods, home):
    store = mods.profiles.ProfileStore(home)
    with pytest.raises(mods.errors.ProfileNotFoundError):
        store.switch_profile("ghost")


def test_profile_delete_refuses_active_without_force(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.switch_profile("work")
    with pytest.raises(mods.errors.ProfileError):
        store.delete_profile("work")
    assert store.profile_exists("work")  # untouched
    # with force the active profile is removed and the marker is cleared
    store.delete_profile("work", force=True)
    assert not store.profile_exists("work")
    assert store.active_profile() is None


def test_profile_delete_nonactive_and_missing(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("a")
    store.create_profile("b")
    store.switch_profile("a")
    store.delete_profile("b")  # non-active: fine, no force needed
    assert store.list_profiles() == ["a"]
    with pytest.raises(mods.errors.ProfileNotFoundError):
        store.delete_profile("ghost")


def test_profile_rename(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.switch_profile("work")
    store.rename_profile("work", "work-old")
    assert store.list_profiles() == ["work-old"]
    assert store.active_profile() == "work-old"  # marker followed the rename
    store.create_profile("other")
    with pytest.raises(mods.errors.ProfileExistsError):
        store.rename_profile("other", "work-old")
    with pytest.raises(mods.errors.ProfileNotFoundError):
        store.rename_profile("ghost", "z")


def test_profile_store_confines_writes_to_home(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.switch_profile("work")
    # everything created lives under the temp home
    created = list(home.rglob("*"))
    assert created, "expected files under the temp home"
    for p in created:
        assert p.is_relative_to(home)
    assert not (Path.home() / ".notebooklm" / "profiles" / "work").exists()


def test_path_info_reports_paths_under_home(mods, home):
    store = mods.profiles.ProfileStore(home)
    info = store.path_info("work")
    for key in (
        "home",
        "profiles_dir",
        "profile_dir",
        "storage_state",
        "context",
        "config",
    ):
        assert key in info
        assert Path(info[key]).is_relative_to(home)


# --------------------------------------------------------------------------- #
# Session context (use / status / clear)
# --------------------------------------------------------------------------- #


def test_session_context_set_get_clear(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    ctx_path = store.context_path("work")

    assert mods.profiles.read_context(ctx_path) == {}
    assert mods.profiles.get_active_notebook(ctx_path) is None

    saved = mods.profiles.set_active_notebook(ctx_path, "nb123", title="My Notebook")
    assert saved["notebook_id"] == "nb123"
    assert saved["notebook_title"] == "My Notebook"

    again = mods.profiles.get_active_notebook(ctx_path)
    assert again["notebook_id"] == "nb123"
    assert Path(ctx_path).is_relative_to(home)

    assert mods.profiles.clear_context(ctx_path) is True
    assert mods.profiles.get_active_notebook(ctx_path) is None
    assert mods.profiles.clear_context(ctx_path) is False  # already clear


# --------------------------------------------------------------------------- #
# Cookie / storage formats
# --------------------------------------------------------------------------- #


def test_storage_state_round_trip(mods, tmp_path):
    state = _good_storage_state(mods)
    path = tmp_path / "storage_state.json"
    mods.cookies.save_storage_state(path, state)
    loaded = mods.cookies.load_storage_state(path)
    assert mods.cookies.storage_state_cookie_names(loaded) == {
        "SID",
        "__Secure-1PSIDTS",
    }
    names = {c["name"] for c in mods.cookies.cookies_from_storage_state(loaded)}
    assert names == {"SID", "__Secure-1PSIDTS"}


def test_load_storage_state_rejects_corrupt_and_nonmapping(mods, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(mods.errors.ValidationError):
        mods.cookies.load_storage_state(bad)

    notmap = tmp_path / "list.json"
    notmap.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(mods.errors.ValidationError):
        mods.cookies.load_storage_state(notmap)


def test_netscape_round_trip(mods):
    cookies = [
        {
            "name": "SID",
            "value": SECRET_SID,
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "http_only": False,
            "expires": 1893456000,
        },
        {
            "name": "__Secure-1PSIDTS",
            "value": SECRET_PSIDTS,
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "http_only": True,
            "expires": 1893456000,
        },
    ]
    text = mods.cookies.format_netscape(cookies)
    assert text.startswith("# Netscape HTTP Cookie File")
    assert "#HttpOnly_.google.com" in text  # httpOnly marker preserved
    parsed = mods.cookies.parse_netscape(text)
    got = {
        (c["name"], c["value"], c["domain"], c["secure"], c["http_only"])
        for c in parsed
    }
    want = {
        (c["name"], c["value"], c["domain"], c["secure"], c["http_only"])
        for c in cookies
    }
    assert got == want


def test_netscape_skips_comments_and_blanks(mods):
    text = (
        "# Netscape HTTP Cookie File\n"
        "\n"
        "# a comment\n"
        ".google.com\tTRUE\t/\tTRUE\t1893456000\tSID\t" + SECRET_SID + "\n"
    )
    parsed = mods.cookies.parse_netscape(text)
    assert [c["name"] for c in parsed] == ["SID"]


def test_cookie_json_import_export(mods):
    # list of cookie dicts
    as_list = mods.cookies.parse_cookie_json(
        json.dumps([{"name": "SID", "value": SECRET_SID, "domain": ".google.com"}])
    )
    assert as_list[0]["name"] == "SID" and as_list[0]["value"] == SECRET_SID

    # flat name->value mapping
    flat = mods.cookies.parse_cookie_json({"SID": SECRET_SID, "HSID": "x"})
    assert {c["name"] for c in flat} == {"SID", "HSID"}

    dumped = mods.cookies.dump_cookie_json(as_list)
    assert json.loads(dumped)[0]["name"] == "SID"


def test_storage_state_to_netscape_preserves_identity(mods):
    state = _good_storage_state(mods)
    text = mods.cookies.format_netscape(mods.cookies.cookies_from_storage_state(state))
    back = mods.cookies.parse_netscape(text)
    assert {c["name"] for c in back} == {"SID", "__Secure-1PSIDTS"}
    by_name = {c["name"]: c for c in back}
    assert by_name["SID"]["value"] == SECRET_SID


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def test_looks_like_secret_detects_tokens(mods):
    # Format-specific tokens assembled at runtime (no contiguous literal in source).
    ya29_token = "ya29." + ("A0b1C2d3e4" * 3)
    refresh_token = "1//" + ("0abcDEF123" * 4)
    assert mods.cookies.looks_like_secret(ya29_token)
    assert mods.cookies.looks_like_secret(refresh_token)
    assert mods.cookies.looks_like_secret(SECRET_OAUTH)  # generic high-entropy
    assert mods.cookies.looks_like_secret("Bearer " + SECRET_OAUTH)
    assert mods.cookies.looks_like_secret(
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    )  # long hex-ish
    assert not mods.cookies.looks_like_secret("hello")
    assert not mods.cookies.looks_like_secret("a-plain-notebook-title")  # no digits
    assert not mods.cookies.looks_like_secret(
        "/home/user/.notebooklm/profiles/x1"
    )  # path
    assert not mods.cookies.looks_like_secret(None)


def test_is_sensitive_cookie_name(mods):
    for name in (
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "__Secure-1PSIDTS",
        "__Secure-3PSID",
        "__Host-GAPS",
    ):
        assert mods.cookies.is_sensitive_cookie_name(name)
    assert not mods.cookies.is_sensitive_cookie_name("theme_pref")


def test_redact_storage_state_hides_values_keeps_metadata(mods):
    state = _good_storage_state(mods)
    red = mods.cookies.redact_storage_state(state)
    blob = json.dumps(red)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
        assert secret not in blob
    assert red["cookie_count"] == 2
    assert set(red["cookie_names"]) == {"SID", "__Secure-1PSIDTS"}
    by_name = {c["name"]: c for c in red["cookies"]}
    assert by_name["SID"]["value_length"] == len(SECRET_SID)
    assert "value" not in by_name["SID"]


def test_scrub_redacts_sensitive_keys_and_tokens(mods):
    payload = {
        "authorization": "Bearer " + SECRET_OAUTH,
        "note": "harmless text",
        "nested": {"cookie": "SID=" + SECRET_SID, "count": 3},
        "items": [{"token": SECRET_PSIDTS}, "plain-string"],
        "free": SECRET_OAUTH,
    }
    scrubbed = mods.cookies.scrub(payload)
    blob = json.dumps(scrubbed)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
        assert secret not in blob
    assert scrubbed["note"] == "harmless text"
    assert scrubbed["nested"]["count"] == 3


# --------------------------------------------------------------------------- #
# auth check / inspect / logout
# --------------------------------------------------------------------------- #


def test_auth_check_valid_storage(mods, tmp_path):
    path = tmp_path / "storage_state.json"
    mods.cookies.save_storage_state(path, _good_storage_state(mods))
    res = mods.auth.check_storage(path)
    assert res["exists"] and res["valid_json"]
    assert res["has_required_cookies"] is True
    assert res["missing_cookies"] == []
    assert res["domains_ok"] is True
    assert res["ok"] is True
    # never emit values
    blob = json.dumps(res)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
        assert secret not in blob


def test_auth_check_missing_file(mods, tmp_path):
    res = mods.auth.check_storage(tmp_path / "nope.json")
    assert res["exists"] is False
    assert res["ok"] is False


def test_auth_check_missing_required_cookie(mods, tmp_path):
    state = _good_storage_state(mods)
    state["cookies"] = [c for c in state["cookies"] if c["name"] != "SID"]
    path = tmp_path / "storage_state.json"
    mods.cookies.save_storage_state(path, state)
    res = mods.auth.check_storage(path)
    assert res["has_required_cookies"] is False
    assert "SID" in res["missing_cookies"]
    assert res["ok"] is False


def test_auth_check_corrupt_json_does_not_raise(mods, tmp_path):
    path = tmp_path / "storage_state.json"
    path.write_text("{ broken", encoding="utf-8")
    res = mods.auth.check_storage(path)
    assert res["exists"] is True
    assert res["valid_json"] is False
    assert res["ok"] is False


def test_auth_inspect_reports_accounts_without_values(mods, tmp_path):
    accounts = [
        {"authuser": 0, "email": "alice@example.com", "is_default": True},
        {"authuser": 1, "email": "bob@work.example", "is_default": False},
    ]
    path = tmp_path / "storage_state.json"
    mods.cookies.save_storage_state(path, _good_storage_state(mods, accounts=accounts))
    res = mods.auth.inspect_storage(path)
    assert res["account_count"] == 2
    blob = json.dumps(res)
    assert "alice@example.com" not in blob and "bob@work.example" not in blob
    first = res["accounts"][0]
    assert first["authuser"] == 0 and first["is_default"] is True
    assert first["email_present"] is True


def test_auth_logout_scoped_to_profile(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.create_profile("play")
    work_storage = store.storage_state_path("work")
    play_storage = store.storage_state_path("play")
    mods.cookies.save_storage_state(work_storage, _good_storage_state(mods))
    mods.cookies.save_storage_state(play_storage, _good_storage_state(mods))

    res = mods.auth.logout(
        storage_path=work_storage, browser_profile_dir=store.browser_profile_dir("work")
    )
    assert res["storage_removed"] is True
    assert not Path(work_storage).exists()
    assert Path(play_storage).exists()  # other profile untouched


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def test_doctor_healthy_profile_is_ok(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    store.switch_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )
    report = mods.auth.doctor(home)
    assert report["ok"] is True
    names = {c["name"] for c in report["checks"]}
    assert {"home_dir", "profiles_dir", "storage_present", "auth_cookies"} <= names
    assert all(c["ok"] for c in report["checks"])


def test_doctor_missing_storage_reports_not_ok(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    report = mods.auth.doctor(home, profile="default")
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["storage_present"]["ok"] is False
    assert report["ok"] is False


def test_doctor_is_deterministic_and_redacted(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    store.switch_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )
    r1 = mods.auth.doctor(home)
    r2 = mods.auth.doctor(home)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    blob = json.dumps(r1)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
        assert secret not in blob


# --------------------------------------------------------------------------- #
# CLI surfaces
# --------------------------------------------------------------------------- #


def _run(mods, capsys, argv):
    """Run cli.console and return (exit_code, stdout, stderr)."""
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_public_read_account_metadata_accepts_storage_path_like_upstream(mods, home):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    storage = store.storage_state_path("work")
    mods.cookies.save_storage_state(storage, _good_storage_state(mods))

    mods.auth.write_account_metadata(
        storage, authuser=2, email="profile-user@example.test"
    )

    assert mods.auth.read_account_metadata(None) == {}
    assert mods.auth.read_account_metadata(storage) == {
        "authuser": 2,
        "email": "profile-user@example.test",
    }
    assert mods.auth.get_authuser_for_storage(storage) == 2
    assert mods.auth.get_account_email_for_storage(storage) == "profile-user@example.test"


def test_public_account_metadata_reads_and_clears_legacy_context_like_upstream(
    mods, home
):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("legacy")
    storage = store.storage_state_path("legacy")
    context = storage.with_name("context.json")
    mods.cookies.save_storage_state(storage, _good_storage_state(mods))
    mods.profiles.write_json_atomic(
        context,
        {
            "notebook_id": "notebook-synthetic",
            "account": {"authuser": 1, "email": "legacy-user@example.test"},
        },
    )

    assert mods.auth.read_account_metadata(storage) == {
        "authuser": 1,
        "email": "legacy-user@example.test",
    }

    mods.auth.clear_account_metadata(storage)

    assert mods.auth.read_account_metadata(storage) == {}
    assert mods.profiles.read_json(context) == {"notebook_id": "notebook-synthetic"}


def test_cli_profile_list_json_includes_auth_status_account_and_default_active(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    s = str(home)

    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "list", "--json"])
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"profiles": [], "active": "default"}

    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "list"])
    assert code == 0
    assert err == ""
    assert out == "No profiles found. Run 'notebooklm login' to create one.\n"

    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.create_profile("play")
    store.switch_profile("work")
    storage = store.storage_state_path("work")
    mods.cookies.save_storage_state(storage, _good_storage_state(mods))
    mods.auth.write_account_metadata(
        storage, authuser=3, email="work-user@example.test"
    )

    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "list", "--json"])
    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["active"] == "work"
    profiles = {item["name"]: item for item in data["profiles"]}
    assert profiles["work"] == {
        "name": "work",
        "active": True,
        "authenticated": True,
        "account": "work-user@example.test",
    }
    assert profiles["play"] == {
        "name": "play",
        "active": False,
        "authenticated": False,
        "account": None,
    }


def test_cli_profile_list_human_renders_upstream_table_content(
    mods, home, capsys, monkeypatch, tmp_path
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    store = mods.profiles.ProfileStore(home)
    store.create_profile("work")
    store.create_profile("play")
    store.switch_profile("work")
    storage = store.storage_state_path("work")
    mods.cookies.save_storage_state(storage, _good_storage_state(mods))
    mods.auth.write_account_metadata(
        storage, authuser=3, email="work-user@example.test"
    )

    code, out, err = _run(
        mods, capsys, ["--storage", str(tmp_path / "ignored"), "profile", "list"]
    )

    assert code == 0
    assert err == ""
    assert "Profiles" in out
    assert "Name" in out
    assert "Account" in out
    assert "Auth Status" in out
    assert "*" in out
    assert "work-user@example.test" in out
    assert "authenticated" in out
    assert "not authenticated" in out
    assert "Active profile: work" in out
    assert "profiles:" not in out


@pytest.mark.parametrize(
    ("override_name", "profile_name"),
    [("override-dir", "workdir"), ("override-storage.json", "workfile")],
)
def test_cli_profile_commands_ignore_root_storage_override_like_upstream(
    mods, home, capsys, monkeypatch, tmp_path, override_name, profile_name
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    override = tmp_path / override_name

    code, out, err = _run(
        mods, capsys, ["--storage", str(override), "profile", "create", profile_name]
    )

    assert code == 0
    assert err == ""
    assert f"Profile '{profile_name}' created." in out
    assert mods.profiles.ProfileStore(home).profile_exists(profile_name)
    assert not (override / "profiles" / profile_name).exists()
    assert not (override.parent / "profiles" / profile_name).exists()


def test_cli_profile_lifecycle(mods, home, capsys, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    s = str(home)
    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "create", "work"])
    assert code == 0
    assert err == ""
    assert out == "Profile 'work' created.\nRun 'notebooklm -p work login' to authenticate.\n"
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "play"])[0] == 0
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "old"])[0] == 0
    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "switch", "work"])
    assert code == 0
    assert err == ""
    assert out == "Switched default profile: default → work\n"

    code, out, _ = _run(mods, capsys, ["--storage", s, "profile", "list", "--json"])
    assert code == 0
    data = json.loads(out)
    names = (
        {p["name"] for p in data["profiles"]}
        if isinstance(data, dict)
        else {p["name"] for p in data}
    )
    assert {"work", "play"} <= names

    # delete active refused, then rename works
    code, _, err = _run(mods, capsys, ["--storage", s, "profile", "delete", "work"])
    assert code != 0
    code, out, err = _run(
        mods, capsys, ["--storage", s, "profile", "delete", "play", "--yes"]
    )
    assert code == 0
    assert err == ""
    assert out == "Profile 'play' deleted.\n"
    code, out, err = _run(
        mods, capsys, ["--storage", s, "profile", "delete", "old", "--confirm"]
    )
    assert code == 0
    assert err == ""
    assert out == "Profile 'old' deleted.\n"

    assert _run(mods, capsys, ["--storage", s, "profile", "create", "rename-me"])[0] == 0
    code, out, err = _run(
        mods, capsys, ["--storage", s, "profile", "rename", "rename-me", "renamed"]
    )
    assert code == 0
    assert err == ""
    assert out == "Profile renamed: rename-me → renamed\n"


def test_cli_profile_delete_prompts_before_nonactive_delete_like_upstream(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    s = str(home)
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "work"])[0] == 0
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "play"])[0] == 0
    assert _run(mods, capsys, ["--storage", s, "profile", "switch", "work"])[0] == 0

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    code, out, err = _run(mods, capsys, ["--storage", s, "profile", "delete", "play"])

    assert code == 0
    assert err == ""
    assert "Cancelled." in out
    assert mods.profiles.ProfileStore(home).profile_exists("play")


def test_cli_profile_delete_rejects_zero_only_force_like_upstream(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    s = str(home)
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "work"])[0] == 0
    assert _run(mods, capsys, ["--storage", s, "profile", "create", "play"])[0] == 0
    assert _run(mods, capsys, ["--storage", s, "profile", "switch", "work"])[0] == 0

    with pytest.raises(SystemExit) as exc:
        mods.cli.console(["--storage", s, "profile", "delete", "play", "--force"])

    assert exc.value.code == 2
    assert mods.profiles.ProfileStore(home).profile_exists("play")


def test_cli_use_status_clear(mods, home, capsys, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    s = str(home)
    _run(mods, capsys, ["--storage", s, "profile", "create", "default"])
    # Later Phase 3A5 validates unforced ``use`` against the offline fake list;
    # Phase 2A's blind local context write remains available under --force.
    assert (
        _run(mods, capsys, ["--storage", s, "use", "nb-xyz", "--force", "--json"])[0]
        == 0
    )

    code, out, _ = _run(mods, capsys, ["--storage", s, "status", "--json"])
    assert code == 0
    assert json.loads(out)["notebook"]["id"] == "nb-xyz"

    code, out, _ = _run(mods, capsys, ["--storage", s, "status", "--paths", "--json"])
    assert code == 0
    assert set(json.loads(out)) == {"paths"}

    assert _run(mods, capsys, ["--storage", s, "clear"])[0] == 0
    code, out, _ = _run(mods, capsys, ["--storage", s, "status", "--json"])
    assert json.loads(out) == {
        "conversation_id": None,
        "has_context": False,
        "notebook": None,
    }


def test_cli_auth_check_and_logout(mods, home, capsys):
    s = str(home)
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )

    code, out, _ = _run(mods, capsys, ["--storage", s, "auth", "check", "--json"])
    assert code == 0
    assert json.loads(out)["status"] == "ok"
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
        assert secret not in out

    assert _run(mods, capsys, ["--storage", s, "auth", "logout"])[0] == 0
    assert not store.storage_state_path("default").exists()


def test_cli_doctor_json_deterministic(mods, home, capsys, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    store.switch_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )

    code, out1, _ = _run(mods, capsys, ["doctor", "--json"])
    assert code == 0
    code, out2, _ = _run(mods, capsys, ["doctor", "--json"])
    assert json.loads(out1) == json.loads(out2)


def test_cli_doctor_json_uses_upstream_shape_and_failure_exit(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))

    code, out, err = _run(mods, capsys, ["doctor", "--json"])

    assert code == 1
    assert err == ""
    data = json.loads(out)
    assert set(data) == {"checks", "profile", "profile_source"}
    assert set(data["checks"]) == {"migration", "profile_dir", "auth", "config"}
    assert data["checks"]["auth"] == {
        "status": "fail",
        "detail": "not authenticated",
    }


def test_cli_doctor_auth_check_requires_sid_only_like_upstream(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    state = _good_storage_state(mods)
    state["cookies"] = [c for c in state["cookies"] if c["name"] == "SID"]
    mods.cookies.save_storage_state(store.storage_state_path("default"), state)

    code, out, err = _run(mods, capsys, ["doctor", "--json"])

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["checks"]["auth"]["status"] == "pass"
    assert "local SID cookie present" in data["checks"]["auth"]["detail"]


def test_cli_doctor_ignores_global_storage_file_like_upstream(
    mods, home, tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    storage = tmp_path / "storage_state.json"
    mods.cookies.save_storage_state(storage, _good_storage_state(mods))

    code, out, err = _run(
        mods, capsys, ["--storage", str(storage), "doctor", "--json"]
    )

    assert code == 1
    assert err == ""
    data = json.loads(out)
    assert data["profile_source"] == "default"
    assert data["checks"]["migration"]["status"] == "pass"
    assert data["checks"]["profile_dir"]["status"] == "fail"
    assert data["checks"]["auth"] == {
        "status": "fail",
        "detail": "not authenticated",
    }


def test_cli_redaction_end_to_end(mods, home, capsys):
    s = str(home)
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    store.switch_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )
    _run(mods, capsys, ["--storage", s, "use", "nb-xyz", "--force"])

    for argv in (
        ["auth", "check", "--json"],
        ["auth", "inspect", "--json"],
        ["status", "--json"],
        ["doctor", "--json"],
    ):
        _, out, err = _run(mods, capsys, ["--storage", s, *argv])
        combined = out + err
        for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_OAUTH):
            assert secret not in combined, f"secret leaked via {argv}"


def test_cli_unsupported_note_alias_still_phase_stub(mods, capsys):
    # Pinned static agent, completion, and skill roots are promoted. Non-pinned
    # convenience note aliases remain explicit phase stubs (exit 78).
    code, out, err = _run(mods, capsys, ["agent", "show", "codex"])
    assert code == 0
    assert out.startswith("# Repository Guidelines")
    assert err == ""

    code, out, err = _run(mods, capsys, ["completion", "bash"])
    assert code == 0
    assert "_NOTEBOOKLM_COMPLETE" in out
    assert err == ""

    code, out, err = _run(mods, capsys, ["skill", "show"])
    assert code == 0
    assert "name: notebooklm" in out
    assert err == ""

    code, _, err = _run(mods, capsys, ["note", "update"])
    assert code == 78
    assert "later parity phase" in err.lower() or "reserved" in err.lower()


def test_cli_auth_refresh_network_foundation_and_login_is_wired(
    mods, home, capsys, monkeypatch
):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )
    # Age the storage file so the mtime guard doesn't skip RotateCookies
    import os as _os
    import time as _time
    _sp = store.storage_state_path("default")
    mt = _time.time() - 120
    _os.utime(_sp, (mt, mt))

    def fake_post(url, **_kwargs):
        return mods.auth._http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": (
                    "__" + "Secure-1PSIDTS="
                    "rotatedSyntheticPhase2GValue; Domain=.google.com; Path=/; Secure; HttpOnly"
                )
            },
            body=b"[]",
        )

    def fake_get(url, **_kwargs):
        return mods.auth._http_std.Response(
            status=200,
            url=url,
            headers={},
            body=b'<script>{"SNlM0e":"csrfSyntheticPhase2GValue","FdrFJe":"sessionSyntheticPhase2GValue"}</script>',
        )

    monkeypatch.setattr(mods.auth, "_default_post", fake_post)
    monkeypatch.setattr(mods.auth, "_default_get", fake_get)
    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )
    assert code == 0, err
    assert json.loads(out)["token_fetch_ok"] is True
    assert SECRET_SID not in out
    assert SECRET_PSIDTS not in out
    assert "csrfSyntheticPhase2GValue" not in out
    assert "sessionSyntheticPhase2GValue" not in out

    monkeypatch.setattr(
        mods.cli._il,
        "launch_browser_session",
        lambda browser, **kw: {
            "source_kind": "interactive_browser",
            "browser": browser,
            "debugging_host": "127.0.0.1",
            "debugging_port": kw["debugging_port"],
            "profile_prepared": True,
            "process_id": None,
            "url_opened": True,
        },
    )
    monkeypatch.setattr(
        mods.cli._il,
        "read_devtools_page_websocket_url",
        lambda port, **kw: "ws://127.0.0.1:9222/devtools/browser/session-id",
    )
    monkeypatch.setattr(
        mods.cli._il,
        "read_cdp_all_cookies",
        lambda ws: [
            {"name": "SID", "value": SECRET_SID, "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "domain": ".google.com",
                "path": "/",
            },
        ],
    )
    code, out, err = _run(mods, capsys, ["--storage", str(home), "login", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "interactive_browser"
    assert data["browser"] == "chromium"
    assert data["auth_source_written"] is False
    assert SECRET_SID not in out
    assert SECRET_PSIDTS not in out


def test_cli_auth_network_commands_use_explicit_storage_not_default_home(
    mods, home, capsys, monkeypatch
):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _good_storage_state(mods)
    )
    # Age the storage file so the mtime guard doesn't skip RotateCookies
    import os as _os
    import time as _time
    _sp = store.storage_state_path("default")
    mt = _time.time() - 120
    _os.utime(_sp, (mt, mt))

    def fake_post(url, **_kwargs):
        return mods.auth._http_std.Response(status=200, url=url, headers={}, body=b"[]")

    def fake_get(url, **_kwargs):
        return mods.auth._http_std.Response(
            status=200,
            url=url,
            headers={},
            body=b'<script>{"SNlM0e":"csrfSyntheticPhase2GValue","FdrFJe":"sessionSyntheticPhase2GValue"}</script>',
        )

    monkeypatch.setattr(mods.auth, "_default_post", fake_post)
    monkeypatch.setattr(mods.auth, "_default_get", fake_get)
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )
    for argv in (["auth", "refresh", "--json"], ["auth", "check", "--test", "--json"]):
        code, out, err = _run(mods, capsys, ["--storage", str(home), *argv])
        assert code == 0, err
        payload = json.loads(out)
        assert (payload.get("ok") is True) or (payload.get("status") == "ok")
        assert "csrfSyntheticPhase2GValue" not in out
        assert "sessionSyntheticPhase2GValue" not in out


def test_cli_quiet_verbose_conflict_matches_upstream_before_dispatch(
    mods, home, capsys
):
    s = str(home)

    code = mods.cli.console(["--storage", s, "--quiet", "-v", "profile", "create", "x"])
    out = capsys.readouterr()

    assert code == 2
    assert out.out == ""
    assert "Error: --quiet and -v are mutually exclusive." in out.err
    assert not mods.profiles.ProfileStore(home).profile_exists("x")


def test_cli_help_makes_no_parity_claims(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        mods.cli.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--version", "--storage", "--profile", "--verbose", "--quiet"):
        assert flag in help_text
    assert "100%" not in help_text
    assert "full parity" not in help_text.lower()


# --------------------------------------------------------------------------- #
# Boundary / import-origin audit
# --------------------------------------------------------------------------- #


def test_phase2a_modules_have_no_denylisted_imports(repo_root):
    violations = import_origin_audit.audit(roots=("notebooklm",))
    assert violations == []


def test_phase2a_modules_exist_in_package(repo_root):
    pkg = repo_root / "notebooklm"
    for name in ("profiles.py", "cookies.py", "auth.py"):
        assert (pkg / name).is_file(), f"missing Phase 2A module: notebooklm/{name}"
