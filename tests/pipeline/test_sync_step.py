"""Tests for the sync pipeline step.

Tests the Arkiv blockchain sync step including:
- Configuration loading
- Skip conditions
- Entity creation and updates
- Database persistence
- Error handling
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.pipeline.context import (
    AIAnalysisResult,
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
    VideoMetadata,
)
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import ErrorCategory, StepResult
from haven_cli.pipeline.steps.sync_step import SyncStep
from haven_cli.services.arkiv_sync import InsufficientGasError


class TestSyncStepBasics:
    """Basic tests for SyncStep."""
    
    def test_step_name(self):
        """Test step name is correct."""
        step = SyncStep()
        assert step.name == "sync"
    
    def test_enabled_option(self):
        """Test enabled option is correct."""
        step = SyncStep()
        assert step.enabled_option == "arkiv_sync_enabled"
    
    def test_default_enabled(self):
        """Test default enabled value."""
        step = SyncStep()
        assert step.default_enabled is False
    
    def test_max_retries(self):
        """Test max retries is set correctly."""
        step = SyncStep()
        assert step.max_retries == 3


class TestSyncStepSkipConditions:
    """Tests for sync step skip conditions."""
    
    async def test_skip_when_disabled(self):
        """Test step is skipped when arkiv_sync_enabled is False."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": False}
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    async def test_skip_when_no_upload_result(self):
        """Test step is skipped when no upload result."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True}
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    async def test_skip_when_no_root_cid(self):
        """Test step is skipped when upload result has no root CID."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid=""
            )
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is True
    
    async def test_no_skip_when_ready(self):
        """Test step is not skipped when ready to sync."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        should_skip = await step.should_skip(context)
        
        assert should_skip is False
    
    async def test_skip_reason_no_upload(self):
        """Test skip reason when no upload result."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True}
        )
        
        reason = await step._get_skip_reason(context)
        
        assert "upload" in reason.lower()
    
    async def test_skip_reason_no_cid(self):
        """Test skip reason when no CID."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid=""
            )
        )
        
        reason = await step._get_skip_reason(context)
        
        assert "CID" in reason


class TestSyncStepConfig:
    """Tests for configuration loading."""
    
    def test_get_arkiv_config_with_explicit_values(self):
        """Test config with explicit values."""
        step = SyncStep(config={
            "arkiv_private_key": "test_key",
            "arkiv_rpc_url": "https://test.rpc",
            "arkiv_sync_enabled": True,
            "arkiv_expiration_seconds": 7200
        })
        
        config = step._get_arkiv_config()
        
        assert config.private_key == "test_key"
        assert config.rpc_url == "https://test.rpc"
        assert config.expires_in == 7200
    
    def test_get_arkiv_config_defaults(self):
        """Test config with default values."""
        step = SyncStep(config={})
        
        with patch("haven_cli.pipeline.steps.sync_step.build_arkiv_config") as mock_build:
            mock_config = MagicMock()
            mock_build.return_value = mock_config
            
            config = step._get_arkiv_config()
            
            mock_build.assert_called_once()


class TestSyncStepProcess:
    """Tests for the process method."""
    
    async def test_successful_sync_new_entity(self):
        """Test successful sync creating new entity."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        mock_client = MagicMock()
        mock_client.sync_context.return_value = {
            "entity_key": "entity_abc123",
            "transaction_hash": "0xtxhash",
            "is_update": False
        }
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_update_database") as mock_update_db:
                with patch.object(step, "_emit_event", new=AsyncMock()) as mock_emit:
                    result = await step.process(context)
        
        assert result.success is True
        assert result.data["entity_key"] == "entity_abc123"
        assert result.data["transaction_hash"] == "0xtxhash"
        assert result.data["is_update"] is False
        assert context.arkiv_entity_key == "entity_abc123"
    
    async def test_successful_sync_update_entity(self):
        """Test successful sync updating existing entity."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        mock_client = MagicMock()
        mock_client.sync_context.return_value = {
            "entity_key": "entity_existing",
            "transaction_hash": "0xtxhash",
            "is_update": True
        }
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_update_database") as mock_update_db:
                with patch.object(step, "_emit_event", new=AsyncMock()) as mock_emit:
                    result = await step.process(context)
        
        assert result.success is True
        assert result.data["is_update"] is True
    
    async def test_sync_disabled_returns_skipped(self):
        """Test that disabled sync returns skipped result."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        mock_client = MagicMock()
        mock_client.sync_context.return_value = None  # Disabled
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_emit_event", new=AsyncMock()) as mock_emit:
                result = await step.process(context)
        
        assert result.success is True
        assert result.data.get("skipped") is True
    
    async def test_insufficient_gas_error(self):
        """Test handling of insufficient gas error."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        gas_error = InsufficientGasError(
            message="Insufficient GLM for gas",
            wallet_address="0x123",
            original_error=Exception("insufficient funds"),
            chain_name="Arkiv",
            native_token_symbol="GLM"
        )
        
        mock_client = MagicMock()
        mock_client.sync_context.side_effect = gas_error
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_emit_event", new=AsyncMock()) as mock_emit:
                result = await step.process(context)
        
        assert result.success is False
        assert result.error.code == "INSUFFICIENT_GAS"
        assert result.error.details["wallet_address"] == "0x123"
        assert result.error.details["chain_name"] == "Arkiv"
    
    async def test_general_error(self):
        """Test handling of general errors."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        mock_client = MagicMock()
        mock_client.sync_context.side_effect = Exception("Connection failed")
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_emit_event", new=AsyncMock()) as mock_emit:
                result = await step.process(context)
        
        assert result.success is False
        assert result.error.code == "SYNC_ERROR"


