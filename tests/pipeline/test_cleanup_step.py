"""Tests for the cleanup pipeline step.

Tests the file cleanup step including:
- File deletion after successful upload
- Conditional enabling/disabling
- Handling of missing files
- Proper handling of encrypted files and metadata
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.pipeline.context import (
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.results import StepResult
from haven_cli.pipeline.steps.cleanup_step import CleanupStep


class TestCleanupStepBasics:
    """Basic tests for CleanupStep."""
    
    def test_step_name(self):
        """Test step name is correct."""
        step = CleanupStep()
        assert step.name == "cleanup"
    
    def test_default_enabled(self):
        """Test cleanup is disabled by default for safety."""
        step = CleanupStep()
        assert step.default_enabled is False
    
    def test_enabled_option(self):
        """Test enabled option name."""
        step = CleanupStep()
        assert step.enabled_option == "cleanup_enabled"


class TestCleanupStepShouldSkip:
    """Tests for the should_skip logic."""
    
    @pytest.mark.asyncio
    async def test_skip_when_disabled(self):
        """Test cleanup is skipped when not enabled."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"cleanup_enabled": False},
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    @pytest.mark.asyncio
    async def test_skip_when_no_upload(self):
        """Test cleanup is skipped when upload didn't succeed."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"cleanup_enabled": True},
        )
        # No upload_result set
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    @pytest.mark.asyncio
    async def test_skip_when_no_cid(self):
        """Test cleanup is skipped when upload has no CID."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="",  # Empty CID
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    @pytest.mark.asyncio
    async def test_no_skip_when_enabled_and_uploaded(self):
        """Test cleanup runs when enabled and upload succeeded."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is False
    
    @pytest.mark.asyncio
    async def test_no_skip_when_enabled_via_step_config(self):
        """Test cleanup runs when enabled via step config (not context options).
        
        This tests the fix for the bug where cleanup_enabled in the config file
        was not being respected because the step only checked context.options.
        """
        step = CleanupStep(config={"cleanup_enabled": True})
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},  # cleanup_enabled NOT in context.options
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is False
    
    @pytest.mark.asyncio
    async def test_skip_when_disabled_via_step_config(self):
        """Test cleanup is skipped when disabled via step config."""
        step = CleanupStep(config={"cleanup_enabled": False})
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},  # cleanup_enabled NOT in context.options
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    @pytest.mark.asyncio
    async def test_context_options_override_step_config(self):
        """Test that context.options takes precedence over step config."""
        # Step config says enabled=True, but context.options says False
        step = CleanupStep(config={"cleanup_enabled": True})
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"cleanup_enabled": False},  # Should override step config
        )
        context.upload_result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True


class TestCleanupStepProcess:
    """Tests for the cleanup process."""
    
    @pytest.mark.asyncio
    async def test_cleanup_deletes_original_file(self, tmp_path):
        """Test cleanup deletes the original video file."""
        step = CleanupStep()
        
        # Create test files
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test video content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        
        result = await step.process(context)
        
        assert result.success is True
        assert not video_file.exists()
        assert str(video_file) in result.data["files_deleted"]
    
    
    @pytest.mark.asyncio
    async def test_cleanup_handles_missing_files(self, tmp_path):
        """Test cleanup handles already-deleted files gracefully."""
        step = CleanupStep()
        
        # Create only the video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        # Referenced encrypted file doesn't exist
        encrypted_file = tmp_path / "test.mp4.enc"
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        context.encrypted_video_path = str(encrypted_file)
        
        result = await step.process(context)
        
        assert result.success is True
        assert not video_file.exists()
        assert str(video_file) in result.data["files_deleted"]
        assert str(encrypted_file) in result.data["files_missing"]
    
    @pytest.mark.asyncio
    async def test_cleanup_reports_bytes_freed(self, tmp_path):
        """Test cleanup reports bytes freed correctly."""
        step = CleanupStep()
        
        # Create test file with known size
        video_file = tmp_path / "test.mp4"
        content = b"x" * 1024  # 1KB
        video_file.write_bytes(content)
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        
        result = await step.process(context)
        
        assert result.success is True
        assert result.data["bytes_freed"] == 1024
    
    @pytest.mark.asyncio
    async def test_cleanup_continues_on_error(self, tmp_path):
        """Test cleanup continues even if one file fails to delete."""
        step = CleanupStep()
        
        # Create test file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        
        # Mock Path.unlink to fail (simulating permission error)
        def mock_unlink(self, missing_ok=False):
            raise PermissionError("Permission denied")
        
        with patch.object(Path, 'unlink', mock_unlink):
            result = await step.process(context)
        
        # Should still succeed (non-fatal error) - cleanup errors don't fail the pipeline
        assert result.success is True
        # File should be in neither deleted nor missing (permission error)
        assert str(video_file) not in result.data["files_deleted"]
        assert str(video_file) not in result.data["files_missing"]


class TestCleanupStepGetFiles:
    """Tests for the _get_files_to_cleanup method."""
    
    def test_get_files_original_only(self):
        """Test getting files when only original exists."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        
        files = step._get_files_to_cleanup(context)
        
        assert files == ["/tmp/test.mp4"]
    
    
    def test_get_files_no_duplicates(self):
        """Test that duplicate file paths are removed."""
        step = CleanupStep()
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        # Set encrypted path same as original (edge case)
        context.encrypted_video_path = "/tmp/test.mp4"
        
        files = step._get_files_to_cleanup(context)
        
        # Should only appear once
        assert files.count("/tmp/test.mp4") == 1


class TestCleanupStepMetadataPath:
    """Tests for the _get_metadata_path method."""
    
    def test_get_metadata_from_step_data(self, tmp_path):
        """Test getting metadata path from step_data."""
        step = CleanupStep()
        
        metadata_file = tmp_path / "test.meta"
        metadata_file.write_bytes(b"metadata")
        
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        context.set_step_data("encrypt", "metadata_path", str(metadata_file))
        
        path = step._get_metadata_path(context)
        
        assert path == str(metadata_file)
    
    


class TestCleanupStepEvents:
    """Tests for event emission."""
    
    @pytest.mark.asyncio
    async def test_cleanup_started_event(self, tmp_path):
        """Test CLEANUP_STARTED event is emitted."""
        step = CleanupStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test")
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        
        emitted_events = []
        
        async def mock_emit(event_type, ctx, data):
            emitted_events.append((event_type, data))
        
        with patch.object(step, '_emit_event', mock_emit):
            await step.process(context)
        
        from haven_cli.pipeline.events import EventType
        event_types = [e[0] for e in emitted_events]
        assert EventType.CLEANUP_STARTED in event_types
        assert EventType.CLEANUP_COMPLETE in event_types
    
    @pytest.mark.asyncio
    async def test_cleanup_complete_event_data(self, tmp_path):
        """Test CLEANUP_COMPLETE event contains correct data."""
        step = CleanupStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"cleanup_enabled": True},
        )
        context.upload_result = UploadResult(
            video_path=str(video_file),
            root_cid="bafybeigtest123",
        )
        
        emitted_events = []
        
        async def mock_emit(event_type, ctx, data):
            emitted_events.append((event_type, data))
        
        with patch.object(step, '_emit_event', mock_emit):
            await step.process(context)
        
        from haven_cli.pipeline.events import EventType
        complete_event = next(e for e in emitted_events if e[0] == EventType.CLEANUP_COMPLETE)
        
        assert "files_deleted" in complete_event[1]
        assert "files_missing" in complete_event[1]
        assert "bytes_freed" in complete_event[1]
