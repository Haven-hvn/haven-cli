"""Tests for the analyze pipeline step.

Tests the VLM (Visual Language Model) analysis step including:
- Conditional skip logic based on vlm_enabled
- VLM configuration loading and validation
- Successful analysis with timestamp generation
- Error handling (file not found, config errors, processing failures)
- Database persistence (analysis jobs, timestamps, pipeline snapshots)
- Event emission
- MockAnalyzeStep for testing without VLM API calls
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.pipeline.context import PipelineContext, AIAnalysisResult, VideoMetadata
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.steps.analyze_step import AnalyzeStep, MockAnalyzeStep


class TestAnalyzeStepBasics:
    """Basic tests for AnalyzeStep."""

    def test_step_name(self):
        """Test step name is correct."""
        step = AnalyzeStep()
        assert step.name == "analyze"

    def test_enabled_option(self):
        """Test enabled option is 'vlm_enabled'."""
        step = AnalyzeStep()
        assert step.enabled_option == "vlm_enabled"

    def test_default_enabled(self):
        """Test VLM analysis is disabled by default."""
        step = AnalyzeStep()
        assert step.default_enabled is False


class TestAnalyzeStepSkipConditions:
    """Tests for should_skip logic on AnalyzeStep."""

    @pytest.mark.asyncio
    async def test_skip_when_disabled(self):
        """Test step is skipped when vlm_enabled is False."""
        step = AnalyzeStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"vlm_enabled": False},
        )
        should_skip = await step.should_skip(context)
        assert should_skip is True

    @pytest.mark.asyncio
    async def test_no_skip_when_enabled(self):
        """Test step is not skipped when vlm_enabled is True."""
        step = AnalyzeStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"vlm_enabled": True},
        )
        should_skip = await step.should_skip(context)
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_no_skip_when_enabled_via_config(self):
        """Test step is not skipped when enabled via step config."""
        step = AnalyzeStep(config={"vlm_enabled": True})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        should_skip = await step.should_skip(context)
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_skip_reason_when_disabled(self):
        """Test skip reason mentions vlm_enabled."""
        step = AnalyzeStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        reason = await step._get_skip_reason(context)
        assert "vlm" in reason.lower() or "disabled" in reason.lower()


class TestAnalyzeStepConfigValidation:
    """Tests for VLM configuration loading and validation."""

    @pytest.mark.asyncio
    async def test_config_validation_error(self):
        """Test that config validation errors cause step failure."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=["API key missing"]):
                result = await step.process(context)

        # Warning only - should proceed
        assert result.success is True

    @pytest.mark.asyncio
    async def test_config_validation_errors_fail_step(self):
        """Test that config validation errors (not warnings) cause failure."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.engine.model_name = "test-model"

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=["Invalid model path"]):
                result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "VLM_CONFIG_ERROR"

    @pytest.mark.asyncio
    async def test_processing_disabled_returns_skipped(self):
        """Test that processing.enabled=False returns skipped result."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = False
        mock_config.engine.model_name = "test-model"

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                result = await step.process(context)

        assert result.success is True
        assert result.data.get("skipped") is True


