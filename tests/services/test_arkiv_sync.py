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

# Filecoin Pin piece CID (matches haven-dapp / filecoin-pin glossary)
TEST_PIECE_CID = (
    "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
)

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
        """Ensure clear payloads use fcid, not root_cid (and never piece)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123abc",
                piece_cid=TEST_PIECE_CID,
            )
        )
        payload = _build_payload(context)

        assert "fcid" in payload
        assert payload["fcid"] == "QmTest123abc"
        assert "root_cid" not in payload
        assert "filecoin_root_cid" not in payload
        # Clear records carry fcid only — never piece.
        assert "piece" not in payload
        assert "piece_cid" not in payload

    def test_is_encrypted_field_name(self):
        """Ensure gate presence (not is_encrypted) marks encrypted records."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata(),
        )
        payload = _build_payload(context)

        assert "gate" in payload
        assert "is_encrypted" not in payload
        assert "encrypted" not in payload

        clear = PipelineContext(source_path=Path("/tmp/test.mp4"))
        assert "gate" not in _build_payload(clear)
    
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
        """Ensure gate payload has correct 2.0 structure (short keys, no mirrors)."""
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

        assert "gate" in payload
        encryption_meta = json.loads(payload["gate"])

        assert encryption_meta["version"] == 1
        assert encryption_meta["encryptedAesKey"] == "base64encryptedkey"
        assert encryption_meta["chain"] == "EthMainnet"
        assert encryption_meta["tokenAddress"] == "0x" + "11" * 20

        assert payload["size"] == 10485760
        assert payload["pt_hash"] == "sha256originalhash"

        # No attribute mirrors in payload.
        assert "encryption_metadata" not in payload
        assert "content_mime_type" not in payload
        assert "content_file_size" not in payload
        assert "original_hash" not in payload
        assert "gate_type" not in payload
        assert "epoch" not in payload

    def test_cid_hash_in_payload(self):
        """Ensure no locator hash lives in payload (attrs-side sha256_ct only)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTestCID123",
                piece_cid=TEST_PIECE_CID,
            )
        )
        payload = _build_payload(context)

        assert "cid_hash" not in payload
        assert "sha256_ct" not in payload
        assert payload["fcid"] == "QmTestCID123"
    
    def test_vlm_json_cid_present(self):
        """Ensure vlm is present when VLM analysis exists."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmRootCID",
                piece_cid=TEST_PIECE_CID,
                vlm_json_cid="QmVlmAnalysisCID456",
            )
        )
        payload = _build_payload(context)

        assert "vlm" in payload
        assert payload["vlm"] == "QmVlmAnalysisCID456"
        assert "vlm_json_cid" not in payload

    def test_vlm_json_cid_with_bafy_prefix(self):
        """Ensure vlm handles bafy prefix CIDs."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="bafybeiaaav5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q",
                piece_cid=TEST_PIECE_CID,
                vlm_json_cid="bafybeibbbv5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q",
            )
        )
        payload = _build_payload(context)

        assert "vlm" in payload
        assert payload["vlm"].startswith("bafy")
    
    def test_non_encrypted_video_structure(self):
        """Ensure non-encrypted videos have correct 2.0 structure."""
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
                root_cid="QmNonEncryptedCID",
                piece_cid=TEST_PIECE_CID,
            )
        )
        payload = _build_payload(context)

        # Clear records: fcid locator + size, no gate material.
        assert payload["fcid"] == "QmNonEncryptedCID"
        assert payload["size"] == 1024000

        # Should NOT have encrypted-specific fields
        assert "piece" not in payload
        assert "gate" not in payload
        assert "cid_gate" not in payload
        assert "pt_hash" not in payload

        # 2.0 carries no legacy mirrors or flags
        assert "is_encrypted" not in payload
        assert "filecoin_root_cid" not in payload
        assert "cid_hash" not in payload
        assert "piece_cid" not in payload
        assert "encryption_metadata" not in payload
        assert "cid_encryption_metadata" not in payload

        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload

    def test_encrypted_video_structure(self):
        """Ensure encrypted videos have correct 2.0 structure."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmEncryptedCID",
                piece_cid="bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq",
            ),
            encryption_metadata=_content_encryption_metadata(),
            encrypted_cid="encryptedcid123",
            cid_encryption_metadata=_cid_encryption_metadata(
                encrypted_aes_key="cidencryptedkey",
            ),
        )
        payload = _build_payload(context)

        assert payload["piece"] == "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
        assert "gate" in payload
        assert "cid_gate" in payload

        encryption_meta = json.loads(payload["gate"])
        assert encryption_meta["version"] == 1
        assert encryption_meta["encryptedAesKey"] == "base64encryptedkey"

        cid_meta = json.loads(payload["cid_gate"])
        assert cid_meta["version"] == 1
        assert cid_meta["encryptedAesKey"] == "cidencryptedkey"

        # For encrypted videos, fcid should NOT be in payload (privacy)
        assert "fcid" not in payload
        # No legacy mirrors or flags
        assert "is_encrypted" not in payload
        assert "filecoin_root_cid" not in payload
        assert "cid_hash" not in payload
        assert "piece_cid" not in payload
        assert "encryption_metadata" not in payload
        assert "cid_encryption_metadata" not in payload

    def test_segment_metadata_structure(self):
        """Ensure seg has correct 2.0 structure."""
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

        assert "seg" in payload
        assert "segment_metadata" not in payload
        segment_data = payload["seg"]
        assert segment_data["segment_index"] == 0
        assert segment_data["start_timestamp"] == "2026-02-20T10:00:00Z"
        assert segment_data["end_timestamp"] == "2026-02-20T10:05:00Z"
        assert segment_data["mint_id"] == "test-mint-id-123"
        assert segment_data["recording_session_id"] == "session-uuid-456"

    def test_payload_without_upload_result(self):
        """Ensure payload handles missing upload_result gracefully."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        payload = _build_payload(context)

        # 2.0: bare contexts produce an empty payload — no flags, no mirrors.
        assert payload == {}

        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload


class TestBuildPayload:
    """Tests for _build_payload function."""
    
    def test_basic_payload(self):
        """Test basic payload structure — 2.0 bare contexts are empty."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        payload = _build_payload(context)

        # 2.0: no flags, no mirrors — empty payload.
        assert payload == {}

        # Gold standard does NOT include these fields (minimized payload)
        assert "version" not in payload
        assert "type" not in payload
        assert "archived_at" not in payload
    
    def test_payload_with_video_metadata(self):
        """Test payload with video metadata — only non-recalculable hints."""
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

        # duration/file_size live in attrs (dur_s) or with uploads (size);
        # codec survives as a playback hint.
        assert "duration" not in payload
        assert "file_size" not in payload
        assert "codec" not in payload
        assert payload["codecs"] == ["h264"]

        # No flags in 2.0.
        assert "is_encrypted" not in payload

    def test_payload_with_upload_result(self):
        """Test payload with upload result — clear records carry fcid only."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid="bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq",
            )
        )

        payload = _build_payload(context)

        # Clear records: fcid locator; piece_cid is ignored for clear records.
        assert payload["fcid"] == "QmTest123"
        assert "piece" not in payload
        assert "piece_cid" not in payload
        # No locator hash in payload (attrs-side sha256_ct only).
        assert "cid_hash" not in payload
        assert "sha256_ct" not in payload

    def test_payload_requires_piece_cid_for_encrypted_records(self):
        """Encrypted Arkiv records must carry piece (haven-dapp Synapse path)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid="",
            ),
            encryption_metadata=_content_encryption_metadata(),
        )

        with pytest.raises(ValueError, match="piece_cid"):
            _build_payload(context)

    def test_payload_clear_records_do_not_require_piece_cid(self):
        """Clear records resolve bytes via fcid — no piece requirement."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid="",
            ),
        )

        payload = _build_payload(context)
        assert payload["fcid"] == "QmTest123"
    
    def test_payload_with_analysis(self):
        """Test payload with analysis result - recalculable fields excluded."""
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

        # 2.0: model-less analysis leaves no trace.
        assert payload == {}

    def test_payload_with_encryption(self):
        """Test payload with encryption metadata includes gate."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata(
                encrypted_aes_key="base64encryptedkey",
            ),
        )

        payload = _build_payload(context)

        assert "is_encrypted" not in payload
        assert "encryption_chain" not in payload
        assert "encryption_data_hash" not in payload
        assert "encryption_metadata" not in payload
        assert "gate" in payload

        encryption_metadata = json.loads(payload["gate"])
        assert encryption_metadata["version"] == 1
        assert encryption_metadata["encryptedAesKey"] == "base64encryptedkey"
        assert encryption_metadata["chain"] == "EthMainnet"

    def test_payload_with_encryption_and_video_metadata(self):
        """Test payload includes size beside gate metadata (mime lives in attrs)."""
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

        assert "is_encrypted" not in payload
        assert "content_mime_type" not in payload
        assert "content_file_size" not in payload
        assert payload["size"] == 10485760

    def test_payload_without_encryption(self):
        """Test payload without encryption does not include gate."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        payload = _build_payload(context)

        assert "is_encrypted" not in payload
        assert "gate" not in payload
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

        assert "cid_gate" in payload
        assert "cid_encryption_metadata" not in payload

        cid_metadata = json.loads(payload["cid_gate"])
        assert cid_metadata["version"] == 1
        assert cid_metadata["encryptedAesKey"] == "cidlayerkey"
        assert cid_metadata["chain"] == "EthMainnet"

    def test_payload_without_cid_encryption(self):
        """Test payload without CID encryption does not include cid_gate."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        payload = _build_payload(context)

        assert "cid_gate" not in payload
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

        assert "seg" in payload
        assert "segment_metadata" not in payload
        segment_data = payload["seg"]
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

        assert "seg" in payload
        assert "segment_metadata" not in payload
        segment_data = payload["seg"]
        assert segment_data["segment_index"] == 1
        # Optional fields should not be present when not set
        assert "start_timestamp" not in segment_data
        assert "end_timestamp" not in segment_data
        assert "mint_id" not in segment_data
        assert "recording_session_id" not in segment_data

    def test_payload_without_segment_metadata(self):
        """Test payload without segment metadata does not include seg field."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        payload = _build_payload(context)

        assert "seg" not in payload
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
                root_cid=root_cid,
                piece_cid=TEST_PIECE_CID,
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
        """Ensure no locator CIDs are exposed in public attributes."""
        context = self.create_test_context(uploaded=True)
        attributes = _build_attributes(context)

        assert "root_cid" not in attributes
        assert "filecoin_root_cid" not in attributes
        assert "fcid" not in attributes
        assert "piece" not in attributes
        assert "piece_cid" not in attributes
        assert "encrypted_cid" not in attributes

    def test_cid_hash_in_attributes(self):
        """Ensure sha256_ct is present in attributes for dedup/restore."""
        context = self.create_test_context(uploaded=True, root_cid="QmTestCID456")
        attributes = _build_attributes(context)

        assert "sha256_ct" in attributes
        assert "cid_hash" not in attributes
        # Verify it's a valid SHA256 hash (64 hex characters)
        assert len(attributes["sha256_ct"]) == 64
        # Verify correct hash
        expected_hash = hashlib.sha256("QmTestCID456".encode()).hexdigest()
        assert attributes["sha256_ct"] == expected_hash

    def test_required_attributes_present(self):
        """Ensure all required attributes are present."""
        context = self.create_test_context()
        attributes = _build_attributes(context)

        assert attributes["grp"] == "haven.video.full"
        assert "title" in attributes

    def test_is_encrypted_as_integer(self):
        """Ensure gate_type (int) marks gated records; clear records omit it."""
        # Non-encrypted
        context = self.create_test_context(encrypted=False)
        attributes = _build_attributes(context)
        assert "gate_type" not in attributes
        assert "is_encrypted" not in attributes

        # Encrypted
        context = self.create_test_context(encrypted=True)
        attributes = _build_attributes(context)
        assert attributes["gate_type"] == 1
        assert isinstance(attributes["gate_type"], int)
        assert attributes["gate_type"] is not True  # Should not be boolean

    def test_no_timestamps_in_attributes(self):
        """Ensure recency comes from system attrs, not custom timestamp keys."""
        context = self.create_test_context()
        attributes = _build_attributes(context)

        assert "created_at" not in attributes
        assert "updated_at" not in attributes
        assert "created_at_ts" not in attributes

    def test_optional_attributes(self):
        """Ensure provenance moved payload-side (off the indexed surface)."""
        context = self.create_test_context(
            creator_handle="@testuser",
            source_uri="https://example.com/video.mp4",
            phash="a1b2c3d4",
            mint_id="mint-123",
            analysis_model="llava-1.5-7b"
        )
        attributes = _build_attributes(context)

        assert "creator_handle" not in attributes
        assert "source_uri" not in attributes
        assert "phash" not in attributes
        assert "mint_id" not in attributes
        assert "analysis_model" not in attributes

    def test_provenance_lives_in_payload(self):
        """Ensure src/creator/phash/vlm_model land in payload, not attrs."""
        context = self.create_test_context(
            creator_handle="@testuser",
            source_uri="https://example.com/video.mp4",
            phash="a1b2c3d4",
            analysis_model="llava-1.5-7b"
        )
        payload = _build_payload(context)

        assert payload["creator"] == "@testuser"
        assert payload["src"] == "https://example.com/video.mp4"
        assert payload["phash"] == "a1b2c3d4"
        assert payload["vlm_model"] == "llava-1.5-7b"

    def test_gate_corpus_attributes(self):
        """Ensure the gate corpus is filterable without payload fetch."""
        context = self.create_test_context(encrypted=True)
        attributes = _build_attributes(context)

        assert attributes["gate_type"] == 1
        assert attributes["gate_token"] == "0x" + "11" * 20
        # EthMainnet variant maps to EIP-155 id 1.
        assert attributes["gate_chain"] == 1
        assert attributes["gate_threshold"] == 1
        assert "gate_epoch" not in attributes  # v1 has no epoch
    
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

        # Should not contain these sensitive fields.
        # Note: encrypted_cid is NOT in attributes in 2.0 — the locator is
        # sha256_ct + payload piece; nothing raw-gated stays indexed.
        sensitive_fields = [
            "root_cid", "filecoin_root_cid",
            "fcid", "piece", "piece_cid",
            "vlm_json_cid", "encryption_metadata",
            "ciphertext", "encryption_key",
            "encrypted_cid",
        ]

        for field in sensitive_fields:
            assert field not in attributes, f"Sensitive field '{field}' found in attributes"


class TestBuildAttributes:
    """Tests for _build_attributes function."""
    
    def test_basic_attributes(self):
        """Test basic attributes."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4")
        )

        attrs = _build_attributes(context)

        assert attrs["grp"] == "haven.video.full"
        assert attrs["title"] == "test"  # stem of filename
        assert "created_at" not in attrs

    def test_attributes_with_metadata(self):
        """Test attributes with video metadata (provenance stays payload-side)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="My Video",
                duration=125.7,
                creator_handle="@creator",
                source_uri="https://example.com/video",
                phash="abc123"
            )
        )

        attrs = _build_attributes(context)

        assert attrs["title"] == "My Video"
        # Provenance is payload-only in 2.0.
        assert "creator_handle" not in attrs
        assert "source_uri" not in attrs
        assert "phash" not in attrs
        # mime defaults to video/mp4 -> enum 1; duration truncates to seconds.
        assert attrs["mime"] == 1
        assert attrs["dur_s"] == 125

    def test_attributes_with_upload(self):
        """Test attributes with upload result."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid="QmTest123",
                piece_cid=TEST_PIECE_CID,
            ),
        )

        attrs = _build_attributes(context)

        assert "sha256_ct" in attrs
        assert "cid_hash" not in attrs

        # Verify locator hash calculation (root_cid is NOT stored in attributes for privacy)
        expected_hash = hashlib.sha256("QmTest123".encode()).hexdigest()
        assert attrs["sha256_ct"] == expected_hash

    def test_attributes_with_encryption(self):
        """Test attributes with encryption."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encryption_metadata=_content_encryption_metadata()
        )

        attrs = _build_attributes(context)

        assert attrs["gate_type"] == 1
        assert attrs["gate_token"] == "0x" + "11" * 20
        assert attrs["gate_chain"] == 1
        assert attrs["gate_threshold"] == 1
        assert "is_encrypted" not in attrs

    def test_mime_enum_in_attributes(self):
        """Test that MIME types map to the shared enum (unknowns omitted)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                mime_type="video/mp4"
            )
        )

        attrs = _build_attributes(context)

        assert attrs["mime"] == 1

        context.video_metadata.mime_type = "application/x-unknown"
        attrs = _build_attributes(context)
        assert "mime" not in attrs

    def test_title_truncated_to_128_bytes(self):
        """Test that overlong titles truncate at a UTF-8 boundary."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="é" * 100,  # 200 bytes in UTF-8
            )
        )

        attrs = _build_attributes(context)

        assert len(attrs["title"].encode("utf-8")) <= 128

    def test_attributes_with_cid_encryption(self):
        """Test CID-level encryption alone adds no gate attributes."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            encrypted_cid="encryptedcid123",
            cid_encryption_metadata=_cid_encryption_metadata(),
        )

        attrs = _build_attributes(context)

        # encrypted_cid is never indexed in 2.0; gate corpus needs content gate.
        assert "encrypted_cid" not in attrs
        assert "gate_type" not in attrs

    def test_attributes_without_cid_encryption(self):
        """Test attributes without CID encryption does not include encrypted_cid."""
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))

        attrs = _build_attributes(context)

        assert "encrypted_cid" not in attrs

    def test_attributes_with_mint_id(self):
        """Test mint_id stays payload-side (seg only, never attrs)."""
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            video_metadata=VideoMetadata(
                path="/tmp/test.mp4",
                title="NFT Video",
                mint_id="nft-mint-123"
            )
        )

        attrs = _build_attributes(context)

        assert "mint_id" not in attrs
    
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
        """Test vlm_model lands in payload, never in attributes."""
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

        assert "analysis_model" not in attrs
        assert _build_payload(context)["vlm_model"] == "llava-1.5-7b"
    
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



