"""Batch accumulator for collecting pipeline contexts before flush.

Buffers completed PipelineContext objects after upload and signals when
a batch is ready for downstream processing (attestation + entity creation).
Supports configurable batch size, timeout-based partial flushing, explicit
drain on shutdown, and advisory backpressure signaling.
"""

import asyncio
import logging
from typing import List

from haven_cli.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 10
BATCH_FLUSH_TIMEOUT_SECONDS = 30.0


class BatchAccumulator:
    """Accumulates pipeline contexts and produces batches for processing.

    Items are added via `add()`. A batch becomes ready when either:
    - The buffer reaches `batch_size` items, or
    - `flush_timeout` seconds elapse with a non-empty buffer.

    `drain()` returns all pending items immediately for shutdown scenarios.
    `has_backpressure` is advisory — callers should slow down but are not blocked.
    """

    def __init__(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_timeout: float = BATCH_FLUSH_TIMEOUT_SECONDS,
        max_pending: int = 50,
    ) -> None:
        self._batch_size = batch_size
        self._flush_timeout = flush_timeout
        self._max_pending = max_pending
        self._buffer: List[PipelineContext] = []
        self._ready = asyncio.Event()

    async def add(self, context: PipelineContext) -> None:
        """Append a context to the buffer. Signals ready if batch_size reached."""
        self._buffer.append(context)
        logger.debug(
            "Added context %s to accumulator (%d/%d)",
            context.context_id,
            len(self._buffer),
            self._batch_size,
        )
        if len(self._buffer) >= self._batch_size:
            self._ready.set()

    async def flush(self) -> List[PipelineContext]:
        """Wait until a batch is ready (size or timeout), then return and clear it.

        Returns an empty list if nothing is pending and the timeout expires.
        """
        if len(self._buffer) >= self._batch_size:
            return self._take_batch()

        if not self._buffer:
            # Wait for at least one item or timeout
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=self._flush_timeout)
            except asyncio.TimeoutError:
                return []
            return self._take_batch()

        # Have items but not enough — wait for batch_size or timeout
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._flush_timeout)
        except asyncio.TimeoutError:
            pass

        return self._take_batch()

    async def drain(self) -> List[PipelineContext]:
        """Return all pending contexts immediately. Clears the buffer."""
        batch = self._buffer[:]
        self._buffer.clear()
        self._ready.clear()
        logger.debug("Drained %d contexts from accumulator", len(batch))
        return batch

    @property
    def pending_count(self) -> int:
        """Number of items currently buffered."""
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """True when buffer has reached batch_size."""
        return len(self._buffer) >= self._batch_size

    @property
    def has_backpressure(self) -> bool:
        """True when pending_count >= max_pending. Advisory signal."""
        return len(self._buffer) >= self._max_pending

    def _take_batch(self) -> List[PipelineContext]:
        """Snapshot up to batch_size items, clear them from buffer."""
        batch = self._buffer[: self._batch_size]
        self._buffer = self._buffer[self._batch_size :]
        self._ready.clear()
        if len(self._buffer) >= self._batch_size:
            self._ready.set()
        logger.debug("Flushed batch of %d contexts", len(batch))
        return batch
