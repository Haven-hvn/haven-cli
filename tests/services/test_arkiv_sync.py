"""Tests for Arkiv sync service.

Tests the Arkiv synchronization service including:
- Configuration building
- Payload and attribute building
- Entity creation and updates
- Error handling
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haven_cli.pipeline.context import (
    AIAnalysisResult,
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
    VideoMetadata,
)
from haven_cli.services.arkiv_sync import (
    ArkivSyncClient,
    ArkivSyncConfig,
    _build_attributes,
    _build_payload,
    _extract_transaction_hash,
    _is_413_error,
    build_arkiv_config,
)


class TestBuildArkivConfig:
    """Tests for build_arkiv_config function."""
    
    def test_explicit_values(self):
        """Test config with explicit values."""
        config = build_arkiv_config(
            private_key="test_key",
            rpc_url="https://test.rpc",
            enabled=True,
            expires_in=3600
        )
        
        assert config.private_key == "test_key"
        assert config.rpc_url == "https://test.rpc"
        assert config.enabled is True
        assert config.expires_in == 3600
    
    def test_disabled_when_no_private_key(self):
        """Test that sync is disabled when no private key provided."""
        config = build_arkiv_config(
            private_key=None,
            enabled=True
        )
        
        assert config.enabled is False
        assert config.private_key is None
    
    def test_disabled_by_setting(self):
        """Test that sync is disabled when enabled=False."""
        config = build_arkiv_config(
            private_key="test_key",
            enabled=False
        )
        
        assert config.enabled is False
    
    def test_default_rpc_url(self):
        """Test default RPC URL."""
        config = build_arkiv_config(private_key="test_key")
        
        assert "arkiv" in config.rpc_url
    
    def test_default_expiration(self):
        """Test default expiration (4 weeks)."""
        config = build_arkiv_config(private_key="test_key")
        
        # 4 weeks in seconds
        expected_expires = 4 * 7 * 24 * 60 * 60
        assert config.expires_in == expected_expires
    
    @patch.dict(os.environ, {"FILECOIN_PRIVATE_KEY": "filecoin_key"}, clear=True)
    def test_env_var_filecoin_key(self):
        """Test reading private key from FILECOIN_PRIVATE_KEY env var."""
        config = build_arkiv_config(enabled=True)
        
        assert config.private_key == "filecoin_key"
    
    @patch.dict(os.environ, {"ARKIV_PRIVATE_KEY": "arkiv_key"}, clear=True)
    def test_env_var_arkiv_key(self):
        """Test reading private key from ARKIV_PRIVATE_KEY env var."""
        config = build_arkiv_config(enabled=True)
        
        assert config.private_key == "arkiv_key"
    
    @patch.dict(os.environ, {
        "FILECOIN_PRIVATE_KEY": "filecoin_key",
        "ARKIV_PRIVATE_KEY": "arkiv_key"
    }, clear=True)
    def test_env_var_filecoin_takes_precedence(self):
        """Test that FILECOIN_PRIVATE_KEY takes precedence over ARKIV_PRIVATE_KEY."""
        config = build_arkiv_config(enabled=True)
        
        assert config.private_key == "filecoin_key"
    
    @patch.dict(os.environ, {"ARKIV_SYNC_ENABLED": "true"}, clear=True)
    def test_env_var_enabled_true(self):
        """Test ARKIV_SYNC_ENABLED=true."""
        config = build_arkiv_config(private_key="test_key")
        
        assert config.enabled is True
    
    @patch.dict(os.environ, {"ARKIV_SYNC_ENABLED": "false"}, clear=True)
    def test_env_var_enabled_false(self):
        """Test ARKIV_SYNC_ENABLED=false."""
        config = build_arkiv_config(private_key="test_key")
        
        assert config.enabled is False
    
    @patch.dict(os.environ, {"ARKIV_RPC_URL": "https://custom.rpc"}, clear=True)
    def test_env_var_rpc_url(self):
        """Test ARKIV_RPC_URL env var."""
        config = build_arkiv_config(private_key="test_key")
        
        assert config.rpc_url == "https://custom.rpc"
    
    @patch.dict(os.environ, {"ARKIV_EXPIRATION_WEEKS": "8"}, clear=True)
    def test_env_var_expiration_weeks(self):
        """Test ARKIV_EXPIRATION_WEEKS env var."""
        config = build_arkiv_config(private_key="test_key")
        
        # 8 weeks in seconds
        expected_expires = 8 * 7 * 24 * 60 * 60
        assert config.expires_in == expected_expires


class TestBuildPayload:
    """Tests for _build_payload function."""
    
    def test_basic_payload(self):
        """Test basic payload structure."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        payload = _build_payload(context)
        
        assert payload["version"] == "1.0"
        assert payload["type"] == "video"
        assert "archived_at" in payload
    
    def test_payload_with_video_metadata(self):
        """Test payload with video metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="Test Video",
                duration=120.5,
                file_size=1024000,
                codec="h264"
            )
        )
        
        payload = _build_payload(context)
        
        assert payload["duration"] == 120.5
        assert payload["file_size"] == 1024000
        assert payload["codec"] == "h264"
    
    def test_payload_with_upload_result(self):
        """Test payload with upload result."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid="QmPiece456"
            )
        )
        
        payload = _build_payload(context)
        
        assert payload["root_cid"] == "QmTest123"
        assert payload["piece_cid"] == "QmPiece456"
    
    def test_payload_with_analysis(self):
        """Test payload with analysis result."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            analysis_result=AIAnalysisResult(
                video_path="/tmp/test.mp4",
                timestamps=[{"start": 0, "end": 10}],
                tags={"tag1": 0.9},
                confidence=0.85
            )
        )
        
        payload = _build_payload(context)
        
        assert payload["has_ai_data"] is True
        assert payload["tag_count"] == 1
        assert payload["timestamp_count"] == 1
        assert payload["analysis_confidence"] == 0.85
    
    def test_payload_with_encryption(self):
        """Test payload with encryption metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=EncryptionMetadata(
                ciphertext="encrypted_data",
                data_to_encrypt_hash="hash123",
                chain="ethereum"
            )
        )
        
        payload = _build_payload(context)
        
        assert payload["encrypted"] is True
        assert payload["encryption_chain"] == "ethereum"
        assert payload["encryption_ciphertext"] == "encrypted_data"
        assert payload["encryption_data_hash"] == "hash123"


