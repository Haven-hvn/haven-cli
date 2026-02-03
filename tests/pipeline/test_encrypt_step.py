"""Tests for the encrypt pipeline step.

Tests the Lit Protocol encryption step including:
- Access condition generation for different patterns
- JS bridge integration
- Error handling
- Database persistence
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from haven_cli.pipeline.context import EncryptionMetadata, PipelineContext
from haven_cli.pipeline.results import StepResult
from haven_cli.pipeline.steps.encrypt_step import EncryptStep


class TestEncryptStepBasics:
    """Basic tests for EncryptStep."""
    
    def test_step_name(self):
        """Test step name is correct."""
        step = EncryptStep()
        assert step.name == "encrypt"
    
    def test_enabled_option(self):
        """Test enabled option is 'encrypt'."""
        step = EncryptStep()
        assert step.enabled_option == "encrypt"
    
    def test_default_enabled(self):
        """Test encryption is disabled by default."""
        step = EncryptStep()
        assert step.default_enabled is False
    
    def test_max_retries(self):
        """Test max retries is set correctly."""
        step = EncryptStep()
        assert step.max_retries == 3


class TestEncryptStepAccessConditions:
    """Tests for access condition generation."""
    
    def test_owner_only_conditions(self):
        """Test owner-only access conditions."""
        step = EncryptStep(config={"owner_wallet": "0x1234567890abcdef", "chain": "ethereum"})
        context = PipelineContext(source_path=Path("/tmp/test.mp4"), options={})
        
        conditions = step._owner_only_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["chain"] == "ethereum"
        assert conditions[0]["returnValueTest"]["value"] == "0x1234567890abcdef"
        assert conditions[0]["parameters"] == [":userAddress"]
    
    def test_owner_only_conditions_from_context(self):
        """Test owner-only conditions from context options."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"owner_wallet": "0xabcdef1234567890"}
        )
        
        conditions = step._owner_only_conditions(context)
        
        assert conditions[0]["returnValueTest"]["value"] == "0xabcdef1234567890"
    
    def test_owner_only_conditions_missing_wallet(self):
        """Test error when owner wallet is missing."""
        step = EncryptStep(config={})
        context = PipelineContext(source_path=Path("/tmp/test.mp4"), options={})
        
        with pytest.raises(ValueError, match="owner_wallet required"):
            step._owner_only_conditions(context)
    
    def test_nft_gated_conditions(self):
        """Test NFT-gated access conditions."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"nft_contract": "0xNFTContractAddress"}
        )
        
        conditions = step._nft_gated_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["contractAddress"] == "0xNFTContractAddress"
        assert conditions[0]["standardContractType"] == "ERC721"
        assert conditions[0]["method"] == "balanceOf"
        assert conditions[0]["returnValueTest"]["comparator"] == ">"
        assert conditions[0]["returnValueTest"]["value"] == "0"
    
    def test_nft_gated_conditions_missing_contract(self):
        """Test error when NFT contract is missing."""
        step = EncryptStep(config={})
        context = PipelineContext(source_path=Path("/tmp/test.mp4"), options={})
        
        with pytest.raises(ValueError, match="nft_contract required"):
            step._nft_gated_conditions(context)
    
    def test_token_gated_conditions_erc20(self):
        """Test token-gated access conditions for ERC20."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "token_contract": "0xTokenContractAddress",
                "min_balance": "100",
            }
        )
        
        conditions = step._token_gated_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["contractAddress"] == "0xTokenContractAddress"
        assert conditions[0]["standardContractType"] == "ERC20"
        assert conditions[0]["returnValueTest"]["comparator"] == ">="
        assert conditions[0]["returnValueTest"]["value"] == "100"
    
    def test_token_gated_conditions_erc721(self):
        """Test token-gated access conditions for ERC721."""
        step = EncryptStep(config={"chain": "ethereum", "token_standard": "ERC721"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "token_contract": "0xTokenContractAddress",
                "min_balance": "5",
            }
        )
        
        conditions = step._token_gated_conditions(context)
        
        assert conditions[0]["standardContractType"] == "ERC721"
        assert conditions[0]["returnValueTest"]["value"] == "5"
    
    def test_token_gated_conditions_missing_contract(self):
        """Test error when token contract is missing."""
        step = EncryptStep(config={})
        context = PipelineContext(source_path=Path("/tmp/test.mp4"), options={})
        
        with pytest.raises(ValueError, match="token_contract required"):
            step._token_gated_conditions(context)
    
    def test_token_gated_conditions_unsupported_standard(self):
        """Test error for unsupported token standard."""
        step = EncryptStep(config={"token_standard": "ERC1155"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"token_contract": "0xTokenContractAddress"}
        )
        
        with pytest.raises(ValueError, match="Unsupported token standard"):
            step._token_gated_conditions(context)
    
    def test_public_conditions(self):
        """Test public access conditions."""
        step = EncryptStep(config={"chain": "ethereum"})
        
        conditions = step._public_conditions()
        
        assert len(conditions) == 1
        assert conditions[0]["returnValueTest"]["value"] == "true"
    
    def test_get_access_conditions_explicit(self):
        """Test getting explicit access conditions from context."""
        step = EncryptStep(config={})
        explicit_conditions = [{"custom": "condition"}]
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_conditions": explicit_conditions}
        )
        
        conditions = step._get_access_conditions(context)
        
        assert conditions == explicit_conditions
    
    def test_get_access_conditions_owner_only_pattern(self):
        """Test owner_only access pattern."""
        step = EncryptStep(config={"owner_wallet": "0x123", "chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "owner_only"}
        )
        
        conditions = step._get_access_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["returnValueTest"]["value"] == "0x123"
    
    def test_get_access_conditions_nft_gated_pattern(self):
        """Test nft_gated access pattern."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "access_pattern": "nft_gated",
                "nft_contract": "0xNFT",
            }
        )
        
        conditions = step._get_access_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["standardContractType"] == "ERC721"
    
    def test_get_access_conditions_token_gated_pattern(self):
        """Test token_gated access pattern."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "access_pattern": "token_gated",
                "token_contract": "0xToken",
            }
        )
        
        conditions = step._get_access_conditions(context)
        
        assert len(conditions) == 1
        assert conditions[0]["standardContractType"] == "ERC20"
    
    def test_get_access_conditions_public_pattern(self):
        """Test public access pattern."""
        step = EncryptStep(config={"chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "public"}
        )
        
        conditions = step._get_access_conditions(context)
        
        assert conditions[0]["returnValueTest"]["value"] == "true"
    
    def test_get_access_conditions_unknown_pattern(self):
        """Test error for unknown access pattern."""
        step = EncryptStep(config={})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "unknown_pattern"}
        )
        
        with pytest.raises(ValueError, match="Unknown access pattern"):
            step._get_access_conditions(context)
    
    def test_get_access_conditions_default_pattern(self):
        """Test default access pattern is owner_only."""
        step = EncryptStep(config={"owner_wallet": "0x123", "chain": "ethereum"})
        context = PipelineContext(source_path=Path("/tmp/test.mp4"), options={})
        
        conditions = step._get_access_conditions(context)
        
        # Should default to owner_only
        assert conditions[0]["returnValueTest"]["value"] == "0x123"