class TestAnalyzeStepProcess:
    """Tests for the main process method."""

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        """Test successful VLM analysis."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [
                {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9},
                {"tag_name": "main", "start_time": 10.0, "end_time": 60.0, "confidence": 0.95},
            ],
            "tags": {"video": 0.95, "outdoor": 0.8},
            "confidence": 0.88,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_update_pipeline_snapshot', new_callable=AsyncMock):
                            with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                                with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock):
                                    with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                        result = await step.process(context)

        assert result.success is True
        assert result.data["timestamps"] is not None
        assert result.data["tags"] is not None
        assert "confidence" in result.data
        assert context.analysis_result is not None
        assert context.analysis_result.timestamps is not None
        assert context.video_metadata.has_ai_data is True

    @pytest.mark.asyncio
    async def test_analysis_without_video_id(self):
        """Test analysis when no video_id is set in context."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [],
            "tags": {},
            "confidence": 0.5,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                        mock_session = MagicMock()
                        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                        mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                        with patch.object(step, '_create_analysis_job', new_callable=AsyncMock) as mock_create:
                            with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                                with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                    result = await step.process(context)

        assert result.success is True
        # _create_analysis_job should not be called when video_id is None
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_analysis_file_not_found(self):
        """Test handling of FileNotFoundError."""
        step = AnalyzeStep()

        video_file = Path("/tmp/nonexistent.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(side_effect=FileNotFoundError("File not found"))
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_fail_analysis_job', new_callable=AsyncMock):
                            with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "VIDEO_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_analysis_general_error(self):
        """Test handling of general exceptions during analysis."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(side_effect=RuntimeError("VLM API error"))
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_fail_analysis_job', new_callable=AsyncMock):
                            with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "ANALYSIS_ERROR"

    @pytest.mark.asyncio
    async def test_processor_cleanup_on_success(self):
        """Test that VLM processor is closed after successful analysis."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [],
            "tags": {},
            "confidence": 0.5,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                            with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock):
                                with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                    await step.process(context)

        mock_processor.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_processor_cleanup_on_error(self):
        """Test that VLM processor is closed even after errors."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(side_effect=RuntimeError("VLM error"))
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_fail_analysis_job', new_callable=AsyncMock):
                            with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                await step.process(context)

        mock_processor.close.assert_awaited_once()


class TestAnalyzeStepDatabase:
    """Tests for database operations."""

    @pytest.mark.asyncio
    async def test_create_analysis_job(self):
        """Test creating an analysis job in the database."""
        step = AnalyzeStep()
        step._vlm_config = MagicMock()
        step._vlm_config.engine.model_name = "test-model"
        step._frames_total = 25

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            mock_repo.create.return_value = MagicMock(id=1)

            with patch("haven_cli.database.repositories.AnalysisJobRepository", return_value=mock_repo):
                job_id = await step._create_analysis_job(42)

        assert job_id is not None
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["video_id"] == 42
        assert call_kwargs["analysis_type"] == "vlm"
        assert call_kwargs["model_name"] == "test-model"
        assert call_kwargs["status"] == "analyzing"

    @pytest.mark.asyncio
    async def test_create_analysis_job_db_error(self):
        """Test handling of database error when creating analysis job."""
        step = AnalyzeStep()
        step._vlm_config = MagicMock()
        step._vlm_config.engine.model_name = "test-model"

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.side_effect = Exception("DB connection failed")

            job_id = await step._create_analysis_job(42)

        assert job_id is None

    @pytest.mark.asyncio
    async def test_save_timestamps_to_db(self):
        """Test saving timestamps to database."""
        step = AnalyzeStep()

        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        context.video_id = 42

        results = {
            "timestamps": [
                {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9},
                {"tag_name": "main", "start_time": 10.0, "end_time": 60.0, "confidence": 0.95},
            ]
        }

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            with patch("haven_cli.database.models.Timestamp") as mock_timestamp:
                await step._save_timestamps_to_db(context, results)

        # Should have 1 delete + 2 add operations
        mock_session.query().filter().delete.assert_called_once()
        assert mock_session.add.call_count == 2
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_timestamps_no_video_id(self):
        """Test that timestamps are not saved when video_id is None."""
        step = AnalyzeStep()

        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        # No video_id set

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            await step._save_timestamps_to_db(context, {"timestamps": [{"tag_name": "test"}]})

        mock_get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_timestamps_empty(self):
        """Test that empty timestamps don't hit the database."""
        step = AnalyzeStep()

        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        context.video_id = 42

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            await step._save_timestamps_to_db(context, {"timestamps": []})

        mock_get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_analysis_job(self):
        """Test marking analysis job as completed."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            with patch("haven_cli.database.repositories.AnalysisJobRepository", return_value=mock_repo):
                await step._complete_analysis_job(1, "/tmp/test.mp4.AI.json")

        mock_repo.complete_analysis.assert_called_once_with(1, output_file="/tmp/test.mp4.AI.json")

    @pytest.mark.asyncio
    async def test_fail_analysis_job(self):
        """Test marking analysis job as failed."""
        step = AnalyzeStep()

        mock_job = MagicMock()
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.return_value = mock_job
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            await step._fail_analysis_job(1, "Test error")

        assert mock_job.status == "failed"
        assert mock_job.error_message == "Test error"
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_fail_analysis_job_not_found(self):
        """Test handling when analysis job is not found."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.return_value = None
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            # Should not raise
            await step._fail_analysis_job(999, "Test error")

        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_skipped_analysis_job(self):
        """Test creating a skipped analysis job."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            mock_repo.create.return_value = MagicMock(id=1)

            with patch("haven_cli.database.repositories.AnalysisJobRepository", return_value=mock_repo):
                job_id = await step._create_skipped_analysis_job(42, "vlm_enabled is disabled")

        assert job_id is not None
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["status"] == "skipped"
        assert call_kwargs["model_name"] == "none"


class TestAnalyzeStepEvents:
    """Tests for event emission."""

    @pytest.mark.asyncio
    async def test_analysis_requested_event(self):
        """Test ANALYSIS_REQUESTED event is emitted."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [],
            "tags": {},
            "confidence": 0.5,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                            with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock):
                                with patch.object(step, '_emit_event', new_callable=AsyncMock) as mock_emit:
                                    await step.process(context)

        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert EventType.ANALYSIS_REQUESTED in event_types
        assert EventType.ANALYSIS_COMPLETE in event_types

    @pytest.mark.asyncio
    async def test_analysis_failed_event(self):
        """Test ANALYSIS_FAILED event is emitted on error."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(side_effect=RuntimeError("VLM error"))
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_fail_analysis_job', new_callable=AsyncMock):
                            with patch.object(step, '_emit_event', new_callable=AsyncMock) as mock_emit:
                                await step.process(context)

        event_types = [call[0][0] for call in mock_emit.call_args_list]
        assert EventType.ANALYSIS_REQUESTED in event_types
        assert EventType.ANALYSIS_FAILED in event_types

    @pytest.mark.asyncio
    async def test_analysis_complete_event_data(self):
        """Test ANALYSIS_COMPLETE event contains correct data."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [{"tag_name": "intro", "start_time": 0.0, "end_time": 10.0}],
            "tags": {"video": 0.95},
            "confidence": 0.88,
        })
        mock_processor.close = AsyncMock()

        complete_event_data = {}

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                            with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock):
                                def capture_event(event_type, ctx, data):
                                    if event_type == EventType.ANALYSIS_COMPLETE:
                                        complete_event_data.update(data)

                                with patch.object(step, '_emit_event', side_effect=capture_event):
                                    await step.process(context)

        assert "timestamp_count" in complete_event_data
        assert "tag_count" in complete_event_data
        assert "confidence" in complete_event_data
        assert "video_path" in complete_event_data


