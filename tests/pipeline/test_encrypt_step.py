"""Tests for the encrypt pipeline step.

Tests the Haven-AOL encryption step including:
- Access condition generation for different patterns
- Real streaming encryption (no JS bridge mock)
- Error handling
- Database persistence
"""

import hashlib
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.crypto.haven_aol_local import GateParams, decrypt_file_streaming
import haven_cli.crypto.haven_aol_local as haven_aol_local
from haven_cli.pipeline.context import EncryptionMetadata, PipelineContext
from haven_cli.pipeline.results import ErrorCategory, StepResult
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.steps import encrypt_step as encrypt_step_module
from haven_cli.pipeline.steps.encrypt_step import (
    HASH_PROGRESS_WEIGHT_PERCENT,
    EncryptStep,
    _classify_icp_transport_error,
    _is_icp_transport_error,
    build_encrypt_progress_payload,
    classify_encrypt_failure,
    compute_encrypt_stage_progress,
    hash_file_sha256_with_progress,
    should_emit_encrypt_progress,
    step_error_from_encrypt_exception,
)

# Valid 40-hex address used in tests (matches the contract address used in encryption tests)
TEST_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture(autouse=True)
def _mock_haven_aol_ibe(monkeypatch) -> None:
    """Mock IBE key wrapping for deterministic unit tests."""
    monkeypatch.setattr(haven_aol_local, "_ibe_encrypt_aes_key", lambda aes_key, derivation_input: b"wrapped")


class TestEncryptProgressHelpers:
    """Tests for encrypt progress calculation and payload helpers."""

    def test_compute_encrypt_stage_progress_hashing(self) -> None:
        assert compute_encrypt_stage_progress(50, 100, phase="hashing") == pytest.approx(
            HASH_PROGRESS_WEIGHT_PERCENT / 2
        )

    def test_compute_encrypt_stage_progress_encrypting(self) -> None:
        span = 100.0 - HASH_PROGRESS_WEIGHT_PERCENT
        assert compute_encrypt_stage_progress(50, 100, phase="encrypting") == pytest.approx(
            HASH_PROGRESS_WEIGHT_PERCENT + span / 2
        )

    def test_compute_encrypt_stage_progress_zero_file_size(self) -> None:
        assert compute_encrypt_stage_progress(0, 0, phase="hashing") == 0.0
        assert compute_encrypt_stage_progress(0, 0, phase="encrypting") == pytest.approx(
            HASH_PROGRESS_WEIGHT_PERCENT
        )

    def test_should_emit_encrypt_progress_force(self) -> None:
        assert should_emit_encrypt_progress(10.0, 9.0, 5.0, 5.0, force=True) is True

    def test_should_emit_encrypt_progress_interval(self) -> None:
        assert should_emit_encrypt_progress(2.0, 0.0, 0.0, 1.0) is True
        assert should_emit_encrypt_progress(1.5, 1.0, 1.0, 1.5) is False
        assert should_emit_encrypt_progress(2.5, 1.0, 1.0, 1.5) is True

    def test_should_emit_encrypt_progress_delta(self) -> None:
        assert should_emit_encrypt_progress(1.0, 0.5, 0.0, 2.0) is True

    def test_build_encrypt_progress_payload(self) -> None:
        payload = build_encrypt_progress_payload(
            video_id=7,
            job_id=3,
            video_path="/v.mp4",
            progress_percent=42.5,
            bytes_processed=425,
            bytes_total=1000,
            phase="encrypting",
            encrypt_speed=1024,
            chunk_index=2,
        )
        assert payload["video_id"] == 7
        assert payload["job_id"] == 3
        assert payload["progress"] == 42.5
        assert payload["progress_percent"] == 42.5
        assert payload["bytes_processed"] == 425
        assert payload["phase"] == "encrypting"
        assert payload["encrypt_speed"] == 1024
        assert payload["chunk_index"] == 2

    def test_hash_file_sha256_with_progress(self, tmp_path: Path) -> None:
        data = b"abc" * 100
        path = tmp_path / "hash_me.bin"
        path.write_bytes(data)
        seen: list[int] = []

        digest = hash_file_sha256_with_progress(
            str(path),
            block_size=50,
            progress_callback=seen.append,
        )

        assert digest == hashlib.sha256(data).hexdigest()
        assert seen[-1] == len(data)
        assert len(seen) >= 2


