"""Tests for Tier 1 dedup skip-flag plumbing across pipeline steps.

When ``IngestStep`` detects an ``original_hash`` match, it sets the
``context.skip_*`` flags. Each downstream step's ``should_skip`` must
honor those flags. These tests pin that contract so the four downstream
steps (encrypt, upload, sync, cleanup) cannot regress independently.

See ``docs/BATCH_SYNC_TIER1_PREUPLOAD_DEDUP.md`` for the design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haven_cli.pipeline.context import PipelineContext, UploadResult
from haven_cli.pipeline.steps.cleanup_step import CleanupStep
from haven_cli.pipeline.steps.encrypt_step import EncryptStep
from haven_cli.pipeline.steps.sync_step import SyncStep
from haven_cli.pipeline.steps.upload_step import UploadStep


def _make_context(**flags) -> PipelineContext:
    """Build a context with all step-enable options set so the only thing
    that can cause a skip is the dedup flag we're testing."""
    ctx = PipelineContext(source_path=Path("/tmp/dedup_test.mp4"))
    ctx.options.update(
        {
            "encrypt": True,
            "upload_enabled": True,
            "arkiv_sync_enabled": True,
            "cleanup_enabled": True,
        }
    )
    # Give upload a CID so sync's "no upload result" guard doesn't fire.
    ctx.upload_result = UploadResult(
        video_path="/tmp/dedup_test.mp4", root_cid="bafybeihello"
    )
    # encryption_metadata so upload's "encryption requested but not
    # produced" security check doesn't fire when ``encrypt`` is True.
    from haven_cli.pipeline.context import EncryptionMetadata

    ctx.encryption_metadata = EncryptionMetadata(
        gate={"chain": "EthMainnet", "tokenAddress": "0x" + "a" * 40, "threshold": "1"},
        ciphertext="/tmp/dedup_test.mp4.encrypted",
        iv="aaaa",
    )
    ctx.encrypted_video_path = None  # don't trigger missing-file guard
    for k, v in flags.items():
        setattr(ctx, k, v)
    return ctx


class TestEncryptSkipFlag:
    @pytest.mark.asyncio
    async def test_encrypt_skipped_when_skip_encrypt_set(self) -> None:
        ctx = _make_context(skip_encrypt=True)
        step = EncryptStep()
        assert await step.should_skip(ctx) is True
        reason = await step._get_skip_reason(ctx)
        assert "Tier 1" in reason

    @pytest.mark.asyncio
    async def test_encrypt_runs_when_flag_unset(self) -> None:
        # Without the flag, encrypt's normal logic runs (encrypt=True ⇒ should not skip).
        ctx = _make_context(skip_encrypt=False)
        step = EncryptStep()
        assert await step.should_skip(ctx) is False


class TestUploadSkipFlag:
    @pytest.mark.asyncio
    async def test_upload_skipped_when_skip_upload_set(self) -> None:
        ctx = _make_context(skip_upload=True)
        step = UploadStep()
        assert await step.should_skip(ctx) is True
        reason = await step._get_skip_reason(ctx)
        assert "Tier 1" in reason

    @pytest.mark.asyncio
    async def test_upload_runs_when_flag_unset(self) -> None:
        ctx = _make_context(skip_upload=False)
        step = UploadStep()
        assert await step.should_skip(ctx) is False


class TestSyncSkipFlag:
    @pytest.mark.asyncio
    async def test_sync_skipped_when_skip_sync_set(self) -> None:
        ctx = _make_context(skip_sync=True)
        step = SyncStep()
        assert await step.should_skip(ctx) is True
        reason = await step._get_skip_reason(ctx)
        assert "Tier 1" in reason

    @pytest.mark.asyncio
    async def test_sync_runs_when_flag_unset(self) -> None:
        ctx = _make_context(skip_sync=False)
        step = SyncStep()
        assert await step.should_skip(ctx) is False


class TestCleanupSkipFlag:
    @pytest.mark.asyncio
    async def test_cleanup_skipped_when_skip_cleanup_set(self) -> None:
        ctx = _make_context(skip_cleanup=True)
        step = CleanupStep()
        assert await step.should_skip(ctx) is True
        reason = await step._get_skip_reason(ctx)
        assert "Tier 1" in reason

    @pytest.mark.asyncio
    async def test_cleanup_runs_when_flag_unset(self) -> None:
        # cleanup_enabled=True from the context fixture, and the upload
        # has a root_cid, so cleanup's normal guards pass.
        ctx = _make_context(skip_cleanup=False)
        step = CleanupStep()
        assert await step.should_skip(ctx) is False


class TestSkipFlagsAreIndependent:
    """Setting one flag must not affect the others.

    If setting ``skip_upload=True`` accidentally caused encrypt to also
    skip, IngestStep's careful per-step decisions (e.g., ``skip_sync``
    only when ``arkiv_entity_key`` exists) would be silently overridden.
    """

    @pytest.mark.asyncio
    async def test_skip_upload_does_not_cause_sync_to_skip_via_dedup(
        self,
    ) -> None:
        """skip_upload=True alone should not flip the sync dedup branch."""
        ctx = _make_context(skip_upload=True, skip_sync=False)
        # Sync still runs (or skips for non-dedup reasons), but the dedup
        # short-circuit branch is *not* taken because ``skip_sync`` is
        # False. We assert the reason is *not* the Tier 1 dedup reason.
        step = SyncStep()
        if await step.should_skip(ctx):
            reason = await step._get_skip_reason(ctx)
            assert "Tier 1" not in reason, (
                "Sync's dedup short-circuit must only fire when "
                "context.skip_sync is True, not skip_upload."
            )
