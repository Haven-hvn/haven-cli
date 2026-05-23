"""Integration tests for the batched upload pipeline.

Validates end-to-end flow: accumulate → flush → attest → entity creation.
All external services (ICP canister, FOC upload, Arkiv RPC) are mocked.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haven_cli.pipeline.batch_accumulator import BatchAccumulator
from haven_cli.pipeline.batch_sync import BatchSyncProcessor, HAVEN_AOL_MAX_PER_CALL
from haven_cli.pipeline.context import (
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.flush_queue import FlushQueue, PermanentError


def _make_config(enabled: bool = True):
    from haven_cli.services.arkiv_sync import ArkivSyncConfig

    return ArkivSyncConfig(
        enabled=enabled,
        private_key="0x" + "a" * 64,
        rpc_url="http://localhost:8545",
    )


def _make_context(
    index: int = 0,
    encrypted: bool = False,
) -> PipelineContext:
    ctx = PipelineContext(source_path=Path(f"/tmp/video{index}.mp4"))
    ctx.upload_result = UploadResult(
        video_path=f"/tmp/video{index}.mp4",
        root_cid=f"bafytest{index:04d}",
        piece_cid=f"bafkzcib{index:04d}",
    )
    if encrypted:
        ctx.encryption_metadata = EncryptionMetadata(
            gate={
                "version": 1,
                "cid": f"bafytest{index:04d}",
                "chain": "ethereum",
                "tokenAddress": "0x" + "b" * 40,
                "threshold": "1",
                "encryptedAesKey": "abc123",
            }
        )
    return ctx


class TestHappyPathTenFiles:
    """Scenario 1: 10 files through batched pipeline."""

    @pytest.mark.asyncio
    async def test_ten_files_batch_flush_and_sync(self):
        accumulator = BatchAccumulator(batch_size=10)
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )

        contexts = [_make_context(i, encrypted=True) for i in range(10)]
        for ctx in contexts:
            await accumulator.add(ctx)

        batch = await accumulator.flush()
        assert len(batch) == 10

        mock_attestations = [
            {"balanceAtCheck": "100", "signature": f"sig{i}"} for i in range(10)
        ]

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", return_value=mock_attestations), \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"entity_{i}", "transaction_hash": "0xabc"}
                for i in range(10)
            ]
            await processor(batch)

        assert processor.processed_batches == 1
        assert processor.total_entities_created == 10
        for i, ctx in enumerate(batch):
            assert ctx.attestation == mock_attestations[i]
            assert ctx.arkiv_entity_key == f"entity_{i}"


class TestPartialBatchTimeoutFlush:
    """Scenario 2: 3 files with timeout flush (batch_size=10)."""

    @pytest.mark.asyncio
    async def test_partial_flush_on_timeout(self):
        accumulator = BatchAccumulator(batch_size=10, flush_timeout=0.05)
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )

        contexts = [_make_context(i) for i in range(3)]
        for ctx in contexts:
            await accumulator.add(ctx)

        # Timeout triggers partial flush
        batch = await accumulator.flush()
        assert len(batch) == 3

        with patch.object(processor, "_create_entities") as mock_create:
            mock_create.return_value = [
                {"entity_key": f"entity_{i}", "transaction_hash": "0xdef"}
                for i in range(3)
            ]
            await processor(batch)

        assert processor.total_entities_created == 3
        for i, ctx in enumerate(batch):
            assert ctx.arkiv_entity_key == f"entity_{i}"


class TestMixedContentBatch:
    """Scenario 3: encrypted + non-encrypted in same batch."""

    @pytest.mark.asyncio
    async def test_only_encrypted_get_attestation(self):
        accumulator = BatchAccumulator(batch_size=10, flush_timeout=0.05)
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )

        # 5 encrypted + 5 non-encrypted
        contexts = []
        for i in range(10):
            contexts.append(_make_context(i, encrypted=(i < 5)))
        for ctx in contexts:
            await accumulator.add(ctx)

        batch = await accumulator.flush()
        assert len(batch) == 10

        mock_attestations = [
            {"balanceAtCheck": "100", "signature": f"sig{i}"} for i in range(5)
        ]

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", return_value=mock_attestations) as mock_attest, \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"entity_{i}", "transaction_hash": "0xabc"}
                for i in range(10)
            ]
            await processor(batch)

            # Only 5 encrypted items attested
            call_args = mock_attest.call_args
            assert len(call_args.kwargs["cid_hashes"]) == 5

        # Non-encrypted have no attestation
        for ctx in batch[5:]:
            assert ctx.attestation is None
        # Encrypted have attestation
        for i, ctx in enumerate(batch[:5]):
            assert ctx.attestation == mock_attestations[i]
        # All have entity keys
        for i, ctx in enumerate(batch):
            assert ctx.arkiv_entity_key == f"entity_{i}"


class TestLargeBatchAttestationChunking:
    """Scenario 4: 25 encrypted contexts → chunked attestation (20 + 5)."""

    @pytest.mark.asyncio
    async def test_attestation_chunked_at_max_per_call(self):
        accumulator = BatchAccumulator(batch_size=25, flush_timeout=0.05)
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )

        contexts = [_make_context(i, encrypted=True) for i in range(25)]
        for ctx in contexts:
            await accumulator.add(ctx)

        batch = await accumulator.flush()
        assert len(batch) == 25

        def mock_attest_fn(**kwargs):
            return [{"balanceAtCheck": "1", "signature": "s"}] * len(kwargs["cid_hashes"])

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", side_effect=mock_attest_fn) as mock_attest, \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"entity_{i}", "transaction_hash": "0xabc"}
                for i in range(25)
            ]
            await processor(batch)

            assert mock_attest.call_count == 2
            first_chunk = mock_attest.call_args_list[0].kwargs["cid_hashes"]
            second_chunk = mock_attest.call_args_list[1].kwargs["cid_hashes"]
            assert len(first_chunk) == 20
            assert len(second_chunk) == 5

        # Entity creation called once with all 25
        mock_create.assert_called_once_with(batch)


class TestFlushQueueRetryOnTransientError:
    """Scenario 5: FlushQueue retries on transient network error."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        call_count = 0

        async def flaky_processor(batch):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network timeout")
            # Success on retry

        queue: FlushQueue = FlushQueue(
            processor=flaky_processor,
            max_retries=3,
            base_delay=0.01,
        )
        await queue.start()

        await queue.enqueue([_make_context(0)])

        # Wait for processing
        await asyncio.sleep(0.2)
        await queue.stop()

        assert call_count == 2
        assert queue.processed_count == 1
        assert queue.failed_count == 0