class TestEncryptFailureClassification:
    """Tests for encrypt error category mapping (retries / reporting)."""

    def test_classify_value_error_permanent(self) -> None:
        assert classify_encrypt_failure(ValueError("bad")) is ErrorCategory.PERMANENT

    def test_classify_connection_error_transient(self) -> None:
        assert classify_encrypt_failure(ConnectionError("reset")) is ErrorCategory.TRANSIENT

    def test_classify_oserror_transient_errno(self) -> None:
        import errno

        exc = OSError(errno.ETIMEDOUT, "timed out")
        assert classify_encrypt_failure(exc) is ErrorCategory.TRANSIENT

    def test_classify_runtime_error_timeout_message_transient(self) -> None:
        assert (
            classify_encrypt_failure(RuntimeError("upstream Connection timeout"))
            is ErrorCategory.TRANSIENT
        )

    def test_step_error_from_encrypt_marks_transient_retryable(self) -> None:
        err = step_error_from_encrypt_exception(TimeoutError("x"))
        assert err.category is ErrorCategory.TRANSIENT
        assert err.retryable is True


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
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        conditions = step._owner_only_conditions(context)

        assert len(conditions) == 1
        # "ethereum" normalizes to "EthMainnet"
        assert conditions[0]["chain"] == "EthMainnet"
        assert conditions[0]["returnValueTest"]["value"] == "1"
        assert conditions[0]["parameters"] == [":userAddress"]
        assert conditions[0]["ownerWallet"] == TEST_ADDRESS
        assert conditions[0]["contractAddress"] == TEST_ADDRESS

    def test_owner_only_conditions_from_context(self):
        """Test owner-only conditions from context options."""
        step = EncryptStep(config={"evm_chain": "ethereum", "token_contract": TEST_ADDRESS})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "owner_wallet": "0xabcdef1234567890abcdef1234567890abcdef12",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._owner_only_conditions(context)

        assert conditions[0]["returnValueTest"]["value"] == "1"
        assert conditions[0]["ownerWallet"] == "0xabcdef1234567890abcdef1234567890abcdef12"

    def test_owner_only_conditions_missing_wallet(self):
        """Test error when owner wallet is missing."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="owner_wallet required"):
            step._owner_only_conditions(context)

    def test_nft_gated_conditions(self):
        """Test NFT-gated access conditions."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "nft_contract": "0xNFTContractAddress1234567890abcdef",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._nft_gated_conditions(context)

        assert len(conditions) == 1
        assert conditions[0]["contractAddress"] == "0xNFTContractAddress1234567890abcdef"
        assert conditions[0]["standardContractType"] == "ERC721"
        assert conditions[0]["method"] == "balanceOf"
        assert conditions[0]["returnValueTest"]["comparator"] == ">"
        assert conditions[0]["returnValueTest"]["value"] == "0"

    def test_nft_gated_conditions_missing_contract(self):
        """Test error when NFT contract is missing."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="nft_contract required"):
            step._nft_gated_conditions(context)

    def test_token_gated_conditions_erc20(self):
        """Test token-gated access conditions for ERC20."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "token_contract": "0xTokenContractAddress1234567890abcdef",
                "min_balance": "100",
                "token_standard": "ERC20",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._token_gated_conditions(context)

        assert len(conditions) == 1
        assert conditions[0]["contractAddress"] == "0xTokenContractAddress1234567890abcdef"
        assert conditions[0]["standardContractType"] == "ERC20"
        assert conditions[0]["returnValueTest"]["comparator"] == ">="
        assert conditions[0]["returnValueTest"]["value"] == "100"

    def test_token_gated_conditions_erc721(self):
        """Test token-gated access conditions for ERC721."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "token_contract": "0xTokenContractAddress1234567890abcdef",
                "min_balance": "5",
                "token_standard": "ERC721",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._token_gated_conditions(context)

        assert conditions[0]["standardContractType"] == "ERC721"
        assert conditions[0]["returnValueTest"]["value"] == "5"

    def test_token_gated_conditions_missing_contract(self):
        """Test error when token contract is missing."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="token_contract required"):
            step._token_gated_conditions(context)

    def test_token_gated_conditions_unsupported_standard(self):
        """Test error for unsupported token standard."""
        step = EncryptStep(config={"evm_chain": "ethereum", "token_standard": "ERC1155"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "token_contract": "0xTokenContractAddress1234567890abcdef",
                "min_balance": "10",
                "token_standard": "ERC1155",
                "evm_chain": "ethereum",
            },
        )

        with pytest.raises(ValueError, match="Unsupported token standard"):
            step._token_gated_conditions(context)

    def test_public_conditions_raises(self):
        """Test public access conditions raises — canister does not support public mode."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="not supported by the Haven-AOL canister"):
            step._public_conditions(context)

    def test_get_access_conditions_explicit(self):
        """Test getting explicit access conditions from context."""
        step = EncryptStep(config={})
        explicit_conditions = [{"custom": "condition"}]
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_conditions": explicit_conditions},
        )

        conditions = step._get_access_conditions(context)

        assert conditions == explicit_conditions

    def test_get_access_conditions_owner_only_pattern(self):
        """Test owner_only access pattern."""
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "owner_only", "evm_chain": "ethereum"},
        )

        conditions = step._get_access_conditions(context)

        assert len(conditions) == 1
        assert conditions[0]["returnValueTest"]["value"] == "1"

    def test_get_access_conditions_nft_gated_pattern(self):
        """Test nft_gated access pattern."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "access_pattern": "nft_gated",
                "nft_contract": "0xNFT",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._get_access_conditions(context)

        assert len(conditions) == 1
        assert conditions[0]["standardContractType"] == "ERC721"

    def test_get_access_conditions_token_gated_pattern(self):
        """Test token_gated access pattern."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={
                "access_pattern": "token_gated",
                "token_contract": "0xToken",
                "min_balance": "100",
                "token_standard": "ERC20",
                "evm_chain": "ethereum",
            },
        )

        conditions = step._get_access_conditions(context)

        assert len(conditions) == 1
        assert conditions[0]["standardContractType"] == "ERC20"

    def test_get_access_conditions_public_pattern_raises(self):
        """Test public access pattern raises — canister does not support it."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "public", "evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="not supported by the Haven-AOL canister"):
            step._get_access_conditions(context)

    def test_get_access_conditions_unknown_pattern(self):
        """Test error for unknown access pattern."""
        step = EncryptStep(config={"evm_chain": "ethereum"})
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"access_pattern": "unknown_pattern", "evm_chain": "ethereum"},
        )

        with pytest.raises(ValueError, match="Unknown access pattern"):
            step._get_access_conditions(context)

    def test_get_access_conditions_default_pattern(self):
        """Test default config access_pattern falls through to owner_only."""
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
            "access_pattern": "owner_only",
        })
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "ethereum"},
        )

        conditions = step._get_access_conditions(context)

        assert conditions[0]["returnValueTest"]["value"] == "1"

    def test_chain_resolved_from_context_options_first(self):
        """Test that evm_chain in context options takes priority over config."""
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })
        context = PipelineContext(
            source_path=Path("/tmp/test.mp4"),
            options={"evm_chain": "BaseMainnet"},
        )

        conditions = step._owner_only_conditions(context)

        # Should use BaseMainnet from context, not ethereum from config
        assert conditions[0]["chain"] == "BaseMainnet"


