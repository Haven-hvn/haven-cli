"""Tests for BatchSyncProcessor and create_batched_pipeline."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haven_cli.pipeline.batch_sync import BatchSyncProcessor
from haven_cli.pipeline.context import (
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.flush_queue import PermanentError
from haven_cli.services.arkiv_sync import ArkivSyncConfig


def _make_config(enabled: bool = True) -> ArkivSyncConfig:
    return ArkivSyncConfig(
        enabled=enabled,
        private_key="0x" + "a" * 64,
        rpc_url="http://localhost:8545",
    )


def _make_context(
    index: int = 0,
    encrypted: bool = False,
    video_id: int | None = None,
) -> PipelineContext:
    ctx = PipelineContext(source_path=Path(f"/tmp/video{index}.mp4"))
    ctx.upload_result = UploadResult(
        video_path=f"/tmp/video{index}.mp4",
        root_cid=f"bafytest{index}",
        piece_cid=f"bafkzcib{index}",
    )
    ctx.video_id = video_id
    if encrypted:
        ctx.encryption_metadata = EncryptionMetadata(
            gate={
                "version": 1,
                "cid": f"bafytest{index}",
                "chain": "ethereum",
                "tokenAddress": "0x" + "b" * 40,
                "threshold": "1",
                "encryptedAesKey": "abc123",
            }
        )
    return ctx


class TestBatchSyncProcessor:
    """Tests for BatchSyncProcessor."""

    @pytest.mark.asyncio
    async def test_empty_batch_is_noop(self):
        processor = BatchSyncProcessor(arkiv_config=_make_config())
        await processor([])
        assert processor.processed_batches == 0

    @pytest.mark.asyncio
    async def test_disabled_config_raises_permanent_error(self):
        processor = BatchSyncProcessor(arkiv_config=_make_config(enabled=False))
        with pytest.raises(PermanentError, match="disabled"):
            await processor([_make_context()])

    @pytest.mark.asyncio
    async def test_no_private_key_raises_permanent_error(self):
        config = ArkivSyncConfig(enabled=True, private_key=None, rpc_url="http://x")
        processor = BatchSyncProcessor(arkiv_config=config, private_key="")
        with pytest.raises(PermanentError, match="No private key"):
            await processor([_make_context()])

    @pytest.mark.asyncio
    async def test_non_gated_batch_skips_attestation(self):
        """Non-encrypted contexts should not call batch_attest_holding."""
        processor = BatchSyncProcessor(arkiv_config=_make_config())
        contexts = [_make_context(i) for i in range(3)]

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding") as mock_attest:
            mock_create.return_value = [
                {"entity_key": f"key_{i}", "transaction_hash": "0xabc"}
                for i in range(3)
            ]
            await processor(contexts)

            mock_attest.assert_not_called()
            mock_create.assert_called_once_with(contexts)

        assert processor.processed_batches == 1
        assert processor.total_entities_created == 3
        for i, ctx in enumerate(contexts):
            assert ctx.arkiv_entity_key == f"key_{i}"

    @pytest.mark.asyncio
    async def test_gated_batch_calls_attestation(self):
        """Encrypted contexts should trigger batch_attest_holding."""
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        contexts = [_make_context(i, encrypted=True) for i in range(3)]

        mock_attestations = [
            {"balanceAtCheck": "100", "signature": f"sig{i}"} for i in range(3)
        ]

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", return_value=mock_attestations) as mock_attest, \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"key_{i}", "transaction_hash": "0xabc"}
                for i in range(3)
            ]
            await processor(contexts)

            mock_attest.assert_called_once()
            # Verify attestations were assigned
            for i, ctx in enumerate(contexts):
                assert ctx.attestation == mock_attestations[i]

    @pytest.mark.asyncio
    async def test_mixed_batch_only_attests_gated(self):
        """Mixed batch: only encrypted items get attestation."""
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        contexts = [
            _make_context(0, encrypted=True),
            _make_context(1, encrypted=False),
            _make_context(2, encrypted=True),
        ]

        mock_attestations = [
            {"balanceAtCheck": "100", "signature": "sig0"},
            {"balanceAtCheck": "100", "signature": "sig2"},
        ]

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", return_value=mock_attestations) as mock_attest, \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"key_{i}", "transaction_hash": "0xabc"}
                for i in range(3)
            ]
            await processor(contexts)

            # Only 2 gated items attested
            call_args = mock_attest.call_args
            assert len(call_args.kwargs["cid_hashes"]) == 2
            # Non-gated has no attestation
            assert contexts[1].attestation is None

    @pytest.mark.asyncio
    async def test_attestation_chunking_over_20(self):
        """Batches > 20 gated items should chunk attestation calls."""
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        contexts = [_make_context(i, encrypted=True) for i in range(25)]

        def mock_attest_fn(**kwargs):
            return [{"balanceAtCheck": "1", "signature": "s"}] * len(kwargs["cid_hashes"])

        with patch.object(processor, "_create_entities") as mock_create, \
             patch("haven_cli.pipeline.batch_sync.batch_attest_holding", side_effect=mock_attest_fn) as mock_attest, \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            mock_create.return_value = [
                {"entity_key": f"key_{i}", "transaction_hash": "0xabc"}
                for i in range(25)
            ]
            await processor(contexts)

            # Should be called twice: 20 + 5
            assert mock_attest.call_count == 2
            first_call = mock_attest.call_args_list[0].kwargs["cid_hashes"]
            second_call = mock_attest.call_args_list[1].kwargs["cid_hashes"]
            assert len(first_call) == 20
            assert len(second_call) == 5

    @pytest.mark.asyncio
    async def test_attestation_value_error_raises_permanent(self):
        """ValueError from batch_attest_holding → PermanentError."""
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        contexts = [_make_context(0, encrypted=True)]

        with patch("haven_cli.pipeline.batch_sync.batch_attest_holding", side_effect=ValueError("bad hex")), \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            with pytest.raises(PermanentError, match="config error"):
                await processor(contexts)

    @pytest.mark.asyncio
    async def test_transient_attestation_error_propagates(self):
        """Network errors from attestation propagate for retry."""
        processor = BatchSyncProcessor(
            arkiv_config=_make_config(), private_key="0x" + "a" * 64
        )
        contexts = [_make_context(0, encrypted=True)]

        with patch("haven_cli.pipeline.batch_sync.batch_attest_holding", side_effect=RuntimeError("timeout")), \
             patch("haven_cli.pipeline.batch_sync.get_wallet_address_from_private_key", return_value="0x1234"):
            with pytest.raises(RuntimeError, match="timeout"):
                await processor(contexts)

    @pytest.mark.asyncio
    async def test_entity_creation_permanent_error(self):
        """Invalid config in entity creation → PermanentError."""
        processor = BatchSyncProcessor(arkiv_config=_make_config())
        contexts = [_make_context(0)]

        with patch.object(processor, "_create_entities", side_effect=Exception("unauthorized access")):
            with pytest.raises(PermanentError, match="config error"):
                await processor(contexts)

    @pytest.mark.asyncio
    async def test_entity_creation_transient_error_propagates(self):
        """Network errors from entity creation propagate for retry."""
        processor = BatchSyncProcessor(arkiv_config=_make_config())
        contexts = [_make_context(0)]

        with patch.object(processor, "_create_entities", side_effect=Exception("connection refused")):
            with pytest.raises(Exception, match="connection refused"):
                await processor(contexts)

    @pytest.mark.asyncio
    async def test_database_update_on_success(self):
        """Database should be updated after successful entity creation."""
        processor = BatchSyncProcessor(arkiv_config=_make_config())
        ctx = _make_context(0, video_id=42)

        with patch.object(processor, "_create_entities") as mock_create, \
             patch.object(processor, "_update_database") as mock_db:
            mock_create.return_value = [{"entity_key": "key_0", "transaction_hash": "0x1"}]
            mock_db.return_value = None
            await processor([ctx])

            mock_db.assert_called_once_with(ctx, "key_0")


class TestCreateBatchedPipeline:
    """Tests for create_batched_pipeline factory."""

    def test_returns_tuple_of_three(self):
        from haven_cli.pipeline.manager import create_batched_pipeline

        with patch.dict("os.environ", {"HAVEN_PRIVATE_KEY": "0x" + "a" * 64}):
            manager, accumulator, queue = create_batched_pipeline(batch_size=5)

        assert manager is not None
        assert accumulator is not None
        assert queue is not None

    def test_pipeline_has_no_sync_step(self):
        from haven_cli.pipeline.manager import create_batched_pipeline

        with patch.dict("os.environ", {"HAVEN_PRIVATE_KEY": "0x" + "a" * 64}):
            manager, _, _ = create_batched_pipeline()

        assert "sync" not in manager.step_names
        assert "upload" in manager.step_names
        assert "cleanup" in manager.step_names

    def test_pipeline_has_upload_phase_steps(self):
        from haven_cli.pipeline.manager import create_batched_pipeline

        with patch.dict("os.environ", {"HAVEN_PRIVATE_KEY": "0x" + "a" * 64}):
            manager, _, _ = create_batched_pipeline()

        expected = ["ingest", "analyze", "encrypt", "upload", "cleanup"]
        assert manager.step_names == expected


class TestPipelineBuilderUploadPhase:
    """Tests for PipelineBuilder.with_upload_phase_steps()."""

    def test_upload_phase_has_no_sync(self):
        from haven_cli.pipeline.manager import PipelineBuilder

        pipeline = PipelineBuilder().with_upload_phase_steps().build()
        assert "sync" not in pipeline.step_names

    def test_upload_phase_has_correct_steps(self):
        from haven_cli.pipeline.manager import PipelineBuilder

        pipeline = PipelineBuilder().with_upload_phase_steps().build()
        assert "ingest" in pipeline.step_names
        assert "upload" in pipeline.step_names
        assert "cleanup" in pipeline.step_names

    def test_default_steps_still_has_sync(self):
        """Ensure existing with_default_steps is unchanged."""
        from haven_cli.pipeline.manager import PipelineBuilder

        pipeline = PipelineBuilder().with_default_steps().build()
        assert "sync" in pipeline.step_names


class TestIntegrationBatchFlow:
    """Integration test: upload N files → accumulate → flush → verify."""

    @pytest.mark.asyncio
    async def test_full_batch_flow(self):
        """Simulate: contexts added to accumulator → flushed → processed."""
        from haven_cli.pipeline.batch_accumulator import BatchAccumulator
        from haven_cli.pipeline.flush_queue import FlushQueue

        config = _make_config()
        processor = BatchSyncProcessor(arkiv_config=config)
        accumulator = BatchAccumulator(batch_size=3)
        queue: FlushQueue = FlushQueue(processor=processor)

        # Add 3 contexts
        contexts = [_make_context(i) for i in range(3)]
        for ctx in contexts:
            await accumulator.add(ctx)

        # Flush the batch
        batch = await accumulator.flush()
        assert len(batch) == 3

        # Process via the processor directly (mocked network)
        with patch.object(processor, "_create_entities") as mock_create:
            mock_create.return_value = [
                {"entity_key": f"entity_{i}", "transaction_hash": "0xdef"}
                for i in range(3)
            ]
            await processor(batch)

        # Verify entities were created
        assert processor.processed_batches == 1
        assert processor.total_entities_created == 3
        for i, ctx in enumerate(batch):
            assert ctx.arkiv_entity_key == f"entity_{i}"
