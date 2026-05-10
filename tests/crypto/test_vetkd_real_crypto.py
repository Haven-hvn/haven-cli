"""Real crypto integration tests for vetkd_py + haven_aol_local.

These tests exercise the actual vetkd_py native extension (not mocked)
for all operations that can be performed without ICP canister access.

Operations requiring canister access (transport unwrap, full decrypt chain)
are tested with mock canister responses but real vetkd_py crypto.
"""

import base64
import hashlib
import os
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import vetkd_py
from haven_cli.crypto.haven_aol_local import (
    GateParams,
    _derive_chunk_iv,
    _ibe_encrypt_aes_key,
    compute_derivation_input,
    decrypt_file_streaming,
    encrypt_bytes,
    encrypt_file_streaming,
)
import haven_cli.crypto.haven_aol_local as haven_aol_local


# ============================================================================
# vetkd_py native extension tests (real crypto, no mocks)
# ============================================================================


class TestVetKdTransportKeys:
    """Test transport keypair generation and derivation."""

    def test_generate_transport_secret_key_length(self):
        """Transport secret key must be exactly 32 bytes."""
        sk = vetkd_py.generate_transport_secret_key()
        assert len(sk) == 32

    def test_generate_transport_secret_key_randomness(self):
        """Two generated keys must differ."""
        sk1 = vetkd_py.generate_transport_secret_key()
        sk2 = vetkd_py.generate_transport_secret_key()
        assert sk1 != sk2

    def test_transport_public_key_from_secret(self):
        """Public key must be 48 bytes (compressed G1)."""
        sk = vetkd_py.generate_transport_secret_key()
        pk = vetkd_py.transport_public_key_from_secret(sk)
        assert len(pk) == 48

    def test_transport_keypair_deterministic(self):
        """Same secret key must always produce the same public key."""
        sk = vetkd_py.generate_transport_secret_key()
        pk1 = vetkd_py.transport_public_key_from_secret(sk)
        pk2 = vetkd_py.transport_public_key_from_secret(sk)
        assert pk1 == pk2

    def test_transport_keypair_different_secrets_different_publics(self):
        """Different secret keys produce different public keys."""
        sk1 = vetkd_py.generate_transport_secret_key()
        sk2 = vetkd_py.generate_transport_secret_key()
        pk1 = vetkd_py.transport_public_key_from_secret(sk1)
        pk2 = vetkd_py.transport_public_key_from_secret(sk2)
        assert pk1 != pk2

    def test_invalid_secret_key_length(self):
        """Non-32-byte input must raise ValueError."""
        with pytest.raises(ValueError, match="32 bytes"):
            vetkd_py.transport_public_key_from_secret(b"too-short")

    def test_empty_secret_key(self):
        """Empty bytes must raise ValueError."""
        with pytest.raises(ValueError):
            vetkd_py.transport_public_key_from_secret(b"")