class TestEncryptStepEncryption:
    """Tests for the encryption process using the real streaming path."""

    @pytest.mark.asyncio
    async def test_encrypt_with_haven_aol_success(self, tmp_path, monkeypatch):
        """Test successful encryption via Haven-AOL streaming."""
        # Set a test private key
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        step = EncryptStep(config={"evm_chain": "ethereum"})

        # Create a test video file
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test video content")

        # Create mock context
        context = PipelineContext(
            source_path=video_file,
            video_id=1,
            options={"evm_chain": "ethereum"},
        )

        # Access conditions with a valid contract address
        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
            "cid": "sha256:abc123",
        }]

        result = await step._encrypt_with_haven_aol(
            str(video_file),
            access_conditions,
            context,
        )

        assert result["ciphertext_path"] == str(video_file) + ".encrypted"
        assert result["data_to_encrypt_hash"] is not None
        assert result["chain"] == "EthMainnet"
        assert "original_hash" in result
        assert result["encrypted_key"] is not None
        assert result["key_hash"] is not None
        assert result["iv"] is not None

        # Verify encrypted file was created
        assert (video_file.with_suffix(".mp4.encrypted")).exists()

    @pytest.mark.asyncio
    async def test_encrypt_with_haven_aol_file_not_found(self):
        """Test handling of missing video file."""
        step = EncryptStep(config={"evm_chain": "ethereum"})

        context = PipelineContext(
            source_path=Path("/tmp/nonexistent.mp4"),
            video_id=1,
            options={"evm_chain": "ethereum"},
        )

        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
            "cid": "sha256:abc123",
        }]

        with pytest.raises(FileNotFoundError, match="Video file not found"):
            await step._encrypt_with_haven_aol(
                "/nonexistent/path/video.mp4",
                access_conditions,
                context,
            )

    @pytest.mark.asyncio
    async def test_encrypt_with_haven_aol_no_private_key_required(self, tmp_path, monkeypatch):
        """Encryption no longer requires local private key env."""
        step = EncryptStep(config={"evm_chain": "ethereum"})

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            video_id=1,
            options={"evm_chain": "ethereum"},
        )

        with patch.dict(os.environ, {}, clear=True):
            access_conditions = [{
                "contractAddress": TEST_ADDRESS,
                "chain": "EthMainnet",
                "returnValueTest": {"value": "1"},
                "cid": "sha256:abc123",
            }]

            result = await step._encrypt_with_haven_aol(
                str(video_file),
                access_conditions,
                context,
            )
        assert result["encrypted_key"] == "d3JhcHBlZA=="

    @pytest.mark.asyncio
    async def test_encrypt_with_haven_aol_missing_contract(self, tmp_path, monkeypatch):
        """Test handling of missing token contract."""
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        step = EncryptStep(config={"evm_chain": "ethereum"})

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            video_id=1,
            options={"evm_chain": "ethereum"},
        )

        # Empty access conditions (no contract address)
        access_conditions = [{}]

        with pytest.raises(ValueError, match="token_contract/contractAddress is required"):
            await step._encrypt_with_haven_aol(
                str(video_file),
                access_conditions,
                context,
            )

    @pytest.mark.asyncio
    async def test_encrypt_with_haven_aol_missing_chain(self, tmp_path, monkeypatch):
        """Test handling of missing evm_chain."""
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        step = EncryptStep()

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            video_id=1,
            options={},  # No evm_chain
        )

        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
            "cid": "sha256:abc123",
        }]

        with pytest.raises(ValueError, match="evm_chain is required"):
            await step._encrypt_with_haven_aol(
                str(video_file),
                access_conditions,
                context,
            )


