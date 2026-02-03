"""Tests for the upload pipeline step.

Tests the Filecoin upload step including:
- JS bridge integration with Synapse SDK
- Progress notification handling
- Error categorization and retry logic
- Database persistence
- Encrypted vs unencrypted file upload
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.pipeline.context import (
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.results import ErrorCategory, StepError, StepResult
from haven_cli.pipeline.steps.upload_step import UploadStep


class TestUploadStepBasics:
    """Basic tests for UploadStep."""
    
    def test_step_name(self):
        """Test step name is correct."""
        step = UploadStep()
        assert step.name == "upload"
    
    def test_max_retries(self):
        """Test max retries is set correctly."""
        step = UploadStep()
        assert step.max_retries == 3
    
    def test_retry_delay(self):
        """Test retry delay is set correctly."""
        step = UploadStep()
        assert step.retry_delay_seconds == 5.0


class TestUploadStepConfig:
    """Tests for configuration loading."""
    
    def test_get_filecoin_config(self):
        """Test Filecoin configuration loading."""
        step = UploadStep(config={"data_set_id": 42})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-api-key"
        
        with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
            config = step._get_filecoin_config(context)
        
        assert config["synapse_endpoint"] == "https://synapse.example.com"
        assert config["synapse_api_key"] == "test-api-key"
        assert config["data_set_id"] == 42
        assert config["wait_for_deal"] is False
    
    def test_get_filecoin_config_with_context_options(self):
        """Test Filecoin config with context options."""
        step = UploadStep(config={"data_set_id": 1})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"dataset_id": 99},
        )
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = None
        mock_config.pipeline.synapse_api_key = None
        
        with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
            config = step._get_filecoin_config(context)
        
        # Context option should override config
        assert config["data_set_id"] == 99
    
    def test_get_filecoin_config_wait_for_deal(self):
        """Test Filecoin config with wait_for_deal enabled."""
        step = UploadStep(config={"wait_for_deal": True})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={},
        )
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "api-key"
        
        with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
            config = step._get_filecoin_config(context)
        
        assert config["wait_for_deal"] is True


class TestUploadStepJSBridge:
    """Tests for JS bridge integration."""
    
    @pytest.mark.asyncio
    async def test_get_js_bridge(self):
        """Test getting JS bridge from manager."""
        step = UploadStep()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        
        with patch("haven_cli.pipeline.steps.upload_step.JSBridgeManager") as mock_mgr:
            mock_instance = MagicMock()
            mock_instance.get_bridge = AsyncMock(return_value=mock_bridge)
            mock_mgr.get_instance.return_value = mock_instance
            
            bridge = await step._get_js_bridge()
            
            assert bridge is mock_bridge
            mock_mgr.get_instance.assert_called_once()


class TestUploadStepUpload:
    """Tests for the upload process."""
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_success(self, tmp_path):
        """Test successful upload to Filecoin."""
        step = UploadStep()
        
        # Create test video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test video content")
        
        # Mock the bridge
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect response
            {
                "cid": "bafybeigtest123",
                "pieceCid": "baga6ea4test456",
                "dealId": "12345",
                "txHash": "0xabcdef123456",
            },  # synapse.upload response
        ])
        
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        progress_calls = []
        
        async def on_progress(stage: str, percent: int) -> None:
            progress_calls.append((stage, percent))
        
        result = await step._upload_to_filecoin(
            mock_bridge,
            str(video_file),
            config,
            None,  # No encryption
            on_progress,
        )
        
        assert result["root_cid"] == "bafybeigtest123"
        assert result["piece_cid"] == "baga6ea4test456"
        assert result["deal_id"] == "12345"
        assert result["transaction_hash"] == "0xabcdef123456"
        
        # Verify bridge calls
        assert mock_bridge.call.call_count == 2
        
        # Check synapse.connect call
        connect_call = mock_bridge.call.call_args_list[0]
        assert connect_call[0][0] == "synapse.connect"
        assert connect_call[0][1]["endpoint"] == "https://synapse.example.com"
        assert connect_call[0][1]["apiKey"] == "test-key"
        
        # Check synapse.upload call
        upload_call = mock_bridge.call.call_args_list[1]
        assert upload_call[0][0] == "synapse.upload"
        assert upload_call[0][1]["filePath"] == str(video_file)
        assert upload_call[0][1]["metadata"]["encrypted"] is False
        assert upload_call[0][1]["metadata"]["dataSetId"] == 1
        assert upload_call[0][1]["onProgress"] is True
        
        # Verify progress was reported
        assert len(progress_calls) > 0
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_with_encryption(self, tmp_path):
        """Test upload with encrypted file."""
        step = UploadStep()
        
        # Create original and encrypted files
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"original content")
        encrypted_file = tmp_path / "test.mp4.enc"
        encrypted_file.write_bytes(b"encrypted content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {
                "cid": "bafybeigencrypted",
                "pieceCid": "baga6ea4encrypted",
                "txHash": "0xencrypthash",
            },  # synapse.upload
        ])
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        encryption_metadata = EncryptionMetadata(
            ciphertext=str(encrypted_file),
            data_to_encrypt_hash="0xhash",
            access_control_conditions=[],
            chain="ethereum",
        )
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        result = await step._upload_to_filecoin(
            mock_bridge,
            str(video_file),
            config,
            encryption_metadata,
            on_progress,
        )
        
        # Verify encrypted file was used for upload
        upload_call = mock_bridge.call.call_args_list[1]
        assert upload_call[0][1]["filePath"] == str(encrypted_file)
        assert upload_call[0][1]["metadata"]["encrypted"] is True
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_missing_endpoint(self, tmp_path):
        """Test upload fails without configured endpoint."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        
        config = {
            "synapse_endpoint": None,
            "synapse_api_key": None,
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        with pytest.raises(RuntimeError, match="Synapse endpoint not configured"):
            await step._upload_to_filecoin(
                mock_bridge,
                str(video_file),
                config,
                None,
                on_progress,
            )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_connection_failure(self, tmp_path):
        """Test handling of Synapse connection failure."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Connection refused"))
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        with pytest.raises(RuntimeError, match="Synapse connection failed"):
            await step._upload_to_filecoin(
                mock_bridge,
                str(video_file),
                config,
                None,
                on_progress,
            )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_upload_failure(self, tmp_path):
        """Test handling of upload failure."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect succeeds
            RuntimeError("Upload failed"),  # synapse.upload fails
        ])
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        with pytest.raises(RuntimeError, match="Upload to Filecoin failed"):
            await step._upload_to_filecoin(
                mock_bridge,
                str(video_file),
                config,
                None,
                on_progress,
            )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_file_not_found(self, tmp_path):
        """Test handling of missing file."""
        step = UploadStep()
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(return_value=None)
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        with pytest.raises(FileNotFoundError, match="File to upload not found"):
            await step._upload_to_filecoin(
                mock_bridge,
                "/nonexistent/path/video.mp4",
                config,
                None,
                on_progress,
            )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_wait_for_deal(self, tmp_path):
        """Test upload with wait_for_deal enabled."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {
                "cid": "bafybeigtest123",
                "txHash": "0xhash",
            },  # synapse.upload
            {"status": "pending"},  # synapse.getStatus - first call
            {"status": "pending"},  # synapse.getStatus - second call
            {"status": "confirmed"},  # synapse.getStatus - confirmed
        ])
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        config = {
            "synapse_endpoint": "https://synapse.example.com",
            "synapse_api_key": "test-key",
            "data_set_id": 1,
            "wait_for_deal": True,
        }
        
        async def on_progress(stage: str, percent: int) -> None:
            pass
        
        result = await step._upload_to_filecoin(
            mock_bridge,
            str(video_file),
            config,
            None,
            on_progress,
        )
        
        assert result["root_cid"] == "bafybeigtest123"
        # Should have called getStatus 3 times
        status_calls = [call for call in mock_bridge.call.call_args_list 
                       if call[0][0] == "synapse.getStatus"]
        assert len(status_calls) == 3


class TestUploadStepErrorCategorization:
    """Tests for error categorization."""
    
    def test_categorize_transient_errors(self):
        """Test categorization of transient errors."""
        step = UploadStep()
        
        transient_errors = [
            RuntimeError("Connection timeout"),
            RuntimeError("Network unreachable"),
            RuntimeError("Rate limit exceeded"),
            RuntimeError("Service unavailable: 503"),
            RuntimeError("Bad gateway: 502"),
            RuntimeError("Gateway timeout: 504"),
        ]
        
        for error in transient_errors:
            category = step._categorize_error(error)
            assert category == ErrorCategory.TRANSIENT, f"Expected TRANSIENT for: {error}"
    
    def test_categorize_permanent_errors(self):
        """Test categorization of permanent errors."""
        step = UploadStep()
        
        permanent_errors = [
            RuntimeError("Unauthorized: 401"),
            RuntimeError("Forbidden: 403"),
            RuntimeError("Not found: 404"),
            RuntimeError("Invalid API key"),
            RuntimeError("Bad request"),
            ValueError("Invalid value"),
            TypeError("Invalid type"),
        ]
        
        for error in permanent_errors:
            category = step._categorize_error(error)
            assert category == ErrorCategory.PERMANENT, f"Expected PERMANENT for: {error}"
    
    def test_categorize_unknown_errors(self):
        """Test categorization of unknown errors."""
        step = UploadStep()
        
        unknown_errors = [
            RuntimeError("Something went wrong"),
            Exception("Generic error"),
        ]
        
        for error in unknown_errors:
            category = step._categorize_error(error)
            assert category == ErrorCategory.UNKNOWN, f"Expected UNKNOWN for: {error}"


class TestUploadStepProcess:
    """Tests for the main process method."""
    
    @pytest.mark.asyncio
    async def test_process_success(self, tmp_path):
        """Test successful upload process."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={},
        )
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {
                "cid": "bafybeigtest123",
                "pieceCid": "baga6ea4test456",
                "txHash": "0xhash",
            },  # synapse.upload
        ])
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                with patch.object(step, '_update_database', new_callable=AsyncMock):
                    result = await step.process(context)
        
        assert result.success is True
        assert result.data["root_cid"] == "bafybeigtest123"
        assert result.data["piece_cid"] == "baga6ea4test456"
        assert result.data["cid"] == "bafybeigtest123"  # Alias
        assert context.upload_result is not None
        assert context.upload_result.root_cid == "bafybeigtest123"
    
    @pytest.mark.asyncio
    async def test_process_transient_error_retry(self, tmp_path):
        """Test retry on transient error."""
        step = UploadStep()
        step._retry_delay_seconds = 0.1  # Short delay for tests
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={},
        )
        
        mock_bridge = MagicMock()
        # First call fails with transient error, second succeeds
        call_count = 0
        
        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Connection timeout")
            return {
                "cid": "bafybeigtest123",
                "pieceCid": "baga6ea4test456",
                "txHash": "0xhash",
            }
        
        mock_bridge.call = mock_call
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                with patch.object(step, '_update_database', new_callable=AsyncMock):
                    result = await step.process(context)
        
        # The process method retries internally
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_process_permanent_error_no_retry(self, tmp_path):
        """Test no retry on permanent error."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={},
        )
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Unauthorized: 401"))
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                result = await step.process(context)
        
        assert result.success is False
        assert result.failed is True
        assert result.error is not None
        assert result.error.code == "UPLOAD_ERROR"
        # Should only call once since it's a permanent error
        assert mock_bridge.call.call_count == 1


class TestUploadStepDatabase:
    """Tests for database persistence."""
    
    @pytest.mark.asyncio
    async def test_update_database(self):
        """Test updating database with upload result."""
        step = UploadStep()
        
        result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
            piece_cid="baga6ea4test456",
            transaction_hash="0xhash",
        )
        
        mock_video = MagicMock()
        mock_video.id = 42
        
        mock_repo = MagicMock()
        mock_repo.get_by_source_path.return_value = mock_video
        
        mock_session_context = MagicMock()
        mock_session_context.__enter__ = MagicMock(return_value=mock_session_context)
        mock_session_context.__exit__ = MagicMock(return_value=None)
        
        with patch("haven_cli.pipeline.steps.upload_step.get_db_session") as mock_get_session:
            mock_get_session.return_value = mock_session_context
            
            with patch("haven_cli.pipeline.steps.upload_step.VideoRepository") as mock_repo_class:
                mock_repo_class.return_value = mock_repo
                
                await step._update_database("/tmp/test.mp4", result)
        
        mock_repo.get_by_source_path.assert_called_once_with("/tmp/test.mp4")
        mock_repo.update.assert_called_once()
        
        # Check the update call
        call_args = mock_repo.update.call_args
        assert call_args[0][0] is mock_video
        assert call_args[1]["cid"] == "bafybeigtest123"
        assert call_args[1]["piece_cid"] == "baga6ea4test456"
    
    @pytest.mark.asyncio
    async def test_update_database_video_not_found(self):
        """Test updating database when video doesn't exist."""
        step = UploadStep()
        
        result = UploadResult(
            video_path="/tmp/nonexistent.mp4",
            root_cid="bafybeigtest123",
        )
        
        mock_repo = MagicMock()
        mock_repo.get_by_source_path.return_value = None
        
        mock_session_context = MagicMock()
        mock_session_context.__enter__ = MagicMock(return_value=mock_session_context)
        mock_session_context.__exit__ = MagicMock(return_value=None)
        
        with patch("haven_cli.pipeline.steps.upload_step.get_db_session") as mock_get_session:
            mock_get_session.return_value = mock_session_context
            with patch("haven_cli.pipeline.steps.upload_step.VideoRepository") as mock_repo_class:
                mock_repo_class.return_value = mock_repo
                
                # Should not raise, just log warning
                await step._update_database("/tmp/nonexistent.mp4", result)
        
        mock_repo.update.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_update_database_error(self):
        """Test handling of database error during update."""
        step = UploadStep()
        
        result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
        )
        
        with patch("haven_cli.pipeline.steps.upload_step.get_db_session") as mock_get_session:
            mock_session_context = MagicMock()
            mock_session_context.__enter__ = MagicMock(side_effect=Exception("DB connection failed"))
            mock_session_context.__exit__ = MagicMock(return_value=None)
            mock_get_session.return_value = mock_session_context
            
            # Should not raise, just log error
            await step._update_database("/tmp/test.mp4", result)


