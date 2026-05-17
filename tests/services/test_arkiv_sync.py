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
    CidEncryptionMetadata,
    EncryptionMetadata,
    PipelineContext,
    SegmentMetadata,
    UploadResult,
    VideoMetadata,
)
from haven_cli.crypto.gate_metadata import build_gate_metadata
from haven_cli.services.arkiv_sync import (
    ArkivSyncClient,
    ArkivSyncConfig,
    _build_attributes,
    _build_payload,
    _extract_transaction_hash,
    _is_413_error,
    build_arkiv_config,
)


def _content_encryption_metadata(**overrides: str) -> EncryptionMetadata:
    gate = build_gate_metadata(
        cid=overrides.get("cid", "sha256:content"),
        chain=overrides.get("chain", "EthMainnet"),
        token_address=overrides.get("token_address", "0x" + "11" * 20),
        threshold=1,
        encrypted_aes_key_b64=overrides.get("encrypted_aes_key", "base64encryptedkey"),
    )
    return EncryptionMetadata(gate=gate, iv=overrides.get("iv", "base64iv"))


def _cid_encryption_metadata(**overrides: str) -> CidEncryptionMetadata:
    gate = build_gate_metadata(
        cid=overrides.get("cid", "bafyencrypted"),
        chain=overrides.get("chain", "EthMainnet"),
        token_address=overrides.get("token_address", "0x" + "22" * 20),
        threshold=1,
        encrypted_aes_key_b64=overrides.get("encrypted_aes_key", "cidencryptedkey"),
    )
    return CidEncryptionMetadata(gate=gate)


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
    
    @patch.dict(os.environ, {"HAVEN_PRIVATE_KEY": "haven_key"}, clear=True)
    def test_env_var_haven_key(self):
        """Test reading private key from HAVEN_PRIVATE_KEY env var."""
        config = build_arkiv_config(enabled=True)
        
        assert config.private_key == "haven_key"
    
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

    @patch.dict(os.environ, {"ARKIV_SYNC_ENABLED": "true"}, clear=True)
    def test_legacy_kaolin_rpc_logs_warning(self, caplog):
        """Warn when RPC still points at sunset Kaolin testnet."""
        import logging

        caplog.set_level(logging.WARNING)
        build_arkiv_config(
            private_key="0x" + "11" * 32,
            rpc_url="https://kaolin.hoodi.arkiv.network/rpc",
            enabled=True,
        )
        assert any("Kaolin" in record.message for record in caplog.records)