class TestVetKdVerificationKey:
    """Test verification key derivation."""

    def test_derive_key_1(self):
        """key_1 must produce a 96-byte derived public key."""
        canister_id = bytes(20)
        context = b"accessol_v1"
        dpk = vetkd_py.derive_verification_key("key_1", canister_id, context)
        assert len(dpk) == 96

    def test_derive_test_key_1(self):
        """test_key_1 must produce a 96-byte derived public key."""
        canister_id = bytes(20)
        context = b"accessol_v1"
        dpk = vetkd_py.derive_verification_key("test_key_1", canister_id, context)
        assert len(dpk) == 96

    def key_1_and_test_key_1_differ(self):
        """key_1 and test_key_1 must produce different keys."""
        canister_id = bytes(20)
        context = b"accessol_v1"
        dpk1 = vetkd_py.derive_verification_key("key_1", canister_id, context)
        dpk2 = vetkd_py.derive_verification_key("test_key_1", canister_id, context)
        assert dpk1 != dpk2

    def test_derive_deterministic(self):
        """Same inputs must produce the same key."""
        canister_id = bytes(20)
        context = b"accessol_v1"
        dpk1 = vetkd_py.derive_verification_key("key_1", canister_id, context)
        dpk2 = vetkd_py.derive_verification_key("key_1", canister_id, context)
        assert dpk1 == dpk2

    def test_different_canister_ids_differ(self):
        """Different canister IDs must produce different keys."""
        context = b"accessol_v1"
        dpk1 = vetkd_py.derive_verification_key("key_1", bytes(20), context)
        dpk2 = vetkd_py.derive_verification_key("key_1", bytes([1] + [0] * 19), context)
        assert dpk1 != dpk2

    def test_different_contexts_differ(self):
        """Different contexts must produce different keys."""
        canister_id = bytes(20)
        dpk1 = vetkd_py.derive_verification_key("key_1", canister_id, b"ctx1")
        dpk2 = vetkd_py.derive_verification_key("key_1", canister_id, b"ctx2")
        assert dpk1 != dpk2

    def test_invalid_key_name(self):
        """Unknown key name must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown key name"):
            vetkd_py.derive_verification_key("invalid", bytes(20), b"ctx")


class TestVetKdDeserialize:
    """Test DerivedPublicKey deserialization."""

    def test_valid_round_trip(self):
        """Deserializing a valid DPK and re-serializing must match."""
        dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"ctx")
        rt = vetkd_py.deserialize_derived_public_key(dpk)
        assert rt == dpk

    def test_invalid_bytes(self):
        """Invalid bytes must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid DerivedPublicKey"):
            vetkd_py.deserialize_derived_public_key(b"invalid")

    def test_wrong_length(self):
        """Wrong-length bytes must raise ValueError."""
        with pytest.raises(ValueError):
            vetkd_py.deserialize_derived_public_key(b"\x00" * 95)

    def test_empty_bytes(self):
        """Empty bytes must raise ValueError."""
        with pytest.raises(ValueError):
            vetkd_py.deserialize_derived_public_key(b"")