class TestBuildAttributes:
    """Tests for _build_attributes function."""
    
    def test_basic_attributes(self):
        """Test basic attributes."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4")
        )
        
        attrs = _build_attributes(context)
        
        assert "title" in attrs
        assert "created_at" in attrs
        assert attrs["title"] == "test"  # stem of filename
    
    def test_attributes_with_metadata(self):
        """Test attributes with video metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="My Video",
                creator_handle="@creator",
                source_uri="https://example.com/video",
                phash="abc123"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["title"] == "My Video"
        assert attrs["creator_handle"] == "@creator"
        assert attrs["source_uri"] == "https://example.com/video"
        assert attrs["phash"] == "abc123"
    
    def test_attributes_with_upload(self):
        """Test attributes with upload result."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["root_cid"] == "QmTest123"
        assert "cid_hash" in attrs
        
        # Verify CID hash calculation
        expected_hash = hashlib.sha256("QmTest123".encode()).hexdigest()
        assert attrs["cid_hash"] == expected_hash
    
    def test_attributes_with_encryption(self):
        """Test attributes with encryption."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=EncryptionMetadata(chain="ethereum")
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["is_encrypted"] == 1
    
    def test_mime_type_attribute(self):
        """Test MIME type attribute."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                mime_type="video/mp4"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["mime_type"] == "video/mp4"


class TestExtractTransactionHash:
    """Tests for _extract_transaction_hash function."""
    
    def test_from_transaction_hash_attribute(self):
        """Test extracting from transactionHash attribute."""
        receipt = MagicMock()
        receipt.transactionHash = "0xabc123"
        
        result = _extract_transaction_hash(receipt)
        
        assert result == "0xabc123"
    
    def test_from_hash_attribute(self):
        """Test extracting from hash attribute."""
        receipt = MagicMock(spec=[])
        receipt.hash = "0xdef456"
        
        result = _extract_transaction_hash(receipt)
        
        assert result == "0xdef456"
    
    def test_from_tx_hash_attribute(self):
        """Test extracting from tx_hash attribute."""
        receipt = MagicMock(spec=[])
        receipt.tx_hash = "0xghi789"
        
        result = _extract_transaction_hash(receipt)
        
        assert result == "0xghi789"
    
    def test_from_dict(self):
        """Test extracting from dict-like receipt."""
        receipt = {
            "transactionHash": "0xjkl012",
            "blockNumber": 123
        }
        
        result = _extract_transaction_hash(receipt)
        
        assert result == "0xjkl012"
    
    def test_from_nested_receipt(self):
        """Test extracting from nested receipt object."""
        inner = MagicMock(spec=[])
        inner.transactionHash = "0xmno345"
        
        receipt = MagicMock(spec=[])
        receipt.receipt = inner
        
        result = _extract_transaction_hash(receipt)
        
        assert result == "0xmno345"
    
    def test_none_receipt(self):
        """Test handling of None receipt."""
        result = _extract_transaction_hash(None)
        
        assert result is None
    
    def test_empty_receipt(self):
        """Test handling of empty receipt."""
        receipt = MagicMock(spec=[])
        
        result = _extract_transaction_hash(receipt)
        
        assert result is None


class TestIs413Error:
    """Tests for _is_413_error function."""
    
    def test_direct_http_error(self):
        """Test direct HTTPError with 413 status."""
        # Test is skipped if requests not available
        try:
            from requests.exceptions import HTTPError
            error = HTTPError("413 Request Entity Too Large")
            error.response = MagicMock()
            error.response.status_code = 413
            
            result = _is_413_error(error)
            
            assert result is True
        except ImportError:
            pytest.skip("requests not installed")
    
    def test_error_string_contains_413(self):
        """Test detection via error string."""
        error = Exception("Request Entity Too Large 413")
        
        result = _is_413_error(error)
        
        assert result is True
    
    def test_regular_error(self):
        """Test that regular errors return False."""
        error = Exception("Some other error")
        
        result = _is_413_error(error)
        
        assert result is False


class TestArkivSyncClient:
    """Tests for ArkivSyncClient class."""
    
    def test_client_creation(self):
        """Test client initialization."""
        config = ArkivSyncConfig(
            enabled=True,
            private_key="test_key",
            rpc_url="https://test.rpc"
        )
        
        client = ArkivSyncClient(config)
        
        assert client.config == config
        assert client._client is None
    
    def test_disabled_client_returns_none_on_sync(self):
        """Test that disabled client returns None on sync."""
        config = ArkivSyncConfig(
            enabled=False,
            private_key=None,
            rpc_url="https://test.rpc"
        )
        
        client = ArkivSyncClient(config)
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        result = client.sync_context(context)
        
        assert result is None
    
    def test_find_existing_disabled_returns_none(self):
        """Test that find_existing_entity returns None when disabled."""
        config = ArkivSyncConfig(
            enabled=False,
            private_key=None,
            rpc_url="https://test.rpc"
        )
        
        client = ArkivSyncClient(config)
        result = client.find_existing_entity("some_hash")
        
        assert result is None
    
    def test_get_client_without_private_key_raises(self):
        """Test that getting client without private key raises error."""
        config = ArkivSyncConfig(
            enabled=True,
            private_key=None,
            rpc_url="https://test.rpc"
        )
        
        client = ArkivSyncClient(config)
        
        with pytest.raises(ValueError, match="private key missing"):
            client._get_client()
    
    def test_get_client_import_error(self):
        """Test handling of ImportError for arkiv package."""
        config = ArkivSyncConfig(
            enabled=True,
            private_key="test_key",
            rpc_url="https://test.rpc"
        )
        
        client = ArkivSyncClient(config)
        
        with patch("builtins.__import__", side_effect=ImportError("No module named 'arkiv'")):
            with pytest.raises(ImportError, match="arkiv package is required"):
                client._get_client()
