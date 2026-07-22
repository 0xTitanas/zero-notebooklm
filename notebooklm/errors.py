"""Deterministic Phase 1 error types and CLI exit-code mapping.

The exception names intentionally mirror broad upstream-compatible failure
classes while remaining stdlib-only and side-effect free.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import exceptions as _public_exceptions


class NotebookLMError(Exception):
    """Base class for NotebookLM Bare errors."""


class NotImplementedInPhaseError(NotebookLMError):
    """Raised for a command surface reserved for a later parity phase."""


class AuthenticationError(NotebookLMError):
    """Authentication/session state is missing or invalid."""


class AuthorizationError(AuthenticationError):
    """Authenticated identity lacks permission for the requested operation."""


class NetworkError(NotebookLMError):
    """HTTP transport or remote connectivity failed."""


class HTTPTransportError(NetworkError):
    """HTTP transport failed before a complete response was available."""


class UnsupportedSchemeError(HTTPTransportError):
    """URL scheme is not supported by the stdlib transport."""


class RedirectError(HTTPTransportError):
    """Redirect handling failed or exceeded the configured bound."""


class BodyTooLargeError(HTTPTransportError):
    """Response body exceeded the configured byte bound."""


class TransportTimeoutError(NetworkError):
    """Async/blocking transport operation exceeded its timeout."""


class TransportClosedError(NetworkError):
    """Async transport was used after close."""


class RateLimitError(NetworkError):
    """Remote service rate limit or retryable quota boundary."""


class ValidationError(NotebookLMError):
    """User input or local data failed validation."""


class ProfileError(NotebookLMError):
    """A profile lifecycle operation could not be completed."""


class ProfileExistsError(ProfileError):
    """A profile with the requested name already exists."""


class ProfileNotFoundError(ProfileError):
    """The requested profile does not exist."""


class ProfileLockError(NotebookLMError):
    """Profile/session lock could not be acquired or released safely."""


_EXIT_CODES: Mapping[type[BaseException], int] = {
    NotImplementedInPhaseError: 78,  # EX_CONFIG-style: command exists, behavior intentionally unavailable.
    AuthenticationError: 77,  # EX_NOPERM-style auth/session failure.
    AuthorizationError: 77,
    NetworkError: 69,  # EX_UNAVAILABLE-style transport failure.
    HTTPTransportError: 69,
    UnsupportedSchemeError: 64,
    RedirectError: 69,
    BodyTooLargeError: 74,
    TransportTimeoutError: 75,
    TransportClosedError: 70,
    RateLimitError: 75,  # EX_TEMPFAIL-style retryable remote boundary.
    ValidationError: 64,  # EX_USAGE-style bad input.
    _public_exceptions.ValidationError: 64,
    _public_exceptions.NetworkError: 69,
    ProfileError: 64,  # EX_USAGE-style profile lifecycle failure.
    ProfileLockError: 73,  # EX_CANTCREAT-style local lock/state failure.
    NotebookLMError: 70,  # EX_SOFTWARE-style generic project error.
    _public_exceptions.NotebookLMError: 70,
}


def exit_code_for(exc: BaseException | type[BaseException]) -> int:
    """Return the deterministic CLI exit code for an exception or exception type."""

    exc_type = exc if isinstance(exc, type) else type(exc)
    for cls in exc_type.__mro__:
        code = _EXIT_CODES.get(cls)
        if code is not None:
            return code
    return 1


__all__ = [
    "NotebookLMError",
    "NotImplementedInPhaseError",
    "AuthenticationError",
    "AuthorizationError",
    "NetworkError",
    "HTTPTransportError",
    "UnsupportedSchemeError",
    "RedirectError",
    "BodyTooLargeError",
    "TransportTimeoutError",
    "TransportClosedError",
    "RateLimitError",
    "ValidationError",
    "ProfileError",
    "ProfileExistsError",
    "ProfileNotFoundError",
    "ProfileLockError",
    "exit_code_for",
]