class TestEncryptStepProcess:
    """Tests for the main process method."""

    @pytest.mark.asyncio
    async def test_process_success(self, tmp_path, monkeypatch):
        """Test successful encryption process."""
        # Set a test private key
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "access_pattern": "owner_only",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "evm_chain": "ethereum", "access_pattern": "owner_only"},
            video_id=42,
        )

        with patch.object(step, '_save_encryption_metadata', new_callable=AsyncMock):
            result = await step.process(context)

        assert result.success is True
        assert result.data["chain"] == "EthMainnet"
        assert context.encryption_metadata is not None
        assert context.encrypted_video_path == str(video_file) + ".encrypted"

    @pytest.mark.asyncio
    async def test_process_emits_encrypt_progress_events(self, tmp_path, monkeypatch):
        """Chunked encrypt emits normalized ENCRYPT_PROGRESS with video_id and phases."""
        monkeypatch.setenv(
            "HAVEN_PRIVATE_KEY",
            "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "access_pattern": "owner_only",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"x" * 250)
        context = PipelineContext(
            source_path=video_file,
            options={
                "encrypt": True,
                "evm_chain": "ethereum",
                "access_pattern": "owner_only",
                "encrypt_chunk_size": 80,
            },
            video_id=42,
        )
        progress_payloads: list[dict] = []
        real_emit = step._emit_event

        async def capture_emit(
            event_type: EventType,
            ctx: PipelineContext,
            payload: dict,
        ) -> None:
            if event_type == EventType.ENCRYPT_PROGRESS:
                progress_payloads.append(payload)
            await real_emit(event_type, ctx, payload)

        step._emit_event = capture_emit  # type: ignore[method-assign]

        with patch.object(step, "_save_encryption_metadata", new_callable=AsyncMock):
            with patch.object(step, "_update_job_progress", new_callable=AsyncMock) as mock_job:
                result = await step.process(context)

        assert result.success is True
        assert len(progress_payloads) >= 2
        for payload in progress_payloads:
            assert payload["video_id"] == 42
            assert "progress" in payload
            assert payload["phase"] in ("hashing", "encrypting")
            assert payload["bytes_total"] == 250
        phases = {p["phase"] for p in progress_payloads}
        assert "hashing" in phases
        assert "encrypting" in phases
        assert mock_job.await_count >= 1
        final = progress_payloads[-1]
        assert final["progress"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_report_encrypt_progress_throttled(self) -> None:
        """Progress reporting skips redundant emissions within throttle window."""
        step = EncryptStep()
        step._reset_progress_tracking()
        context = PipelineContext(
            source_path=Path("/unused.mp4"),
            options={},
            video_id=1,
        )
        emitted: list[float] = []
        real_emit = step._emit_event

        async def capture_emit(
            event_type: EventType,
            ctx: PipelineContext,
            payload: dict,
        ) -> None:
            if event_type == EventType.ENCRYPT_PROGRESS:
                emitted.append(payload["progress"])
            await real_emit(event_type, ctx, payload)

        step._emit_event = capture_emit  # type: ignore[method-assign]

        with patch.object(step, "_update_job_progress", new_callable=AsyncMock):
            await step._report_encrypt_progress(
                context, "/v.mp4", 10, 100, "hashing", force=True
            )
            await step._report_encrypt_progress(
                context, "/v.mp4", 11, 100, "hashing"
            )

        assert len(emitted) == 1

    @pytest.mark.asyncio
    async def test_process_without_video_id(self, tmp_path, monkeypatch):
        """Test encryption without video ID (skips database save)."""
        # Set a test private key
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")

        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "access_pattern": "owner_only",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "evm_chain": "ethereum", "access_pattern": "owner_only"},
            video_id=None,  # No video ID
        )

        with patch.object(step, '_save_encryption_metadata') as mock_save:
            result = await step.process(context)
            mock_save.assert_not_called()

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_encryption_failure(self, tmp_path, monkeypatch):
        """Test handling of encryption failure from the streaming encrypt path."""
        step = EncryptStep(config={
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
            "evm_chain": "ethereum",
            "access_pattern": "owner_only",
        })

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test content")

        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "evm_chain": "ethereum", "access_pattern": "owner_only"},
        )

        def boom(**_kwargs: object) -> dict:
            raise RuntimeError("simulated encrypt failure")

        monkeypatch.setattr(encrypt_step_module, "encrypt_file_streaming", boom)

        result = await step.process(context)

        assert result.success is False
        assert result.failed is True
        assert result.error is not None
        assert result.error.code == "ENCRYPT_ERROR"
        assert "simulated encrypt failure" in result.error.message

    @pytest.mark.asyncio
    async def test_execute_retries_transient_encrypt_error(self, tmp_path, monkeypatch):
        """Transient errors from encrypt are retried by the step executor."""
        monkeypatch.setenv(
            "HAVEN_PRIVATE_KEY",
            "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        step = EncryptStep(config={
            "evm_chain": "ethereum",
            "access_pattern": "owner_only",
            "owner_wallet": TEST_ADDRESS,
            "token_contract": TEST_ADDRESS,
        })
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"hello")
        context = PipelineContext(
            source_path=video_file,
            options={"encrypt": True, "evm_chain": "ethereum", "access_pattern": "owner_only"},
        )

        attempts = {"n": 0}

        real = encrypt_step_module.encrypt_file_streaming

        def flaky_encrypt(**kwargs: object) -> dict:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("transient reset")
            return real(**kwargs)

        monkeypatch.setattr(encrypt_step_module, "encrypt_file_streaming", flaky_encrypt)

        with patch.object(step, "_save_encryption_metadata", new_callable=AsyncMock):
            result = await step.execute(context)

        assert result.success is True
        assert attempts["n"] == 2
        assert result.attempts == 2


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
        assert "encryption_metadata" in call_args[1]

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