class TestUploadStepProgress:
    """Tests for progress notification handling."""
    
    @pytest.mark.asyncio
    async def test_progress_notification_handler(self, tmp_path):
        """Test progress notification handler is registered and called."""
        step = UploadStep()
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {"cid": "bafybeigtest123"},  # synapse.upload
        ])
        
        # Track notification handler registration
        notification_handler = None
        unregister_mock = MagicMock()
        
        def mock_on_notification(method, handler):
            nonlocal notification_handler
            if method == "synapse.uploadProgress":
                notification_handler = handler
            return unregister_mock
        
        mock_bridge.on_notification = mock_on_notification
        
        mock_config = MagicMock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        
        context = PipelineContext(
            source_path=video_file,
            options={},
        )
        
        # Mock _emit_event to capture progress events
        emitted_events = []
        
        async def mock_emit_event(event_type, ctx, data):
            emitted_events.append((event_type, data))
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                with patch.object(step, '_update_database', new_callable=AsyncMock):
                    with patch.object(step, '_emit_event', mock_emit_event):
                        result = await step.process(context)
        
        # Simulate a progress notification from the bridge
        if notification_handler:
            notification_handler({"percentage": 50, "stage": "uploading"})
        
        # Verify unregister was called (cleanup)
        unregister_mock.assert_called_once()
        
        assert result.success is True