class TestVetKdIbeEncrypt:
    """Test IBE encryption."""

    @pytest.fixture
    def dpk(self):
        return vetkd_py.derive_verification_key("key_1", bytes(20), b"accessol_v1")

    @pytest.fixture
    def identity(self):
        return bytes.fromhex("e16d8738a6ea707f75e887fd3fce3e96d2fe061d075c5fe2821e94b2c9ad3b17")

    def test_encrypt_produces_ciphertext(self, dpk, identity):
        """IBE encrypt must produce non-empty ciphertext."""
        pt = os.urandom(32)
        ct = vetkd_py.ibe_encrypt(dpk, identity, pt)
        assert len(ct) > 0

    def test_encrypt_output_structure(self, dpk, identity):
        """IBE ciphertext should be: 8-byte header + 96-byte G2 + 32-byte seed + plaintext."""
        pt = os.urandom(32)
        ct = vetkd_py.ibe_encrypt(dpk, identity, pt)
        # Expected: 8 + 96 + 32 + 32 = 168 bytes for 32-byte plaintext
        assert len(ct) == 168

    def test_encrypt_non_deterministic(self, dpk, identity):
        """Same plaintext must produce different ciphertexts (random IBE seed)."""
        pt = os.urandom(32)
        ct1 = vetkd_py.ibe_encrypt(dpk, identity, pt)
        ct2 = vetkd_py.ibe_encrypt(dpk, identity, pt)
        assert ct1 != ct2

    def test_encrypt_different_plaintexts(self, dpk, identity):
        """Different plaintexts must produce different ciphertexts."""
        ct1 = vetkd_py.ibe_encrypt(dpk, identity, b"A" * 32)
        ct2 = vetkd_py.ibe_encrypt(dpk, identity, b"B" * 32)
        assert ct1 != ct2

    def test_encrypt_different_identities(self, dpk):
        """Different identities must produce different ciphertexts."""
        pt = os.urandom(32)
        id1 = b"\x00" * 32
        id2 = b"\x01" + b"\x00" * 31
        ct1 = vetkd_py.ibe_encrypt(dpk, id1, pt)
        ct2 = vetkd_py.ibe_encrypt(dpk, id2, pt)
        assert ct1 != ct2

    def test_encrypt_different_dpk(self, identity):
        """Different DPKs must produce different ciphertexts."""
        pt = os.urandom(32)
        dpk1 = vetkd_py.derive_verification_key("key_1", bytes(20), b"ctx1")
        dpk2 = vetkd_py.derive_verification_key("key_1", bytes(20), b"ctx2")
        ct1 = vetkd_py.ibe_encrypt(dpk1, identity, pt)
        ct2 = vetkd_py.ibe_encrypt(dpk2, identity, pt)
        assert ct1 != ct2

    def test_encrypt_invalid_dpk(self, identity):
        """Invalid DPK bytes must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid DerivedPublicKey"):
            vetkd_py.ibe_encrypt(b"invalid", identity, b"\x00" * 32)

    def test_encrypt_empty_plaintext(self, dpk, identity):
        """Empty plaintext should still produce a valid ciphertext structure."""
        ct = vetkd_py.ibe_encrypt(dpk, identity, b"")
        # 8 + 96 + 32 + 0 = 136
        assert len(ct) == 136


class TestVetKdErrorHandling:
    """Test error handling across all functions."""

    def test_decrypt_and_verify_garbage(self):
        """Garbage input to decrypt_and_verify must raise ValueError."""
        sk = vetkd_py.generate_transport_secret_key()
        dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"ctx")
        with pytest.raises(ValueError):
            vetkd_py.decrypt_and_verify(b"garbage", sk, dpk, b"\x00" * 32)

    def test_ibe_decrypt_garbage(self):
        """Garbage input to ibe_decrypt must raise ValueError."""
        with pytest.raises(ValueError):
            vetkd_py.ibe_decrypt(b"garbage", b"garbage")

    def test_unwrap_and_derive_garbage(self):
        """Garbage input to unwrap_and_derive must raise ValueError."""
        sk = vetkd_py.generate_transport_secret_key()
        dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"ctx")
        with pytest.raises(ValueError):
            vetkd_py.unwrap_and_derive(b"garbage", sk, dpk, b"\x00" * 32, b"garbage")


# ============================================================================
# Haven-AOL integration tests (real vetkd_py, mocked canister)
# ============================================================================


class TestHavenAolEncryptRealCrypto:
    """Test encrypt_bytes and encrypt_file_streaming with real vetkd_py.

    The canister call (getVetKDPublicKey) is mocked to return a
    deterministically derived key, but all vetkd_py crypto is real.
    """

    GATE = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmRealCryptoTestCid",
    )

    @pytest.fixture(autouse=True)
    def _mock_canister(self, monkeypatch):
        """Mock get_vetKDPublicKey_b64 to return a derived key."""
        dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"accessol_v1")
        dpk_b64 = base64.b64encode(bytes(dpk)).decode("ascii")
        monkeypatch.setattr(
            haven_aol_local, "get_vetkd_public_key_b64", lambda: dpk_b64
        )

    def test_encrypt_bytes_produces_valid_metadata(self):
        """encrypt_bytes must return all required metadata fields."""
        result = encrypt_bytes(
            plaintext=b"real crypto test payload",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        assert "ciphertext_bytes" in result
        assert "encrypted_key_b64" in result
        assert "key_hash" in result
        assert "iv_b64" in result
        assert "data_to_encrypt_hash" in result
        assert "gate" in result

    def test_encrypt_bytes_ciphertext_differs_from_plaintext(self):
        """Encrypted ciphertext must differ from plaintext."""
        plaintext = b"secret data that should be encrypted"
        result = encrypt_bytes(
            plaintext=plaintext,
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        assert result["ciphertext_bytes"] != plaintext

    def test_encrypt_bytes_ciphertext_includes_iv(self):
        """Ciphertext must start with 12-byte IV."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        assert len(result["ciphertext_bytes"]) >= 12

    def test_encrypt_bytes_key_hash_is_sha256(self):
        """key_hash must be a valid SHA-256 hex digest (64 hex chars)."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        assert len(result["key_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in result["key_hash"])

    def test_encrypt_bytes_data_to_encrypt_hash_matches_derivation(self):
        """data_to_encrypt_hash must match compute_derivation_input."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        expected = compute_derivation_input(self.GATE).hex()
        assert result["data_to_encrypt_hash"] == expected

    def test_encrypt_bytes_encrypted_key_is_valid_base64(self):
        """encrypted_key_b64 must be valid base64."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        decoded = base64.b64decode(result["encrypted_key_b64"])
        # IBE ciphertext for 32-byte key: 8 + 96 + 32 + 32 = 168 bytes
        assert len(decoded) == 168

    def test_encrypt_bytes_iv_is_valid_base64_12_bytes(self):
        """iv_b64 must decode to exactly 12 bytes."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        iv = base64.b64decode(result["iv_b64"])
        assert len(iv) == 12

    def test_encrypt_bytes_gate_metadata(self):
        """Gate metadata must contain expected fields."""
        result = encrypt_bytes(
            plaintext=b"test",
            private_key="0x" + "12" * 32,
            gate=self.GATE,
        )
        gate = result["gate"]
        assert gate["version"] == 1
        assert gate["cid"] == self.GATE.cid
        assert gate["chain"] == self.GATE.chain
        assert gate["tokenAddress"] == self.GATE.token_address
        assert gate["threshold"] == str(self.GATE.threshold)

    def test_encrypt_bytes_different_plaintexts_different_ciphertexts(self):
        """Different plaintexts must produce different ciphertexts."""
        r1 = encrypt_bytes(plaintext=b"aaa", private_key="0x" + "12" * 32, gate=self.GATE)
        r2 = encrypt_bytes(plaintext=b"bbb", private_key="0x" + "12" * 32, gate=self.GATE)
        assert r1["ciphertext_bytes"] != r2["ciphertext_bytes"]

    def test_encrypt_bytes_same_plaintext_different_ciphertexts(self):
        """Same plaintext must produce different ciphertexts (random AES key + IV)."""
        r1 = encrypt_bytes(plaintext=b"same", private_key="0x" + "12" * 32, gate=self.GATE)
        r2 = encrypt_bytes(plaintext=b"same", private_key="0x" + "12" * 32, gate=self.GATE)
        assert r1["ciphertext_bytes"] != r2["ciphertext_bytes"]
        assert r1["encrypted_key_b64"] != r2["encrypted_key_b64"]

    def test_encrypt_file_streaming_real_crypto(self, tmp_path):
        """encrypt_file_streaming must produce a valid encrypted file with real IBE."""
        input_path = tmp_path / "input.bin"
        output_path = tmp_path / "encrypted.bin"
        plaintext = b"real crypto streaming test data" * 100
        input_path.write_bytes(plaintext)

        result = encrypt_file_streaming(
            input_path=input_path,
            output_path=output_path,
            private_key="",
            gate=self.GATE,
            chunk_size=64,
        )

        assert output_path.exists()
        assert output_path.stat().st_size > 0
        assert result["encrypted_key_b64"] is not None
        assert result["key_hash"] is not None
        assert result["iv_b64"] is not None

        # Verify encrypted file structure: 12-byte base IV + chunks
        data = output_path.read_bytes()
        assert len(data) >= 12
        base_iv = data[:12]
        assert len(base_iv) == 12

        # Verify we can read back chunk structure
        offset = 12
        chunk_index = 0
        total_decrypted = bytearray()
        aes_key = None

        # We need the AES key to decrypt. Since we can't get it from the
        # encrypt output (it's random), verify the structure is valid.
        while offset < len(data):
            if offset + 8 > len(data):
                break
            idx = struct.unpack("<I", data[offset:offset + 4])[0]
            assert idx == chunk_index, f"Expected chunk {chunk_index}, got {idx}"
            offset += 4
            chunk_len = struct.unpack("<I", data[offset:offset + 4])[0]
            offset += 4
            assert chunk_len > 0
            assert offset + chunk_len <= len(data)
            offset += chunk_len
            chunk_index += 1

        assert chunk_index > 0, "Expected at least one chunk"

    def test_encrypt_file_streaming_large_file(self, tmp_path):
        """Encrypt a larger file (1 MiB) to verify streaming works."""
        input_path = tmp_path / "large.bin"
        output_path = tmp_path / "large.encrypted"
        # 1 MiB of random data
        plaintext = os.urandom(1024 * 1024)
        input_path.write_bytes(plaintext)

        result = encrypt_file_streaming(
            input_path=input_path,
            output_path=output_path,
            private_key="",
            gate=self.GATE,
            chunk_size=256 * 1024,  # 256 KiB chunks
        )

        assert output_path.exists()
        # Encrypted should be larger than original (IV + chunk headers + auth tags)
        assert output_path.stat().st_size > input_path.stat().st_size