# ---------------------------------------------------------------------------
# Tests for batch_sync_contexts
# ---------------------------------------------------------------------------


class TestBatchSyncContexts:
    """Tests for ArkivSyncClient.batch_sync_contexts."""

    def _make_context(self, cid: str = "bafytest123") -> PipelineContext:
        ctx = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            upload_result=UploadResult(
                video_path="/tmp/test.mp4",
                root_cid=cid,
                piece_cid=TEST_PIECE_CID,
            ),
            video_metadata=VideoMetadata(path="/tmp/test.mp4", title="Test Video"),
        )
        return ctx

    def test_returns_empty_when_disabled(self):
        config = ArkivSyncConfig(enabled=False, private_key=None, rpc_url="")
        client = ArkivSyncClient(config)
        result = client.batch_sync_contexts([self._make_context()])
        assert result == []

    def test_returns_empty_for_empty_list(self):
        config = ArkivSyncConfig(enabled=True, private_key="0x" + "11" * 32, rpc_url="https://test.rpc")
        client = ArkivSyncClient(config)
        result = client.batch_sync_contexts([])
        assert result == []

    def test_batch_creates_entities_single_tx(self):
        """All entities share the same transaction hash."""
        config = ArkivSyncConfig(
            enabled=True,
            private_key="0x" + "11" * 32,
            rpc_url="https://test.rpc",
        )
        client = ArkivSyncClient(config)

        # Mock the arkiv SDK — new BatchBuilder API
        mock_entity_key_1 = MagicMock()
        mock_entity_key_1.__str__ = lambda self: "entity-key-1"
        mock_entity_key_2 = MagicMock()
        mock_entity_key_2.__str__ = lambda self: "entity-key-2"

        mock_create_event_1 = MagicMock()
        mock_create_event_1.key = mock_entity_key_1
        mock_create_event_2 = MagicMock()
        mock_create_event_2.key = mock_entity_key_2

        mock_receipt = MagicMock()
        mock_receipt.tx_hash = "0xdeadbeef" + "00" * 28
        mock_receipt.creates = [mock_create_event_1, mock_create_event_2]

        mock_batch = MagicMock()
        mock_batch.receipt = mock_receipt
        mock_batch.create_entity.return_value = mock_batch  # fluent

        mock_arkiv_client = MagicMock()
        mock_arkiv_client.arkiv.batch.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_arkiv_client.arkiv.batch.return_value.__exit__ = MagicMock(return_value=False)
        client._client = mock_arkiv_client

        contexts = [
            self._make_context("bafycid1"),
            self._make_context("bafycid2"),
        ]

        mock_attributes = MagicMock(side_effect=lambda x: x)

        with patch.dict("sys.modules", {
            "arkiv": MagicMock(),
            "arkiv.types": MagicMock(Attributes=mock_attributes),
        }):
            results = client.batch_sync_contexts(contexts)

        assert len(results) == 2
        assert results[0]["entity_key"] == "entity-key-1"
        assert results[1]["entity_key"] == "entity-key-2"
        # All share same tx hash
        tx_hash = "0xdeadbeef" + "00" * 28
        assert results[0]["transaction_hash"] == tx_hash
        assert results[1]["transaction_hash"] == tx_hash

    def test_batch_each_entity_has_unique_key(self):
        """Each entity in the batch gets a unique key."""
        config = ArkivSyncConfig(
            enabled=True,
            private_key="0x" + "11" * 32,
            rpc_url="https://test.rpc",
        )
        client = ArkivSyncClient(config)

        keys = [MagicMock() for _ in range(3)]
        for i, k in enumerate(keys):
            k.__str__ = lambda self, idx=i: f"entity-key-{idx}"

        create_events = [MagicMock() for _ in range(3)]
        for i, evt in enumerate(create_events):
            evt.key = keys[i]

        mock_receipt = MagicMock()
        mock_receipt.tx_hash = "0xabc123" + "00" * 29
        mock_receipt.creates = create_events

        mock_batch = MagicMock()
        mock_batch.receipt = mock_receipt
        mock_batch.create_entity.return_value = mock_batch  # fluent

        mock_arkiv_client = MagicMock()
        mock_arkiv_client.arkiv.batch.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_arkiv_client.arkiv.batch.return_value.__exit__ = MagicMock(return_value=False)
        client._client = mock_arkiv_client

        contexts = [self._make_context(f"bafycid{i}") for i in range(3)]

        mock_attributes = MagicMock(side_effect=lambda x: x)

        with patch.dict("sys.modules", {
            "arkiv": MagicMock(),
            "arkiv.types": MagicMock(Attributes=mock_attributes),
        }):
            results = client.batch_sync_contexts(contexts)

        entity_keys = [r["entity_key"] for r in results]
        assert len(set(entity_keys)) == 3  # All unique
