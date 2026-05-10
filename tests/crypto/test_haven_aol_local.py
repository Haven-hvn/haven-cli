"""Tests for standalone Haven-AOL crypto helpers."""

import pytest

from haven_cli.crypto.haven_aol_local import (
    GateParams,
    compute_derivation_input,
    decrypt_file_streaming,
    decrypt_bytes,
    encrypt_bytes,
    encrypt_file_streaming,
)
import haven_cli.crypto.haven_aol_local as haven_aol_local


def test_compute_derivation_input_is_stable() -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco",
    )
    digest = compute_derivation_input(gate)
    assert digest.hex() == "e16d8738a6ea707f75e887fd3fce3e96d2fe061d075c5fe2821e94b2c9ad3b17"


def test_encrypt_bytes_uses_haven_aol_ibe(monkeypatch) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmRoundTripCid",
    )
    private_key = "0x" + ("12" * 32)
    plaintext = b"haven-cli aol encryption test"

    monkeypatch.setattr(haven_aol_local, "_ibe_encrypt_aes_key", lambda aes_key, derivation_input: b"wrapped")
    encrypted = encrypt_bytes(plaintext=plaintext, private_key=private_key, gate=gate)
    assert encrypted["encrypted_key_b64"] == "d3JhcHBlZA=="


def test_streaming_encrypt_writes_output(tmp_path, monkeypatch) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmStreamingRoundTripCid",
    )
    private_key = "0x" + ("34" * 32)
    plaintext = (b"haven-streaming-" * 1024) + b"end"
    input_path = tmp_path / "input.bin"
    encrypted_path = tmp_path / "encrypted.bin"
    input_path.write_bytes(plaintext)
    monkeypatch.setattr(haven_aol_local, "_ibe_encrypt_aes_key", lambda aes_key, derivation_input: b"wrapped")

    metadata = encrypt_file_streaming(
        input_path=input_path,
        output_path=encrypted_path,
        private_key=private_key,
        gate=gate,
        chunk_size=64,
    )
    assert encrypted_path.exists() is True
    assert metadata["encrypted_key_b64"] == "d3JhcHBlZA=="


def test_streaming_encrypt_requires_positive_chunk_size(tmp_path) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmChunkSizeCid",
    )
    private_key = "0x" + ("56" * 32)
    input_path = tmp_path / "input.bin"
    encrypted_path = tmp_path / "encrypted.bin"
    input_path.write_bytes(b"abc")

    with pytest.raises(ValueError, match="chunk_size must be > 0"):
        encrypt_file_streaming(
            input_path=input_path,
            output_path=encrypted_path,
            private_key=private_key,
            gate=gate,
            chunk_size=0,
        )


def test_decrypt_bytes_disabled_for_icp_only_mode(monkeypatch) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmDecryptDisabled",
    )
    monkeypatch.setattr(haven_aol_local, "get_vetkd_public_key_b64", lambda: "AQI=")
    monkeypatch.setattr(haven_aol_local, "request_decryption_key", lambda **kwargs: b"encrypted")
    with pytest.raises(RuntimeError, match="Derived-key transport unwrapping is not yet wired"):
        decrypt_bytes(
            ciphertext_bytes=b"abc",
            private_key="",
            encrypted_key_b64="",
            gate=gate,
        )


def test_decrypt_file_streaming_disabled_for_icp_only_mode(tmp_path, monkeypatch) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmDecryptFileDisabled",
    )
    monkeypatch.setattr(haven_aol_local, "get_vetkd_public_key_b64", lambda: "AQI=")
    monkeypatch.setattr(haven_aol_local, "request_decryption_key", lambda **kwargs: b"encrypted")
    with pytest.raises(RuntimeError, match="Derived-key transport unwrapping is not yet wired"):
        decrypt_file_streaming(
            input_path=tmp_path / "enc.bin",
            output_path=tmp_path / "dec.bin",
            private_key="",
            encrypted_key_b64="",
            gate=gate,
        )
