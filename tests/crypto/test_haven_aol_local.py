"""Tests for standalone Haven-AOL crypto helpers."""

import base64
import os
import struct

import pytest

from haven_cli.crypto.haven_aol_local import (
    GateParams,
    compute_derivation_input,
    derivation_threshold_from_access_condition,
    decrypt_bytes,
    decrypt_file_streaming,
    encrypt_bytes,
    encrypt_file_streaming,
    _load_transport_secret_key,
    _validate_transport_keypair_consistency,
    _vetkd_unwrap_aes_key,
)
from haven_cli.services.haven_aol_icp import DecryptionKeyResponse
import haven_cli.crypto.haven_aol_local as haven_aol_local


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"returnValueTest": {"value": "0"}}, 1),
        ({"returnValueTest": {"value": "1"}}, 1),
        ({"returnValueTest": {"value": "100"}}, 100),
        ({"returnValueTest": {"value": "0xOwnerWallet"}}, 1),
        ({}, 1),
    ],
)
def test_derivation_threshold_from_access_condition(
    condition: dict[str, object],
    expected: int,
) -> None:
    assert derivation_threshold_from_access_condition(condition) == expected


def test_nft_gated_derivation_threshold_differs_from_raw_zero() -> None:
    """NFT gating uses on-chain value 0 but VetKD derivation must use threshold 1."""
    condition = {
        "contractAddress": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "returnValueTest": {"comparator": ">", "value": "0"},
        "cid": "QmTestCid",
    }
    threshold = derivation_threshold_from_access_condition(condition)
    gate_clamped = GateParams(
        chain="EthMainnet",
        token_address=condition["contractAddress"],
        threshold=threshold,
        cid=condition["cid"],
    )
    gate_zero = GateParams(
        chain="EthMainnet",
        token_address=condition["contractAddress"],
        threshold=0,
        cid=condition["cid"],
    )
    assert threshold == 1
    assert compute_derivation_input(gate_clamped) != compute_derivation_input(gate_zero)


def test_compute_derivation_input_is_stable() -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco",
    )
    digest = compute_derivation_input(gate)
    assert digest.hex() == "e16d8738a6ea707f75e887fd3fce3e96d2fe061d075c5fe2821e94b2c9ad3b17"


def test_compute_derivation_input_test_vector_2() -> None:
    """Test vector 2 from derivation-spec.md."""
    gate = GateParams(
        chain="ArbitrumOne",
        token_address="0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
        threshold=500000000000000000,
        cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
    )
    digest = compute_derivation_input(gate)
    assert digest.hex() == "04308f0e299c1647072257d0965e1f982fba21030538ee89323f82cab1c995d3"


def test_compute_derivation_input_test_vector_3() -> None:
    """Test vector 3 from derivation-spec.md — threshold=0."""
    gate = GateParams(
        chain="EthSepolia",
        token_address="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        threshold=0,
        cid="QmUNLLsPACCz1vLxQVkXqqLX5R1X345qqfHbsf67hvA3Nn",
    )
    digest = compute_derivation_input(gate)
    assert digest.hex() == "6ea156594a7f7400610f328f4b5daf61d3036100d7bab69d33eb2a53575936d7"


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


def test_streaming_encrypt_progress_callback(tmp_path, monkeypatch) -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmProgressCb",
    )
    private_key = "0x" + ("34" * 32)
    plaintext = b"x" * 500
    input_path = tmp_path / "in.bin"
    encrypted_path = tmp_path / "out.bin"
    input_path.write_bytes(plaintext)
    monkeypatch.setattr(haven_aol_local, "_ibe_encrypt_aes_key", lambda aes_key, derivation_input: b"wrapped")

    seen: list[tuple[int, int]] = []

    def cb(chunk_index: int, nbytes: int) -> None:
        seen.append((chunk_index, nbytes))

    encrypt_file_streaming(
        input_path=input_path,
        output_path=encrypted_path,
        private_key=private_key,
        gate=gate,
        chunk_size=100,
        progress_callback=cb,
    )
    assert seen == [(0, 100), (1, 200), (2, 300), (3, 400), (4, 500)]


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


# ============================================================================
# Decrypt path tests (VetKD unwrap chain)
# ============================================================================


