"""Public migration helper parity."""

from __future__ import annotations

import json
import sys

import pytest


def test_migration_public_surface_matches_upstream():
    import notebooklm.migration as migration

    assert migration.__all__ == [
        "MigrationLockTimeoutError",
        "ensure_profiles_dir",
        "migrate_to_profiles",
    ]
    assert issubclass(migration.MigrationLockTimeoutError, RuntimeError)


def test_migrate_to_profiles_moves_legacy_state_and_is_idempotent(monkeypatch, tmp_path):
    import notebooklm.migration as migration
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    paths.set_active_profile(None)
    paths._reset_config_cache()
    (tmp_path / "storage_state.json").write_text('{"cookies": []}', encoding="utf-8")
    (tmp_path / "context.json").write_text('{"active_notebook": "n"}', encoding="utf-8")
    (tmp_path / "browser_profile").mkdir()
    (tmp_path / "browser_profile" / "marker").write_text("ok", encoding="utf-8")

    assert migration.migrate_to_profiles() is True

    default_dir = tmp_path / "profiles" / "default"
    assert json.loads((default_dir / "storage_state.json").read_text(encoding="utf-8")) == {
        "cookies": []
    }
    assert json.loads((default_dir / "context.json").read_text(encoding="utf-8")) == {
        "active_notebook": "n"
    }
    assert (default_dir / "browser_profile" / "marker").read_text(encoding="utf-8") == "ok"
    assert (default_dir / ".migration_complete").read_text(encoding="utf-8") == "migrated\n"
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))[
        "default_profile"
    ] == "default"
    assert not (tmp_path / "storage_state.json").exists()
    assert not (tmp_path / "context.json").exists()
    assert not (tmp_path / "browser_profile").exists()

    assert migration.migrate_to_profiles() is False


def test_ensure_profiles_dir_creates_fresh_profiles_dir(monkeypatch, tmp_path):
    import notebooklm.migration as migration
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    paths.set_active_profile(None)
    paths._reset_config_cache()

    migration.ensure_profiles_dir()

    assert (tmp_path / "profiles").is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock contention test")
def test_migrate_to_profiles_reports_lock_timeout(monkeypatch, tmp_path):
    import fcntl

    import notebooklm.migration as migration
    import notebooklm.paths as paths

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    monkeypatch.setattr(migration, "_MIGRATION_LOCK_TIMEOUT", 0.01)
    paths.set_active_profile(None)
    paths._reset_config_cache()
    tmp_path.mkdir(exist_ok=True)
    lock_path = tmp_path / ".migration.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with pytest.raises(migration.MigrationLockTimeoutError, match="Could not acquire"):
            migration.migrate_to_profiles()
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
