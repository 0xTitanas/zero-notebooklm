"""Phase 2A profile layout, lifecycle, and per-profile session context.

This module implements the *offline*, stdlib-only foundation for NotebookLM
Bare's auth/session/profile parity:

  * a profile-directory layout that mirrors upstream's ``~/.notebooklm`` /
    ``profiles/<name>/`` shape (home root, ``profiles/`` dir, per-profile
    ``storage_state.json`` + ``context.json``, and a global ``config.json``
    carrying the default-profile marker);
  * conservative profile-name validation;
  * profile ``create``/``delete``/``list``/``rename``/``switch``;
  * per-profile session context for ``use``/``status``/``clear``.

It never performs any network I/O, reads no real browser cookie store or OS
keychain, and writes only under a caller-provided (or explicitly resolved) home
root. Storage-root precedence is: an explicit argument, then the
``NOTEBOOKLM_HOME`` environment variable, then ``~/.notebooklm``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from .errors import (
    ProfileError,
    ProfileExistsError,
    ProfileNotFoundError,
    ValidationError,
)

# --------------------------------------------------------------------------- #
# Layout constants
# --------------------------------------------------------------------------- #

DEFAULT_HOME_DIRNAME = ".notebooklm"
PROFILES_DIRNAME = "profiles"
CONFIG_FILENAME = "config.json"
STORAGE_STATE_FILENAME = "storage_state.json"
CONTEXT_FILENAME = "context.json"
# Phase 2C: redacted metadata describing the *explicit* browser-cookie source a
# profile's storage_state.json was imported from, so ``auth refresh`` can re-import
# without re-asking for the source (and never falling back to a live machine read).
AUTH_SOURCE_FILENAME = "auth_source.json"
BROWSER_PROFILE_DIRNAME = "browser"

DEFAULT_PROFILE_NAME = "default"
NOTEBOOKLM_HOME_ENV = "NOTEBOOKLM_HOME"

# Conservative profile-name rule: start with an alphanumeric, then alphanumerics
# plus a small safe punctuation set. This forbids path separators, ``..``
# traversal, leading dots/dashes, whitespace, and control characters by design.
PROFILE_NAME_MAX_LENGTH = 64
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

__all__ = [
    "DEFAULT_HOME_DIRNAME",
    "PROFILES_DIRNAME",
    "CONFIG_FILENAME",
    "STORAGE_STATE_FILENAME",
    "CONTEXT_FILENAME",
    "AUTH_SOURCE_FILENAME",
    "BROWSER_PROFILE_DIRNAME",
    "DEFAULT_PROFILE_NAME",
    "NOTEBOOKLM_HOME_ENV",
    "PROFILE_NAME_MAX_LENGTH",
    "validate_profile_name",
    "resolve_home",
    "read_json",
    "write_json_atomic",
    "ProfileStore",
    "read_context",
    "write_context",
    "set_active_notebook",
    "get_active_notebook",
    "clear_context",
]


# --------------------------------------------------------------------------- #
# Validation + storage-root resolution
# --------------------------------------------------------------------------- #


def validate_profile_name(name: str) -> str:
    """Return ``name`` if it is a safe profile name, else raise ``ValidationError``."""

    if not isinstance(name, str):
        raise ValidationError("profile name must be a string")
    if not name:
        raise ValidationError("profile name must not be empty")
    if len(name) > PROFILE_NAME_MAX_LENGTH:
        raise ValidationError(
            f"profile name too long (max {PROFILE_NAME_MAX_LENGTH} characters)"
        )
    if name in (".", ".."):
        raise ValidationError("profile name must not be a path component")
    if not _PROFILE_NAME_RE.fullmatch(name):
        raise ValidationError(
            "profile name may contain only letters, digits, '.', '_', and '-', "
            "and must start with a letter or digit"
        )
    return name


def resolve_home(
    home: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the NotebookLM home directory.

    Precedence: explicit ``home`` argument, then ``$NOTEBOOKLM_HOME``, then
    ``~/.notebooklm``. This is a pure path computation — it never creates the
    directory.
    """

    if home is not None:
        return Path(home).expanduser()
    env = os.environ if environ is None else environ
    env_home = env.get(NOTEBOOKLM_HOME_ENV)
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / DEFAULT_HOME_DIRNAME


# --------------------------------------------------------------------------- #
# JSON helpers (stdlib-only atomic write)
# --------------------------------------------------------------------------- #


def read_json(path: str | os.PathLike[str], *, default: Any = None) -> Any:
    """Read and parse a JSON file.

    Returns ``default`` if the file is missing. Raises ``ValidationError`` if the
    file exists but does not contain valid JSON.
    """

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"could not read {p}: {exc}") from exc
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"invalid JSON in {p}: {exc}") from exc


def write_json_atomic(
    path: str | os.PathLike[str], data: Any, *, mode: int = 0o600
) -> None:
    """Write ``data`` as pretty JSON to ``path`` atomically (temp file + replace)."""

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:  # pragma: no cover - defensive
            pass
    try:
        os.chmod(p, mode)
    except OSError:  # pragma: no cover - platform dependent (e.g. Windows)
        pass


# --------------------------------------------------------------------------- #
# Profile store
# --------------------------------------------------------------------------- #