def test_decrypt_bytes_performs_full_vetkd_chain(monkeypatch) -> None:
    """Test that decrypt_bytes calls the full VetKD unwrap chain."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmDecryptTest",
    )

    # Mock the ICP service call (returns bundled response)
    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    # Mock the VetKD unwrap to return a known 32-byte AES key
    fake_aes_key = os.urandom(32)

    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    # Create a valid AES-GCM ciphertext with the fake key
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(fake_aes_key)
    iv = os.urandom(12)
    plaintext = b"hello decrypted world"
    ct = aesgcm.encrypt(iv, plaintext, None)
    ciphertext_bytes = iv + ct

    result = decrypt_bytes(
        ciphertext_bytes=ciphertext_bytes,
        private_key="",
        encrypted_key_b64="AQIDBA==",
        gate=gate,
    )
    assert result == plaintext


def test_decrypt_bytes_fails_on_bad_ciphertext(monkeypatch) -> None:
    """Test that decrypt_bytes raises RuntimeError on AES-GCM failure."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmDecryptFail",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    # Garbage ciphertext (valid IV but bad data)
    ciphertext_bytes = os.urandom(12) + b"corrupt data that is not aes-gcm"

    with pytest.raises(RuntimeError, match="AES-GCM decryption failed"):
        decrypt_bytes(
            ciphertext_bytes=ciphertext_bytes,
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )


def test_decrypt_bytes_fails_on_short_ciphertext(monkeypatch) -> None:
    """Test that decrypt_bytes raises RuntimeError on too-short input."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmShort",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    with pytest.raises(RuntimeError, match="Ciphertext too short"):
        decrypt_bytes(
            ciphertext_bytes=b"short",
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )


def test_decrypt_file_streaming_performs_full_chain(tmp_path, monkeypatch) -> None:
    """Test decrypt_file_streaming decrypts a multi-chunk file."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmStreamDecryptTest",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    # Create a properly encrypted streaming file
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from haven_cli.crypto.haven_aol_local import _derive_chunk_iv

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    plaintext = b"A" * 100 + b"B" * 50
    chunk_size = 64
    base_iv = os.urandom(12)
    aesgcm = AESGCM(fake_aes_key)

    encrypted_path = tmp_path / "encrypted.bin"
    with encrypted_path.open("wb") as f:
        f.write(base_iv)
        offset = 0
        chunk_index = 0
        while offset < len(plaintext):
            chunk = plaintext[offset:offset + chunk_size]
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            encrypted_chunk = aesgcm.encrypt(per_iv, chunk, None)
            f.write(struct.pack("<I", chunk_index))
            f.write(struct.pack("<I", len(encrypted_chunk)))
            f.write(encrypted_chunk)
            offset += chunk_size
            chunk_index += 1

    output_path = tmp_path / "decrypted.bin"
    decrypt_file_streaming(
        input_path=encrypted_path,
        output_path=output_path,
        private_key="",
        encrypted_key_b64="AQIDBA==",
        gate=gate,
    )

    assert output_path.read_bytes() == plaintext


