#!/usr/bin/env python3
"""
Cross-application compatibility test for Haven data format (v2.0.0).

This script:
1. Creates a test video upload context
2. Builds payload and attributes using haven-cli logic
3. Verifies field names match ARKIV_FORMAT 2.0.0
4. Simulates parsing with haven-dapp reader logic (2.0 keys)
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from haven_cli.services.arkiv_sync import _build_payload, _build_attributes
from haven_cli.crypto.gate_metadata import build_gate_metadata
from haven_cli.pipeline.context import (
    PipelineContext,
    VideoMetadata,
    AIAnalysisResult,
    EncryptionMetadata,
    CidEncryptionMetadata,
    SegmentMetadata,
    UploadResult,
)

# Filecoin Pin piece CID (matches haven-dapp / filecoin-pin glossary).
TEST_PIECE_CID = (
    "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
)

# ARKIV_FORMAT 2.0.0 field inventory.
FORMAT_2_0_PAYLOAD_KEYS = [
    "fcid",      # clear records only
    "piece",     # gated records only
    "gate",      # gated records
    "cid_gate",  # gated records with distinct CID-layer gate
    "size",
    "pt_hash",
    "vlm",
    "vlm_model",
    "src",
    "creator",
    "phash",
    "codecs",
    "seg",
    "attn",
]
FORMAT_2_0_ATTR_KEYS = [
    "grp",
    "title",
    "gate_type",
    "gate_token",
    "gate_chain",
    "gate_threshold",
    "gate_epoch",
    "sha256_ct",
    "mime",
    "dur_s",
]

FORBIDDEN_FIELDS = {
    "payload": [
        "root_cid", "filecoin_root_cid", "piece_cid", "cid_hash", "sha256_ct",
        "is_encrypted", "encrypted", "encryption_ciphertext", "ciphertext",
        "encryption_metadata", "cid_encryption_metadata",
        "content_mime_type", "content_file_size", "original_hash",
        "vlm_json_cid", "analysis_model", "segment_metadata", "attestation",
        "description", "gate_type", "epoch",
    ],
    "attributes": [
        "root_cid", "filecoin_root_cid", "fcid", "piece", "piece_cid",
        "encrypted_cid", "cid_hash",
        "project", "type", "category", "tags", "language", "is_encrypted",
        "created_at", "updated_at", "created_at_ts",
        "creator_handle", "source_uri", "mint_id", "phash", "analysis_model",
        "description", "gate_version", "expires_at_block", "created_at_block",
    ],
}


def _content_gate(**overrides: Any) -> EncryptionMetadata:
    gate = build_gate_metadata(
        cid=overrides.get("cid", "sha256:content"),
        chain=overrides.get("chain", "EthMainnet"),
        token_address=overrides.get("token_address", "0x" + "11" * 20),
        threshold=1,
        encrypted_aes_key_b64=overrides.get("encrypted_aes_key", "base64encryptedkey"),
    )
    return EncryptionMetadata(gate=gate, iv="base64iv")


def _cid_gate(**overrides: Any) -> CidEncryptionMetadata:
    gate = build_gate_metadata(
        cid=overrides.get("cid", "bafyencrypted"),
        chain=overrides.get("chain", "EthMainnet"),
        token_address=overrides.get("token_address", "0x" + "22" * 20),
        threshold=1,
        encrypted_aes_key_b64=overrides.get("encrypted_aes_key", "cidencryptedkey"),
    )
    return CidEncryptionMetadata(gate=gate)


def create_test_context(
    uploaded: bool = False,
    encrypted: bool = False,
    vlm_analyzed: bool = False,
    with_segments: bool = False,
    title: str = "Test Video",
    creator_handle: str = "",
    source_uri: str = "",
    phash: str = "",
    mint_id: str = "",
    analysis_model: str = "",
    segment_index: int = 0,
    root_cid: str = "bafybeiaaav5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q",
) -> PipelineContext:
    """Create a test context with specified parameters."""
    context = PipelineContext(
        source_path=Path("/tmp/test_video.mp4"),
        options={"encrypt": encrypted, "vlm_enabled": vlm_analyzed}
    )

    # Set video metadata
    context.video_metadata = VideoMetadata(
        path="/tmp/test_video.mp4",
        title=title,
        duration=300.5,
        file_size=10485760,
        mime_type="video/mp4",
        codec="h264",
        creator_handle=creator_handle or "@testuser",
        source_uri=source_uri or "https://example.com/video.mp4",
        phash=phash or "a1b2c3d4e5f6",
        mint_id=mint_id if mint_id else None,
    )

    # Set upload result if requested
    if uploaded:
        context.upload_result = UploadResult(
            video_path="/tmp/test_video.mp4",
            root_cid=root_cid,
            piece_cid=TEST_PIECE_CID,
        )

    # Set encryption metadata if requested (real v1 gate records)
    if encrypted:
        context.encryption_metadata = _content_gate()
        context.encrypted_cid = "encryptedcidstring123"
        context.cid_encryption_metadata = _cid_gate()
        # Add original hash for pt_hash
        context.set_step_data("encrypt", "original_hash", "sha256originalhash789")

    # Set analysis result if VLM analyzed
    if vlm_analyzed:
        context.analysis_result = AIAnalysisResult(
            video_path="/tmp/test_video.mp4",
            timestamps=[{"start": 0, "end": 10}, {"start": 15, "end": 25}],
            tags={"nature": 0.95, "waterfall": 0.87},
            confidence=0.91,
            analysis_model=analysis_model or "llava-1.5-7b"
        )
        # Add VLM JSON CID to upload result if uploaded
        if uploaded and context.upload_result:
            context.upload_result.vlm_json_cid = "bafybeicccv5q7z3b2q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q3v3q"

    # Set segment metadata if requested
    if with_segments:
        context.segment_metadata = SegmentMetadata(
            segment_index=segment_index,
            start_timestamp="2026-02-20T10:00:00Z",
            end_timestamp="2026-02-20T10:05:00Z",
            mint_id=mint_id or "test-mint-123",
            recording_session_id="session-uuid-456"
        )

    return context


def validate_payload(payload: Dict[str, Any], encrypted: bool = False) -> None:
    """Validate payload against ARKIV_FORMAT 2.0.0."""
    print("  Validating payload...")

    # Unknown keys are a format violation.
    for key in payload:
        assert key in FORMAT_2_0_PAYLOAD_KEYS, f"Unknown payload key: {key}"

    # Check forbidden fields
    for field in FORBIDDEN_FIELDS["payload"]:
        assert field not in payload, f"Forbidden field found: {field}"

    # One locator per record class — never both.
    if encrypted:
        assert "piece" in payload, "Missing piece locator for gated record"
        assert "fcid" not in payload, "fcid must NOT be in payload for gated records"
        assert "gate" in payload, "Missing gate for gated record"
    else:
        if "fcid" in payload or "piece" in payload:
            assert "fcid" in payload, "Clear records must use fcid"
            assert "piece" not in payload, "piece must NOT be in payload for clear records"
            cid = payload["fcid"]
            assert cid.startswith(("Qm", "bafy", "bafk")), f"Invalid CID format: {cid}"

    # Validate gate structure if gated
    if encrypted:
        gate = json.loads(payload["gate"])
        assert gate["version"] == 1
        assert gate["chain"] == "EthMainnet"
        assert gate["tokenAddress"] == "0x" + "11" * 20
        assert "encryptedAesKey" in gate
        # Frozen verbose spellings stay inside the blob.
        assert gate["threshold"] == "1"
        # Ciphertext is NOT in the gate (it's on Filecoin)
        assert "ciphertext" not in gate

    print("    ✅ Payload validation passed")


def validate_attributes(attributes: Dict[str, Any], encrypted: bool = False) -> None:
    """Validate attributes against ARKIV_FORMAT 2.0.0."""
    print("  Validating attributes...")

    for key in attributes:
        assert key in FORMAT_2_0_ATTR_KEYS, f"Unknown attribute: {key}"

    # Check required fields
    assert attributes.get("grp") == "haven.video.full", "grp must be haven.video.full"
    assert "title" in attributes, "Missing required attribute: title"

    # Check forbidden fields
    for field in FORBIDDEN_FIELDS["attributes"]:
        assert field not in attributes, f"Forbidden attribute found: {field}"

    # Gate corpus for gated records (compact forms).
    if encrypted:
        assert attributes["gate_type"] == 1
        assert isinstance(attributes["gate_type"], int)
        assert attributes["gate_token"] == "0x" + "11" * 20
        assert attributes["gate_chain"] == 1  # EthMainnet -> EIP-155
        assert attributes["gate_threshold"] == 1
    else:
        assert "gate_type" not in attributes

    # Verify sha256_ct format if present
    if "sha256_ct" in attributes:
        digest = attributes["sha256_ct"]
        assert len(digest) == 64, f"sha256_ct must be 64 hex chars, got {len(digest)}"
        assert all(c in "0123456789abcdef" for c in digest), "sha256_ct must be hex"

    print("    ✅ Attributes validation passed")


def simulate_dapp_parsing(
    payload: Dict[str, Any],
    attributes: Dict[str, Any],
    encrypted: bool = False
) -> Dict[str, Any]:
    """
    Simulate how haven-dapp parses the entity (2.0 keys).

    Mirrors lib/parse-arkiv-video.ts: attributes merged with decoded
    payload, canonical snake_case keys, gate presence decides encryption.
    """
    print("  Simulating dapp parsing...")

    data = {**attributes, **payload}

    # Parse gate blob (payload `gate`, JSON string).
    gate = None
    raw_gate = data.get("gate")
    if isinstance(raw_gate, str):
        try:
            gate = json.loads(raw_gate)
        except json.JSONDecodeError:
            gate = None

    # Parse segment block.
    raw_seg = data.get("seg") or {}
    segment_metadata = None
    if raw_seg:
        segment_metadata = {
            'segmentIndex': raw_seg.get('segment_index', 0),
            'mintId': raw_seg.get('mint_id', ''),
            'startTimestamp': raw_seg.get('start_timestamp'),
            'endTimestamp': raw_seg.get('end_timestamp'),
        }

    vlm = data.get("vlm")

    video = {
        'id': 'entity-key-placeholder',
        'title': data.get('title', 'Untitled'),
        'duration': data.get('dur_s', 0),
        'filecoinCid': data.get('fcid') or '',
        'pieceCid': data.get('piece'),
        'isEncrypted': gate is not None,
        'encryptionMetadata': gate,
        'cidEncryptionMetadata': json.loads(data['cid_gate']) if data.get('cid_gate') else None,
        'hasAiData': bool(vlm),
        'vlmJsonCid': vlm,
        'vlmModel': data.get('vlm_model'),
        'sourceUri': data.get('src'),
        'creatorHandle': data.get('creator'),
        'phash': data.get('phash'),
        'sha256Ct': data.get('sha256_ct'),
        'mimeEnum': data.get('mime'),
        'segmentMetadata': segment_metadata,
    }

    # Validate dapp can find the locator
    if not encrypted and not video['filecoinCid']:
        raise ValueError("DApp cannot find Filecoin CID for non-encrypted video!")

    # For gated videos, gate + piece must be available
    if encrypted and (not video['encryptionMetadata'] or not video['pieceCid']):
        raise ValueError("DApp cannot find gate/piece for gated video!")

    print(f"    ✅ DApp parsing: locator={(video['filecoinCid'] or video['pieceCid'] or '')[:30]}...")
    print(f"       Encrypted={video['isEncrypted']}, Title={video['title']}")

    return video


def test_non_encrypted_upload():
    """Test CLI upload of non-encrypted video produces compatible data."""
    print("\n=== Test: Non-encrypted Video Upload ===")

    # Create test context
    context = create_test_context(uploaded=True, encrypted=False)

    # Build payload and attributes
    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # Validate
    validate_payload(payload, encrypted=False)
    validate_attributes(attributes, encrypted=False)

    # Simulate dapp parsing
    video = simulate_dapp_parsing(payload, attributes, encrypted=False)

    # Additional assertions
    assert video['isEncrypted'] == False
    assert video['filecoinCid'].startswith(('bafy', 'Qm'))
    assert video['title'] == "Test Video"

    print("✅ Non-encrypted upload test passed")


def test_encrypted_upload():
    """Test CLI upload of encrypted video produces compatible data."""
    print("\n=== Test: Encrypted Video Upload ===")

    # Create test context with encryption
    context = create_test_context(uploaded=True, encrypted=True)

    # Build payload and attributes
    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # Validate
    validate_payload(payload, encrypted=True)
    validate_attributes(attributes, encrypted=True)

    # Simulate dapp parsing
    video = simulate_dapp_parsing(payload, attributes, encrypted=True)

    # Additional assertions for encrypted videos
    assert video['isEncrypted'] == True
    assert video['pieceCid'] == TEST_PIECE_CID
    assert video['encryptionMetadata'] is not None
    assert video['encryptionMetadata']['version'] == 1
    assert video['cidEncryptionMetadata']['version'] == 1

    print("✅ Encrypted upload test passed")


def test_vlm_analysis_upload():
    """Test upload with VLM analysis metadata."""
    print("\n=== Test: Video with VLM Analysis ===")

    context = create_test_context(
        uploaded=True,
        encrypted=False,
        vlm_analyzed=True,
        analysis_model="llava-1.5-7b"
    )

    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # Check VLM fields per 2.0 (short keys, payload-side)
    assert "vlm" in payload, "Missing vlm"
    assert payload["vlm"].startswith(("bafy", "Qm")), "Invalid vlm format"
    assert payload["vlm_model"] == "llava-1.5-7b", "vlm_model mismatch"
    assert "analysis_model" not in attributes, "analysis_model must NOT be in attributes"

    # 2.0 carries no recalculable counters
    assert "has_ai_data" not in payload, "has_ai_data should NOT be in payload"
    assert "tag_count" not in payload, "tag_count should NOT be in payload"
    assert "timestamp_count" not in payload, "timestamp_count should NOT be in payload"

    # Simulate dapp parsing
    video = simulate_dapp_parsing(payload, attributes)
    # Dapp derives hasAiData from vlm presence
    assert video['hasAiData'] == True
    assert video['vlmJsonCid'] is not None
    assert video['vlmModel'] == "llava-1.5-7b"

    print("✅ VLM analysis upload test passed")


def test_segment_metadata_upload():
    """Test upload with segment metadata."""
    print("\n=== Test: Video with Segment Metadata ===")

    context = create_test_context(
        uploaded=True,
        with_segments=True,
        segment_index=0,
        mint_id="test-mint-123"
    )

    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # Check segment fields (short key, payload-side only)
    assert "seg" in payload, "Missing seg"
    assert "segment_metadata" not in payload
    segment_data = payload["seg"]
    assert segment_data["segment_index"] == 0, "segment_index mismatch"
    assert segment_data["mint_id"] == "test-mint-123", "mint_id mismatch"
    assert segment_data["start_timestamp"] == "2026-02-20T10:00:00Z", "start_timestamp mismatch"
    assert segment_data["end_timestamp"] == "2026-02-20T10:05:00Z", "end_timestamp mismatch"
    assert segment_data["recording_session_id"] == "session-uuid-456", "recording_session_id mismatch"

    # mint_id is payload-side only in 2.0
    assert "mint_id" not in attributes, "mint_id must NOT be in attributes"

    # Simulate dapp parsing
    video = simulate_dapp_parsing(payload, attributes)
    assert video['segmentMetadata'] is not None
    assert video['segmentMetadata']['segmentIndex'] == 0
    assert video['segmentMetadata']['mintId'] == "test-mint-123"

    print("✅ Segment metadata upload test passed")


def test_cross_application_field_consistency():
    """Test that the locator hash is attrs-side only and correct."""
    print("\n=== Test: Cross-Application Field Consistency ===")

    context = create_test_context(uploaded=True, encrypted=False)

    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # sha256_ct lives in attributes only — never mirrored in payload.
    assert "sha256_ct" not in payload, "sha256_ct must NOT be in payload"
    assert "cid_hash" not in payload, "cid_hash must NOT be in payload"
    assert "sha256_ct" in attributes, "sha256_ct missing from attributes"

    # Verify hash is correct
    expected_hash = hashlib.sha256(context.upload_result.root_cid.encode()).hexdigest()
    assert attributes["sha256_ct"] == expected_hash, "sha256_ct value is incorrect"

    print(f"    ✅ sha256_ct attrs-only: {attributes['sha256_ct'][:20]}...")
    print("✅ Field consistency test passed")


def test_privacy_rules():
    """Test that privacy rules are enforced per 2.0."""
    print("\n=== Test: Privacy Rules Enforcement ===")

    context = create_test_context(uploaded=True, encrypted=True)

    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # 2.0 privacy rules:
    # - For gated videos: fcid is NOT in payload (locator is piece)
    # - Attributes NEVER contain raw CIDs (only sha256_ct)
    # - encrypted_cid is NOT indexed (locator is sha256_ct + piece)

    # For gated videos: fcid should NOT be in payload
    assert "fcid" not in payload, "Payload should NOT have fcid for gated videos"
    assert "filecoin_root_cid" not in payload

    # Attributes should NEVER contain raw CIDs
    assert "filecoin_root_cid" not in attributes, "Attributes should NEVER have filecoin_root_cid"
    assert "root_cid" not in attributes, "Attributes should NEVER have root_cid"
    assert "fcid" not in attributes
    assert "piece" not in attributes
    assert "encrypted_cid" not in attributes, "Attributes should NEVER have encrypted_cid"

    # Attributes should have sha256_ct instead (for deduplication)
    assert "sha256_ct" in attributes, "Attributes should have sha256_ct"

    # Payload should have gate material for decryption
    assert "gate" in payload, "Payload should have gate"
    assert "cid_gate" in payload, "Payload should have cid_gate"

    print("    ✅ Privacy rules enforced:")
    print(f"       - Payload does NOT have fcid (gated video)")
    print(f"       - Attributes has sha256_ct: {attributes['sha256_ct'][:25]}...")
    print("✅ Privacy rules test passed")


def test_gold_standard_compliance():
    """Test full compliance with ARKIV_FORMAT 2.0.0."""
    print("\n=== Test: Format 2.0 Compliance ===")

    # Test with comprehensive context
    context = create_test_context(
        uploaded=True,
        encrypted=True,
        vlm_analyzed=True,
        with_segments=True,
        title="Gold Standard Test",
        creator_handle="@goldstandard",
        source_uri="https://goldstandard.example/video",
        phash="goldphash123",
        mint_id="gold-mint-456",
        analysis_model="llava-1.5-13b",
        segment_index=2,
    )

    payload = _build_payload(context)
    attributes = _build_attributes(context)

    # Full validators cover every key.
    validate_payload(payload, encrypted=True)
    validate_attributes(attributes, encrypted=True)

    # Provenance payload-side.
    assert payload["src"] == "https://goldstandard.example/video"
    assert payload["creator"] == "@goldstandard"
    assert payload["phash"] == "goldphash123"
    assert payload["vlm_model"] == "llava-1.5-13b"
    assert payload["pt_hash"] == "sha256originalhash789"
    assert payload["size"] == 10485760
    assert payload["codecs"] == ["h264"]

    # Attr enum facts.
    assert attributes["mime"] == 1
    assert attributes["dur_s"] == 300
    assert attributes["title"] == "Gold Standard Test"

    # Verify gate structure
    gate = json.loads(payload["gate"])
    for field in ["version", "cid", "chain", "tokenAddress", "threshold", "encryptedAesKey"]:
        assert field in gate, f"Required gate field missing: {field}"

    # Verify cid_gate structure
    cid_gate = json.loads(payload["cid_gate"])
    for field in ["version", "cid", "chain", "tokenAddress", "threshold", "encryptedAesKey"]:
        assert field in cid_gate, f"Required cid_gate field missing: {field}"

    # Verify seg structure
    seg = payload["seg"]
    assert seg["segment_index"] == 2
    assert seg["mint_id"] == "gold-mint-456"

    print("    ✅ All 2.0 fields present and valid")
    print("✅ Format 2.0 compliance test passed")


def main():
    """Run all integration tests."""
    print("=" * 70)
    print("Haven Cross-Application Compatibility Tests (ARKIV_FORMAT 2.0.0)")
    print("=" * 70)
    print("\nThese tests verify compatibility between:")
    print("  - haven-cli (this implementation)")
    print("  - haven-dapp (reader application)")
    print("  - haven-mobile (reader application)")

    tests = [
        test_non_encrypted_upload,
        test_encrypted_upload,
        test_vlm_analysis_upload,
        test_segment_metadata_upload,
        test_cross_application_field_consistency,
        test_privacy_rules,
        test_gold_standard_compliance,
    ]

    failed = 0
    passed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ Test failed: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ Test error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("✅ All tests passed!")
        return 0
    else:
        print(f"❌ {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
