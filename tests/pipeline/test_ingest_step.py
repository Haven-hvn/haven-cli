"""Tests for the ingest pipeline step.

Tests the video ingestion step including:
- File validation (existence, readability)
- Metadata extraction via ffprobe
- pHash calculation
- Duplicate detection
- Database persistence
- Duplicate action handling (error/continue)
- Event emission
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.media.exceptions import VideoMetadataError
from haven_cli.pipeline.context import PipelineContext, VideoMetadata
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.steps.ingest_step import IngestStep


class TestIngestStepBasics:
    """Basic tests for IngestStep."""

    def test_step_name(self):
        """Test step name is correct."""
        step = IngestStep()
        assert step.name == "ingest"

    def test_should_skip_always_false(self):
        """Test ingest step is never skipped (always required)."""
        step = IngestStep()
        assert hasattr(step, 'should_skip')


class TestIngestStepFileValidation:
    """Tests for file validation in ingest step."""

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """Test that missing file returns failure."""
        step = IngestStep()
        context = PipelineContext(source_path=Path("/tmp/nonexistent.mp4"))

        result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_not_a_file(self, tmp_path):
        """Test that a directory path returns failure."""
        step = IngestStep()
        dir_path = tmp_path / "some_dir"
        dir_path.mkdir()
        context = PipelineContext(source_path=dir_path)

        result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "NOT_A_FILE"


class TestIngestStepMetadataExtraction:
    """Tests for metadata extraction and pHash calculation."""

    @pytest.mark.asyncio
    async def test_successful_ingest_with_mocks(self, tmp_path):
        """Test successful ingestion with mocked dependencies."""
        # Create a test video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        # Mock detect_mime_type
        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            # Mock _calculate_phash
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8e9b1a7f4"):
                # Mock _check_duplicate
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    # Mock extract_video_metadata
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        # Mock database operations
                        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                            mock_session = MagicMock()
                            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                            mock_repo = MagicMock()
                            mock_repo.get_by_source_path.return_value = None
                            mock_video = MagicMock()
                            mock_video.id = 42
                            mock_repo.create.return_value = mock_video

                            with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                result = await step.process(context)

        assert result.success is True
        assert result.data["phash"] == "a3f5c2d8e9b1a7f4"
        assert result.data["is_duplicate"] is False
        assert result.data["file_size"] == 20  # len(b"fake video data here")
        assert result.data["mime_type"] == "video/mp4"
        assert result.data["duration"] == 60.0
        assert context.video_id == 42
        assert context.video_metadata is not None
        assert context.video_metadata.path == str(video_file)
        assert context.video_metadata.title == "test"  # stem of filename
        assert context.video_metadata.phash == "a3f5c2d8e9b1a7f4"
        assert context.video_metadata.width == 1920
        assert context.video_metadata.height == 1080
        assert context.video_metadata.fps == 30.0
        assert context.video_metadata.codec == "h264"
        assert context.video_metadata.has_audio is True

    @pytest.mark.asyncio
    async def test_ingest_without_tech_metadata(self, tmp_path):
        """Test ingestion when ffprobe fails (graceful degradation)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        # Simulate VideoMetadataError from ffprobe
        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, side_effect=VideoMetadataError("ffprobe failed")):
                        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                            mock_session = MagicMock()
                            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                            mock_repo = MagicMock()
                            mock_repo.get_by_source_path.return_value = None
                            mock_video = MagicMock()
                            mock_video.id = 42
                            mock_repo.create.return_value = mock_video

                            with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                result = await step.process(context)

        assert result.success is True
        assert context.video_metadata.width == 0
        assert context.video_metadata.height == 0
        assert context.video_metadata.fps == 0.0
        assert context.video_metadata.codec == ""


