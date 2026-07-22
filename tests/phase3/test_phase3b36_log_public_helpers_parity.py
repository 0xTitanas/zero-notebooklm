"""Public logging helper parity."""

from __future__ import annotations

import io
import logging
import re


def test_log_public_surface_matches_upstream():
    import notebooklm.log as log

    assert log.__all__ == ["install_redaction"]
    assert hasattr(log, "install_redaction")
    for name in ("set_request_id", "get_request_id", "reset_request_id", "correlation_id"):
        assert not hasattr(log, name)


def test_root_request_id_helpers_match_upstream_context_behavior():
    import notebooklm

    baseline = notebooklm.get_request_id()
    outer_token = notebooklm.set_request_id("outer")
    try:
        assert notebooklm.get_request_id() == "outer"

        generated_token = notebooklm.set_request_id()
        generated = notebooklm.get_request_id()
        assert isinstance(generated, str)
        assert re.fullmatch(r"[0-9a-f]{8}", generated)
        notebooklm.reset_request_id(generated_token)
        assert notebooklm.get_request_id() == "outer"

        with notebooklm.correlation_id("inner") as request_id:
            assert request_id == "inner"
            assert notebooklm.get_request_id() == "inner"
        assert notebooklm.get_request_id() == "outer"
    finally:
        notebooklm.reset_request_id(outer_token)
    assert notebooklm.get_request_id() == baseline


def test_install_redaction_scrubs_existing_logger_handlers():
    import notebooklm.log as log

    logger = logging.getLogger("notebooklm-test-redaction")
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        log.install_redaction("notebooklm-test-redaction")
        logger.info(
            "%s %s %s %s",
            "SI" "D=secret",
            "at=AF1_QpN-" "secret",
            "f.sid=session",
            "Authorization: Bearer token",
        )
    finally:
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    rendered = stream.getvalue()
    assert "SI" "D=***" in rendered
    assert "at=***" in rendered
    assert "f.sid=***" in rendered
    assert "Authorization: Bearer ***" in rendered
    assert "secret" not in rendered
    assert "session" not in rendered
    assert "token" not in rendered
