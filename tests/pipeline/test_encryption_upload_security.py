"""Security tests for encryption/upload interaction.

Tests to verify that when encryption is requested but fails,
the system does NOT upload the unencrypted file (fail open).
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.manager import PipelineManager
from haven_cli.pipeline.steps.encrypt_step import EncryptStep
from haven_cli.pipeline.steps.upload_step import UploadStep
from haven_cli.pipeline.steps.ingest_step import IngestStep


class TestEncryptionUploadSecurity:
    """Security tests for encryption/upload fail-open scenarios."""
    
    @pytest.mark.asyncio
    async def test_encryption_failure_prevents_upload_fail_open(self, tmp_path):
        """
        SECURITY TEST: Verify that encryption failure prevents upload.
        
        This is a critical security test to prevent "fail open" behavior.
        When a user requests encryption, if encryption fails, the system
        MUST NOT upload the unencrypted file.
        
        Scenario:
        1. User requests encryption (encrypt=True)
        2. Encryption step fails (e.g., Haven-AOL unavailable)
        3. Upload step should NOT execute
        4. Original file should NOT be uploaded
        """
        # Create test video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"sensitive video content")
        
        # Create context with encryption enabled
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "upload_enabled": True},
        )
        
        # Create pipeline with ingest, encrypt, and upload steps
        manager = PipelineManager()
        manager.register_step(IngestStep())
        manager.register_step(EncryptStep())
        manager.register_step(UploadStep())
        
        # Mock encryption to fail
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Haven-AOL unavailable"))
        
        # Mock upload bridge (should NOT be called)
        mock_upload_bridge = MagicMock()
        mock_upload_bridge.call = AsyncMock(return_value={
            "cid": "bafybeigtest123",
        })
        
        with patch("haven_cli.pipeline.steps.encrypt_step.JSBridgeManager") as mock_encrypt_mgr:
            mock_encrypt_instance = MagicMock()
            mock_encrypt_instance.get_bridge = AsyncMock(return_value=mock_bridge)
            mock_encrypt_mgr.get_instance.return_value = mock_encrypt_instance
            
            with patch("haven_cli.pipeline.steps.upload_step.JSBridgeManager") as mock_upload_mgr:
                mock_upload_instance = MagicMock()
                mock_upload_instance.get_bridge = AsyncMock(return_value=mock_upload_bridge)
                mock_upload_mgr.get_instance.return_value = mock_upload_instance
                
                # Execute pipeline
                result = await manager.process(context)
        
        # SECURITY ASSERTIONS
        
        # 1. Pipeline should fail (encryption failed)
        assert result.success is False, "Pipeline should fail when encryption fails"
        
        # 2. Encryption step should have failed
        encrypt_result = result.get_step_result("encrypt")
        assert encrypt_result is not None, "Encrypt step should have executed"
        assert encrypt_result.failed is True, "Encrypt step should have failed"
        
        # 3. Upload step should NOT have executed (this is the critical security check)
        upload_result = result.get_step_result("upload")
        if upload_result is not None:
            # If upload step exists, it should be SKIPPED, not executed
            assert upload_result.skipped is True, (
                "Upload step should be SKIPPED when encryption fails, "
                "not executed with unencrypted file"
            )
        
        # 4. Upload bridge should NOT have been called
        # (or at minimum, should not have uploaded the original file)
        upload_calls = [
            call for call in mock_upload_bridge.call.call_args_list
            if call[0][0] == "synapse.upload"
        ]
        assert len(upload_calls) == 0, (
            "Upload should NOT be attempted when encryption fails. "
            "This would be a security vulnerability (fail open)."
        )
        
        # 5. No CID should be present (nothing was uploaded)
        assert result.final_cid is None, "No CID should exist when encryption fails"
    
    @pytest.mark.asyncio
    async def test_encryption_skipped_allows_upload(self, tmp_path):
        """
        Verify that when encryption is NOT requested, upload proceeds normally.
        
        This is the expected behavior when encryption is disabled.
        """
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"video content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": False, "upload_enabled": True},
        )
        
        manager = PipelineManager()
        manager.register_step(IngestStep())
        manager.register_step(EncryptStep())
        manager.register_step(UploadStep())
        
        # Mock upload to succeed
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {"cid": "bafybeigtest123"},  # synapse.upload
        ])
        mock_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        with patch("haven_cli.pipeline.steps.upload_step.JSBridgeManager") as mock_mgr:
            mock_instance = MagicMock()
            mock_instance.get_bridge = AsyncMock(return_value=mock_bridge)
            mock_mgr.get_instance.return_value = mock_instance
            
            result = await manager.process(context)
        
        # Encryption should be skipped
        encrypt_result = result.get_step_result("encrypt")
        assert encrypt_result is not None
        assert encrypt_result.skipped is True
        
        # Upload should succeed
        upload_result = result.get_step_result("upload")
        assert upload_result is not None
        assert upload_result.success is True
        assert result.final_cid == "bafybeigtest123"
    
    @pytest.mark.asyncio
    async def test_encryption_success_upload_encrypted_file(self, tmp_path, monkeypatch):
        """
        Verify that when encryption succeeds, the encrypted file is uploaded.
        
        This is the expected secure behavior.
        """
        # Set a test private key
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"sensitive content")
        
        # Create the encrypted file (mocked encryption won't create it)
        encrypted_file = tmp_path / "test.mp4.encrypted"
        encrypted_file.write_bytes(b"encrypted content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "upload_enabled": True},
        )
        
        manager = PipelineManager()
        manager.register_step(IngestStep())
        manager.register_step(EncryptStep(config={"owner_wallet": "0x123", "chain": "ethereum"}))
        manager.register_step(UploadStep())
        
        # Mock encryption to succeed
        mock_encrypt_bridge = MagicMock()
        mock_encrypt_bridge.call = AsyncMock(side_effect=[
            None,  # connect
            {
                "encryptedFilePath": str(encrypted_file),
                "metadataPath": str(video_file) + ".encrypted.meta.json",
                "metadata": {
                    "keyHash": "0xhash123",
                    "version": "hybrid-v1",
                },
                "originalSize": 17,
                "encryptedSize": 33,
            },  # encrypt
        ])
        mock_encrypt_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        # Mock upload to succeed
        mock_upload_bridge = MagicMock()
        mock_upload_bridge.call = AsyncMock(side_effect=[
            None,  # synapse.connect
            {"cid": "bafybeigencrypted"},  # synapse.upload
        ])
        mock_upload_bridge.on_notification = MagicMock(return_value=MagicMock())
        
        with patch("haven_cli.pipeline.steps.encrypt_step.JSBridgeManager") as mock_encrypt_mgr:
            mock_encrypt_instance = MagicMock()
            mock_encrypt_instance.get_bridge = AsyncMock(return_value=mock_encrypt_bridge)
            mock_encrypt_mgr.get_instance.return_value = mock_encrypt_instance
            
            with patch("haven_cli.pipeline.steps.upload_step.JSBridgeManager") as mock_upload_mgr:
                mock_upload_instance = MagicMock()
                mock_upload_instance.get_bridge = AsyncMock(return_value=mock_upload_bridge)
                mock_upload_mgr.get_instance.return_value = mock_upload_instance
                
                result = await manager.process(context)
        
        # Both steps should succeed
        encrypt_result = result.get_step_result("encrypt")
        assert encrypt_result.success is True
        
        upload_result = result.get_step_result("upload")
        assert upload_result.success is True
        
        # Verify encrypted file was uploaded, not original
        upload_call = [
            call for call in mock_upload_bridge.call.call_args_list
            if call[0][0] == "synapse.upload"
        ][0]
        
        uploaded_file_path = upload_call[0][1]["filePath"]
        assert uploaded_file_path.endswith(".encrypted"), (
            "Encrypted file (.encrypted) should be uploaded, not original file"
        )
        assert upload_call[0][1]["metadata"]["encrypted"] is True
        
        assert result.final_cid == "bafybeigencrypted"
    
    @pytest.mark.asyncio
    async def test_partial_encryption_metadata_prevents_upload(self, tmp_path):
        """
        Verify that incomplete encryption metadata prevents upload.
        
        Edge case: What if encryption partially succeeds but doesn't
        produce a valid encrypted file?
        """
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "upload_enabled": True},
        )
        
        manager = PipelineManager()
        manager.register_step(IngestStep())
        manager.register_step(EncryptStep())
        manager.register_step(UploadStep())
        
        # Mock encryption to return incomplete result (no ciphertext path)
        mock_encrypt_bridge = MagicMock()
        mock_encrypt_bridge.call = AsyncMock(side_effect=[
            None,
            {
                "dataToEncryptHash": "0xhash",
                # Missing ciphertext
            },
        ])
        
        mock_upload_bridge = MagicMock()
        mock_upload_bridge.call = AsyncMock(return_value={"cid": "bafybeigtest"})
        
        with patch("haven_cli.pipeline.steps.encrypt_step.JSBridgeManager") as mock_encrypt_mgr:
            mock_encrypt_instance = MagicMock()
            mock_encrypt_instance.get_bridge = AsyncMock(return_value=mock_encrypt_bridge)
            mock_encrypt_mgr.get_instance.return_value = mock_encrypt_instance
            
            with patch("haven_cli.pipeline.steps.upload_step.JSBridgeManager") as mock_upload_mgr:
                mock_upload_instance = MagicMock()
                mock_upload_instance.get_bridge = AsyncMock(return_value=mock_upload_bridge)
                mock_upload_mgr.get_instance.return_value = mock_upload_instance
                
                result = await manager.process(context)
        
        # Pipeline should fail
        assert result.success is False
        
        # Upload should not have been called with original file
        upload_calls = [
            call for call in mock_upload_bridge.call.call_args_list
            if call[0][0] == "synapse.upload"
        ]
        assert len(upload_calls) == 0, (
            "Upload should not proceed with incomplete encryption metadata"
        )