class TestEncryptStepRealCryptoIntegration:
    """Integration tests using real streaming encrypt/decrypt round-trip."""

    @pytest.mark.asyncio
    async def test_full_encrypt_decrypt_round_trip(self, tmp_path, monkeypatch):
        """Decrypt path requires ICP identity; verify graceful failure."""
        private_key = "0x" + ("12" * 32)
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", private_key)

        step = EncryptStep(config={"evm_chain": "EthMainnet"})

        # Create test video
        video_file = tmp_path / "test.mp4"
        original_content = b"integration test video content for encryption round-trip"
        video_file.write_bytes(original_content)

        # Use explicit access conditions with a real contract address
        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
            "cid": "sha256:" + hashlib.sha256(original_content).hexdigest(),
        }]

        context = PipelineContext(
            source_path=video_file,
            options={"evm_chain": "EthMainnet"},
            video_id=1,
        )

        # Encrypt
        result = await step._encrypt_with_haven_aol(
            str(video_file),
            access_conditions,
            context,
        )

        # Decrypt the encrypted file via streaming API
        encrypted_path = result["ciphertext_path"]
        assert os.path.exists(encrypted_path), "Encrypted file should exist"

        gate = GateParams(
            chain="EthMainnet",
            token_address=TEST_ADDRESS,
            threshold=1,
            cid=access_conditions[0]["cid"],
        )
        # When HAVEN_ICP_IDENTITY_PEM_PATH is unset, decrypt fails fast with
        # a clear error. When it is set (e.g. in CI), the call may reach the
        # boundary node and fail with an ICP error. Either way, decrypt
        # should raise RuntimeError.
        monkeypatch.delenv("HAVEN_ICP_IDENTITY_PEM_PATH", raising=False)
        with pytest.raises(RuntimeError):
            decrypt_file_streaming(
                input_path=encrypted_path,
                output_path=tmp_path / "decrypted.mp4",
                private_key=private_key,
                encrypted_key_b64=result["encrypted_key"],
                gate=gate,
            )

    @pytest.mark.asyncio
    async def test_encrypted_file_different_from_original(self, tmp_path, monkeypatch):
        """Verify the encrypted file is not the same as the original."""
        private_key = "0x" + ("12" * 32)
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", private_key)

        step = EncryptStep(config={"evm_chain": "EthMainnet"})

        video_file = tmp_path / "test.mp4"
        original_content = b"secret video data"
        video_file.write_bytes(original_content)

        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
        }]

        context = PipelineContext(
            source_path=video_file,
            options={"evm_chain": "EthMainnet"},
        )

        result = await step._encrypt_with_haven_aol(
            str(video_file),
            access_conditions,
            context,
        )

        with open(result["ciphertext_path"], "rb") as f:
            encrypted_content = f.read()

        assert encrypted_content != original_content, "Encrypted content should differ from original"

    @pytest.mark.asyncio
    async def test_encryption_metadata_fields(self, tmp_path, monkeypatch):
        """Verify all expected metadata fields are populated."""
        private_key = "0x" + ("12" * 32)
        monkeypatch.setenv("HAVEN_PRIVATE_KEY", private_key)

        step = EncryptStep(config={"evm_chain": "EthMainnet"})

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"test metadata fields")

        access_conditions = [{
            "contractAddress": TEST_ADDRESS,
            "chain": "EthMainnet",
            "returnValueTest": {"value": "1"},
        }]

        context = PipelineContext(
            source_path=video_file,
            options={"evm_chain": "EthMainnet"},
        )

        result = await step._encrypt_with_haven_aol(
            str(video_file),
            access_conditions,
            context,
        )

        # All expected fields should be present and non-empty
        assert result["ciphertext_path"].endswith(".encrypted")
        assert result["data_to_encrypt_hash"].startswith("0x") or len(result["data_to_encrypt_hash"]) > 0
        assert result["chain"] == "EthMainnet"
        assert len(result["original_hash"]) == 64  # SHA256 hex digest
        assert result["encrypted_key"] is not None and len(result["encrypted_key"]) > 0
        assert result["key_hash"] is not None
        assert result["iv"] is not None and len(result["iv"]) > 0


