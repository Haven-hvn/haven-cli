"""Tests for the batch accumulator.

Tests the BatchAccumulator including:
- Full batch flush when buffer reaches batch_size
- Timeout-based partial flush
- Drain returns all pending immediately
- Backpressure advisory signal
- Empty flush returns empty list
- Adding beyond max_pending still works (advisory only)
"""

import asyncio
from pathlib import Path

import pytest

from haven_cli.pipeline.batch_accumulator import (
    BATCH_FLUSH_TIMEOUT_SECONDS,
    DEFAULT_BATCH_SIZE,
    BatchAccumulator,
)
from haven_cli.pipeline.context import PipelineContext


def _make_context(index: int = 0) -> PipelineContext:
    """Create a minimal PipelineContext for testing."""
    return PipelineContext(source_path=Path(f"/tmp/video{index}.mp4"))


class TestBatchAccumulatorConstants:
    """Verify module constants."""

    def test_default_batch_size(self):
        assert DEFAULT_BATCH_SIZE == 10

    def test_flush_timeout(self):
        assert BATCH_FLUSH_TIMEOUT_SECONDS == 30.0


class TestBatchAccumulatorFullFlush:
    """Tests for flushing when batch_size is reached."""

    @pytest.mark.asyncio
    async def test_flush_returns_exact_batch_size(self):
        """add() batch_size items then flush() returns exactly that many."""
        acc = BatchAccumulator(batch_size=3, flush_timeout=5.0)
        for i in range(3):
            await acc.add(_make_context(i))

        batch = await acc.flush()
        assert len(batch) == 3
        assert acc.pending_count == 0

    @pytest.mark.asyncio
    async def test_flush_returns_batch_size_when_overfilled(self):
        """If buffer has more than batch_size, flush returns only batch_size."""
        acc = BatchAccumulator(batch_size=2, flush_timeout=5.0)
        for i in range(5):
            await acc.add(_make_context(i))

        batch = await acc.flush()
        assert len(batch) == 2
        assert acc.pending_count == 3

    @pytest.mark.asyncio
    async def test_flush_returns_immutable_snapshot(self):
        """Returned batch is a copy — mutating it doesn't affect accumulator."""
        acc = BatchAccumulator(batch_size=2, flush_timeout=5.0)
        for i in range(2):
            await acc.add(_make_context(i))

        batch = await acc.flush()
        batch.append(_make_context(99))
        assert acc.pending_count == 0

    @pytest.mark.asyncio
    async def test_is_full_property(self):
        """is_full is True when buffer >= batch_size."""
        acc = BatchAccumulator(batch_size=2, flush_timeout=5.0)
        assert acc.is_full is False
        await acc.add(_make_context(0))
        assert acc.is_full is False
        await acc.add(_make_context(1))
        assert acc.is_full is True


class TestBatchAccumulatorTimeoutFlush:
    """Tests for timeout-based partial flushing."""

    @pytest.mark.asyncio
    async def test_partial_flush_after_timeout(self):
        """flush() returns partial batch after timeout when buffer < batch_size."""
        acc = BatchAccumulator(batch_size=10, flush_timeout=0.05)
        await acc.add(_make_context(0))

        batch = await acc.flush()
        assert len(batch) == 1
        assert acc.pending_count == 0

    @pytest.mark.asyncio
    async def test_empty_flush_returns_empty_list(self):
        """flush() on empty buffer returns [] after timeout."""
        acc = BatchAccumulator(batch_size=10, flush_timeout=0.05)

        batch = await acc.flush()
        assert batch == []
        assert acc.pending_count == 0


class TestBatchAccumulatorDrain:
    """Tests for drain() shutdown behavior."""

    @pytest.mark.asyncio
    async def test_drain_returns_all_pending(self):
        """drain() returns everything immediately."""
        acc = BatchAccumulator(batch_size=10, flush_timeout=30.0)
        for i in range(7):
            await acc.add(_make_context(i))

        batch = await acc.drain()
        assert len(batch) == 7
        assert acc.pending_count == 0

    @pytest.mark.asyncio
    async def test_drain_empty_buffer(self):
        """drain() on empty buffer returns []."""
        acc = BatchAccumulator(batch_size=10, flush_timeout=30.0)
        batch = await acc.drain()
        assert batch == []


class TestBatchAccumulatorBackpressure:
    """Tests for backpressure advisory signal."""

    @pytest.mark.asyncio
    async def test_backpressure_false_below_threshold(self):
        """has_backpressure is False when below max_pending."""
        acc = BatchAccumulator(batch_size=5, flush_timeout=5.0, max_pending=10)
        for i in range(9):
            await acc.add(_make_context(i))
        assert acc.has_backpressure is False

    @pytest.mark.asyncio
    async def test_backpressure_true_at_threshold(self):
        """has_backpressure is True when pending_count >= max_pending."""
        acc = BatchAccumulator(batch_size=5, flush_timeout=5.0, max_pending=10)
        for i in range(10):
            await acc.add(_make_context(i))
        assert acc.has_backpressure is True

    @pytest.mark.asyncio
    async def test_add_beyond_max_pending_still_works(self):
        """Backpressure is advisory — adding beyond max_pending does not block."""
        acc = BatchAccumulator(batch_size=5, flush_timeout=5.0, max_pending=3)
        for i in range(10):
            await acc.add(_make_context(i))
        assert acc.pending_count == 10
        assert acc.has_backpressure is True


class TestBatchAccumulatorConcurrency:
    """Tests for concurrent add + flush interaction."""

    @pytest.mark.asyncio
    async def test_flush_unblocks_when_batch_size_reached(self):
        """A waiting flush() returns once enough items are added concurrently."""
        acc = BatchAccumulator(batch_size=3, flush_timeout=5.0)

        async def add_items():
            await asyncio.sleep(0.01)
            for i in range(3):
                await acc.add(_make_context(i))

        flush_task = asyncio.create_task(acc.flush())
        add_task = asyncio.create_task(add_items())

        batch = await asyncio.wait_for(flush_task, timeout=2.0)
        await add_task
        assert len(batch) == 3
