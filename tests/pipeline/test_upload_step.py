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

PIECE = "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"

FOC_UPLOAD_RESULT = {
    "cid": "bafybeigtest123",
    "pieceCid": PIECE,
    "complete": True,
    "copies": [{"dataSetId": "42", "providerId": "0x1111111111111111111111111111111111111111"}],
    "catalogOwner": "0xb24ca10fb6907a2d94b0dc5dbea6b5e379d19ffd",
    "txHash": "0xabcdef123456",
}


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
        
        config = step._get_filecoin_config(context)
        
        assert config["data_set_id"] == 42
        assert config["wait_for_deal"] is False
    
    def test_get_filecoin_config_with_context_options(self):
        """Test Filecoin config with context options."""
        step = UploadStep(config={"data_set_id": 1})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"dataset_id": 99},
        )
        
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
        step = UploadStep(config={"network_mode": "testnet"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test video content")
        
        mock_js = AsyncMock(side_effect=[
            None,
            FOC_UPLOAD_RESULT,
            {"retrievable": True, "retrievalUrl": "https://example.com/piece"},
        ])
        
        config = {
            "data_set_id": 1,
            "wait_for_deal": False,
        }
        
        progress_calls = []
        
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            progress_calls.append((stage, percent, bytes_uploaded, total_bytes))
        
        with patch.object(step, "_js_call_with_retry", mock_js):
            result = await step._upload_to_filecoin(
                str(video_file),
                config,
                None,
                on_progress,
            )
        
        assert result["root_cid"] == "bafybeigtest123"
        assert result["piece_cid"] == PIECE
        assert result["filecoin_data_set_id"] == "42"
        assert mock_js.call_count == 3
        assert mock_js.call_args_list[2][0][0] == "synapse.verifyPieceRetrieval"
        assert len(progress_calls) > 0
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_with_encryption(self, tmp_path):
        """Test upload with encrypted file."""
        step = UploadStep(config={"network_mode": "testnet"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"original content")
        encrypted_file = tmp_path / "test.mp4.enc"
        encrypted_file.write_bytes(b"encrypted content")
        
        enc_result = {**FOC_UPLOAD_RESULT, "cid": "bafybeigencrypted", "txHash": "0xencrypthash"}
        mock_js = AsyncMock(side_effect=[None, enc_result, {"retrievable": True}])
        
        encryption_metadata = EncryptionMetadata(
            ciphertext=str(encrypted_file),
            iv="dGVzdGl2MTIzNDU2",
            gate={
                "chain": "BaseMainnet",
                "tokenAddress": "0x3C7d1aDdC0ED70e186a60224ab1c9f8c8969c108",
                "threshold": "1",
                "encryptedAesKey": "encKeyB64",
                "cid": "bafybeigencrypted",
            },
        )
        
        config = {"data_set_id": 1, "wait_for_deal": False}
        
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            pass
        
        with patch.object(step, "_js_call_with_retry", mock_js):
            await step._upload_to_filecoin(
                str(video_file),
                config,
                encryption_metadata,
                on_progress,
            )
        
        upload_call = mock_js.call_args_list[1]
        assert upload_call[0][1]["filePath"] == str(encrypted_file)
        assert upload_call[0][1]["metadata"]["encrypted"] is True
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_connection_failure(self, tmp_path):
        """Test handling of Synapse connection failure."""
        step = UploadStep(config={"network_mode": "testnet"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_js = AsyncMock(side_effect=RuntimeError("Connection refused"))
        config = {"data_set_id": 1, "wait_for_deal": False}
        
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            pass
        
        with patch.object(step, "_js_call_with_retry", mock_js):
            with pytest.raises(RuntimeError, match="Synapse connection failed"):
                await step._upload_to_filecoin(
                    str(video_file),
                    config,
                    None,
                    on_progress,
                )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_upload_failure(self, tmp_path):
        """Test handling of upload failure."""
        step = UploadStep(config={"network_mode": "testnet"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_js = AsyncMock(side_effect=[None, RuntimeError("Upload failed")])
        config = {"data_set_id": 1, "wait_for_deal": False}
        
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            pass
        
        with patch.object(step, "_js_call_with_retry", mock_js):
            with pytest.raises(RuntimeError, match="Upload to Filecoin failed"):
                await step._upload_to_filecoin(
                    str(video_file),
                    config,
                    None,
                    on_progress,
                )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_file_not_found(self, tmp_path):
        """Test handling of missing file."""
        step = UploadStep(config={"network_mode": "testnet"})
        config = {"data_set_id": 1, "wait_for_deal": False}
        
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            pass
        
        with pytest.raises(FileNotFoundError, match="File to upload not found"):
            await step._upload_to_filecoin(
                "/nonexistent/path/video.mp4",
                config,
                None,
                on_progress,
            )
    
    @pytest.mark.asyncio
    async def test_upload_to_filecoin_wait_for_deal(self, tmp_path):
        """Test upload with wait_for_deal enabled."""
        step = UploadStep(config={"network_mode": "testnet"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_js = AsyncMock(side_effect=[
            None,
            FOC_UPLOAD_RESULT,
            {"retrievable": True},
            {"status": "pending", "retrievable": False},
            {"status": "active", "retrievable": True},
        ])

        config = {"data_set_id": 1, "wait_for_deal": True}

        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            pass

        with patch.object(step, "_js_call_with_retry", mock_js):
            with patch("haven_cli.pipeline.steps.upload_step.asyncio.sleep", new_callable=AsyncMock):
                result = await step._upload_to_filecoin(
                    str(video_file),
                    config,
                    None,
                    on_progress,
                )

        assert result["root_cid"] == "bafybeigtest123"
        status_calls = [c for c in mock_js.call_args_list if c[0][0] == "synapse.getStatus"]
        assert len(status_calls) == 2


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
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        mock_js = AsyncMock(side_effect=[None, FOC_UPLOAD_RESULT, {"retrievable": True}])
        mock_config = MagicMock()
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch.object(step, '_js_call_with_retry', mock_js):
                with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                    with patch.object(step, '_update_database', new_callable=AsyncMock):
                        result = await step.process(context)
        
        assert result.success is True
        assert result.data["root_cid"] == "bafybeigtest123"
        assert result.data["piece_cid"] == "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
        assert result.data["cid"] == "bafybeigtest123"  # Alias
        assert context.upload_result is not None
        assert context.upload_result.root_cid == "bafybeigtest123"
    
    @pytest.mark.asyncio
    async def test_process_transient_error_retry(self, tmp_path):
        """Test retry on transient error."""
        step = UploadStep()
        step._retry_delay_seconds = 0.01
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={},
        )
        
        mock_bridge = MagicMock()
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        call_count = 0

        async def mock_js(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Connection timeout")
            if call_count == 2:
                return None
            if call_count == 3:
                return FOC_UPLOAD_RESULT
            return {"retrievable": True}

        mock_config = MagicMock()

        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch.object(step, '_js_call_with_retry', mock_js):
                with patch("haven_cli.pipeline.steps.upload_step.asyncio.sleep", new_callable=AsyncMock):
                    with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                        with patch.object(step, '_update_database', new_callable=AsyncMock):
                            result = await step.process(context)

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
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        mock_js = AsyncMock(side_effect=RuntimeError("Unauthorized: 401"))
        mock_config = MagicMock()

        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch.object(step, '_js_call_with_retry', mock_js):
                with patch("haven_cli.pipeline.steps.upload_step.get_config", return_value=mock_config):
                    result = await step.process(context)

        assert result.success is False
        assert result.failed is True
        assert result.error is not None
        assert result.error.code == "UPLOAD_ERROR"
        assert mock_js.call_count == 1


class TestUploadStepDatabase:
    """Tests for database persistence."""
    
    @pytest.mark.asyncio
    async def test_update_database(self):
        """Test updating database with upload result."""
        step = UploadStep()
        
        result = UploadResult(
            video_path="/tmp/test.mp4",
            root_cid="bafybeigtest123",
            piece_cid="bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq",
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
        assert call_args[1]["piece_cid"] == "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
    
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
