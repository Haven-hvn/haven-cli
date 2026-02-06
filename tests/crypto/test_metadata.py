"""Tests for crypto metadata module."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from haven_cli.crypto.metadata import (
    load_encryption_metadata,
    save_encryption_metadata,
    verify_cid_format,
    get_encryption_metadata_path,
    delete_encryption_metadata,
    find_encryption_metadata,
    _parse_encryption_metadata,
)
from haven_cli.pipeline.context import EncryptionMetadata


class TestVerifyCidFormat:
    """Tests for CID format verification."""
    
    def test_valid_cidv0(self):
        """Test valid CIDv0 format."""
        # CIDv0 is 46 chars, starts with Qm, base58
        cid = "QmYwAPJzv5CZsnAzt8auVKLJdf3SRr7Fz1tJ3qA1xQcQdE"
        assert verify_cid_format(cid) is True
    
    def test_valid_cidv1_base32(self):
        """Test valid CIDv1 base32 format."""
        # CIDv1 base32 starts with bafy
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        assert verify_cid_format(cid) is True
    
    def test_valid_cidv1_bafk_prefix(self):
        """Test valid CIDv1 with bafk prefix (raw binary codec)."""
        # CIDv1 with bafk prefix - used by Filecoin for raw data
        cid = "bafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm"
        assert verify_cid_format(cid) is True
    
    def test_valid_cidv1_other_prefixes(self):
        """Test valid CIDv1 with various baf prefixes."""
        # Various CIDv1 prefixes should all be valid
        # Note: base32 only uses a-z and 2-7 (not 0, 1, 8, 9)
        cids = [
            "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",  # dag-pb
            "bafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm",  # raw
            "bafyreiabc345def234ghi2jkl3mno4pqr5stu6vwx7yz2abc3def4ghi5jkl6mno7pqr",  # dag-cbor
        ]
        for cid in cids:
            assert verify_cid_format(cid) is True, f"CID {cid[:20]}... should be valid"
    
    def test_cid_with_whitespace_stripping(self):
        """Test that CIDs with whitespace/newline are handled correctly by caller."""
        # The verify_cid_format function itself doesn't strip, but the CLI should
        cid_clean = "bafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm"
        cid_with_newline = "\nbafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm"
        cid_with_crlf = "\r\nbafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm"
        cid_with_space = "  bafkreiemazil222k4kmfbkoheoet4h3rqfj2vqwpkun47g5bh5tqmy2ekm  "
        
        # Clean CID should be valid
        assert verify_cid_format(cid_clean) is True
        
        # CIDs with whitespace should be invalid (stripping is caller's responsibility)
        assert verify_cid_format(cid_with_newline) is False
        assert verify_cid_format(cid_with_crlf) is False
        assert verify_cid_format(cid_with_space) is False
        
        # After stripping, they should be valid
        assert verify_cid_format(cid_with_newline.strip()) is True
        assert verify_cid_format(cid_with_crlf.strip()) is True
        assert verify_cid_format(cid_with_space.strip()) is True
    
    def test_invalid_empty(self):
        """Test empty CID."""
        assert verify_cid_format("") is False
        assert verify_cid_format(None) is False  # type: ignore
    
    def test_invalid_too_short(self):
        """Test CID that is too short."""
        assert verify_cid_format("Qm") is False
        assert verify_cid_format("baf") is False
    
    def test_invalid_wrong_prefix(self):
        """Test CID with wrong prefix."""
        assert verify_cid_format("invalid") is False
        assert verify_cid_format("xyz123") is False


class TestParseEncryptionMetadata:
    """Tests for parsing encryption metadata."""
    
    def test_parse_standard_format(self):
        """Test parsing standard snake_case format."""
        data = {
            "ciphertext": "abc123",
            "data_to_encrypt_hash": "hash123",
            "access_control_conditions": [{"conditionType": "evmBasic"}],
            "chain": "ethereum",
        }
        
        result = _parse_encryption_metadata(data)
        
        assert result.ciphertext == "abc123"
        assert result.data_to_encrypt_hash == "hash123"
        assert result.chain == "ethereum"
        assert len(result.access_control_conditions) == 1
    
    def test_parse_camel_case_format(self):
        """Test parsing camelCase format."""
        data = {
            "ciphertext": "abc123",
            "dataToEncryptHash": "hash123",
            "accessControlConditions": [{"conditionType": "evmBasic"}],
            "chain": "ethereum",
        }
        
        result = _parse_encryption_metadata(data)
        
        assert result.data_to_encrypt_hash == "hash123"
        assert len(result.access_control_conditions) == 1
    
    def test_parse_default_values(self):
        """Test parsing with default values."""
        data = {
            "ciphertext": "",
            "data_to_encrypt_hash": "",
        }
        
        result = _parse_encryption_metadata(data)
        
        assert result.chain == "ethereum"
        assert result.access_control_conditions == []


class TestLoadEncryptionMetadata:
    """Tests for loading encryption metadata from sidecar."""
    
    @pytest.mark.asyncio
    async def test_load_from_sidecar(self, tmp_path):
        """Test loading metadata from sidecar file."""
        file_path = tmp_path / "video.mp4"
        metadata_path = tmp_path / "video.mp4.lit"
        
        # Create metadata file
        metadata_data = {
            "ciphertext": "encrypted_content",
            "data_to_encrypt_hash": "hash_value",
            "access_control_conditions": [{"conditionType": "evmBasic"}],
            "chain": "ethereum",
        }
        metadata_path.write_text(json.dumps(metadata_data))
        
        result = await load_encryption_metadata(file_path)
        
        assert result is not None
        assert result.ciphertext == "encrypted_content"
        assert result.data_to_encrypt_hash == "hash_value"
    
    @pytest.mark.asyncio
    async def test_load_no_sidecar(self, tmp_path):
        """Test loading when sidecar doesn't exist."""
        file_path = tmp_path / "video.mp4"
        
        result = await load_encryption_metadata(file_path)
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_load_invalid_json(self, tmp_path):
        """Test loading with invalid JSON."""
        file_path = tmp_path / "video.mp4"
        metadata_path = tmp_path / "video.mp4.lit"
        
        metadata_path.write_text("invalid json")
        
        result = await load_encryption_metadata(file_path)
        
        assert result is None