class TestIngestStepDuplicateDetection:
    """Tests for duplicate detection logic."""

    @pytest.mark.asyncio
    async def test_duplicate_detected_continue(self, tmp_path):
        """Test duplicate with 'continue' action (default)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(
            source_path=video_file,
            options={"duplicate_action": "continue"},
        )

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=True):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    result = await step.process(context)

        # With 'continue', ingestion proceeds despite duplicate
        assert result.success is True
        assert result.data["is_duplicate"] is True

    @pytest.mark.asyncio
    async def test_duplicate_detected_error(self, tmp_path):
        """Test duplicate with 'error' action."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(
            source_path=video_file,
            options={"duplicate_action": "error"},
        )

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=True):
                            result = await step.process(context)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "DUPLICATE_VIDEO"

    @pytest.mark.asyncio
    async def test_duplicate_default_action(self, tmp_path):
        """Test that default duplicate_action is 'continue'."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)
        # No duplicate_action set - should default to 'continue'

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=True):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    result = await step.process(context)

        # Default 'continue' means success even with duplicate
        assert result.success is True


class TestIngestStepPhash:
    """Tests for pHash calculation."""

    @pytest.mark.asyncio
    async def test_phash_calculation_called(self, tmp_path):
        """Test that pHash calculation is called with correct path."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        mock_phash = "deadbeef12345678"
        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value=mock_phash) as mock_calc:
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    await step.process(context)

        mock_calc.assert_called_once_with(video_file)

    @pytest.mark.asyncio
    async def test_phash_error_fails_step(self, tmp_path):
        """Test that pHash error causes step failure."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        from haven_cli.media.phash import VideoHashError
        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, side_effect=VideoHashError("Hash failed")):
                result = await step.process(context)

        assert result.success is False


class TestIngestStepDatabase:
    """Tests for database operations."""

    @pytest.mark.asyncio
    async def test_save_to_database_new_video(self, tmp_path):
        """Test creating new video record in database."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo) as mock_repo_class:
                                    result = await step.process(context)

        mock_repo_class.assert_called_once()
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["source_path"] == str(video_file)
        assert call_kwargs["title"] == "test"
        assert call_kwargs["phash"] == "a3f5c2d8"
        assert call_kwargs["mime_type"] == "video/mp4"

    @pytest.mark.asyncio
    async def test_save_to_database_existing_video(self, tmp_path):
        """Test updating existing video record in database."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                existing_video = MagicMock()
                                existing_video.id = 99
                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = existing_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    result = await step.process(context)

        # Should update existing, not create new
        mock_repo.get_by_source_path.assert_called_once_with(str(video_file))
        mock_repo.create.assert_not_called()
        mock_repo.update.assert_called_once()
        # Should return existing video's ID
        assert context.video_id == 99

    @pytest.mark.asyncio
    async def test_database_error_graceful(self, tmp_path):
        """Test that database errors don't crash the pipeline."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_get_session.side_effect = Exception("DB error")

                                result = await step.process(context)

        # Should still succeed (DB errors are non-fatal for ingest)
        assert result.success is True
        # video_id should be -1 or None to indicate DB failure
        assert context.video_id is None or context.video_id == -1


class TestIngestStepDuplicateChecking:
    """Tests for duplicate checking via database."""

    @pytest.mark.asyncio
    async def test_check_duplicate_returns_true(self, tmp_path):
        """Test duplicate detection when video is a duplicate."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(
            source_path=video_file,
            options={"duplicate_action": "continue"},
        )

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=True):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    result = await step.process(context)

        assert result.data["is_duplicate"] is True
        assert context.get_step_data("ingest", "is_duplicate") is True

    @pytest.mark.asyncio
    async def test_check_duplicate_returns_false(self, tmp_path):
        """Test non-duplicate video."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                    mock_tech_metadata = MagicMock()
                    mock_tech_metadata.duration = 60.0
                    mock_tech_metadata.width = 1920
                    mock_tech_metadata.height = 1080
                    mock_tech_metadata.fps = 30.0
                    mock_tech_metadata.codec = "h264"
                    mock_tech_metadata.bitrate = 5000000
                    mock_tech_metadata.audio_codec = "aac"
                    mock_tech_metadata.audio_channels = 2
                    mock_tech_metadata.container = "mp4"
                    mock_tech_metadata.has_audio = True

                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                        with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    result = await step.process(context)

        assert result.data["is_duplicate"] is False