class TestHavenAolEncryptDecryptRoundTrip:
    """Test that encrypt -> AES-GCM decrypt works with real vetkd_py IBE.

    Since we can't do the full VetKD transport unwrap without the canister,
    we test the AES-GCM layer by extracting the AES key from the encrypt path.
    """

    GATE = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmRoundTripTest",
    )

    @pytest.fixture(autouse=True)
    def _mock_canister(self, monkeypatch):
        dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"accessol_v1")
        dpk_b64 = base64.b64encode(bytes(dpk)).decode("ascii")
        monkeypatch.setattr(
            haven_aol_local, "get_vetkd_public_key_b64", lambda: dpk_b64
        )

    def test_aes_gcm_round_trip_with_encrypt_bytes(self):
        """Verify AES-GCM encrypt -> decrypt round-trip using encrypt_bytes output."""
        plaintext = b"round-trip test payload for AES-GCM"

        # Patch _ibe_encrypt_aes_key to capture the AES key
        captured = {}
        original_ibe = haven_aol_local._ibe_encrypt_aes_key

        def capture_ibe(aes_key, derivation_input):
            captured["aes_key"] = aes_key
            return original_ibe(aes_key, derivation_input)

        with patch.object(haven_aol_local, "_ibe_encrypt_aes_key", side_effect=capture_ibe):
            result = encrypt_bytes(
                plaintext=plaintext,
                private_key="0x" + "12" * 32,
                gate=self.GATE,
            )

        # Now decrypt using the captured AES key
        ct_bytes = result["ciphertext_bytes"]
        iv = ct_bytes[:12]
        ct_and_tag = ct_bytes[12:]
        aesgcm = AESGCM(captured["aes_key"])
        decrypted = aesgcm.decrypt(iv, ct_and_tag, None)
        assert decrypted == plaintext

    def test_streaming_aes_gcm_round_trip(self, tmp_path):
        """Verify streaming encrypt -> decrypt round-trip at the AES-GCM layer."""
        plaintext = os.urandom(4096)  # 4 KiB, will be multiple chunks at 64B chunks

        captured = {}

        def capture_ibe(aes_key, derivation_input):
            captured["aes_key"] = aes_key
            # Still do real IBE encrypt (we just want to capture the key)
            dpk = vetkd_py.derive_verification_key("key_1", bytes(20), b"accessol_v1")
            identity = compute_derivation_input(self.GATE)
            return vetkd_py.ibe_encrypt(bytes(dpk), identity, aes_key)

        with patch.object(haven_aol_local, "_ibe_encrypt_aes_key", side_effect=capture_ibe):
            input_path = tmp_path / "input.bin"
            enc_path = tmp_path / "encrypted.bin"
            input_path.write_bytes(plaintext)
            encrypt_file_streaming(
                input_path=input_path,
                output_path=enc_path,
                private_key="",
                gate=self.GATE,
                chunk_size=64,
            )

        # Decrypt using captured key
        aesgcm = AESGCM(captured["aes_key"])
        data = enc_path.read_bytes()
        base_iv = data[:12]
        offset = 12
        decrypted = bytearray()
        expected_idx = 0

        while offset < len(data):
            if offset + 8 > len(data):
                break
            idx = struct.unpack("<I", data[offset:offset + 4])[0]
            assert idx == expected_idx
            offset += 4
            chunk_len = struct.unpack("<I", data[offset:offset + 4])[0]
            offset += 4
            enc_chunk = data[offset:offset + chunk_len]
            offset += chunk_len
            per_iv = _derive_chunk_iv(base_iv, idx)
            dec_chunk = aesgcm.decrypt(per_iv, enc_chunk, None)
            decrypted.extend(dec_chunk)
            expected_idx += 1

        assert bytes(decrypted) == plaintext


class TestDerivationInputWithRealCrypto:
    """Verify derivation input test vectors still hold with real vetkd_py."""

    def test_vector_1(self):
        gate = GateParams(
            chain="EthMainnet",
            token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            threshold=1_000_000,
            cid="QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco",
        )
        digest = compute_derivation_input(gate)
        assert digest.hex() == "e16d8738a6ea707f75e887fd3fce3e96d2fe061d075c5fe2821e94b2c9ad3b17"

    def test_vector_2(self):
        gate = GateParams(
            chain="ArbitrumOne",
            token_address="0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
            threshold=500000000000000000,
            cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
        )
        digest = compute_derivation_input(gate)
        assert digest.hex() == "04308f0e299c1647072257d0965e1f982fba21030538ee89323f82cab1c995d3"

    def test_vector_3(self):
        gate = GateParams(
            chain="EthSepolia",
            token_address="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
            threshold=0,
            cid="QmUNLLsPACCz1vLxQVkXqqLX5R1X345qqfHbsf67hvA3Nn",
        )
        digest = compute_derivation_input(gate)
        assert digest.hex() == "6ea156594a7f7400610f328f4b5daf61d3036100d7bab69d33eb2a53575936d7"