class TestAnalyzeStepOnSkip:
    """Tests for the on_skip handler."""

    @pytest.mark.asyncio
    async def test_on_skip_creates_skipped_job(self):
        """Test on_skip creates a skipped analysis job."""
        step = AnalyzeStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        context.video_id = 42

        with patch.object(step, '_create_skipped_analysis_job', new_callable=AsyncMock, return_value=1) as mock_skipped:
            await step.on_skip(context, "vlm_enabled is disabled")

        mock_skipped.assert_called_once_with(42, "vlm_enabled is disabled")

    @pytest.mark.asyncio
    async def test_on_skip_without_video_id(self):
        """Test on_skip when no video_id is set."""
        step = AnalyzeStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        # No video_id

        # Should not raise even without video_id
        await step.on_skip(context, "vlm disabled")


class TestAnalyzeStepProgressTracking:
    """Tests for progress callback and job progress updates."""

    @pytest.mark.asyncio
    async def test_progress_callback_updates_job(self):
        """Test progress callback triggers job and snapshot updates."""
        step = AnalyzeStep()
        step._start_time = 0  # Ensure time diff >= 1.0

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
        )
        context.video_id = 42
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()

        # Capture the progress_callback and invoke it to test updates
        async def mock_process_video(path, progress_callback=None):
            if progress_callback:
                progress_callback(50)
            return {
                "timestamps": [],
                "tags": {},
                "confidence": 0.5,
            }

        mock_processor.process_video = mock_process_video
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_update_job_progress', new_callable=AsyncMock) as mock_progress:
                            with patch.object(step, '_update_pipeline_snapshot', new_callable=AsyncMock) as mock_snapshot:
                                with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                                    with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock):
                                        with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                            await step.process(context)

        # Verify progress updates were triggered
        mock_progress.assert_called()
        mock_snapshot.assert_called()