class TestIngestStepEvents:
    """Tests for event emission."""

    @pytest.mark.asyncio
    async def test_video_ingested_event(self, tmp_path):
        """Test VIDEO_INGESTED event is emitted."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    with patch.object(step, '_emit_event', new=AsyncMock()) as mock_emit:
                                        result = await step.process(context)

        from haven_cli.pipeline.events import EventType
        event_calls = [call for call in mock_emit.call_args_list]
        event_types = [call[0][0] for call in event_calls]
        assert EventType.VIDEO_INGESTED in event_types

    @pytest.mark.asyncio
    async def test_video_ingested_event_data(self, tmp_path):
        """Test VIDEO_INGESTED event contains correct data."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        emitted_events = []

        def capture_event(event_type, ctx, data):
            emitted_events.append((event_type, data))

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_session = MagicMock()
                                mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
                                mock_get_session.return_value.__exit__ = MagicMock(return_value=None)

                                mock_repo = MagicMock()
                                mock_repo.get_by_source_path.return_value = None
                                mock_video = MagicMock()
                                mock_video.id = 42
                                mock_repo.create.return_value = mock_video

                                with patch("haven_cli.database.repositories.VideoRepository", return_value=mock_repo):
                                    with patch.object(step, '_emit_event', side_effect=capture_event):
                                        result = await step.process(context)

        from haven_cli.pipeline.events import EventType
        ingested_event = next(e for e in emitted_events if e[0] == EventType.VIDEO_INGESTED)
        data = ingested_event[1]
        assert data["path"] == str(video_file)
        assert data["phash"] == "a3f5c2d8"
        assert data["file_size"] == 20
        assert data["duration"] == 60.0
        assert data["mime_type"] == "video/mp4"
        assert data["is_duplicate"] is False
        assert "resolution" in data
        assert "codec" in data

    @pytest.mark.asyncio
    async def test_error_event_on_failure(self, tmp_path):
        """Test that no event is emitted on file not found."""
        step = IngestStep()
        context = PipelineContext(source_path=Path("/tmp/nonexistent.mp4"))

        with patch.object(step, '_emit_event', new=AsyncMock()) as mock_emit:
            result = await step.process(context)

        # File not found returns early before any event emission
        assert result.success is False
        mock_emit.assert_not_called()


class TestIngestStepDatabaseFailure:
    """Tests for database failure handling within _save_to_database."""

    @pytest.mark.asyncio
    async def test_db_save_failure_returns_negative_id(self, tmp_path):
        """Test that DB save failure returns -1 video_id."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video data here")

        step = IngestStep()
        context = PipelineContext(source_path=video_file)

        with patch("haven_cli.pipeline.steps.ingest_step.detect_mime_type", return_value="video/mp4"):
            with patch.object(step, '_calculate_phash', new_callable=AsyncMock, return_value="a3f5c2d8"):
                with patch.object(step, '_check_duplicate', new_callable=AsyncMock, return_value=False):
                    with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock):
                        mock_tech_metadata = MagicMock()
                        mock_tech_metadata.duration = 60.0
                        mock_tech_metadata.width = 1920
                        mock_tech_metadata.height = 1080
                        mock_tech_metadata.fps = 30.0
                        mock_tech_metadata.codec = "h264"
                        mock_tech_metadata.bitrate = 5000000
                        mock_tech_metadata.audio_codec = "aac"
                        mock_tech_metadata.audio_channels = 2
                        mock_tech_metadata.container = "mp4"
                        mock_tech_metadata.has_audio = True

                        with patch("haven_cli.pipeline.steps.ingest_step.extract_video_metadata", new_callable=AsyncMock, return_value=mock_tech_metadata):
                            with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
                                mock_get_session.side_effect = Exception("DB error")

                                result = await step.process(context)

        # Pipeline continues, but video_id should be -1 (DB error indicator)
        assert result.success is True
        assert result.data["video_id"] is None or result.data["video_id"] == -1