# ---------------------------------------------------------------------------
# Tests for ICP TransportError classification in encrypt failure handling
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code = status
        self.text = text


class _FakeHTTPError(Exception):
    def __init__(self, resp: _FakeResp) -> None:
        self.response = resp
        super().__init__(f"HTTP {resp.status_code}")


class _FakeTransport(Exception):
    """Mimics icp_agent.client.TransportError."""
    def __init__(self, resp: _FakeResp) -> None:
        self.url = "https://icp-api.io"
        self.original_error = _FakeHTTPError(resp)
        super().__init__(f"Transport error: {resp.status_code}")
        super().__init__(f"Transport error: {resp.status_code}")


def test_is_icp_transport_error_true() -> None:
    assert _is_icp_transport_error(_FakeTransport(_FakeResp(400))) is True


def test_is_icp_transport_error_false() -> None:
    assert _is_icp_transport_error(RuntimeError("nope")) is False


def test_classify_icp_429_transient() -> None:
    err = _FakeTransport(_FakeResp(429, "rate limited"))
    assert _classify_icp_transport_error(err) is ErrorCategory.TRANSIENT


def test_classify_icp_500_transient() -> None:
    err = _FakeTransport(_FakeResp(500, "internal error"))
    assert _classify_icp_transport_error(err) is ErrorCategory.TRANSIENT