class TestEncryptStepEncryption:
    """Tests for the encryption process."""
    
    @pytest.mark.asyncio
    async def test_get_js_bridge(self):
        """Test getting JS bridge from manager."""
        step = EncryptStep()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        
        with patch("haven_cli.pipeline.steps.encrypt_step.JSBridgeManager") as mock_mgr:
            mock_instance = MagicMock()
            mock_instance.get_bridge = AsyncMock(return_value=mock_bridge)
            mock_mgr.get_instance.return_value = mock_instance
            
            bridge = await step._get_js_bridge()
            
            assert bridge is mock_bridge
            mock_mgr.get_instance.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_encrypt_with_lit_success(self, tmp_path):
        """Test successful encryption via Lit Protocol."""
        step = EncryptStep(config={"chain": "ethereum", "lit_network": "datil-dev"})
        
        # Create a test video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test video content")
        
        # Mock the bridge
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # lit.connect response
            {
                "ciphertext": "ZW5jcnlwdGVk",  # base64 for "encrypted"
                "dataToEncryptHash": "0xhash123",
                "accessControlConditionHash": "0xaccHash",
            },  # lit.encrypt response
        ])
        
        access_conditions = [{"conditionType": "evmBasic"}]
        
        result = await step._encrypt_with_lit(
            mock_bridge,
            str(video_file),
            access_conditions,
        )
        
        assert result["ciphertext_path"] == str(video_file) + ".enc"
        assert result["data_to_encrypt_hash"] == "0xhash123"
        assert result["chain"] == "ethereum"
        assert "original_hash" in result
        
        # Verify encrypted file was created
        encrypted_file = tmp_path / "test.mp4.enc"
        assert encrypted_file.exists()
        assert encrypted_file.read_bytes() == b"encrypted"
        
        # Verify bridge calls
        assert mock_bridge.call.call_count == 2
        
        # Check lit.connect call
        connect_call = mock_bridge.call.call_args_list[0]
        assert connect_call[0][0] == "lit.connect"
        assert connect_call[0][1]["network"] == "datil-dev"
        
        # Check lit.encrypt call
        encrypt_call = mock_bridge.call.call_args_list[1]
        assert encrypt_call[0][0] == "lit.encrypt"
        assert encrypt_call[0][1]["chain"] == "ethereum"
        assert encrypt_call[0][1]["accessControlConditions"] == access_conditions
    
    @pytest.mark.asyncio
    async def test_encrypt_with_lit_connection_failure(self, tmp_path):
        """Test handling of Lit connection failure."""
        step = EncryptStep(config={"chain": "ethereum"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Connection failed"))
        
        with pytest.raises(RuntimeError, match="Lit Protocol connection failed"):
            await step._encrypt_with_lit(
                mock_bridge,
                str(video_file),
                [{}],
            )
    
    @pytest.mark.asyncio
    async def test_encrypt_with_lit_encryption_failure(self, tmp_path):
        """Test handling of Lit encryption failure."""
        step = EncryptStep(config={"chain": "ethereum"})
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # lit.connect succeeds
            RuntimeError("Encryption failed"),  # lit.encrypt fails
        ])
        
        with pytest.raises(RuntimeError, match="Encryption failed"):
            await step._encrypt_with_lit(
                mock_bridge,
                str(video_file),
                [{}],
            )
    
    @pytest.mark.asyncio
    async def test_encrypt_with_lit_file_not_found(self):
        """Test handling of missing video file."""
        step = EncryptStep(config={"chain": "ethereum"})
        
        # Use AsyncMock to properly mock async calls
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(return_value=None)
        
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            await step._encrypt_with_lit(
                mock_bridge,
                "/nonexistent/path/video.mp4",
                [{}],
            )
    
    @pytest.mark.asyncio
    async def test_encrypt_large_file(self, tmp_path):
        """Test encryption of large files uses chunked method."""
        step = EncryptStep(config={"chain": "ethereum"})
        
        # Create a large test file (> 10MB)
        video_file = tmp_path / "large.mp4"
        video_file.write_bytes(b"x" * (11 * 1024 * 1024))
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # lit.connect
            {
                "ciphertext": "ZW5j",  # base64 for "enc"
                "dataToEncryptHash": "0xlargehash",
            },  # lit.encrypt
        ])
        
        result = await step._encrypt_with_lit(
            mock_bridge,
            str(video_file),
            [{}],
        )
        
        assert result["data_to_encrypt_hash"] == "0xlargehash"
        assert Path(result["ciphertext_path"]).exists()