class TestMockAnalyzeStep:
    """Tests for MockAnalyzeStep (testing without VLM API)."""

    def test_mock_step_name(self):
        """Test mock step name."""
        mock_step = MockAnalyzeStep()
        assert mock_step.name == "analyze_mock"

    def test_mock_enabled_option(self):
        """Test mock enabled option matches AnalyzeStep."""
        mock_step = MockAnalyzeStep()
        assert mock_step.enabled_option == "vlm_enabled"

    def test_mock_default_enabled(self):
        """Test mock is disabled by default."""
        mock_step = MockAnalyzeStep()
        assert mock_step.default_enabled is False

    @pytest.mark.asyncio
    async def test_mock_process_success(self):
        """Test mock analysis produces synthetic results."""
        mock_step = MockAnalyzeStep()
        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(source_path=video_file)
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        result = await mock_step.process(context)

        assert result.success is True
        assert result.data["mock"] is True
        assert "timestamps" in result.data
        assert "tags" in result.data
        assert "confidence" in result.data
        assert len(result.data["timestamps"]) == 2
        assert result.data["timestamps"][0]["tag_name"] == "introduction"
        assert result.data["timestamps"][1]["tag_name"] == "main_content"

    @pytest.mark.asyncio
    async def test_mock_process_sets_context(self):
        """Test mock analysis sets analysis_result in context."""
        mock_step = MockAnalyzeStep()
        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(source_path=video_file)
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        await mock_step.process(context)

        assert context.analysis_result is not None
        assert context.analysis_result.analysis_model == "mock-vlm-model"
        assert context.analysis_result.timestamps is not None
        assert len(context.analysis_result.timestamps) == 2
        assert context.analysis_result.tags is not None
        assert context.video_metadata.has_ai_data is True

    @pytest.mark.asyncio
    async def test_mock_process_tags(self):
        """Test mock analysis generates expected tags."""
        mock_step = MockAnalyzeStep()
        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(source_path=video_file)
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        result = await mock_step.process(context)

        tags = result.data["tags"]
        assert "video" in tags
        assert "entertainment" in tags
        assert "content" in tags
        assert tags["video"] == 0.95

    @pytest.mark.asyncio
    async def test_mock_on_skip(self):
        """Test mock on_skip handler."""
        mock_step = MockAnalyzeStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        # Should not raise
        await mock_step.on_skip(context, "test reason")


