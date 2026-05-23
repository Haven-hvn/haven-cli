"""Tests for the FlushQueue.

Tests cover:
- Normal serial processing (FIFO order)
- Retry with exponential backoff then success
- Retry exhaustion → dead-letter
- PermanentError → immediate dead-letter (no retries)
- Queue full raises QueueFullError
- Graceful stop waits for in-flight processing
- on_dead_letter callback invocation
- Idempotent start/stop
- processed_count and failed_count tracking
"""

import asyncio

import pytest

from haven_cli.pipeline.flush_queue import (
    FLUSH_QUEUE_BASE_DELAY,
    FLUSH_QUEUE_MAX_DEPTH,
    FLUSH_QUEUE_MAX_RETRIES,
    FlushQueue,
    PermanentError,
    QueueFullError,
)


class TestFlushQueueConstants:
    """Verify module constants."""

    def test_max_depth(self):
        assert FLUSH_QUEUE_MAX_DEPTH == 50

    def test_max_retries(self):
        assert FLUSH_QUEUE_MAX_RETRIES == 3

    def test_base_delay(self):
        assert FLUSH_QUEUE_BASE_DELAY == 2.0


class TestFlushQueueNormalProcessing:
    """Tests for successful serial FIFO processing."""

    @pytest.mark.asyncio
    async def test_processes_items_in_fifo_order(self):
        processed: list[str] = []

        async def processor(item: str) -> None:
            processed.append(item)

        queue: FlushQueue[str] = FlushQueue(processor=processor)
        await queue.start()

        await queue.enqueue("a")
        await queue.enqueue("b")
        await queue.enqueue("c")

        await asyncio.sleep(0.3)
        await queue.stop()

        assert processed == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_processed_count_increments(self):
        async def processor(item: int) -> None:
            pass

        queue: FlushQueue[int] = FlushQueue(processor=processor)
        await queue.start()

        for i in range(5):
            await queue.enqueue(i)

        await asyncio.sleep(0.3)
        await queue.stop()

        assert queue.processed_count == 5
        assert queue.failed_count == 0

    @pytest.mark.asyncio
    async def test_serial_processing_no_concurrency(self):
        """Only one item processes at a time."""
        active = 0
        max_active = 0

        async def processor(item: int) -> None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

        queue: FlushQueue[int] = FlushQueue(processor=processor)
        await queue.start()

        for i in range(5):
            await queue.enqueue(i)

        await asyncio.sleep(0.5)
        await queue.stop()

        assert max_active == 1