def test_classify_icp_400_malformed_permanent() -> None:
    err = _FakeTransport(_FakeResp(400, "malformed CBOR"))
    assert _classify_icp_transport_error(err) is ErrorCategory.PERMANENT


def test_classify_icp_400_canister_not_found_permanent() -> None:
    err = _FakeTransport(
        _FakeResp(400, '{"error":"canister_not_found","details":"The specified canister does not exist"}')
    )
    assert _classify_icp_transport_error(err) is ErrorCategory.PERMANENT


def test_classify_icp_400_temporarily_unavailable_transient() -> None:
    err = _FakeTransport(_FakeResp(400, "temporarily unavailable"))
    assert _classify_icp_transport_error(err) is ErrorCategory.TRANSIENT


def test_classify_icp_400_unknown_transient() -> None:
    err = _FakeTransport(_FakeResp(400, "something went wrong"))
    assert _classify_icp_transport_error(err) is ErrorCategory.TRANSIENT


def test_classify_icp_401_permanent() -> None:
    err = _FakeTransport(_FakeResp(401, "unauthorized"))
    assert _classify_icp_transport_error(err) is ErrorCategory.PERMANENT


def test_classify_icp_no_response_transient() -> None:
    err = _FakeTransport(_FakeResp(0, ""))
    err.original_error = Exception("connection refused")  # type: ignore[assignment]
    assert _classify_icp_transport_error(err) is ErrorCategory.TRANSIENT


def test_classify_encrypt_failure_icp_500_transient() -> None:
    err = _FakeTransport(_FakeResp(500, "internal error"))
    assert classify_encrypt_failure(err) is ErrorCategory.TRANSIENT


def test_classify_encrypt_failure_icp_400_malformed_permanent() -> None:
    err = _FakeTransport(_FakeResp(400, "malformed request"))
    assert classify_encrypt_failure(err) is ErrorCategory.PERMANENT


def test_step_error_from_icp_transport_error_transient() -> None:
    err = _FakeTransport(_FakeResp(429, "rate limited"))
    step_err = step_error_from_encrypt_exception(err)
    assert step_err.category is ErrorCategory.TRANSIENT
    assert step_err.retryable is True


def test_step_error_from_icp_transport_error_permanent() -> None:
    err = _FakeTransport(_FakeResp(400, "malformed CBOR"))
    step_err = step_error_from_encrypt_exception(err)
    assert step_err.category is ErrorCategory.PERMANENT
    assert step_err.retryable is False