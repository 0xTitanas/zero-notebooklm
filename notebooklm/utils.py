"""Small stdlib-only utility compatibility surface."""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, ValuesView
import os
import warnings
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from .exceptions import ChatResponseParseError

if TYPE_CHECKING:
    from .client import NotebookLMClient
    from .types import ChatReference

T = TypeVar("T")

_QUIET_DEPRECATIONS_ENV = "NOTEBOOKLM_QUIET_DEPRECATIONS"
_FUTURE_ERRORS_ENV = "NOTEBOOKLM_FUTURE_ERRORS"
_GET_RETURNS_NONE_FLIP_ISSUE = 1247
_DEFAULT_DEPRECATION_REMOVAL = "0.8.0"


async def resolve_chat_reference_passage(
    client: NotebookLMClient,
    notebook_id: str,
    reference: ChatReference,
    context_chars: int = 200,
) -> str:
    if not reference.cited_text:
        raise ChatResponseParseError(
            f"ChatReference for source {reference.source_id!r} has no "
            "cited_text to resolve. This is typical of structural-anchor "
            "citations (image/section markers) that have no plaintext "
            "passage to surface."
        )

    fulltext = await client.sources.get_fulltext(notebook_id, reference.source_id)
    matches = fulltext.find_citation_context(
        reference.cited_text,
        context_chars=context_chars,
    )
    if not matches:
        raise ChatResponseParseError(
            f"Could not locate cited_text in source {reference.source_id!r} "
            f"of notebook {notebook_id!r}. The source may have been "
            "re-indexed since the citation was emitted, or the cited span "
            "may have been transformed during chunking."
        )

    passage, _position = matches[0]
    return passage


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _future_errors_enabled() -> bool:
    return _truthy_env(_FUTURE_ERRORS_ENV)


def _warn_deprecated(
    message: str, *, removal: str | None = None, stacklevel: int = 3
) -> None:
    if _truthy_env(_QUIET_DEPRECATIONS_ENV):
        return
    text = message
    if removal is not None and f"v{removal}" not in text and removal not in text:
        text = f"{text} It will be removed in v{removal}."
    warnings.warn(text, DeprecationWarning, stacklevel=stacklevel)


def _deprecated_kwarg(
    old_value: T | None,
    new_value: T | None,
    *,
    old: str,
    new: str,
    owner: str,
    removal: str = _DEFAULT_DEPRECATION_REMOVAL,
    sentinel: object = None,
    stacklevel: int = 3,
) -> T | None:
    old_provided = old_value is not sentinel
    new_provided = new_value is not sentinel
    if old_provided and new_provided:
        raise TypeError(
            f"{owner}() received both {new!r} and the deprecated alias {old!r}; pass only {new!r}."
        )
    if old_provided:
        if _future_errors_enabled():
            raise TypeError(
                f"{owner}() got an unexpected keyword argument {old!r}; "
                f"use {new!r} instead (the {old!r} alias was removed in v{removal})."
            )
        _warn_deprecated(
            f"{owner}({old}=...) is deprecated and will be removed in v{removal}; "
            f"use {new}=... instead (same behavior). "
            f"Set {_QUIET_DEPRECATIONS_ENV}=1 to silence this warning.",
            removal=removal,
            stacklevel=stacklevel + 1,
        )
        return old_value
    return new_value


class _MappingCompatMixin:
    _COMPAT_KEYS: ClassVar[dict[str, str]] = {}

    def to_public_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def _block_if_future_errors(self, exc_type: type[Exception], message: str) -> None:
        if _future_errors_enabled():
            raise exc_type(message)

    def __getitem__(self, key: str) -> Any:
        self._block_if_future_errors(
            TypeError, f"{type(self).__name__!r} object is not subscriptable"
        )
        legacy = self.to_public_dict()
        if key not in legacy:
            raise KeyError(key)
        if not _truthy_env(_QUIET_DEPRECATIONS_ENV):
            attr = self._COMPAT_KEYS.get(key, key)
            warnings.warn(
                f"{type(self).__name__}[{key!r}] dict-style access is "
                f"deprecated and will be removed in v{_DEFAULT_DEPRECATION_REMOVAL}; "
                f"use the typed attribute .{attr} instead. "
                f"Set {_QUIET_DEPRECATIONS_ENV}=1 to silence this warning.",
                DeprecationWarning,
                stacklevel=2,
            )
        return legacy[key]

    def get(self, key: str, default: Any = None) -> Any:
        self._block_if_future_errors(
            AttributeError, f"{type(self).__name__!r} object has no attribute 'get'"
        )
        return self.to_public_dict().get(key, default)

    def keys(self) -> KeysView[str]:
        self._block_if_future_errors(
            AttributeError, f"{type(self).__name__!r} object has no attribute 'keys'"
        )
        return self.to_public_dict().keys()

    def items(self) -> ItemsView[str, Any]:
        self._block_if_future_errors(
            AttributeError, f"{type(self).__name__!r} object has no attribute 'items'"
        )
        return self.to_public_dict().items()

    def values(self) -> ValuesView[Any]:
        self._block_if_future_errors(
            AttributeError, f"{type(self).__name__!r} object has no attribute 'values'"
        )
        return self.to_public_dict().values()

    def __len__(self) -> int:
        self._block_if_future_errors(
            TypeError, f"object of type {type(self).__name__!r} has no len()"
        )
        return len(self.to_public_dict())

    def __contains__(self, key: object) -> bool:
        self._block_if_future_errors(
            TypeError, f"argument of type {type(self).__name__!r} is not iterable"
        )
        return key in self.to_public_dict()

    def __iter__(self) -> Iterator[str]:
        self._block_if_future_errors(
            TypeError, f"{type(self).__name__!r} object is not iterable"
        )
        return iter(self.to_public_dict())


def _warn_get_returns_none(
    resource: str, *, removal: str = "0.8.0", stacklevel: int = 3
) -> None:
    if _truthy_env(_QUIET_DEPRECATIONS_ENV):
        return
    exc_stem = "".join(part.capitalize() for part in resource.split("_"))
    exc_name = f"{exc_stem}NotFoundError"
    warnings.warn(
        f"{resource}s.get() returning None for a missing {resource} is "
        f"deprecated and will be removed in v{removal}: in v{removal} it will "
        f"raise {exc_name} instead (issue #{_GET_RETURNS_NONE_FLIP_ISSUE}). "
        f"To keep handling missing {resource}s, wrap the call in try/except {exc_name}.",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def _resolve_get(result: T | None, *, not_found: Exception, resource: str) -> T | None:
    if result is not None:
        return result
    if _future_errors_enabled():
        raise not_found
    _warn_get_returns_none(resource, stacklevel=4)
    return None


__all__ = ["resolve_chat_reference_passage"]
