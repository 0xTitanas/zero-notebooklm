"""Stdlib asyncio boundary for NotebookLM Bare Phase 1.

The transport runs blocking callables outside the event loop and provides a
small close/drain boundary. It intentionally contains no NotebookLM-specific
network, auth, cookie, or RPC behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from .errors import TransportClosedError, TransportTimeoutError

T = TypeVar("T")


class AsyncTransport:
    """Run blocking callables through a bounded stdlib executor."""

    def __init__(self, *, max_workers: int | None = None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="notebooklm-bare"
        )
        self._closed = False
        self._tasks: set[asyncio.Task[object]] = set()

    def _submit(self, func: Callable[..., T], *args, **kwargs) -> Future[T]:
        if self._closed:
            raise TransportClosedError("async transport is closed")
        return self._executor.submit(func, *args, **kwargs)

    async def _await_future(self, future: Future[T], timeout: float | None) -> T:
        wrapped = asyncio.wrap_future(future)
        try:
            if timeout is None:
                return await wrapped
            return await asyncio.wait_for(wrapped, timeout=timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise TransportTimeoutError(
                f"blocking operation exceeded {timeout} seconds"
            ) from exc
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def run(
        self, func: Callable[..., T], *args, timeout: float | None = None, **kwargs
    ) -> T:
        """Run ``func`` off the event loop, optionally with a timeout."""

        future = self._submit(func, *args, **kwargs)
        return await self._await_future(future, timeout)

    def start(
        self, func: Callable[..., T], *args, timeout: float | None = None, **kwargs
    ) -> asyncio.Task[T]:
        """Schedule ``func`` immediately and return a cancellable task handle."""

        future = self._submit(func, *args, **kwargs)
        task: asyncio.Task[T] = asyncio.create_task(self._await_future(future, timeout))
        self._tasks.add(task)  # type: ignore[arg-type]
        task.add_done_callback(lambda done: self._tasks.discard(done))  # type: ignore[arg-type]
        return task

    async def drain(self) -> None:
        """Allow already-created tasks to settle without raising their results."""

        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Close the transport and release executor resources.

        Running threadpool callables cannot be forcibly killed by stdlib asyncio;
        callers should use cooperative callables for cancellation-sensitive work.
        """

        if self._closed:
            return
        self._closed = True
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        await asyncio.sleep(0)
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def __aenter__(self) -> "AsyncTransport":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


__all__ = ["AsyncTransport"]