class TestFlushQueueRetry:
    """Tests for transient failure retry with backoff."""

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        attempts = 0

        async def processor(item: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient")

        queue: FlushQueue[str] = FlushQueue(
            processor=processor, base_delay=0.01, max_retries=3
        )
        await queue.start()
        await queue.enqueue("x")

        await asyncio.sleep(0.5)
        await queue.stop()

        assert attempts == 3
        assert queue.processed_count == 1
        assert queue.failed_count == 0
        assert queue.dead_letters == []

    @pytest.mark.asyncio
    async def test_retry_exhaustion_dead_letters(self):
        async def processor(item: str) -> None:
            raise RuntimeError("always fails")

        queue: FlushQueue[str] = FlushQueue(
            processor=processor, base_delay=0.01, max_retries=2
        )
        await queue.start()
        await queue.enqueue("doomed")

        await asyncio.sleep(0.5)
        await queue.stop()

        assert queue.processed_count == 0
        assert queue.failed_count == 1
        assert len(queue.dead_letters) == 1
        item, exc = queue.dead_letters[0]
        assert item == "doomed"
        assert "always fails" in str(exc)

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Verify delays increase exponentially."""
        timestamps: list[float] = []

        async def processor(item: str) -> None:
            timestamps.append(asyncio.get_event_loop().time())
            raise RuntimeError("fail")

        base = 0.05
        queue: FlushQueue[str] = FlushQueue(
            processor=processor, base_delay=base, max_retries=2
        )
        await queue.start()
        await queue.enqueue("x")

        await asyncio.sleep(1.0)
        await queue.stop()

        # 3 attempts total (initial + 2 retries)
        assert len(timestamps) == 3
        # First retry delay ~base_delay * 2^0 = base
        delay1 = timestamps[1] - timestamps[0]
        # Second retry delay ~base_delay * 2^1 = 2*base
        delay2 = timestamps[2] - timestamps[1]
        assert delay1 >= base * 0.8
        assert delay2 >= base * 1.6  # 2*base with tolerance


class TestFlushQueuePermanentError:
    """Tests for PermanentError immediate dead-lettering."""

    @pytest.mark.asyncio
    async def test_permanent_error_skips_retries(self):
        attempts = 0

        async def processor(item: str) -> None:
            nonlocal attempts
            attempts += 1
            raise PermanentError("not recoverable")

        queue: FlushQueue[str] = FlushQueue(
            processor=processor, base_delay=0.01, max_retries=5
        )
        await queue.start()
        await queue.enqueue("perm")

        await asyncio.sleep(0.3)
        await queue.stop()

        assert attempts == 1  # No retries
        assert queue.failed_count == 1
        assert queue.processed_count == 0
        item, exc = queue.dead_letters[0]
        assert item == "perm"
        assert isinstance(exc, PermanentError)


class TestFlushQueueBackpressure:
    """Tests for queue full behavior."""

    @pytest.mark.asyncio
    async def test_enqueue_raises_when_full(self):
        async def processor(item: int) -> None:
            await asyncio.sleep(10)  # Block forever

        queue: FlushQueue[int] = FlushQueue(
            processor=processor, max_depth=3
        )
        await queue.start()

        await queue.enqueue(1)
        await queue.enqueue(2)
        await queue.enqueue(3)

        with pytest.raises(QueueFullError):
            await queue.enqueue(4)

        await queue.stop(timeout=0.1)

    @pytest.mark.asyncio
    async def test_is_full_property(self):
        async def processor(item: int) -> None:
            await asyncio.sleep(10)

        queue: FlushQueue[int] = FlushQueue(
            processor=processor, max_depth=2
        )

        assert not queue.is_full
        await queue.enqueue(1)
        assert not queue.is_full
        await queue.enqueue(2)
        assert queue.is_full

        await queue.start()
        await queue.stop(timeout=0.1)


class TestFlushQueueLifecycle:
    """Tests for start/stop behavior."""

    @pytest.mark.asyncio
    async def test_graceful_stop_waits_for_inflight(self):
        completed = False

        async def processor(item: str) -> None:
            nonlocal completed
            await asyncio.sleep(0.1)
            completed = True

        queue: FlushQueue[str] = FlushQueue(processor=processor)
        await queue.start()
        await queue.enqueue("slow")

        # Give time for processing to start
        await asyncio.sleep(0.02)
        await queue.stop(timeout=5.0)

        assert completed

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self):
        async def processor(item: str) -> None:
            pass

        queue: FlushQueue[str] = FlushQueue(processor=processor)
        # Should not raise
        await queue.stop()

    @pytest.mark.asyncio
    async def test_start_twice_is_noop(self):
        async def processor(item: str) -> None:
            pass

        queue: FlushQueue[str] = FlushQueue(processor=processor)
        await queue.start()
        task1 = queue._task
        await queue.start()
        task2 = queue._task

        assert task1 is task2
        await queue.stop()

    @pytest.mark.asyncio
    async def test_is_running_property(self):
        async def processor(item: str) -> None:
            pass

        queue: FlushQueue[str] = FlushQueue(processor=processor)
        assert not queue.is_running

        await queue.start()
        assert queue.is_running

        await queue.stop()
        assert not queue.is_running


class TestFlushQueueDeadLetterCallback:
    """Tests for on_dead_letter callback."""

    @pytest.mark.asyncio
    async def test_callback_invoked_on_dead_letter(self):
        callback_items: list[tuple[str, Exception]] = []

        async def on_dl(item: str, exc: Exception) -> None:
            callback_items.append((item, exc))

        async def processor(item: str) -> None:
            raise PermanentError("nope")

        queue: FlushQueue[str] = FlushQueue(
            processor=processor,
            on_dead_letter=on_dl,
            base_delay=0.01,
        )
        await queue.start()
        await queue.enqueue("cb_test")

        await asyncio.sleep(0.3)
        await queue.stop()

        assert len(callback_items) == 1
        assert callback_items[0][0] == "cb_test"

    @pytest.mark.asyncio
    async def test_callback_error_does_not_crash_queue(self):
        async def bad_callback(item: str, exc: Exception) -> None:
            raise ValueError("callback exploded")

        async def processor(item: str) -> None:
            raise PermanentError("fail")

        queue: FlushQueue[str] = FlushQueue(
            processor=processor,
            on_dead_letter=bad_callback,
            base_delay=0.01,
        )
        await queue.start()
        await queue.enqueue("a")
        await queue.enqueue("b")

        await asyncio.sleep(0.5)
        await queue.stop()

        # Both items should be dead-lettered despite callback errors
        assert queue.failed_count == 2