class TestSaveEncryptionMetadata:
    """Tests for saving encryption metadata."""
    
    @pytest.mark.asyncio
    async def test_save_to_sidecar(self, tmp_path):
        """Test saving metadata to sidecar file."""
        file_path = tmp_path / "video.mp4"
        metadata = EncryptionMetadata(
            ciphertext="encrypted_content",
            data_to_encrypt_hash="hash_value",
            access_control_conditions=[{"conditionType": "evmBasic"}],
            chain="ethereum",
        )
        
        await save_encryption_metadata(file_path, metadata)
        
        metadata_path = tmp_path / "video.mp4.lit"
        assert metadata_path.exists()
        
        data = json.loads(metadata_path.read_text())
        assert data["ciphertext"] == "encrypted_content"
        assert data["data_to_encrypt_hash"] == "hash_value"
    
    @pytest.mark.asyncio
    async def test_save_creates_both_field_formats(self, tmp_path):
        """Test that saved metadata includes both snake_case and camelCase."""
        file_path = tmp_path / "video.mp4"
        metadata = EncryptionMetadata(
            ciphertext="test",
            data_to_encrypt_hash="hash",
            access_control_conditions=[],
            chain="ethereum",
        )
        
        await save_encryption_metadata(file_path, metadata)
        
        metadata_path = tmp_path / "video.mp4.lit"
        data = json.loads(metadata_path.read_text())
        
        # Both formats should be present for compatibility
        assert "data_to_encrypt_hash" in data
        assert "dataToEncryptHash" in data
        assert "access_control_conditions" in data
        assert "accessControlConditions" in data


class TestGetEncryptionMetadataPath:
    """Tests for getting metadata path."""
    
    def test_get_path(self):
        """Test getting metadata path from file path."""
        file_path = Path("/path/to/video.mp4")
        
        result = get_encryption_metadata_path(file_path)
        
        assert result == Path("/path/to/video.mp4.lit")
    
    def test_get_path_with_multiple_extensions(self):
        """Test getting metadata path for file with multiple extensions."""
        file_path = Path("/path/to/video.tar.gz")
        
        result = get_encryption_metadata_path(file_path)
        
        assert result == Path("/path/to/video.tar.gz.lit")


class TestDeleteEncryptionMetadata:
    """Tests for deleting encryption metadata."""
    
    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_path):
        """Test deleting existing metadata file."""
        file_path = tmp_path / "video.mp4"
        metadata_path = tmp_path / "video.mp4.lit"
        metadata_path.write_text("{}")
        
        result = await delete_encryption_metadata(file_path)
        
        assert result is True
        assert not metadata_path.exists()
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_path):
        """Test deleting non-existent metadata file."""
        file_path = tmp_path / "video.mp4"
        
        result = await delete_encryption_metadata(file_path)
        
        assert result is False


class TestFindEncryptionMetadata:
    """Tests for finding encryption metadata with multiple methods."""
    
    @pytest.mark.asyncio
    async def test_find_by_cid(self, tmp_path):
        """Test finding metadata by CID from sidecar."""
        file_path = tmp_path / "video.mp4"
        metadata_path = tmp_path / "video.mp4.lit"
        
        metadata_data = {
            "ciphertext": "test",
            "data_to_encrypt_hash": "hash",
            "access_control_conditions": [],
            "chain": "ethereum",
        }
        metadata_path.write_text(json.dumps(metadata_data))
        
        result = await find_encryption_metadata(file_path=file_path)
        
        assert result is not None
        assert result.ciphertext == "test"
    
    @pytest.mark.asyncio
    async def test_find_no_match(self, tmp_path):
        """Test finding metadata when no match exists."""
        file_path = tmp_path / "video.mp4"
        
        result = await find_encryption_metadata(file_path=file_path)
        
        assert result is None
