"""Runtime RPC override resolver surface."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

RPC_OVERRIDES_ENV_VAR = "NOTEBOOKLM_RPC_OVERRIDES"

logger = logging.getLogger(__name__)
_logged_override_hashes: set[int] = set()


def _valid_rpc_method_names() -> set[str]:
    from .types import RPCMethod

    return set(RPCMethod.__members__)


@lru_cache(maxsize=8)
def _parse_rpc_overrides(raw: str | None) -> tuple[tuple[str, str], ...]:
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("%s is not valid JSON: %s", RPC_OVERRIDES_ENV_VAR, exc)
        return ()
    if not isinstance(data, dict):
        logger.warning(
            "%s must be a JSON object mapping method names to RPC IDs, got %s",
            RPC_OVERRIDES_ENV_VAR,
            type(data).__name__,
        )
        return ()

    valid_methods = _valid_rpc_method_names()
    normalized: list[tuple[str, str]] = []
    null_keys: list[str] = []
    for key, value in data.items():
        if value is None:
            null_keys.append(str(key))
            continue
        normalized.append((str(key), str(value)))
    if null_keys:
        logger.warning(
            "Ignoring %s entries with null values: %s",
            RPC_OVERRIDES_ENV_VAR,
            ", ".join(sorted(null_keys)),
        )
    unknown = sorted(key for key, _value in normalized if key not in valid_methods)
    if unknown:
        logger.warning(
            "Ignoring unknown %s method names: %s",
            RPC_OVERRIDES_ENV_VAR,
            ", ".join(unknown),
        )
    return tuple((key, value) for key, value in normalized if key in valid_methods)


def _load_rpc_overrides() -> dict[str, str]:
    return dict(_parse_rpc_overrides(os.environ.get(RPC_OVERRIDES_ENV_VAR)))


def resolve_rpc_id(method_name: str, canonical_id: str) -> str:
    from ..config import get_base_host

    try:
        get_base_host()
    except ValueError:
        return canonical_id

    overrides = _load_rpc_overrides()
    if not overrides:
        return canonical_id

    key = hash(tuple(sorted(overrides.items())))
    if key not in _logged_override_hashes:
        _logged_override_hashes.add(key)
        logger.info(
            "%s applied: %s",
            RPC_OVERRIDES_ENV_VAR,
            ", ".join(f"{k}={v}" for k, v in sorted(overrides.items())),
        )
    return overrides.get(method_name, canonical_id)