class TestEncryptStepProcess:
    """Tests for the main process method."""
    
    @pytest.mark.asyncio
    async def test_process_success(self, tmp_path):
        """Test successful encryption process."""
        step = EncryptStep(config={
            "owner_wallet": "0x123",
            "chain": "ethereum",
        })
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True},
            video_id=42,
        )
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,  # lit.connect
            {
                "ciphertext": "ZW5j",
                "dataToEncryptHash": "0xhash",
                "accessControlConditionHash": "0xacc",
            },  # lit.encrypt
        ])
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            with patch.object(step, '_save_encryption_metadata', new_callable=AsyncMock):
                result = await step.process(context)
        
        assert result.success is True
        assert result.data["ciphertext_hash"] == "0xhash"
        assert result.data["chain"] == "ethereum"
        assert context.encryption_metadata is not None
        assert context.encrypted_video_path == str(video_file) + ".enc"
    
    @pytest.mark.asyncio
    async def test_process_without_video_id(self, tmp_path):
        """Test encryption without video ID (skips database save)."""
        step = EncryptStep(config={
            "owner_wallet": "0x123",
            "chain": "ethereum",
        })
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True},
            video_id=None,  # No video ID
        )
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=[
            None,
            {"ciphertext": "ZW5j", "dataToEncryptHash": "0xhash"},
        ])
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            # Should not call save_encryption_metadata
            with patch.object(step, '_save_encryption_metadata') as mock_save:
                result = await step.process(context)
                mock_save.assert_not_called()
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_process_encryption_failure(self, tmp_path):
        """Test handling of encryption failure."""
        step = EncryptStep(config={
            "owner_wallet": "0x123",
            "chain": "ethereum",
        })
        
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")
        
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True},
        )
        
        mock_bridge = MagicMock()
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Encryption failed"))
        
        with patch.object(step, '_get_js_bridge', return_value=mock_bridge):
            result = await step.process(context)
        
        assert result.success is False
        assert result.failed is True
        assert result.error is not None
        assert result.error.code == "ENCRYPT_ERROR"