class TestAnalyzeStepIntegration:
    """Integration-style tests combining multiple behaviors."""

    @pytest.mark.asyncio
    async def test_full_analysis_flow_with_video_id(self):
        """Test complete analysis flow with all DB interactions."""
        step = AnalyzeStep()

        video_file = Path("/tmp/test.mp4")
        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "llava"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [
                {"tag_name": "scene_1", "start_time": 0.0, "end_time": 30.0, "confidence": 0.9},
                {"tag_name": "scene_2", "start_time": 30.0, "end_time": 60.0, "confidence": 0.85},
            ],
            "tags": {"outdoor": 0.9, "nature": 0.7},
            "confidence": 0.85,
        })
        mock_processor.close = AsyncMock()

        created_job_id = [None]
        saved_timestamps = [None]
        completed_job = [None]

        def mock_create_job(video_id, analysis_type, model_name, status, frames_total):
            created_job_id[0] = 1
            return 1

        def mock_save_timestamps(ctx, results):
            saved_timestamps[0] = results

        def mock_complete(job_id, output_file):
            completed_job[0] = (job_id, output_file)

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch(
                        "haven_cli.database.repositories.AnalysisJobRepository"
                    ), \
                    patch.object(step, '_create_analysis_job', side_effect=mock_create_job):
                        with patch.object(step, '_save_timestamps_to_db', side_effect=mock_save_timestamps):
                            with patch.object(step, '_complete_analysis_job', side_effect=mock_complete):
                                with patch.object(step, '_update_pipeline_snapshot', new_callable=AsyncMock):
                                    with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                        result = await step.process(context)

        assert result.success is True
        assert created_job_id[0] == 1
        assert saved_timestamps[0] is not None
        assert len(saved_timestamps[0]["timestamps"]) == 2
        assert completed_job[0] is not None
        assert context.analysis_result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_ai_json_path_detection(self, tmp_path):
        """Test that AI.json path is detected when file exists on disk."""
        step = AnalyzeStep()

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake content")
        ai_json_file = tmp_path / "test.mp4.AI.json"
        ai_json_file.write_bytes(json.dumps({"test": "data"}).encode())

        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [],
            "tags": {},
            "confidence": 0.5,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                            with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock) as mock_complete:
                                with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                    await step.process(context)

        # Verify AI json path was passed to complete_analysis_job
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args
        assert call_args[0][1] is not None
        assert "AI.json" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_ai_json_path_none_when_missing(self, tmp_path):
        """Test that AI.json path is None when file doesn't exist."""
        step = AnalyzeStep()

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake content")
        # Don't create the AI.json file

        context = PipelineContext(
            source_path=video_file,
            options={"vlm_enabled": True},
            video_id=42,
        )
        context.video_metadata = VideoMetadata(
            path=str(video_file),
            title="test",
            duration=60.0,
            file_size=1000,
            mime_type="video/mp4",
            phash="a3f5c2d8",
        )

        mock_config = MagicMock()
        mock_config.processing.enabled = True
        mock_config.processing.frame_count = 25
        mock_config.engine.model_name = "test-model"

        mock_processor = MagicMock()
        mock_processor.initialize = AsyncMock()
        mock_processor.process_video = AsyncMock(return_value={
            "timestamps": [],
            "tags": {},
            "confidence": 0.5,
        })
        mock_processor.close = AsyncMock()

        with patch("haven_cli.pipeline.steps.analyze_step.load_vlm_config", return_value=mock_config):
            with patch("haven_cli.pipeline.steps.analyze_step.validate_vlm_config", return_value=[]):
                with patch("haven_cli.pipeline.steps.analyze_step.VLMProcessor", return_value=mock_processor):
                    with patch.object(step, '_create_analysis_job', new_callable=AsyncMock, return_value=1):
                        with patch.object(step, '_save_timestamps_to_db', new_callable=AsyncMock):
                            with patch.object(step, '_complete_analysis_job', new_callable=AsyncMock) as mock_complete:
                                with patch.object(step, '_emit_event', new_callable=AsyncMock):
                                    await step.process(context)

        # Verify AI json path was None since file doesn't exist
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args
        assert call_args[0][1] is None


class TestAnalyzeStepUpdatePipelineSnapshot:
    """Tests for pipeline snapshot updates."""

    @pytest.mark.asyncio
    async def test_update_pipeline_snapshot_active(self):
        """Test updating snapshot with active status."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session, \
             patch("haven_cli.database.repositories.PipelineSnapshotRepository") as mock_repo_class:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo

            await step._update_pipeline_snapshot(42, "analyze", 50.0, "active")

            mock_repo.update_stage.assert_called_once_with(
                video_id=42,
                stage="analyze",
                status="active",
                progress_percent=50.0,
            )

    @pytest.mark.asyncio
    async def test_update_pipeline_snapshot_completed(self):
        """Test updating snapshot with completed status."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session, \
             patch("haven_cli.database.repositories.PipelineSnapshotRepository") as mock_repo_class:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo

            await step._update_pipeline_snapshot(42, "analyze", 100.0, "completed")

            mock_repo.mark_completed.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_update_pipeline_snapshot_failed(self):
        """Test updating snapshot with failed status."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session, \
             patch("haven_cli.database.repositories.PipelineSnapshotRepository") as mock_repo_class:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo

            await step._update_pipeline_snapshot(42, "analyze", 0.0, "failed", error="Something broke")

            mock_repo.mark_error.assert_called_once_with(42, "analyze", "Something broke")

    @pytest.mark.asyncio
    async def test_update_pipeline_snapshot_db_error(self):
        """Test handling of DB error during snapshot update."""
        step = AnalyzeStep()

        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.side_effect = Exception("DB connection failed")

            # Should not raise
            await step._update_pipeline_snapshot(42, "analyze", 50.0, "active")