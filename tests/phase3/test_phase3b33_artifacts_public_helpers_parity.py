"""Public artifacts helper parity for rate-limit retry utilities."""

from __future__ import annotations

import asyncio

import pytest


EXPECTED_ARTIFACTS_ALL = [
    "RATE_LIMIT_RETRY_BACKOFF_MULTIPLIER",
    "RATE_LIMIT_RETRY_INITIAL_DELAY",
    "RATE_LIMIT_RETRY_MAX_DELAY",
    "RateLimitRetryEvent",
    "calculate_backoff_delay",
    "with_rate_limit_retry",
]


def test_artifacts_module_public_surface_matches_upstream():
    from notebooklm import artifacts

    assert artifacts.__all__ == EXPECTED_ARTIFACTS_ALL
    assert not hasattr(artifacts, "ArtifactsAPI")
    assert not hasattr(artifacts, "ArtifactStatus")


def test_calculate_backoff_delay_matches_upstream_validation():
    from notebooklm import artifacts

    assert artifacts.calculate_backoff_delay(0) == 60.0
    assert artifacts.calculate_backoff_delay(2) == 240.0
    assert artifacts.calculate_backoff_delay(20) == 300.0

    for value in (-1, True, 1.5):
        with pytest.raises(ValueError, match="attempt must be a non-negative integer"):
            artifacts.calculate_backoff_delay(value)  # type: ignore[arg-type]


def test_with_rate_limit_retry_retries_returned_rate_limit_status():
    from notebooklm import artifacts
    from notebooklm.types import GenerationStatus

    async def scenario():
        attempts = 0
        sleeps: list[float] = []
        events: list[artifacts.RateLimitRetryEvent] = []
        limited = GenerationStatus(
            task_id="task-1",
            status="failed",
            error_code="USER_DISPLAYABLE_ERROR",
        )
        completed = GenerationStatus(task_id="task-1", status="completed")

        async def generate():
            nonlocal attempts
            attempts += 1
            return limited if attempts == 1 else completed

        async def sleep(delay: float):
            sleeps.append(delay)

        result = await artifacts.with_rate_limit_retry(
            generate,
            max_retries=2,
            sleep=sleep,
            on_retry=events.append,
        )

        assert result is completed
        assert attempts == 2
        assert sleeps == [60.0]
        assert len(events) == 1
        assert events[0].result is limited
        assert events[0].next_attempt_number == 2
        assert events[0].total_attempts == 3
        assert events[0].retry_number == 1
        assert events[0].max_retries == 2
        assert events[0].delay == 60.0

    asyncio.run(scenario())