class TestEncryptStepDatabase:
    """Tests for database persistence."""
    
    @pytest.mark.asyncio
    async def test_save_encryption_metadata(self):
        """Test saving encryption metadata to database."""
        step = EncryptStep()
        
        metadata = EncryptionMetadata(
            ciphertext="/path/to/encrypted.enc",
            data_to_encrypt_hash="0xhash123",
            access_control_conditions=[{"conditionType": "evmBasic"}],
            chain="ethereum",
        )
        
        mock_video = MagicMock()
        mock_video.id = 42
        
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = mock_video
        
        # Create a proper context manager mock for get_db_session
        mock_session_context = MagicMock()
        mock_session_context.__enter__ = MagicMock(return_value=mock_session_context)
        mock_session_context.__exit__ = MagicMock(return_value=None)
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value = mock_session_context
            
            with patch("haven_cli.database.repositories.VideoRepository") as mock_repo_class:
                mock_repo_class.return_value = mock_repo
                
                await step._save_encryption_metadata(42, metadata)
        
        mock_repo.get_by_id.assert_called_once_with(42)
        mock_repo.update.assert_called_once()
        
        # Check the update call
        call_args = mock_repo.update.call_args
        assert call_args[0][0] is mock_video
        assert call_args[1]["encrypted"] is True
        assert "lit_encryption_metadata" in call_args[1]
    
    @pytest.mark.asyncio
    async def test_save_encryption_metadata_video_not_found(self):
        """Test saving metadata when video doesn't exist."""
        step = EncryptStep()
        
        metadata = EncryptionMetadata(
            ciphertext="/path/to/encrypted.enc",
            data_to_encrypt_hash="0xhash123",
            access_control_conditions=[],
            chain="ethereum",
        )
        
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None
        
        # Create a proper context manager mock for get_db_session
        mock_session_context = MagicMock()
        mock_session_context.__enter__ = MagicMock(return_value=mock_session_context)
        mock_session_context.__exit__ = MagicMock(return_value=None)
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            mock_get_session.return_value = mock_session_context
            with patch("haven_cli.database.repositories.VideoRepository") as mock_repo_class:
                mock_repo_class.return_value = mock_repo
                
                # Should not raise, just log warning
                await step._save_encryption_metadata(999, metadata)
        
        mock_repo.update.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_save_encryption_metadata_db_error(self):
        """Test handling of database error during save."""
        step = EncryptStep()
        
        metadata = EncryptionMetadata(
            ciphertext="/path/to/encrypted.enc",
            data_to_encrypt_hash="0xhash123",
            access_control_conditions=[],
            chain="ethereum",
        )
        
        with patch("haven_cli.database.connection.get_db_session") as mock_get_session:
            # Create a context manager that raises on __enter__
            mock_session_context = MagicMock()
            mock_session_context.__enter__ = MagicMock(side_effect=Exception("DB connection failed"))
            mock_session_context.__exit__ = MagicMock(return_value=None)
            mock_get_session.return_value = mock_session_context
            
            # Should not raise, just log error
            await step._save_encryption_metadata(1, metadata)


class TestEncryptStepHelpers:
    """Tests for helper methods."""
    
    def test_metadata_to_json(self):
        """Test conversion of metadata to JSON."""
        step = EncryptStep()
        
        metadata = EncryptionMetadata(
            ciphertext="/path/to/enc",
            data_to_encrypt_hash="0xhash",
            access_control_conditions=[{"type": "test"}],
            chain="ethereum",
        )
        
        json_str = step._metadata_to_json(metadata)
        data = json.loads(json_str)
        
        assert data["ciphertext"] == "/path/to/enc"
        assert data["data_to_encrypt_hash"] == "0xhash"
        assert data["dataToEncryptHash"] == "0xhash"  # camelCase
        assert data["chain"] == "ethereum"
        assert data["access_control_conditions"] == [{"type": "test"}]
        assert data["accessControlConditions"] == [{"type": "test"}]  # camelCase
    
    @pytest.mark.asyncio
    async def test_on_skip(self):
        """Test on_skip handler."""
        step = EncryptStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        
        # Should not raise
        await step.on_skip(context, "encryption disabled")
    
    @pytest.mark.asyncio
    async def test_on_error(self):
        """Test on_error handler."""
        step = EncryptStep()
        context = PipelineContext(source_path=Path("/tmp/test.mp4"))
        from haven_cli.pipeline.results import StepError
        error = StepError.permanent(code="TEST", message="Test error")
        
        # Should not raise
        await step.on_error(context, error)