def test_decrypt_file_streaming_fails_on_corrupt_chunk(tmp_path, monkeypatch) -> None:
    """Test that decrypt_file_streaming raises on corrupted chunk data."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmCorruptChunk",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    # Write a file with valid structure but corrupt chunk data
    encrypted_path = tmp_path / "corrupt.bin"
    base_iv = os.urandom(12)
    corrupt_chunk = b"this is not valid aes-gcm ciphertext"
    with encrypted_path.open("wb") as f:
        f.write(base_iv)
        f.write(struct.pack("<I", 0))  # chunk index
        f.write(struct.pack("<I", len(corrupt_chunk)))
        f.write(corrupt_chunk)

    output_path = tmp_path / "decrypted.bin"
    with pytest.raises(RuntimeError, match="AES-GCM decryption failed on chunk 0"):
        decrypt_file_streaming(
            input_path=encrypted_path,
            output_path=output_path,
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )


# ============================================================================
# Transport key validation tests
# ============================================================================


def test_load_transport_secret_key_missing(monkeypatch) -> None:
    """Test that missing transport secret key raises RuntimeError."""
    monkeypatch.delenv("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", raising=False)
    with pytest.raises(RuntimeError, match="HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 is required"):
        _load_transport_secret_key()


def test_load_transport_secret_key_invalid_base64(monkeypatch) -> None:
    """Test that invalid base64 raises RuntimeError."""
    monkeypatch.setenv("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", "not-valid-base64!!!")
    with pytest.raises(RuntimeError, match="not valid base64"):
        _load_transport_secret_key()


def test_load_transport_secret_key_success(monkeypatch) -> None:
    """Test successful loading of transport secret key (returns bytearray)."""
    secret = os.urandom(32)
    monkeypatch.setenv("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", base64.b64encode(secret).decode())
    result = _load_transport_secret_key()
    assert isinstance(result, bytearray)
    assert bytes(result) == secret


def test_ibe_encrypt_missing_vetkd_py(monkeypatch) -> None:
    """Test that missing vetkd_py raises clear RuntimeError on encrypt path."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "vetkd_py":
            raise ImportError("No module named 'vetkd_py'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from haven_cli.crypto.haven_aol_local import _ibe_encrypt_aes_key
    with pytest.raises(RuntimeError, match="vetkd_py package is required"):
        _ibe_encrypt_aes_key(aes_key=os.urandom(32), derivation_input=os.urandom(32))


def test_vetkd_unwrap_missing_vetkd_py(monkeypatch) -> None:
    """Test that missing vetkd_py raises clear RuntimeError."""
    monkeypatch.setenv("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", base64.b64encode(b"x" * 32).decode())
    monkeypatch.delenv("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", raising=False)

    # Force ImportError for vetkd_py
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "vetkd_py":
            raise ImportError("No module named 'vetkd_py'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    with pytest.raises(RuntimeError, match="vetkd_py package is required"):
        _vetkd_unwrap_aes_key(
            encrypted_canister_key=b"\x01\x02",
            verification_key_b64="AQIDBA==",
            derivation_input=b"\x00" * 32,
            encrypted_aes_key_b64="AQIDBA==",
        )


# ============================================================================
# Chunk ordering integrity tests
# ============================================================================


def test_decrypt_file_streaming_rejects_reordered_chunks(tmp_path, monkeypatch) -> None:
    """Test that out-of-order chunks are rejected (prevents reordering attacks)."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmReorderTest",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from haven_cli.crypto.haven_aol_local import _derive_chunk_iv

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    base_iv = os.urandom(12)
    aesgcm = AESGCM(fake_aes_key)

    # Encrypt two chunks
    chunk_0 = b"AAAA" * 16
    chunk_1 = b"BBBB" * 16
    enc_0 = aesgcm.encrypt(_derive_chunk_iv(base_iv, 0), chunk_0, None)
    enc_1 = aesgcm.encrypt(_derive_chunk_iv(base_iv, 1), chunk_1, None)

    # Write them in REVERSED order (chunk 1 first, chunk 0 second)
    encrypted_path = tmp_path / "reordered.bin"
    with encrypted_path.open("wb") as f:
        f.write(base_iv)
        # First record claims to be chunk 1 (should be 0)
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<I", len(enc_1)))
        f.write(enc_1)
        # Second record claims to be chunk 0
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", len(enc_0)))
        f.write(enc_0)

    output_path = tmp_path / "decrypted.bin"
    with pytest.raises(RuntimeError, match="Chunk ordering violation"):
        decrypt_file_streaming(
            input_path=encrypted_path,
            output_path=output_path,
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )


def test_decrypt_file_streaming_rejects_duplicated_chunks(tmp_path, monkeypatch) -> None:
    """Test that duplicated chunk indices are rejected."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmDuplicateTest",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from haven_cli.crypto.haven_aol_local import _derive_chunk_iv

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    base_iv = os.urandom(12)
    aesgcm = AESGCM(fake_aes_key)

    chunk_0 = b"CCCC" * 16
    enc_0 = aesgcm.encrypt(_derive_chunk_iv(base_iv, 0), chunk_0, None)

    # Write chunk 0 twice (second one should trigger ordering violation)
    encrypted_path = tmp_path / "duplicate.bin"
    with encrypted_path.open("wb") as f:
        f.write(base_iv)
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", len(enc_0)))
        f.write(enc_0)
        # Duplicate: chunk index 0 again (expected: 1)
        f.write(struct.pack("<I", 0))
        f.write(struct.pack("<I", len(enc_0)))
        f.write(enc_0)

    output_path = tmp_path / "decrypted.bin"
    with pytest.raises(RuntimeError, match="Chunk ordering violation"):
        decrypt_file_streaming(
            input_path=encrypted_path,
            output_path=output_path,
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )


def test_decrypt_file_streaming_rejects_oversized_chunk(tmp_path, monkeypatch) -> None:
    """Test that a chunk claiming > 64 MiB is rejected."""
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1,
        cid="QmOversizedChunk",
    )

    monkeypatch.setattr(
        haven_aol_local, "request_decryption_key",
        lambda **kwargs: DecryptionKeyResponse(
            encrypted_key=b"\x01\x02\x03\x04",
            verification_key=b"\x01\x02\x03\x04",
        )
    )

    fake_aes_key = os.urandom(32)
    monkeypatch.setattr(
        haven_aol_local, "_vetkd_unwrap_aes_key",
        lambda encrypted_canister_key, verification_key_b64, derivation_input, encrypted_aes_key_b64: fake_aes_key,
    )

    # Write a chunk header claiming 128 MiB (exceeds 64 MiB limit)
    encrypted_path = tmp_path / "oversized.bin"
    base_iv = os.urandom(12)
    with encrypted_path.open("wb") as f:
        f.write(base_iv)
        f.write(struct.pack("<I", 0))  # chunk index 0
        f.write(struct.pack("<I", 128 * 1024 * 1024))  # 128 MiB claim
        f.write(b"\x00" * 100)  # some data (won't matter)

    output_path = tmp_path / "decrypted.bin"
    with pytest.raises(RuntimeError, match="exceeds maximum allowed chunk size"):
        decrypt_file_streaming(
            input_path=encrypted_path,
            output_path=output_path,
            private_key="",
            encrypted_key_b64="AQIDBA==",
            gate=gate,
        )
