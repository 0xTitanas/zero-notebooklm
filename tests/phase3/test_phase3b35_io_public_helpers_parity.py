"""Public I/O helper parity for atomic JSON/file writes."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest


def test_io_public_surface_matches_upstream():
    import notebooklm.io as io

    assert io.__all__ == [
        "atomic_update_json",
        "atomic_write_json",
        "replace_file_atomically",
    ]


def test_atomic_write_json_replaces_file_and_uses_private_mode(tmp_path):
    from notebooklm.io import atomic_write_json

    path = tmp_path / "config.json"
    path.write_text('{"old": true}', encoding="utf-8")

    atomic_write_json(path, {"new": "✓"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": "✓"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_replace_file_atomically_replaces_destination(tmp_path):
    from notebooklm.io import replace_file_atomically

    path = tmp_path / "data.txt"
    temp = tmp_path / "temp.txt"
    path.write_text("old", encoding="utf-8")
    temp.write_text("new", encoding="utf-8")

    replace_file_atomically(temp, path)

    assert path.read_text(encoding="utf-8") == "new"
    assert not temp.exists()


def test_replace_file_atomically_retries_transient_windows_replace_errors(monkeypatch):
    import notebooklm.io as io

    attempts: list[tuple[object, object]] = []

    def fake_replace(temp_path, path):
        attempts.append((temp_path, path))
        if len(attempts) == 1:
            exc = PermissionError("busy")
            exc.winerror = 32
            raise exc

    monkeypatch.setattr(io.sys, "platform", "win32")
    monkeypatch.setattr(io.os, "replace", fake_replace)
    monkeypatch.setattr(io.time, "sleep", lambda delay: None)

    io.replace_file_atomically("temp", "dest")

    assert attempts == [("temp", "dest"), ("temp", "dest")]


@pytest.mark.skipif(sys.platform == "win32", reason="directory fsync is POSIX-only")
def test_parent_directory_fsync_failure_does_not_fail_committed_write(monkeypatch, tmp_path):
    import notebooklm.io as io

    real_open = os.open
    real_fsync = os.fsync
    path = tmp_path / "config.json"
    opened_dir_fd: int | None = None

    def wrapped_open(file, flags, mode=0o777, *, dir_fd=None):
        nonlocal opened_dir_fd
        fd = real_open(file, flags, mode, dir_fd=dir_fd)
        if file == tmp_path:
            opened_dir_fd = fd
        return fd

    def wrapped_fsync(fd):
        if fd == opened_dir_fd:
            raise OSError(5, "simulated directory writeback failure")
        return real_fsync(fd)

    monkeypatch.setattr(io.os, "open", wrapped_open)
    monkeypatch.setattr(io.os, "fsync", wrapped_fsync)

    io.atomic_write_json(path, {"ok": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_atomic_update_json_updates_under_lock_and_handles_corruption(tmp_path):
    from notebooklm.io import atomic_update_json

    path = tmp_path / "context.json"
    path.write_text('{"count": 1}', encoding="utf-8")

    atomic_update_json(path, lambda data: {"count": data["count"] + 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 2}
    assert (tmp_path / "context.json.lock").exists()

    path.write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        atomic_update_json(path, lambda data: data)

    atomic_update_json(path, lambda data: {"recovered": data}, recover_from_corrupt=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"recovered": {}}

    with pytest.raises(ValueError, match="storage_state.json"):
        atomic_update_json(tmp_path / "storage_state.json", lambda data: data)