class TestSyncStepDatabase:
    """Tests for database operations."""
    
    async def test_update_database_with_video_id(self):
        """Test updating database with video_id in context."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_id=42
        )
        
        mock_video = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = mock_video
        
        with patch("haven_cli.pipeline.steps.sync_step.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)
            
            with patch("haven_cli.pipeline.steps.sync_step.VideoRepository", return_value=mock_repo):
                await step._update_database(context, "entity_key_123")
        
        mock_repo.update.assert_called_once_with(mock_video, arkiv_entity_key="entity_key_123")
    
    async def test_update_database_without_video_id(self):
        """Test updating database by looking up source path."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_id=None
        )
        
        mock_video = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_source_path.return_value = mock_video
        
        with patch("haven_cli.pipeline.steps.sync_step.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)
            
            with patch("haven_cli.pipeline.steps.sync_step.VideoRepository", return_value=mock_repo):
                await step._update_database(context, "entity_key_123")
        
        mock_repo.get_by_source_path.assert_called_once_with("/tmp/test.mp4")
        mock_repo.update.assert_called_once_with(mock_video, arkiv_entity_key="entity_key_123")
    
    async def test_update_database_video_not_found(self):
        """Test handling when video not found in database."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_id=None
        )
        
        mock_repo = MagicMock()
        mock_repo.get_by_source_path.return_value = None
        
        with patch("haven_cli.pipeline.steps.sync_step.get_db_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=None)
            
            with patch("haven_cli.pipeline.steps.sync_step.VideoRepository", return_value=mock_repo):
                # Should not raise
                await step._update_database(context, "entity_key_123")
        
        mock_repo.update.assert_not_called()


class TestSyncStepErrorCategorization:
    """Tests for error categorization."""
    
    def test_transient_timeout_error(self):
        """Test categorization of timeout errors."""
        step = SyncStep()
        error = Exception("Connection timeout")
        
        category = step._categorize_error(error)
        
        assert category == ErrorCategory.TRANSIENT
    
    def test_transient_network_error(self):
        """Test categorization of network errors."""
        step = SyncStep()
        error = Exception("Network unreachable")
        
        category = step._categorize_error(error)
        
        assert category == ErrorCategory.TRANSIENT
    
    def test_permanent_invalid_error(self):
        """Test categorization of invalid errors."""
        step = SyncStep()
        error = Exception("Invalid parameters")
        
        category = step._categorize_error(error)
        
        assert category == ErrorCategory.PERMANENT
    
    def test_permanent_unauthorized_error(self):
        """Test categorization of unauthorized errors."""
        step = SyncStep()
        error = Exception("Unauthorized access")
        
        category = step._categorize_error(error)
        
        assert category == ErrorCategory.PERMANENT
    
    def test_unknown_error(self):
        """Test categorization of unknown errors."""
        step = SyncStep()
        error = Exception("Something went wrong")
        
        category = step._categorize_error(error)
        
        assert category == ErrorCategory.UNKNOWN


class TestSyncStepEvents:
    """Tests for event emission."""
    
    async def test_sync_requested_event(self):
        """Test SYNC_REQUESTED event is emitted."""
        step = SyncStep()
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"arkiv_sync_enabled": True},
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        emitted_events = []
        
        async def capture_event(event_type, ctx, data):
            emitted_events.append((event_type, data))
        
        mock_client = MagicMock()
        mock_client.sync_context.return_value = {
            "entity_key": "entity_123",
            "transaction_hash": "0xtx",
            "is_update": False
        }
        
        with patch("haven_cli.pipeline.steps.sync_step.ArkivSyncClient", return_value=mock_client):
            with patch.object(step, "_update_database"):
                with patch.object(step, "_emit_event", side_effect=capture_event):
                    await step.process(context)
        
        # Check that SYNC_REQUESTED was emitted
        event_types = [e[0] for e in emitted_events]
        assert EventType.SYNC_REQUESTED in event_types
        
        # Check that SYNC_COMPLETE was emitted
        assert EventType.SYNC_COMPLETE in event_types
    
    async def test_on_skip_handler(self):
        """Test on_skip handler."""
        step = SyncStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        # Should not raise
        await step.on_skip(context, "Test skip reason")
