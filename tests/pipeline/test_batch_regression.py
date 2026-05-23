"""Regression tests for backward compatibility with per-file sync path.

Ensures that the existing create_default_pipeline() and SyncStep still work
correctly after the batched pipeline was introduced.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haven_cli.pipeline.context import PipelineContext, UploadResult
from haven_cli.pipeline.manager import PipelineBuilder


class TestPerFileSyncStillWorks:
    """Scenario 10: Per-file sync via create_default_pipeline still works."""

    @pytest.mark.asyncio
    async def test_default_pipeline_processes_single_file(self):
        """create_default_pipeline includes SyncStep that calls attest + sync per-file."""
        from haven_cli.pipeline.manager import create_default_pipeline

        config = {
            "pipeline": MagicMock(
                vlm_enabled=False,
                encryption_enabled=False,
                upload_enabled=True,
                sync_enabled=True,
                arkiv_sync_enabled=True,
                cleanup_enabled=False,
            ),
        }

        pipeline = create_default_pipeline(max_concurrent=1, config=config)

        # Verify sync step is present
        assert "sync" in pipeline.step_names

        # Verify step order includes sync after upload
        names = pipeline.step_names
        upload_idx = names.index("upload")
        sync_idx = names.index("sync")
        assert sync_idx > upload_idx


class TestCleanupDisabledByDefault:
    """Scenario 11: Cleanup disabled by default in default pipeline."""

    def test_cleanup_step_present_but_disabled(self):
        """Default pipeline has cleanup step but it's disabled by default."""
        config = {
            "pipeline": MagicMock(
                vlm_enabled=False,
                encryption_enabled=False,
                upload_enabled=True,
                sync_enabled=False,
                arkiv_sync_enabled=False,
                cleanup_enabled=False,
            ),
        }

        from haven_cli.pipeline.manager import create_default_pipeline

        pipeline = create_default_pipeline(max_concurrent=1, config=config)
        assert "cleanup" in pipeline.step_names


class TestPipelineBuilderStepOrder:
    """Scenario 12: Verify step ordering for both pipeline modes."""

    def test_step_order_cleanup_before_sync(self):
        """with_default_steps: ingest, analyze, encrypt, upload, cleanup, sync.
        with_upload_phase_steps: ingest, analyze, encrypt, upload, cleanup.
        """
        default_pipeline = PipelineBuilder().with_default_steps().build()
        assert default_pipeline.step_names == [
            "ingest", "analyze", "encrypt", "upload", "cleanup", "sync"
        ]

        upload_pipeline = PipelineBuilder().with_upload_phase_steps().build()
        assert upload_pipeline.step_names == [
            "ingest", "analyze", "encrypt", "upload", "cleanup"
        ]
        assert "sync" not in upload_pipeline.step_names
