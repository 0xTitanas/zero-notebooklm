"""Public path helper parity."""

from __future__ import annotations

import json

import pytest


def test_paths_public_surface_matches_upstream():
    import notebooklm.paths as paths

    assert paths.__all__ == [
        "get_active_profile",
        "get_browser_profile_dir",
        "get_config_path",
        "get_context_path",
        "get_home_dir",
        "get_path_info",
        "get_profile_dir",
        "get_storage_path",
        "list_profiles",
        "read_default_profile",
        "resolve_profile",
        "set_active_profile",
    ]


def test_profile_resolution_precedence_matches_upstream(monkeypatch, tmp_path):
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "env")
    paths.set_active_profile(None)
    paths._reset_config_cache()

    assert paths.resolve_profile() == "env"

    paths.get_config_path().write_text('{"default_profile": "config"}', encoding="utf-8")
    paths._reset_config_cache()
    monkeypatch.delenv("NOTEBOOKLM_PROFILE")
    assert paths.resolve_profile() == "config"

    paths.set_active_profile("active")
    monkeypatch.setenv("NOTEBOOKLM_PROFILE", "env")
    assert paths.resolve_profile() == "active"
    assert paths.resolve_profile("explicit") == "explicit"


def test_profile_paths_legacy_fallback_and_storage_context_override(monkeypatch, tmp_path):
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    paths.set_active_profile(None)
    paths._reset_config_cache()
    legacy_storage = tmp_path / "storage_state.json"
    legacy_context = tmp_path / "context.json"
    legacy_browser = tmp_path / "browser_profile"
    legacy_storage.write_text("{}", encoding="utf-8")
    legacy_context.write_text("{}", encoding="utf-8")
    legacy_browser.mkdir()

    assert paths.get_storage_path() == legacy_storage
    assert paths.get_context_path() == legacy_context
    assert paths.get_browser_profile_dir() == legacy_browser

    explicit_storage = tmp_path / "custom-state.json"
    assert paths.get_context_path(storage_path=explicit_storage) == (
        tmp_path / "custom-state.json.context.json"
    )

    work_profile = paths.get_profile_dir("work", create=True)
    assert paths.get_storage_path("work") == work_profile / "storage_state.json"
    assert paths.list_profiles() == ["work"]

    with pytest.raises(ValueError, match="Invalid profile name"):
        paths.get_profile_dir("../escape")


def test_path_info_matches_upstream_keys_and_sources(monkeypatch, tmp_path):
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    paths.set_active_profile(None)
    paths._reset_config_cache()
    paths.get_config_path().write_text(json.dumps({"default_profile": "research"}), encoding="utf-8")
    paths._reset_config_cache()

    info = paths.get_path_info()

    assert info["home_dir"] == str(tmp_path.resolve())
    assert info["home_source"] == "NOTEBOOKLM_HOME"
    assert info["profile"] == "research"
    assert info["profile_source"] == "config.json"
    assert info["storage_path"].endswith("profiles/research/storage_state.json")
    assert info["context_path"].endswith("profiles/research/context.json")
    assert info["browser_profile_dir"].endswith("profiles/research/browser_profile")
