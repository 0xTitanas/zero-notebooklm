"""Phase 2D tests: deterministic, offline auth-matrix readiness reporting.

These tests target only the Phase 2D slice, which adds a *pure* classifier that
consumes the pinned ``compat/auth_matrix.json`` shape (from an explicit path or a
data object) and reports, honestly and without overclaiming:

  * which rows have a current offline foundation (Phase 2A/2B/2C) — the
    browser-cookie ``import``/``inspect``/``refresh`` paths are implemented
    offline against explicit fixtures; ``profile-select``/``account-select`` are
    only *partial* (explicit fixture profile / accounts-file selection, no live
    profile or store-derived account discovery);
  * which rows remain blocked behind live authorization, real browser-store
    discovery, the OS credential backend (chromium cookie-value decryptor), or
    an upstream live differential — ``interactive_login`` ``login`` has a
    partial Phase 2F browser/CDP primitive foundation and ``refresh`` has a
    partial Phase 2G stored-cookie network token-refresh foundation;
  * a Core-facing summary (totals, parity_pass/open counts, foundation coverage,
    blocked-live count, ``release_blocked``, reasons, ``next_required_authorization``).

Hard invariants proven here: the module copies row parity verbatim from the
source and offline-foundation coverage never upgrades an ``open`` row, it
**never mutates** ``compat/auth_matrix.json``, and it performs no network /
browser / keychain / DPAPI / Secret-Service access. The readiness report carries
no cookies, tokens, or emails. The CLI surface is a *new flag on the existing*
``doctor`` command (``doctor --auth-matrix``); default ``doctor`` output is
unchanged. Every filesystem read/write is confined to ``tmp_path``.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.abc
import json
import types
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402  (placed on sys.path by tests/conftest.py)
import import_origin_audit  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {
    "aiohttp",
    "urllib3",
    "keyring",
    "cryptography",
}

# A plain (non-credential) email; the repo secret scanner does not flag plain
# emails. Used to prove the readiness report never echoes a source row's stray
# fields (it reads only the known matrix columns).
LEAK_EMAIL = "leak@example.com"


# --------------------------------------------------------------------------- #
# Module import fixture (guards against any denylisted third-party import)
# --------------------------------------------------------------------------- #


class _DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


@pytest.fixture
def mods(repo_root, monkeypatch):
    """Import the Phase 2D module (and its CLI/auth siblings) from the checkout,
    guarding against any denylisted third-party runtime import at import time."""
    monkeypatch.syspath_prepend(str(repo_root))
    import sys

    finder = _DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        ns = types.SimpleNamespace(
            auth_readiness=importlib.import_module("notebooklm.auth_readiness"),
            os_credentials=importlib.import_module("notebooklm.os_credentials"),
            profiles=importlib.import_module("notebooklm.profiles"),
            auth=importlib.import_module("notebooklm.auth"),
            cli=importlib.import_module("notebooklm.cli"),
            errors=importlib.import_module("notebooklm.errors"),
        )
    finally:
        sys.meta_path.remove(finder)
    return ns


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


def _poison_home(monkeypatch):
    """Make any real ``Path.home()`` access an immediate, loud failure."""
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


# --------------------------------------------------------------------------- #
# Synthetic matrix builders (never the real machine; pure data)
# --------------------------------------------------------------------------- #


def _login_row(browser="chrome", os_name="macOS", flow="login", parity="open", **extra):
    return {
        "matrix": "interactive_login",
        "browser": browser,
        "os": os_name,
        "flow": flow,
        "parity_state": parity,
        "differential_basis": "upstream interactive-login behavior vs bare",
        **extra,
    }


def _cookie_row(
    browser="chrome", os_name="macOS", path="import", parity="open", **extra
):
    return {
        "matrix": "browser_cookie_import",
        "browser": browser,
        "os": os_name,
        "path": path,
        "parity_state": parity,
        "differential_basis": "upstream rookiepy-backed cookie import vs bare",
        **extra,
    }


def _matrix(*, login=None, cookie=None, **extra):
    return {
        "schema_version": "phase0/1",
        "target": "notebooklm-py==0.7.2",
        "generated_at": "2026-06-23T00:00:00+00:00",
        "interactive_login_matrix": list(login or []),
        "browser_cookie_import_matrix": list(cookie or []),
        **extra,
    }


def _rows_by_selector(report, matrix):
    return {r["selector"]: r for r in report["rows"] if r["matrix"] == matrix}


def _parity_counts(rows):
    return {
        state: sum(1 for row in rows if row.get("parity_state") == state)
        for state in ("open", "pass", "blocked")
    }


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# --------------------------------------------------------------------------- #
# 1) Loading: explicit path + dict; clear errors; never writes
# --------------------------------------------------------------------------- #


def test_load_auth_matrix_from_path(mods, tmp_path):
    ar = mods.auth_readiness
    p = tmp_path / "auth_matrix.json"
    p.write_text(json.dumps(_matrix(cookie=[_cookie_row()])), encoding="utf-8")
    data = ar.load_auth_matrix(p)
    assert isinstance(data, dict)
    assert data["browser_cookie_import_matrix"][0]["path"] == "import"


def test_load_auth_matrix_missing_and_bad(mods, tmp_path):
    ar = mods.auth_readiness
    with pytest.raises(mods.errors.ValidationError):
        ar.load_auth_matrix(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(mods.errors.ValidationError):
        ar.load_auth_matrix(bad)
    notobj = tmp_path / "list.json"
    notobj.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(mods.errors.ValidationError):
        ar.load_auth_matrix(notobj)


def test_analyze_requires_a_matrix_shape(mods):
    ar = mods.auth_readiness
    # A dict that carries neither matrix list is not an auth matrix.
    with pytest.raises(mods.errors.ValidationError):
        ar.analyze_auth_matrix({"target": "x"})


def test_analyze_is_deterministic(mods):
    ar = mods.auth_readiness
    m = _matrix(
        login=[_login_row(flow="refresh")], cookie=[_cookie_row(path="inspect")]
    )
    r1 = ar.analyze_auth_matrix(m)
    r2 = ar.analyze_auth_matrix(m)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


# --------------------------------------------------------------------------- #
# 2) Classification: cookie-import foundation states
# --------------------------------------------------------------------------- #


def test_cookie_import_paths_foundation_states(mods):
    ar = mods.auth_readiness
    paths = ["import", "inspect", "refresh", "profile-select", "account-select"]
    m = _matrix(cookie=[_cookie_row(path=p) for p in paths])
    rows = _rows_by_selector(ar.analyze_auth_matrix(m), "browser_cookie_import")

    for implemented in ("import", "inspect", "refresh"):
        assert rows[implemented]["foundation_state"] == ar.FOUNDATION_IMPLEMENTED
        assert rows[implemented]["foundation_covered"] is True
        assert rows[implemented]["foundation_ref"]  # names the offline code
        # An offline foundation does NOT mean parity. The row is still live-blocked
        # for upstream closure because real browser discovery / differential proof
        # have not happened.
        assert rows[implemented]["parity_state"] == "open"
        assert rows[implemented]["blocked_live"] is True

    for partial in ("profile-select", "account-select"):
        assert rows[partial]["foundation_state"] == ar.FOUNDATION_PARTIAL
        assert rows[partial]["foundation_covered"] is False
        assert rows[partial]["blocked_live"] is True
        assert rows[partial]["parity_state"] == "open"


def test_cookie_rows_blocked_by_browser_discovery_and_differential(mods):
    ar = mods.auth_readiness
    m = _matrix(cookie=[_cookie_row(browser="chrome", path="import")])
    row = ar.analyze_auth_matrix(m)["rows"][0]
    assert ar.BLOCK_BROWSER_DISCOVERY in row["blocked_by"]
    assert ar.BLOCK_DIFFERENTIAL in row["blocked_by"]


def test_chromium_rows_require_os_credential_plaintext_do_not(mods):
    ar = mods.auth_readiness
    m = _matrix(
        cookie=[
            _cookie_row(browser="chrome", path="import"),
            _cookie_row(browser="brave", path="refresh"),
            _cookie_row(browser="firefox", path="import"),
            _cookie_row(browser="safari", path="inspect"),
        ]
    )
    rows = {(r["browser"], r["selector"]): r for r in ar.analyze_auth_matrix(m)["rows"]}
    for enc in (("chrome", "import"), ("brave", "refresh")):
        assert rows[enc]["requires_os_credential_decryptor"] is True
        assert ar.BLOCK_OS_CREDENTIAL in rows[enc]["blocked_by"]
    for plain in (("firefox", "import"), ("safari", "inspect")):
        assert rows[plain]["requires_os_credential_decryptor"] is False
        assert ar.BLOCK_OS_CREDENTIAL not in rows[plain]["blocked_by"]


# 3) Classification: interactive-login remains live-blocked
# --------------------------------------------------------------------------- #


def test_interactive_login_rows_remain_live_blocked_with_login_and_refresh_partial_foundation(
    mods,
):
    ar = mods.auth_readiness
    flows = ["login", "refresh", "status", "logout", "doctor"]
    m = _matrix(login=[_login_row(flow=f) for f in flows])
    rows = _rows_by_selector(ar.analyze_auth_matrix(m), "interactive_login")
    for flow in flows:
        r = rows[flow]
        if flow in {"login", "refresh"}:
            assert r["foundation_state"] == ar.FOUNDATION_PARTIAL
            assert r["foundation_ref"]
        else:
            assert r["foundation_state"] == ar.FOUNDATION_NONE
            assert r["foundation_ref"] is None
        assert r["foundation_covered"] is False
        assert r["blocked_live"] is True
        assert ar.BLOCK_LIVE_LOGIN in r["blocked_by"]
        assert r["parity_state"] == "open"
    # The interactive refresh flow additionally needs a live network token refresh.
    assert ar.BLOCK_NETWORK_REFRESH in rows["refresh"]["blocked_by"]
    assert ar.BLOCK_NETWORK_REFRESH not in rows["login"]["blocked_by"]


def test_blocked_by_codes_and_foundation_states_are_known(mods):
    ar = mods.auth_readiness
    m = _matrix(
        login=[_login_row(flow=f) for f in ("login", "refresh")],
        cookie=[
            _cookie_row(browser=b, path=p)
            for b in ("chrome", "firefox")
            for p in ("import", "profile-select")
        ],
    )
    report = ar.analyze_auth_matrix(m)
    for r in report["rows"]:
        assert r["foundation_state"] in ar.FOUNDATION_STATES
        assert set(r["blocked_by"]) <= set(ar.KNOWN_BLOCKERS)
    assert set(report["summary"]["blockers"]) <= set(ar.KNOWN_BLOCKERS)


# --------------------------------------------------------------------------- #
# 4) Real pinned matrix: exact counts, source parity, release blocked
# --------------------------------------------------------------------------- #


def test_real_matrix_counts(mods, auth_matrix):
    ar = mods.auth_readiness
    report = ar.analyze_auth_matrix(auth_matrix)
    s = report["summary"]
    assert s["total_rows"] == 146
    assert s["interactive_login_rows"] == 45
    assert s["browser_cookie_import_rows"] == 101
    # 101 in-profile cookie rows give 60 implemented and 41 partial paths;
    # Phase 2F/2G add 18 partial interactive
    # rows (login + refresh); the other 27 interactive rows have no foundation.
    assert s["foundation_covered_count"] == 60
    assert s["foundation_partial_count"] == 59
    assert s["foundation_none_count"] == 27
    # Every selected row has row-specific pass evidence, so none remains live-blocked.
    assert s["blocked_live_count"] == 0
    # The in-profile Chromium-family rows require the OS decryptor.
    enc = sum(1 for r in report["rows"] if r["requires_os_credential_decryptor"])
    assert enc == 81


def test_real_matrix_copies_source_parity_without_upgrade(mods, auth_matrix):
    ar = mods.auth_readiness
    report = ar.analyze_auth_matrix(auth_matrix)
    s = report["summary"]
    source_rows = (
        auth_matrix["browser_cookie_import_matrix"]
        + auth_matrix["interactive_login_matrix"]
    )
    source_counts = _parity_counts(source_rows)
    assert s["parity_pass_count"] == source_counts["pass"]
    assert s["parity_open_count"] == source_counts["open"]
    assert s["parity_blocked_count"] == source_counts["blocked"]
    assert s["parity_pass_count"] == 146
    assert s["parity_open_count"] == 0
    assert s["parity_open_count"] + s["parity_pass_count"] == 146
    assert report["summary"]["blockers"] == []


def test_release_blocked_and_required_summary_fields(mods, auth_matrix):
    ar = mods.auth_readiness
    s = ar.analyze_auth_matrix(auth_matrix)["summary"]
    for key in (
        "total_rows",
        "parity_pass_count",
        "parity_open_count",
        "foundation_covered_count",
        "blocked_live_count",
        "profile_exclusion_path_count",
        "deferred_future_release_path_count",
        "release_blocked",
        "reasons",
        "next_required_authorization",
    ):
        assert key in s, f"missing required summary field: {key}"
    assert s["release_blocked"] is True
    assert s["profile_exclusion_entry_count"] == 29
    assert s["profile_exclusion_path_count"] == 49
    assert s["deferred_future_release_path_count"] == 10
    assert isinstance(s["reasons"], list) and s["reasons"]
    assert (
        isinstance(s["next_required_authorization"], str)
        and s["next_required_authorization"]
    )
    assert "No additional live/browser/credential work" in s[
        "next_required_authorization"
    ]
    assert "future release" in s["next_required_authorization"]


def test_per_matrix_aggregates(mods, auth_matrix):
    ar = mods.auth_readiness
    report = ar.analyze_auth_matrix(auth_matrix)
    mx = report["matrices"]
    assert mx["interactive_login"]["rows"] == 45
    assert mx["interactive_login"]["foundation_partial"] == 18
    assert mx["interactive_login"]["foundation_none"] == 27
    assert mx["interactive_login"]["blocked_live"] == 0
    assert mx["browser_cookie_import"]["rows"] == 101
    assert mx["browser_cookie_import"]["blocked_live"] == 0
    assert mx["browser_cookie_import"]["foundation_covered"] == 60
    assert mx["browser_cookie_import"]["foundation_partial"] == 41
    login_counts = _parity_counts(auth_matrix["interactive_login_matrix"])
    cookie_counts = _parity_counts(auth_matrix["browser_cookie_import_matrix"])
    assert mx["interactive_login"]["parity_pass"] == login_counts["pass"]
    assert mx["interactive_login"]["parity_open"] == login_counts["open"]
    assert mx["interactive_login"]["parity_blocked"] == login_counts["blocked"]
    assert mx["browser_cookie_import"]["parity_pass"] == cookie_counts["pass"]
    assert mx["browser_cookie_import"]["parity_open"] == cookie_counts["open"]
    assert mx["browser_cookie_import"]["parity_blocked"] == cookie_counts["blocked"]


# --------------------------------------------------------------------------- #
# 5) Faithful parity copy: a source 'pass' is echoed, an 'open' is never upgraded
# --------------------------------------------------------------------------- #


def test_parity_state_copied_verbatim_never_upgraded(mods):
    ar = mods.auth_readiness
    # An implemented-foundation row whose source parity is 'open' stays 'open'.
    m_open = _matrix(cookie=[_cookie_row(path="import", parity="open")])
    row = ar.analyze_auth_matrix(m_open)["rows"][0]
    assert row["foundation_state"] == ar.FOUNDATION_IMPLEMENTED
    assert row["parity_state"] == "open"  # foundation coverage != parity pass

    # If a source row already claimed 'pass', the report echoes it faithfully (it
    # reports source state; it does not fabricate one) — but our module never
    # SYNTHESIZES pass for an 'open' row, which the assertion above proves.
    m_pass = _matrix(cookie=[_cookie_row(path="import", parity="pass")])
    rep = ar.analyze_auth_matrix(m_pass)
    assert rep["rows"][0]["parity_state"] == "pass"
    assert rep["rows"][0]["blocked_live"] is False
    assert rep["rows"][0]["blocked_by"] == []
    assert rep["summary"]["parity_pass_count"] == 1
    assert rep["summary"]["release_blocked"] is False


# --------------------------------------------------------------------------- #
# 6) The module never mutates compat/auth_matrix.json
# --------------------------------------------------------------------------- #


def test_compat_matrix_unchanged_after_report(mods, repo_root):
    ar = mods.auth_readiness
    p = repo_root / "compat" / "auth_matrix.json"
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    report = ar.build_report(p)
    after = hashlib.sha256(p.read_bytes()).hexdigest()
    assert before == after, "auth_readiness must never write compat/auth_matrix.json"
    # The report records the explicit source path it was built from.
    assert report["auth_matrix_path"] == str(p)
    assert report["summary"]["total_rows"] == 146


def test_default_auth_matrix_path_points_at_repo_compat(mods, repo_root):
    ar = mods.auth_readiness
    assert (
        Path(ar.default_auth_matrix_path()) == repo_root / "compat" / "auth_matrix.json"
    )


# --------------------------------------------------------------------------- #
# 7) Redaction: report carries no emails / no secret-looking material
# --------------------------------------------------------------------------- #


def test_report_ignores_stray_source_fields_and_leaks_nothing(mods, tmp_path):
    ar = mods.auth_readiness
    # Inject an email-bearing stray field on a source row; it must not propagate.
    m = _matrix(
        cookie=[_cookie_row(path="import", email=LEAK_EMAIL, account=LEAK_EMAIL)]
    )
    blob = json.dumps(ar.analyze_auth_matrix(m))
    assert LEAK_EMAIL not in blob
    assert "@" not in blob  # no email-shaped material anywhere in the report


def test_human_view_redacts_path_and_omits_rows(mods, tmp_path):
    ar = mods.auth_readiness
    p = tmp_path / "secret-dir" / "auth_matrix.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(_matrix(cookie=[_cookie_row()])), encoding="utf-8")
    report = ar.build_report(p)
    human = ar.human_view(report)
    blob = json.dumps(human)
    assert str(p) not in blob and str(p.parent) not in blob  # full path redacted
    assert human["auth_matrix_path"] == "auth_matrix.json"  # basename only
    assert "rows" not in human  # per-row detail omitted from the human view
    assert "summary" in human and "matrices" in human


# --------------------------------------------------------------------------- #
# 8) CLI: doctor --auth-matrix (new flag on the existing command)
# --------------------------------------------------------------------------- #


def test_cli_doctor_auth_matrix_json(mods, home, capsys, repo_root):
    s = str(home)
    src = repo_root / "compat" / "auth_matrix.json"
    source = json.loads(src.read_text(encoding="utf-8"))
    counts = _parity_counts(
        source["browser_cookie_import_matrix"] + source["interactive_login_matrix"]
    )
    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "doctor",
            "--auth-matrix",
            "--auth-matrix-path",
            str(src),
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["summary"]["release_blocked"] is True
    assert data["summary"]["foundation_covered_count"] == 60
    assert data["summary"]["profile_exclusion_path_count"] == 49
    assert data["summary"]["deferred_future_release_path_count"] == 10
    assert "No additional live/browser/credential work" in data["summary"][
        "next_required_authorization"
    ]
    assert data["summary"]["parity_pass_count"] == counts["pass"]
    assert data["summary"]["parity_open_count"] == counts["open"]
    assert data["auth_matrix_path"] == str(src)  # JSON keeps the explicit path
    assert "@" not in out


def test_cli_doctor_auth_matrix_default_path(mods, home, capsys, monkeypatch):
    # With no --auth-matrix-path it falls back to the committed compat artifact.
    # Poisoning Path.home proves the auth-matrix diagnostic bypasses normal profile
    # storage resolution entirely; it should not touch ~/.notebooklm just to read
    # the committed matrix.
    _poison_home(monkeypatch)
    code, out, _ = _run(mods, capsys, ["doctor", "--auth-matrix", "--json"])
    assert code == 0
    assert json.loads(out)["summary"]["total_rows"] == 146


def test_cli_doctor_auth_matrix_human_redacts_path(mods, home, capsys, tmp_path):
    s = str(home)
    src = tmp_path / "deep" / "auth_matrix.json"
    src.parent.mkdir(parents=True)
    src.write_text(json.dumps(_matrix(cookie=[_cookie_row()])), encoding="utf-8")
    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "doctor",
            "--auth-matrix",
            "--auth-matrix-path",
            str(src),
        ],
    )
    assert code == 0
    assert str(src) not in out and str(src.parent) not in out
    assert "release_blocked" in out
    assert "@" not in out


def test_cli_doctor_auth_matrix_missing_path_errors(mods, home, capsys, tmp_path):
    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "doctor",
            "--auth-matrix",
            "--auth-matrix-path",
            str(tmp_path / "nope.json"),
        ],
    )
    assert code == 64  # ValidationError / EX_USAGE — clear, not a crash
    assert "auth matrix" in err.lower() or "not found" in err.lower()


def test_cli_doctor_default_output_unchanged(mods, home, capsys, monkeypatch):
    # doctor WITHOUT --auth-matrix stays on the normal upstream-shaped doctor
    # path: no auth-matrix-only keys, deterministic, and failed checks exit 1.
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(home))
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    store.switch_profile("default")

    code, out1, _ = _run(mods, capsys, ["doctor", "--json"])
    assert code == 1
    code, out2, _ = _run(mods, capsys, ["doctor", "--json"])
    assert code == 1
    assert json.loads(out1) == json.loads(out2)
    data = json.loads(out1)
    assert "checks" in data  # the normal doctor report
    assert data["checks"]["auth"]["status"] == "fail"
    assert "summary" not in data and "foundation_covered_count" not in json.dumps(data)


# --------------------------------------------------------------------------- #
# 9) Boundary: no denylisted imports, no live-discovery primitives
# --------------------------------------------------------------------------- #


def test_phase2d_modules_have_no_denylisted_imports(repo_root):
    violations = import_origin_audit.audit(roots=("notebooklm",))
    assert violations == []


def test_auth_readiness_module_present(repo_root):
    assert (repo_root / "notebooklm" / "auth_readiness.py").is_file()


def test_auth_readiness_has_no_live_discovery_primitives(repo_root):
    # The readiness module is a pure classifier. It may read an EXPLICIT matrix
    # path, but must never reach the real machine: no home/env discovery, no
    # network, no browser store, no OS keychain/DPAPI/secret store, no subprocess.
    src = (repo_root / "notebooklm" / "auth_readiness.py").read_text(encoding="utf-8")
    forbidden = (
        "Path.home",
        "expanduser",
        "os.environ",
        "os.getenv",
        "getenv(",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "sqlite3",
        "keyring",
        "ctypes",
        "Keychain",
        "DPAPI",
        "SecretService",
        "kwallet",
    )
    for token in forbidden:
        assert token not in src, f"auth_readiness uses forbidden primitive {token!r}"
    tree = ast.parse(src, filename="auth_readiness.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"home", "expanduser", "environ", "getenv"}, (
                f"auth_readiness accesses .{node.attr}"
            )
