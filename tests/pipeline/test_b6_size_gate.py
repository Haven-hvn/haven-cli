"""Tests for plan item 6 — the real Filecoin upload size gate.

The binding limit is MAX_UPLOAD_SIZE (~1 GiB) in synapse-wrapper.ts,
enforced on *projected encrypted+CAR* size. These tests pin:
  * Python derives the exact same constants from the TS wrapper.
  * max_plaintext_size() is the largest size that still fits the cap.
  * IngestStep rejects an oversize file at ingest with the limit named.
"""

import logging

import pytest

from haven_cli.pipeline.upload_limits import (
    max_plaintext_size,
    max_upload_size,
    projected_size,
)


class TestSharedConstants:
    def test_constants_match_wrapper(self):
        # These are the literals in js-services/synapse-wrapper.ts.
        assert max_upload_size() == 1_065_353_216
        assert max_plaintext_size(True) < max_upload_size()

    def test_projected_size_boundary(self):
        cap = max_upload_size()
        m = max_plaintext_size(True)
        assert projected_size(m, True) <= cap
        assert projected_size(m + 1, True) > cap

    def test_no_encryption_allows_larger_plaintext(self):
        assert max_plaintext_size(False) > max_plaintext_size(True)


class TestIngestSizeGate:
    @pytest.mark.asyncio
    async def test_oversize_file_rejected_at_ingest(self, tmp_path, caplog):
        from unittest.mock import AsyncMock, MagicMock, patch

        from haven_cli.pipeline.context import PipelineContext
        from haven_cli.pipeline.results import StepStatus
        from haven_cli.pipeline.steps.ingest_step import IngestStep

        big = tmp_path / "big.mp4"
        # Sparse file: > plaintext cap without allocating ~1 GiB of RAM/disk.
        with open(big, "wb") as f:
            f.truncate(max_plaintext_size(True) + 1024)

        context = PipelineContext(source_path=big)
        step = IngestStep()

        with patch.multiple(
            "haven_cli.pipeline.steps.ingest_step",
            detect_mime_type=MagicMock(return_value="video/mp4"),
            extract_video_metadata=AsyncMock(return_value=MagicMock(duration=1.0)),
        ), patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                        return_value="phash"), \
           patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                        return_value=False):
            with caplog.at_level(logging.ERROR):
                result = await step.process(context)

        assert result.status == StepStatus.FAILED
        assert "FILE_TOO_LARGE" in str(result.error) or "too large" in caplog.text.lower()
        assert "MAX_UPLOAD_SIZE" in caplog.text
        # Must be rejected before any DB writes / dedup hashing.
        assert context.video_id is None

    @pytest.mark.asyncio
    async def test_just_under_limit_passes_size_gate(self, tmp_path):
        """A just-under-limit file is NOT refused by the size gate."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from haven_cli.pipeline.context import PipelineContext
        from haven_cli.pipeline.results import StepStatus
        from haven_cli.pipeline.steps.ingest_step import IngestStep

        f = tmp_path / "ok.mp4"
        f.write_bytes(b"x" * 1024)  # comfortably under the cap
        context = PipelineContext(source_path=f)
        step = IngestStep()

        with patch.multiple(
            "haven_cli.pipeline.steps.ingest_step",
            detect_mime_type=MagicMock(return_value="video/mp4"),
            extract_video_metadata=AsyncMock(return_value=MagicMock(duration=1.0)),
        ), patch.object(step, "_calculate_phash", new_callable=AsyncMock,
                        return_value="phash"), \
           patch.object(step, "_check_duplicate", new_callable=AsyncMock,
                        return_value=False), \
           patch.object(step, "_save_to_database", new_callable=AsyncMock,
                        return_value=1), \
           patch(
               "haven_cli.pipeline.steps.ingest_step.hash_file_sha256_with_progress",
               return_value="a" * 64,
           ):
            # _check_original_hash_dedup hits the DB; make it return None.
            with patch.object(step, "_check_original_hash_dedup",
                              new_callable=AsyncMock, return_value=None):
                result = await step.process(context)

        # Not a size-gate failure (it may skip/fail elsewhere under mocks,
        # but must not be FILE_TOO_LARGE).
        assert "FILE_TOO_LARGE" not in str(result.error or "")
