"""Migration from legacy flat layout to profile-based directories."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .io import atomic_update_json
from .paths import get_config_path, get_home_dir

logger = logging.getLogger(__name__)

__all__ = ["MigrationLockTimeoutError", "ensure_profiles_dir", "migrate_to_profiles"]

_MIGRATION_MARKER = ".migration_complete"
_MIGRATION_LOCK = ".migration.lock"
_MIGRATION_LOCK_TIMEOUT = 30.0
_LEGACY_FILES = ["storage_state.json", "context.json"]
_LEGACY_DIRS = ["browser_profile"]


class MigrationLockTimeoutError(RuntimeError):
    """Raised when ``migrate_to_profiles()`` cannot acquire the migration lock."""


def _has_legacy_files(home: Path) -> bool:
    return any((home / name).exists() for name in _LEGACY_FILES) or any(
        (home / name).is_dir() for name in _LEGACY_DIRS
    )


@contextmanager
def _migration_lock(path: Path, timeout: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        if sys.platform == "win32":
            import msvcrt  # pragma: no cover

            deadline = time.monotonic() + timeout
            while True:  # pragma: no cover
                try:
                    lock_file.seek(0)
                    lock_file.write(b"0")
                    lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise MigrationLockTimeoutError(
                            f"Could not acquire migration lock at {path} within {timeout:.0f}s; "
                            "another process may be stuck mid-migration."
                        ) from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise MigrationLockTimeoutError(
                        f"Could not acquire migration lock at {path} within {timeout:.0f}s; "
                        "another process may be stuck mid-migration."
                    ) from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def migrate_to_profiles() -> bool:
    home = get_home_dir(create=True)
    with _migration_lock(home / _MIGRATION_LOCK, _MIGRATION_LOCK_TIMEOUT):
        return _migrate_to_profiles_locked(home)


def _migrate_to_profiles_locked(home: Path) -> bool:
    profiles_dir = home / "profiles"
    default_dir = profiles_dir / "default"

    if (default_dir / _MIGRATION_MARKER).exists() and not _has_legacy_files(home):
        return False
    if profiles_dir.exists() and not _has_legacy_files(home):
        return False

    legacy_files = [home / name for name in _LEGACY_FILES if (home / name).exists()]
    legacy_dirs = [home / name for name in _LEGACY_DIRS if (home / name).is_dir()]
    if not legacy_files and not legacy_dirs:
        if sys.platform == "win32":
            profiles_dir.mkdir(exist_ok=True)
        else:
            profiles_dir.mkdir(exist_ok=True, mode=0o700)
        logger.debug("Created profiles directory (fresh install)")
        return True

    if sys.platform == "win32":
        default_dir.mkdir(parents=True, exist_ok=True)
    else:
        default_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    for src in legacy_files:
        dst = default_dir / src.name
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
            if sys.platform != "win32":
                dst.chmod(src.stat().st_mode)

    for src in legacy_dirs:
        dst = default_dir / src.name
        if not dst.exists():
            shutil.copytree(src, dst)

    for src in legacy_files:
        try:
            src.unlink()
        except FileNotFoundError:
            pass
    for src in legacy_dirs:
        shutil.rmtree(src, ignore_errors=True)

    _set_default_profile_in_config()
    (default_dir / _MIGRATION_MARKER).write_text("migrated\n", encoding="utf-8")
    logger.info("Migration complete: legacy files moved to profiles/default/")
    return True


def _set_default_profile_in_config() -> None:
    config_path = get_config_path()
    if sys.platform == "win32":
        config_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _ensure_default(data: dict[str, Any]) -> dict[str, Any]:
        if "default_profile" not in data:
            data["default_profile"] = "default"
        return data

    try:
        atomic_update_json(config_path, _ensure_default)
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Migration config update failed; leaving as-is: %s", exc)


def ensure_profiles_dir() -> None:
    home = get_home_dir()
    profiles_dir = home / "profiles"
    if not profiles_dir.exists() or _has_legacy_files(home):
        migrate_to_profiles()