class TestBuildPayloadGoldStandard:
    """Gold standard compliance tests for _build_payload function.
    
    These tests verify that the payload structure matches the haven-player
    gold standard implementation for cross-application compatibility.
    """
    
    def test_filecoin_root_cid_field_name(self):
        """Ensure payload uses filecoin_root_cid, not root_cid."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123abc"
            )
        )
        payload = _build_payload(context)
        
        assert "filecoin_root_cid" in payload
        assert payload["filecoin_root_cid"] == "QmTest123abc"
        assert "root_cid" not in payload
    
    def test_is_encrypted_field_name(self):
        """Ensure payload uses is_encrypted (int 0 or 1), not encrypted."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata(),
        )
        payload = _build_payload(context)
        
        assert "is_encrypted" in payload
        assert payload["is_encrypted"] == 1  # Gold standard uses int (0 or 1)
        assert "encrypted" not in payload
    
    def test_no_ciphertext_in_payload(self):
        """Ensure ciphertext is not stored in payload (it's on Filecoin)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=EncryptionMetadata(
                ciphertext="encrypted_data_should_not_be_here",
                gate=build_gate_metadata(
                    cid="sha256:hash123",
                    chain="EthMainnet",
                    token_address="0x" + "33" * 20,
                    threshold=1,
                    encrypted_aes_key_b64="base64key",
                ),
            ),
        )
        payload = _build_payload(context)
        
        # Ciphertext should never be in payload - it's already on Filecoin
        assert "encryption_ciphertext" not in payload
        assert "ciphertext" not in payload
    
    def test_encryption_metadata_structure(self):
        """Ensure encryption_metadata has correct gold standard structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="Test Video",
                mime_type="video/mp4",
                file_size=10485760
            ),
            encryption_metadata=_content_encryption_metadata(
                encrypted_aes_key="base64encryptedkey",
            ),
        )
        context.set_step_data("encrypt", "original_hash", "sha256originalhash")

        payload = _build_payload(context)

        assert "encryption_metadata" in payload
        encryption_meta = json.loads(payload["encryption_metadata"])

        assert encryption_meta["version"] == 1
        assert encryption_meta["encryptedAesKey"] == "base64encryptedkey"
        assert encryption_meta["chain"] == "EthMainnet"
        assert encryption_meta["tokenAddress"] == "0x" + "11" * 20

        assert payload["content_mime_type"] == "video/mp4"
        assert payload["content_file_size"] == 10485760
        assert payload["original_hash"] == "sha256originalhash"
    
    def test_cid_hash_in_payload(self):
        """Ensure cid_hash is present in payload and is valid SHA256."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTestCID123"
            )
        )
        payload = _build_payload(context)
        
        assert "cid_hash" in payload
        # Verify it's a valid SHA256 hash (64 hex characters)
        assert len(payload["cid_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in payload["cid_hash"])
        # Verify correct hash
        expected_hash = hashlib.sha256("QmTestCID123".encode()).hexdigest()
        assert payload["cid_hash"] == expected_hash
    
    def test_vlm_json_cid_present(self):
        """Ensure vlm_json_cid is present when VLM analysis exists."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmRootCID",
                vlm_json_cid="QmVlmAnalysisCID456"
            )
        )
        payload = _build_payload(context)
        
        assert "vlm_json_cid" in payload
        assert payload["vlm_json_cid"] == "QmVlmAnalysisCID456"
    
    def test_vlm_json_cid_with_bafy_prefix(self):
        """Ensure vlm_json_cid handles bafy prefix CIDs."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="bafybeiaaav5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q",
                vlm_json_cid="bafybeibbbv5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q"
            )
        )
        payload = _build_payload(context)
        
        assert "vlm_json_cid" in payload
        assert payload["vlm_json_cid"].startswith("bafy")
    
    def test_non_encrypted_video_structure(self):
        """Ensure non-encrypted videos have correct gold standard structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="Test Video",
                duration=120.5,
                file_size=1024000
            ),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmNonEncryptedCID"
            )
        )
        payload = _build_payload(context)
        
        # Required fields (gold standard uses int 0/1 for is_encrypted)
        assert "is_encrypted" in payload
        assert payload["is_encrypted"] == 0
        assert "filecoin_root_cid" in payload
        assert payload["filecoin_root_cid"] == "QmNonEncryptedCID"
        assert "cid_hash" in payload
        
        # Should NOT have encrypted-specific fields
        assert "encrypted_cid" not in payload
        assert "encryption_metadata" not in payload
        assert "cid_encryption_metadata" not in payload
        
        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload
    
    def test_encrypted_video_structure(self):
        """Ensure encrypted videos have correct gold standard structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmEncryptedCID"
            ),
            encryption_metadata=_content_encryption_metadata(),
            encrypted_cid="encryptedcid123",
            cid_encryption_metadata=_cid_encryption_metadata(
                encrypted_aes_key="cidencryptedkey",
            ),
        )
        payload = _build_payload(context)

        assert payload["is_encrypted"] == 1
        assert "encryption_metadata" in payload
        assert "cid_encryption_metadata" in payload

        encryption_meta = json.loads(payload["encryption_metadata"])
        assert encryption_meta["version"] == 1
        assert encryption_meta["encryptedAesKey"] == "base64encryptedkey"

        cid_meta = json.loads(payload["cid_encryption_metadata"])
        assert cid_meta["version"] == 1
        assert cid_meta["encryptedAesKey"] == "cidencryptedkey"
        
        # For encrypted videos, filecoin_root_cid should NOT be in payload (privacy)
        assert "filecoin_root_cid" not in payload
        # But cid_hash should still be present for deduplication
        assert "cid_hash" in payload
    
    def test_segment_metadata_structure(self):
        """Ensure segment_metadata has correct gold standard structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            segment_metadata=SegmentMetadata(
                segment_index=0,
                start_timestamp="2026-02-20T10:00:00Z",
                end_timestamp="2026-02-20T10:05:00Z",
                mint_id="test-mint-id-123",
                recording_session_id="session-uuid-456"
            )
        )
        payload = _build_payload(context)
        
        assert "segment_metadata" in payload
        segment_data = payload["segment_metadata"]
        assert segment_data["segment_index"] == 0
        assert segment_data["start_timestamp"] == "2026-02-20T10:00:00Z"
        assert segment_data["end_timestamp"] == "2026-02-20T10:05:00Z"
        assert segment_data["mint_id"] == "test-mint-id-123"
        assert segment_data["recording_session_id"] == "session-uuid-456"
    
    def test_payload_without_upload_result(self):
        """Ensure payload handles missing upload_result gracefully."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        payload = _build_payload(context)
        
        # Gold standard: minimal payload without unnecessary fields
        # Should only have is_encrypted (as int 0/1)
        assert payload["is_encrypted"] == 0
        
        # Should NOT have upload-specific fields
        assert "filecoin_root_cid" not in payload
        assert "cid_hash" not in payload
        assert "vlm_json_cid" not in payload
        
        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload


class TestBuildPayload:
    """Tests for _build_payload function."""
    
    def test_basic_payload(self):
        """Test basic payload structure matches gold standard."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        payload = _build_payload(context)
        
        # Gold standard: minimal payload - only is_encrypted (as int 0/1)
        assert payload["is_encrypted"] == 0
        
        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload
    
    def test_payload_with_video_metadata(self):
        """Test payload with video metadata - gold standard excludes recalculable fields."""
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
        
        # Gold standard: duration, file_size, codec are NOT in payload
        # (they can be recalculated from the video file during restore)
        assert "duration" not in payload
        assert "file_size" not in payload
        assert "codec" not in payload
        
        # Only is_encrypted should be present
        assert payload["is_encrypted"] == 0
    
    def test_payload_with_upload_result(self):
        """Test payload with upload result - gold standard structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid="QmPiece456"
            )
        )
        
        payload = _build_payload(context)
        
        # Gold standard includes filecoin_root_cid for non-encrypted videos
        assert payload["filecoin_root_cid"] == "QmTest123"
        # cid_hash should be in payload for verification (same as in attributes)
        assert "cid_hash" in payload
        expected_hash = hashlib.sha256("QmTest123".encode()).hexdigest()
        assert payload["cid_hash"] == expected_hash
        
        # piece_cid is NOT in gold standard payload (not needed for restore)
        assert "piece_cid" not in payload
    
    def test_payload_with_analysis(self):
        """Test payload with analysis result - gold standard excludes recalculable fields."""
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
        
        # Gold standard: has_ai_data, tag_count, timestamp_count, analysis_confidence
        # are NOT in payload (they can be recalculated from VLM JSON during restore)
        assert "has_ai_data" not in payload
        assert "tag_count" not in payload
        assert "timestamp_count" not in payload
        assert "analysis_confidence" not in payload
        
        # Only is_encrypted should be present
        assert payload["is_encrypted"] == 0
    
    def test_payload_with_encryption(self):
        """Test payload with encryption metadata includes encryption_metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata(
                encrypted_aes_key="base64encryptedkey",
            ),
        )

        payload = _build_payload(context)

        assert payload["is_encrypted"] == 1
        assert "encryption_chain" not in payload
        assert "encryption_data_hash" not in payload
        assert "encryption_metadata" in payload

        encryption_metadata = json.loads(payload["encryption_metadata"])
        assert encryption_metadata["version"] == 1
        assert encryption_metadata["encryptedAesKey"] == "base64encryptedkey"
        assert encryption_metadata["chain"] == "EthMainnet"

    def test_payload_with_encryption_and_video_metadata(self):
        """Test payload includes optional content fields beside gate metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="Test Video",
                mime_type="video/mp4",
                file_size=10485760
            ),
            encryption_metadata=_content_encryption_metadata(),
        )

        payload = _build_payload(context)

        assert payload["is_encrypted"] == 1
        assert payload["content_mime_type"] == "video/mp4"
        assert payload["content_file_size"] == 10485760
    
    def test_payload_without_encryption(self):
        """Test payload without encryption does not include encryption_metadata."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        payload = _build_payload(context)
        
        # Gold standard uses int (0 or 1) for is_encrypted
        assert payload["is_encrypted"] == 0
        assert "encryption_metadata" not in payload
    
    def test_payload_with_cid_encryption(self):
        """Test payload with CID-level encryption metadata."""
        # Note: cid_encryption_metadata requires encryption_metadata to be set
        # (encrypted videos only)
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata(),
            encrypted_cid="encryptedcid123",
            cid_encryption_metadata=_cid_encryption_metadata(
                encrypted_aes_key="cidlayerkey",
            ),
        )

        payload = _build_payload(context)

        assert "cid_encryption_metadata" in payload

        cid_metadata = json.loads(payload["cid_encryption_metadata"])
        assert cid_metadata["version"] == 1
        assert cid_metadata["encryptedAesKey"] == "cidlayerkey"
        assert cid_metadata["chain"] == "EthMainnet"
    
    def test_payload_without_cid_encryption(self):
        """Test payload without CID encryption does not include cid_encryption_metadata."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        payload = _build_payload(context)
        
        assert "cid_encryption_metadata" not in payload
    
    def test_payload_with_segment_metadata(self):
        """Test payload with segment metadata."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            segment_metadata=SegmentMetadata(
                segment_index=0,
                start_timestamp="2026-02-20T10:00:00Z",
                end_timestamp="2026-02-20T10:05:00Z",
                mint_id="nft-mint-id",
                recording_session_id="session-uuid-123"
            )
        )
        
        payload = _build_payload(context)
        
        assert "segment_metadata" in payload
        segment_data = payload["segment_metadata"]
        assert segment_data["segment_index"] == 0
        assert segment_data["start_timestamp"] == "2026-02-20T10:00:00Z"
        assert segment_data["end_timestamp"] == "2026-02-20T10:05:00Z"
        assert segment_data["mint_id"] == "nft-mint-id"
        assert segment_data["recording_session_id"] == "session-uuid-123"
    
    def test_payload_with_partial_segment_metadata(self):
        """Test payload with partial segment metadata (only required fields)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            segment_metadata=SegmentMetadata(
                segment_index=1,
            )
        )
        
        payload = _build_payload(context)
        
        assert "segment_metadata" in payload
        segment_data = payload["segment_metadata"]
        assert segment_data["segment_index"] == 1
        # Optional fields should not be present when not set
        assert "start_timestamp" not in segment_data
        assert "end_timestamp" not in segment_data
        assert "mint_id" not in segment_data
        assert "recording_session_id" not in segment_data
    
    def test_payload_without_segment_metadata(self):
        """Test payload without segment metadata does not include segment_metadata field."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        payload = _build_payload(context)
        
        assert "segment_metadata" not in payload


