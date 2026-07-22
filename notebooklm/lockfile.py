"""Cross-platform stdlib lockfile helper for NotebookLM Bare Phase 1."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import ProfileLockError


@dataclass(frozen=True)
class LockMetadata:
    pid: int
    created_at: float
    host: str
    owner_id: str

    def as_json(self) -> str:
        return (
            json.dumps(
                {
                    "pid": self.pid,
                    "created_at": self.created_at,
                    "host": self.host,
                    "owner_id": self.owner_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


class LockHandle:
    """Owned lock handle returned by ``LockFile.acquire``."""

    def __init__(self, path: Path, metadata: LockMetadata) -> None:
        self.path = path
        self.metadata = metadata
        self.owner_id = metadata.owner_id
        self._released = False

    def release(self) -> None:
        """Release the lock only if the file is still owned by this handle."""

        if self._released:
            return
        self._released = True
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            return
        if (
            current.get("owner_id") == self.owner_id
            and current.get("pid") == self.metadata.pid
        ):
            try:
                self.path.unlink()
            except FileNotFoundError:
                return

    def __enter__(self) -> "LockHandle":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class LockFile:
    """Atomic lockfile using stdlib ``O_CREAT | O_EXCL`` semantics."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def acquire(self) -> LockHandle:
        """Acquire the lock or raise ``ProfileLockError``.

        Existing locks, including stale-looking or corrupt locks, are never stolen
        in Phase 1. Later phases may add an explicit recovery path if needed.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        metadata = LockMetadata(
            pid=os.getpid(),
            created_at=time.time(),
            host=socket.gethostname(),
            owner_id=uuid.uuid4().hex,
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise ProfileLockError(f"lock already exists: {self.path}") from exc
        except OSError as exc:
            raise ProfileLockError(f"could not create lock {self.path}: {exc}") from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(metadata.as_json())
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as exc:
            try:
                self.path.unlink()
            except OSError:
                pass
            raise ProfileLockError(f"could not write lock metadata: {exc}") from exc
        return LockHandle(self.path, metadata)

    def __enter__(self) -> LockHandle:
        self._context_handle = self.acquire()
        return self._context_handle

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        handle: Any = getattr(self, "_context_handle", None)
        if handle is not None:
            handle.release()


__all__ = ["LockFile", "LockHandle", "LockMetadata"]