class ProfileStore:
    """Manage profiles under a single NotebookLM home directory."""

    def __init__(
        self,
        home: str | os.PathLike[str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.home = resolve_home(home, environ=environ)

    # -- path helpers ----------------------------------------------------- #

    @property
    def profiles_dir(self) -> Path:
        return self.home / PROFILES_DIRNAME

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_FILENAME

    def profile_dir(self, name: str) -> Path:
        return self.profiles_dir / validate_profile_name(name)

    def storage_state_path(self, name: str) -> Path:
        return self.profile_dir(name) / STORAGE_STATE_FILENAME

    def context_path(self, name: str) -> Path:
        return self.profile_dir(name) / CONTEXT_FILENAME

    def auth_source_path(self, name: str) -> Path:
        """Path to the profile's redacted browser-cookie source metadata file."""

        return self.profile_dir(name) / AUTH_SOURCE_FILENAME

    def browser_profile_dir(self, name: str) -> Path:
        return self.profile_dir(name) / BROWSER_PROFILE_DIRNAME

    def path_info(self, profile: str | None = None) -> dict[str, str]:
        """Return a diagnostic map of resolved paths for ``profile``."""

        name = self.resolve_profile(profile)
        return {
            "home": str(self.home),
            "profiles_dir": str(self.profiles_dir),
            "profile": name,
            "profile_dir": str(self.profile_dir(name)),
            "storage_state": str(self.storage_state_path(name)),
            "context": str(self.context_path(name)),
            "auth_source": str(self.auth_source_path(name)),
            "config": str(self.config_path),
        }

    # -- config / active marker ------------------------------------------ #

    def read_config(self) -> dict[str, Any]:
        cfg = read_json(self.config_path, default={})
        return cfg if isinstance(cfg, dict) else {}

    def _write_config(self, cfg: Mapping[str, Any]) -> None:
        write_json_atomic(self.config_path, dict(cfg))

    def active_profile(self) -> str | None:
        """Return the configured default profile name, or ``None`` if unset."""

        value = self.read_config().get("default_profile")
        return value if isinstance(value, str) and value else None

    def resolve_profile(self, profile: str | None = None) -> str:
        """Resolve the effective profile: explicit, env, config, default."""

        if profile is not None:
            return validate_profile_name(profile)
        env_profile = os.environ.get("NOTEBOOKLM_PROFILE")
        if env_profile:
            return validate_profile_name(env_profile)
        active = self.active_profile()
        if active is not None:
            return active
        return DEFAULT_PROFILE_NAME

    # -- lifecycle -------------------------------------------------------- #

    def list_profiles(self) -> list[str]:
        """Return the sorted list of existing, validly-named profiles."""

        if not self.profiles_dir.is_dir():
            return []
        names = []
        for child in self.profiles_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                validate_profile_name(child.name)
            except ValidationError:
                continue
            names.append(child.name)
        return sorted(names)

    def profile_exists(self, name: str) -> bool:
        return self.profile_dir(name).is_dir()

    def create_profile(self, name: str) -> Path:
        """Create an empty profile directory. Raise if it already exists."""

        target = self.profile_dir(name)
        if target.exists():
            raise ProfileExistsError(f"profile already exists: {name}")
        target.mkdir(parents=True, exist_ok=False)
        return target

    def delete_profile(self, name: str, *, force: bool = False) -> None:
        """Delete a profile and its data.

        Refuses to delete the active (default) profile unless ``force`` is set.
        When the active profile is force-deleted, the default-profile marker is
        cleared so it does not dangle.
        """

        name = validate_profile_name(name)
        target = self.profile_dir(name)
        if not target.is_dir():
            raise ProfileNotFoundError(f"profile not found: {name}")
        if name == self.active_profile() and not force:
            raise ProfileError(
                f"refusing to delete active profile '{name}' without force"
            )
        shutil.rmtree(target)
        if self.active_profile() == name:
            cfg = self.read_config()
            cfg.pop("default_profile", None)
            self._write_config(cfg)

    def rename_profile(self, old_name: str, new_name: str) -> None:
        """Rename a profile directory, following the active marker if needed."""

        old_name = validate_profile_name(old_name)
        new_name = validate_profile_name(new_name)
        src = self.profile_dir(old_name)
        dst = self.profile_dir(new_name)
        if not src.is_dir():
            raise ProfileNotFoundError(f"profile not found: {old_name}")
        if dst.exists():
            raise ProfileExistsError(f"profile already exists: {new_name}")
        os.replace(src, dst)
        if self.active_profile() == old_name:
            cfg = self.read_config()
            cfg["default_profile"] = new_name
            self._write_config(cfg)

    def switch_profile(self, name: str) -> None:
        """Set ``name`` as the default profile. Raise if it does not exist."""

        name = validate_profile_name(name)
        if not self.profile_exists(name):
            raise ProfileNotFoundError(f"profile not found: {name}")
        cfg = self.read_config()
        cfg["default_profile"] = name
        self._write_config(cfg)


# --------------------------------------------------------------------------- #
# Per-profile session context (use / status / clear)
# --------------------------------------------------------------------------- #


def read_context(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return the context mapping at ``path`` (``{}`` if absent)."""

    ctx = read_json(path, default={})
    return ctx if isinstance(ctx, dict) else {}


def write_context(path: str | os.PathLike[str], ctx: Mapping[str, Any]) -> None:
    write_json_atomic(path, dict(ctx))


def set_active_notebook(
    path: str | os.PathLike[str],
    notebook_id: str,
    *,
    title: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Persist the active notebook context (offline; no server verification)."""

    if not isinstance(notebook_id, str) or not notebook_id:
        raise ValidationError("notebook id must be a non-empty string")
    ctx = read_context(path)
    ctx["notebook_id"] = notebook_id
    ctx["notebook_title"] = title
    ctx["conversation_id"] = conversation_id
    write_context(path, ctx)
    return ctx


def get_active_notebook(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Return the active-notebook context, or ``None`` if no notebook is set."""

    ctx = read_context(path)
    if ctx.get("notebook_id"):
        return ctx
    return None


def clear_context(path: str | os.PathLike[str]) -> bool:
    """Clear the session context. Returns ``True`` if a context file was removed."""

    p = Path(path)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