class TestBuildAttributesGoldStandard:
    """Gold standard compliance tests for _build_attributes function.
    
    These tests verify that the attributes structure matches the haven-player
    gold standard implementation for cross-application compatibility.
    """
    
    def create_test_context(
        self,
        uploaded: bool = False,
        encrypted: bool = False,
        title: str | None = None,
        creator_handle: str = "",
        source_uri: str = "",
        phash: str = "",
        mint_id: str = "",
        analysis_model: str = "",
        root_cid: str = "QmTestCID123",
    ) -> PipelineContext:
        """Create a test context with specified parameters."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        # Set video metadata
        context.video_metadata = VideoMetadata(
            path="/tmp/test.mp4",
            title=title if title is not None else "",
            creator_handle=creator_handle,
            source_uri=source_uri,
            phash=phash,
            mint_id=mint_id if mint_id else None,
        )
        
        # Set upload result if requested
        if uploaded:
            context.upload_result = UploadResult(
                video_path="/tmp/test.mp4",
                root_cid=root_cid
            )
        
        # Set encryption metadata if requested
        if encrypted:
            context.encryption_metadata = _content_encryption_metadata()
        
        # Set analysis result if analysis_model provided
        if analysis_model:
            context.analysis_result = AIAnalysisResult(
                video_path="/tmp/test.mp4",
                analysis_model=analysis_model
            )
        
        return context
    
    def test_no_root_cid_in_attributes(self):
        """Ensure CID is not exposed in public attributes."""
        context = self.create_test_context(uploaded=True)
        attributes = _build_attributes(context)
        
        assert "root_cid" not in attributes
        assert "filecoin_root_cid" not in attributes
    
    def test_cid_hash_in_attributes(self):
        """Ensure cid_hash is present in attributes for verification."""
        context = self.create_test_context(uploaded=True, root_cid="QmTestCID456")
        attributes = _build_attributes(context)
        
        assert "cid_hash" in attributes
        # Verify it's a valid SHA256 hash (64 hex characters)
        assert len(attributes["cid_hash"]) == 64
        # Verify correct hash
        expected_hash = hashlib.sha256("QmTestCID456".encode()).hexdigest()
        assert attributes["cid_hash"] == expected_hash
    
    def test_required_attributes_present(self):
        """Ensure all required attributes are present."""
        context = self.create_test_context()
        attributes = _build_attributes(context)
        
        assert "title" in attributes
        assert "created_at" in attributes
    
    def test_is_encrypted_as_integer(self):
        """Ensure is_encrypted is 0 or 1 (not boolean)."""
        # Non-encrypted
        context = self.create_test_context(encrypted=False)
        attributes = _build_attributes(context)
        # When not encrypted, is_encrypted should not be in attributes
        assert "is_encrypted" not in attributes
        
        # Encrypted
        context = self.create_test_context(encrypted=True)
        attributes = _build_attributes(context)
        assert attributes["is_encrypted"] == 1
        assert isinstance(attributes["is_encrypted"], int)
        assert attributes["is_encrypted"] is not True  # Should not be boolean
    
    def test_iso8601_timestamps(self):
        """Ensure timestamps are in ISO8601 format."""
        context = self.create_test_context()
        attributes = _build_attributes(context)
        
        import re
        iso8601_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
        
        assert re.match(iso8601_pattern, attributes["created_at"])
        if "updated_at" in attributes:
            assert re.match(iso8601_pattern, attributes["updated_at"])
    
    def test_optional_attributes(self):
        """Ensure optional attributes are included when available."""
        context = self.create_test_context(
            creator_handle="@testuser",
            source_uri="https://example.com/video.mp4",
            phash="a1b2c3d4",
            mint_id="mint-123",
            analysis_model="llava-1.5-7b"
        )
        attributes = _build_attributes(context)
        
        assert attributes.get("creator_handle") == "@testuser"
        assert attributes.get("source_uri") == "https://example.com/video.mp4"
        assert attributes.get("phash") == "a1b2c3d4"
        assert attributes.get("mint_id") == "mint-123"
        assert attributes.get("analysis_model") == "llava-1.5-7b"
    
    def test_updated_at_attribute(self):
        """Ensure updated_at is present."""
        context = self.create_test_context()
        attributes = _build_attributes(context)
        
        assert "updated_at" in attributes
        # For new uploads, should be same as created_at
        assert attributes["updated_at"] == attributes["created_at"]
    
    def test_title_handling(self):
        """Ensure title is properly set or defaulted."""
        # With title
        context = self.create_test_context(title="My Video")
        attributes = _build_attributes(context)
        assert attributes["title"] == "My Video"
        
        # Without title - should use filename stem
        context = self.create_test_context(title=None)
        # Override video_metadata title to be empty
        context.video_metadata = VideoMetadata(
            path="/tmp/test.mp4",
            title=""
        )
        attributes = _build_attributes(context)
        assert attributes["title"] == "test"  # stem of filename
    
    def test_no_sensitive_data_in_attributes(self):
        """Ensure no sensitive data is in public attributes."""
        context = self.create_test_context(
            uploaded=True,
            encrypted=True
        )
        # Add CID encryption metadata and encrypted_cid
        context.encrypted_cid = "encryptedcid123"
        context.cid_encryption_metadata = _cid_encryption_metadata()
        attributes = _build_attributes(context)
        
        # Should not contain these sensitive fields
        # Note: encrypted_cid IS allowed in attributes (it's the encrypted CID, safe for public)
        sensitive_fields = [
            "root_cid", "filecoin_root_cid",
            "vlm_json_cid", "encryption_metadata",
            "ciphertext", "encryption_key"
        ]
        
        for field in sensitive_fields:
            assert field not in attributes, f"Sensitive field '{field}' found in attributes"
        
        # encrypted_cid SHOULD be present when CID encryption metadata exists
        assert "encrypted_cid" in attributes
        assert attributes["encrypted_cid"] == "encryptedcid123"


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
        
        assert "cid_hash" in attrs
        
        # Verify CID hash calculation (root_cid is NOT stored in attributes for privacy)
        expected_hash = hashlib.sha256("QmTest123".encode()).hexdigest()
        assert attrs["cid_hash"] == expected_hash
    
    def test_attributes_with_encryption(self):
        """Test attributes with encryption."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata()
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["is_encrypted"] == 1
    
    def test_mime_type_not_in_attributes(self):
        """Test that MIME type is NOT in attributes (gold standard excludes it).
        
        The gold standard (haven-player) does not include mime_type in attributes
        because it can be stored in encryption_metadata for encrypted videos
        or recalculated from the file during restore.
        """
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                mime_type="video/mp4"
            )
        )
        
        attrs = _build_attributes(context)
        
        # Gold standard: mime_type is NOT in attributes
        assert "mime_type" not in attrs
    
    def test_attributes_with_cid_encryption(self):
        """Test attributes with CID-level encryption includes encrypted_cid."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encrypted_cid="encryptedcid123",
            cid_encryption_metadata=_cid_encryption_metadata(),
        )
        
        attrs = _build_attributes(context)
        
        # encrypted_cid should be in attributes (it's already encrypted, so safe for public)
        assert "encrypted_cid" in attrs
        assert attrs["encrypted_cid"] == "encryptedcid123"
    
    def test_attributes_without_cid_encryption(self):
        """Test attributes without CID encryption does not include encrypted_cid."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        attrs = _build_attributes(context)
        
        assert "encrypted_cid" not in attrs
    
    def test_attributes_with_mint_id(self):
        """Test attributes with mint_id for NFT tracking."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="NFT Video",
                mint_id="nft-mint-123"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["mint_id"] == "nft-mint-123"
    
    def test_attributes_without_mint_id(self):
        """Test attributes without mint_id does not include mint_id field."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="Regular Video"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert "mint_id" not in attrs
    
    def test_attributes_with_analysis_model(self):
        """Test attributes with analysis_model from VLM analysis."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            analysis_result=AIAnalysisResult(
                video_path="/tmp/test.mp4",
                timestamps=[{"start": 0, "end": 10}],
                tags={"tag1": 0.9},
                confidence=0.85,
                analysis_model="llava-1.5-7b"
            )
        )
        
        attrs = _build_attributes(context)
        
        assert attrs["analysis_model"] == "llava-1.5-7b"
    
    def test_attributes_without_analysis_model(self):
        """Test attributes without analysis_model does not include analysis_model field."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            analysis_result=AIAnalysisResult(
                video_path="/tmp/test.mp4",
                timestamps=[{"start": 0, "end": 10}],
                tags={"tag1": 0.9},
                confidence=0.85
                # analysis_model is None by default
            )
        )
        
        attrs = _build_attributes(context)
        
        assert "analysis_model" not in attrs


class TestExtractTransactionHash:
    """Tests for _extract_transaction_hash function."""
    
    def test_from_transaction_hash_attribute(self):
        """Test extracting from transactionHash attribute."""
        receipt = MagicMock(spec=[])
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
        """Test extracting from tx_hash attribute (arkiv-sdk format)."""
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
