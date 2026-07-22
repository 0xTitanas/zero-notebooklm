"""Public I/O helpers."""

from __future__ import annotations

import errno
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_STORAGE_STATE_FILENAME = "storage_state.json"
logger = logging.getLogger(__name__)
_FSYNC_UNSUPPORTED_ERRNOS = frozenset(
    e
    for e in (
        getattr(errno, "EINVAL", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOSYS", None),
        getattr(errno, "EROFS", None),
    )
    if e is not None
)


def _unsupported_fsync(exc: OSError) -> bool:
    return exc.errno in _FSYNC_UNSUPPORTED_ERRNOS


def _fsync_dir(directory: Path) -> None:
    if sys.platform == "win32" or not hasattr(os, "fsync"):
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.warning("Could not open parent dir %s for fsync: %s", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        if _unsupported_fsync(exc):
            logger.debug("Parent dir %s does not support fsync: %s", directory, exc)
        else:
            logger.warning("Failed to fsync parent dir %s: %s", directory, exc)
    finally:
        os.close(fd)


_WINDOWS_REPLACE_TRANSIENT_WINERRORS = {5, 32}
_WINDOWS_REPLACE_MAX_ATTEMPTS = 10
_WINDOWS_REPLACE_INITIAL_DELAY_SECONDS = 0.001
_WINDOWS_REPLACE_MAX_DELAY_SECONDS = 0.05


def _is_retryable_windows_replace_error(exc: PermissionError) -> bool:
    return (
        sys.platform == "win32"
        and getattr(exc, "winerror", None) in _WINDOWS_REPLACE_TRANSIENT_WINERRORS
    )


def replace_file_atomically(temp_path: Path, path: Path) -> None:
    """Replace ``path`` with ``temp_path``."""
    delay = _WINDOWS_REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(_WINDOWS_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            if (
                not _is_retryable_windows_replace_error(exc)
                or attempt == _WINDOWS_REPLACE_MAX_ATTEMPTS - 1
            ):
                raise
            time.sleep(delay)
            delay = min(delay * 2, _WINDOWS_REPLACE_MAX_DELAY_SECONDS)


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o600) -> None:
    """Write JSON to ``path`` via a same-directory temp file and atomic replace."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, indent=2, ensure_ascii=False)
            if sys.platform != "win32":
                os.fchmod(temp_file.fileno(), mode)
            temp_file.flush()
            if hasattr(os, "fsync"):
                try:
                    os.fsync(temp_file.fileno())
                except OSError as exc:
                    if not _unsupported_fsync(exc):
                        raise
        replace_file_atomically(temp_path, path)
        _fsync_dir(path.parent)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.debug("Failed to clean up temp file %s: %s", temp_path, cleanup_err)
        raise


@contextmanager
def _file_lock(path: Path, timeout: float) -> Iterator[None]:
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
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out acquiring lock {path}")
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
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out acquiring lock {path}")
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_update_json(
    path: Path,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    mode: int = 0o600,
    timeout: float = 10.0,
    recover_from_corrupt: bool = False,
) -> None:
    """Lock, read, mutate, and atomically write a JSON object file."""
    if path.name.casefold() == _STORAGE_STATE_FILENAME:
        raise ValueError(
            "atomic_update_json must not be called with a storage_state.json path; "
            "use the dedicated auth storage writers instead"
        )
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(lock_path, timeout):
        current: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                if not recover_from_corrupt:
                    raise
                loaded = {}
            current = loaded if isinstance(loaded, dict) else {}
        atomic_write_json(path, mutator(current), mode=mode)


__all__ = ["atomic_update_json", "atomic_write_json", "replace_file_atomically"]