class TestFlushQueueDeadLetterOnPermanentError:
    """Scenario 6: FlushQueue dead-letters on PermanentError."""

    @pytest.mark.asyncio
    async def test_permanent_error_goes_to_dead_letter(self):
        dead_letter_items = []

        async def failing_processor(batch):
            raise PermanentError("invalid config")

        async def on_dead_letter(item, exc):
            dead_letter_items.append((item, exc))

        queue: FlushQueue = FlushQueue(
            processor=failing_processor,
            max_retries=3,
            base_delay=0.01,
            on_dead_letter=on_dead_letter,
        )
        await queue.start()

        batch = [_make_context(0)]
        await queue.enqueue(batch)

        await asyncio.sleep(0.1)
        await queue.stop()

        assert queue.failed_count == 1
        assert queue.processed_count == 0
        assert len(dead_letter_items) == 1
        assert dead_letter_items[0][0] is batch
        assert "invalid config" in str(dead_letter_items[0][1])


class TestGracefulShutdownDrainAndStop:
    """Scenario 7: Drain accumulator + stop queue on shutdown."""

    @pytest.mark.asyncio
    async def test_drain_and_process_before_stop(self):
        accumulator = BatchAccumulator(batch_size=10, flush_timeout=60.0)
        processed_batches = []

        async def track_processor(batch):
            processed_batches.append(batch)

        queue: FlushQueue = FlushQueue(processor=track_processor)
        await queue.start()

        # Add 4 items (batch_size=10, won't auto-flush)
        contexts = [_make_context(i) for i in range(4)]
        for ctx in contexts:
            await accumulator.add(ctx)

        assert accumulator.pending_count == 4

        # Drain returns all 4 immediately
        drained = await accumulator.drain()
        assert len(drained) == 4
        assert accumulator.pending_count == 0

        # Enqueue drained batch
        await queue.enqueue(drained)

        # Wait for processing then stop
        await asyncio.sleep(0.1)
        await queue.stop()

        assert len(processed_batches) == 1
        assert len(processed_batches[0]) == 4


class TestVetKDCacheSingleCall:
    """Scenario 8: VetKD cache ensures single ICP call for multiple encryptions."""

    @pytest.mark.asyncio
    async def test_vetkd_fetched_once_for_multiple_files(self):
        import sys
        import haven_cli.crypto.haven_aol_local as aol_local

        # Reset the cache
        original_cache = aol_local._cached_derived_public_key
        aol_local._cached_derived_public_key = None

        # Mock vetkd_py as a module (it's imported locally inside functions)
        mock_vetkd = MagicMock()
        mock_vetkd.deserialize_derived_public_key.return_value = b"\x01" * 48
        mock_vetkd.ibe_encrypt.return_value = b"\x02" * 32

        try:
            with patch("haven_cli.crypto.haven_aol_local.get_vetkd_public_key_b64") as mock_fetch, \
                 patch.dict(sys.modules, {"vetkd_py": mock_vetkd}):
                # Return a fake b64 key
                mock_fetch.return_value = "AAAA"  # valid base64

                # Call _ibe_encrypt_aes_key 5 times
                for _ in range(5):
                    aol_local._ibe_encrypt_aes_key(
                        aes_key=b"\x00" * 32,
                        derivation_input=b"test_input",
                    )

                # get_vetkd_public_key_b64 called exactly once (cached after first)
                mock_fetch.assert_called_once()
        finally:
            aol_local._cached_derived_public_key = original_cache


class TestCleanupTimingBeforeSync:
    """Scenario 9: Files deleted before batch sync runs."""

    @pytest.mark.asyncio
    async def test_cleanup_runs_before_sync(self):
        """In batched pipeline, cleanup is in upload-phase (before sync)."""
        from haven_cli.pipeline.manager import PipelineBuilder

        # Verify step order: cleanup comes before sync would
        pipeline = PipelineBuilder().with_upload_phase_steps().build()
        step_names = pipeline.step_names

        assert "cleanup" in step_names
        assert "sync" not in step_names  # sync is handled by BatchSyncProcessor

        # Verify cleanup is last step in upload phase
        assert step_names[-1] == "cleanup"

        # Verify batch sync works without local file
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        ctx = _make_context(0)
        # Simulate file already deleted by cleanup
        # (source_path doesn't exist — batch sync doesn't need it)

        with patch.object(processor, "_create_entities") as mock_create:
            mock_create.return_value = [
                {"entity_key": "entity_0", "transaction_hash": "0xabc"}
            ]
            await processor([ctx])

        assert ctx.arkiv_entity_key == "entity_0"
