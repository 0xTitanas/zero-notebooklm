"""Private logging helper surface used by public exceptions/root helpers."""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path

REFERENCE_ROOT = Path("notebooklm-py-reference/src/notebooklm")


def _upstream_all(relative: str) -> list[str]:
    tree = ast.parse((REFERENCE_ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{relative} has no __all__")


def test_logging_all_and_exception_scrubber_match_upstream():
    import notebooklm._logging as logging_helpers
    import notebooklm.exceptions as exceptions

    assert logging_helpers.__all__ == _upstream_all("_logging.py")
    assert exceptions.scrub_secrets is logging_helpers.scrub_secrets


def test_configure_logging_installs_redaction_and_honors_env(monkeypatch):
    import notebooklm._logging as logging_helpers

    logger = logging.getLogger("notebooklm")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    old_httpx_filters = list(logging.getLogger("httpx").filters)
    old_urllib3_filters = list(logging.getLogger("urllib3").filters)
    logger.handlers = []
    logging.getLogger("httpx").filters = []
    logging.getLogger("urllib3").filters = []
    monkeypatch.setenv("NOTEBOOKLM_LOG_LEVEL", "INFO")
    try:
        logging_helpers.configure_logging()

        assert logger.level == logging.INFO
        assert logger.propagate is True
        assert any(getattr(handler, "_notebooklm_redacting", False) for handler in logger.handlers)
        assert any(
            isinstance(filter_, logging_helpers.RedactingFilter)
            for filter_ in logging.getLogger("httpx").filters
        )
        assert logging_helpers.scrub_secrets("at=csrf-secret") == "at=***"
        assert logging_helpers.scrub_secrets("plain message") == "plain message"
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        logging.getLogger("httpx").filters = old_httpx_filters
        logging.getLogger("urllib3").filters = old_urllib3_filters


def test_scrub_secrets_matches_upstream_representative_patterns():
    import notebooklm._logging as logging_helpers

    spec = importlib.util.spec_from_file_location(
        "upstream_notebooklm_logging",
        REFERENCE_ROOT / "_logging.py",
    )
    assert spec and spec.loader
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)

    cases = [
        "plain message",
        "SNlM0e\":\"AF1_QpN-secret_suffix\"",
        "FdrFJe\":\"1234567890abcdef\"",
        "upload_id=upload-secret",
        "csrf=csrf-secret",
    ]
    for text in cases:
        assert logging_helpers.scrub_secrets(text) == upstream.scrub_secrets(text)


def test_logging_private_defaults_and_formatter_methods_match_upstream():
    import notebooklm._logging as logging_helpers

    spec = importlib.util.spec_from_file_location(
        "upstream_notebooklm_logging_defaults",
        REFERENCE_ROOT / "_logging.py",
    )
    assert spec and spec.loader
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)

    assert logging_helpers._DEFAULT_FMT == upstream._DEFAULT_FMT
    assert logging_helpers._DEFAULT_DATEFMT == upstream._DEFAULT_DATEFMT
    assert logging_helpers._scrub is logging_helpers.scrub_secrets
    for name in ("formatTime", "formatException", "formatStack"):
        assert name in logging_helpers.RedactingFormatter.__dict__


def test_package_import_configures_logging_like_upstream(monkeypatch):
    import importlib
    import notebooklm
    import notebooklm._logging as logging_helpers

    upstream_init = (REFERENCE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "configure_logging()" in upstream_init

    logger = logging.getLogger("notebooklm")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    old_httpx_filters = list(logging.getLogger("httpx").filters)
    old_urllib3_filters = list(logging.getLogger("urllib3").filters)
    logger.handlers = []
    logging.getLogger("httpx").filters = []
    logging.getLogger("urllib3").filters = []
    monkeypatch.setenv("NOTEBOOKLM_LOG_LEVEL", "INFO")
    try:
        importlib.reload(notebooklm)

        assert logger.level == logging.INFO
        assert logger.propagate is True
        assert any(getattr(handler, "_notebooklm_redacting", False) for handler in logger.handlers)
        assert any(
            isinstance(filter_, logging_helpers.RedactingFilter)
            for filter_ in logging.getLogger("httpx").filters
        )
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        logging.getLogger("httpx").filters = old_httpx_filters
        logging.getLogger("urllib3").filters = old_urllib3_filters
