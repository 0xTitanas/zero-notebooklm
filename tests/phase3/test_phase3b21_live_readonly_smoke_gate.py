"""Phase 3B21 opt-in live read-only smoke gate.

The committed suite stays hermetic. The smoke runner must default to skip and
must require both an environment gate and explicit storage-state path before it
can read auth material or perform the optional network token-fetch probe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _state(psidts: str = "sidtsSyntheticRotatingValue000111222333") -> dict:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": "sidSyntheticValue000111222333444555",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": psidts,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
        ],
        "origins": [],
    }


def _write_storage(tmp_path: Path) -> Path:
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(_state()), encoding="utf-8")
    return path


def test_live_smoke_defaults_to_skip_without_touching_home(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from scripts import live_readonly_smoke

    def boom_home():
        raise AssertionError(
            "Path.home must not be consulted by default-skip live smoke"
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))

    code, payload = live_readonly_smoke.run([], env={})

    assert code == 0
    assert payload["status"] == "skipped"
    assert payload["live_enabled"] is False
    assert payload["network_auth"] is False


def test_live_smoke_requires_env_flag_and_explicit_storage_state(repo_root):
    from scripts import live_readonly_smoke

    code, payload = live_readonly_smoke.run(
        ["--allow-live"], env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"}
    )

    assert code == 64
    assert payload["status"] == "error"
    assert "--storage-state" in payload["reason"]


def test_live_smoke_rejects_missing_allow_flag_even_with_env(repo_root, tmp_path):
    from scripts import live_readonly_smoke

    storage = _write_storage(tmp_path)
    code, payload = live_readonly_smoke.run(
        ["--storage-state", str(storage), "--network-auth"],
        env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"},
    )

    assert code == 0
    assert payload["status"] == "skipped"
    assert payload["live_enabled"] is False
    assert payload["storage_state"] is None


def test_live_smoke_offline_storage_check_is_readonly_and_redacted(repo_root, tmp_path):
    from scripts import live_readonly_smoke

    storage = _write_storage(tmp_path)
    before = storage.read_text(encoding="utf-8")

    code, payload = live_readonly_smoke.run(
        ["--allow-live", "--storage-state", str(storage)],
        env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"},
    )

    assert code == 0
    assert payload["status"] == "passed"
    assert payload["checks"]["offline_auth"]["ok"] is True
    assert payload["checks"]["network_auth"] == {
        "skipped": True,
        "reason": "--network-auth not set",
    }
    assert payload["storage_state"] == "set"
    assert storage.read_text(encoding="utf-8") == before
    dumped = json.dumps(payload)
    assert "sidSyntheticValue" not in dumped
    assert "sidtsSynthetic" not in dumped


def test_main_uses_sys_argv_when_executed_as_script(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    from scripts import live_readonly_smoke

    monkeypatch.setenv("NOTEBOOKLM_BARE_LIVE_SMOKE", "1")
    monkeypatch.setattr(sys, "argv", ["live_readonly_smoke.py", "--allow-live"])

    code = live_readonly_smoke.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 64
    assert payload["status"] == "error"
    assert "--storage-state" in payload["reason"]


def test_live_smoke_network_auth_uses_injected_fetcher_without_persisting(
    repo_root, tmp_path
):
    from scripts import live_readonly_smoke

    storage = _write_storage(tmp_path)
    before = storage.read_text(encoding="utf-8")
    calls = []

    def fake_fetch(path, *, persist):
        calls.append((Path(path), persist))
        return {
            "ok": True,
            "network_test": True,
            "token_fetch_ok": True,
            "csrf_token_present": True,
            "session_id_present": True,
            "rotated_cookie_names": ["__Secure-1PSIDTS"],
            "cookie_count": 2,
            "cookie_names": ["SID", "__Secure-1PSIDTS"],
        }

    code, payload = live_readonly_smoke.run(
        ["--allow-live", "--storage-state", str(storage), "--network-auth"],
        env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"},
        fetcher=fake_fetch,
    )

    assert code == 0
    assert payload["status"] == "passed"
    assert calls == [(storage, False)]
    assert payload["checks"]["network_auth"]["token_fetch_ok"] is True
    assert storage.read_text(encoding="utf-8") == before


def test_live_smoke_rotatecookies_429_writes_and_observes_cooldown(
    repo_root, tmp_path
):
    from notebooklm.errors import RateLimitError
    from scripts import live_readonly_smoke

    storage = _write_storage(tmp_path)
    calls = []

    def rate_limited(path, *, persist):
        calls.append((Path(path), persist))
        raise RateLimitError("RotateCookies returned HTTP 429")

    code, payload = live_readonly_smoke.run(
        ["--allow-live", "--storage-state", str(storage), "--network-auth"],
        env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"},
        fetcher=rate_limited,
    )

    assert code == 75
    assert payload["status"] == "failed"
    assert payload["checks"]["network_auth"]["cooldown_active"] is True
    assert calls == [(storage, False)]
    marker = live_readonly_smoke._rotate_429_cooldown_path(storage)
    assert marker.exists()
    marker_text = marker.read_text(encoding="utf-8")
    assert "rotatecookies_http_429" in marker_text
    assert "sidSyntheticValue" not in marker_text
    assert str(storage) not in marker_text

    def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("active cooldown must stop before RotateCookies")

    code2, payload2 = live_readonly_smoke.run(
        ["--allow-live", "--storage-state", str(storage), "--network-auth"],
        env={"NOTEBOOKLM_BARE_LIVE_SMOKE": "1"},
        fetcher=should_not_fetch,
    )

    assert code2 == 77
    assert payload2["status"] == "failed"
    assert payload2["checks"]["network_auth"] == {
        "ok": False,
        "skipped": True,
        "reason": "rotatecookies_429_cooldown_active",
        "cooldown_active": True,
    }
