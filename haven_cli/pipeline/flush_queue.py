"""Generic async flush queue for serial background processing with retry.

Processes work items one at a time (FIFO) in a background asyncio task.
Transient failures are retried with exponential backoff. Permanently failed
items are dead-lettered. Provides backpressure via queue depth limits.

Modeled on lmstudio-bridge flush-queue pattern.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

FLUSH_QUEUE_MAX_DEPTH = 50
FLUSH_QUEUE_MAX_RETRIES = 3
FLUSH_QUEUE_BASE_DELAY = 2.0  # seconds


class QueueFullError(Exception):
    """Raised when enqueue is called on a full queue (backpressure)."""

    pass


class PermanentError(Exception):
    """Raised by processor to skip retries and dead-letter immediately."""

    pass


class FlushQueue(Generic[T]):
    """Serial background processor with retry, backoff, and dead-lettering.

    Args:
        processor: Async callable that processes a single item.
        max_depth: Maximum queue depth before backpressure (QueueFullError).
        max_retries: Number of retry attempts for transient failures.
        base_delay: Base delay in seconds for exponential backoff.
        on_dead_letter: Optional callback invoked when an item is dead-lettered.
    """

    def __init__(
        self,
        processor: Callable[[T], Awaitable[None]],
        max_depth: int = FLUSH_QUEUE_MAX_DEPTH,
        max_retries: int = FLUSH_QUEUE_MAX_RETRIES,
        base_delay: float = FLUSH_QUEUE_BASE_DELAY,
        on_dead_letter: Callable[[T, Exception], Awaitable[None]] | None = None,
    ) -> None:
        self._processor = processor
        self._max_depth = max_depth
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._on_dead_letter = on_dead_letter

        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._dead_letters: list[tuple[T, Exception]] = []
        self._processed_count = 0
        self._failed_count = 0

    async def enqueue(self, item: T) -> None:
        """Add an item to the processing queue.

        Raises:
            QueueFullError: If queue depth >= max_depth.
        """
        if self.depth >= self._max_depth:
            raise QueueFullError(
                f"Queue full ({self.depth}/{self._max_depth})"
            )
        self._queue.put_nowait(item)

    async def start(self) -> None:
        """Start the background processing loop. No-op if already running."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self, timeout: float = 15.0) -> None:
        """Signal stop and wait for in-flight processing to complete.

        Args:
            timeout: Max seconds to wait for in-flight item before cancelling.
        """
        if self._task is None or self._task.done():
            return
        self._stopping = True
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    @property
    def depth(self) -> int:
        """Current number of items waiting in the queue."""
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        """Whether the queue is at capacity."""
        return self.depth >= self._max_depth

    @property
    def is_running(self) -> bool:
        """Whether the background processing loop is active."""
        return self._task is not None and not self._task.done()

    @property
    def dead_letters(self) -> list[tuple[T, Exception]]:
        """Items that permanently failed processing."""
        return list(self._dead_letters)

    @property
    def processed_count(self) -> int:
        """Lifetime count of successfully processed items."""
        return self._processed_count

    @property
    def failed_count(self) -> int:
        """Lifetime count of dead-lettered items."""
        return self._failed_count

    async def _run(self) -> None:
        """Background loop: dequeue and process items serially."""
        while True:
            if self._stopping and self._queue.empty():
                break
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            await self._process_item(item)

    async def _process_item(self, item: T) -> None:
        """Process a single item with retry logic."""
        for attempt in range(self._max_retries + 1):
            try:
                await self._processor(item)
                self._processed_count += 1
                return
            except PermanentError as exc:
                logger.error("Permanent failure, dead-lettering: %s", exc)
                await self._dead_letter(item, exc)
                return
            except Exception as exc:
                if attempt >= self._max_retries:
                    logger.error(
                        "Retries exhausted (%d/%d), dead-lettering: %s",
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    await self._dead_letter(item, exc)
                    return

                delay = self._base_delay * (2**attempt)
                logger.warning(
                    "Transient error (attempt %d/%d, retry in %.1fs): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

    async def _dead_letter(self, item: T, exc: Exception) -> None:
        """Move an item to the dead-letter store."""
        self._dead_letters.append((item, exc))
        self._failed_count += 1
        if self._on_dead_letter is not None:
            try:
                await self._on_dead_letter(item, exc)
            except Exception as cb_exc:
                logger.warning("on_dead_letter callback error: %s", cb_exc)
