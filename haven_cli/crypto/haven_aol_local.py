"""Standalone Haven-AOL encryption/decryption helpers for haven-cli.

This module is intentionally self-contained so haven-cli does not depend on
publishing haven-aol to PyPI/npm.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from haven_cli.services.haven_aol_icp import get_vetkd_public_key_b64, request_decryption_key, DecryptionKeyResponse

VALID_CHAINS = frozenset(
    {
        "EthMainnet",
        "EthSepolia",
        "ArbitrumOne",
        "BaseMainnet",
        "OptimismMainnet",
    }
)

_TOKEN_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class GateParams:
    """Token gate parameters for Haven-AOL key derivation.

    ``chain`` is the EVM network where access-control conditions are evaluated
    (where the token/NFT contract exists), not the Internet Computer network
    that hosts Haven-AOL.
    """

    chain: str
    token_address: str
    threshold: int
    cid: str


def derivation_threshold_from_access_condition(
    gate_condition: dict[str, Any],
) -> int:
    """Map on-chain ``returnValueTest.value`` to the VetKD derivation threshold.

    On-chain conditions may use value ``"0"`` (e.g. ``balanceOf > 0`` for NFT
    gating). The Haven-AOL canister rejects ``threshold=0`` in
    ``requestDecryptionKey``, so the derivation input always uses
    ``threshold >= 1`` while the stored access-condition value is unchanged.
    """
    threshold_raw = gate_condition.get("returnValueTest", {}).get("value", "1")
    try:
        threshold = int(str(threshold_raw))
    except (ValueError, TypeError):
        threshold = 1
    return max(1, threshold)


def compute_derivation_input(gate: GateParams) -> bytes:
    """Compute SHA-256 derivation input from gate params."""
    if gate.chain not in VALID_CHAINS:
        raise ValueError(f"Invalid chain: {gate.chain!r}")
    if not _TOKEN_ADDR_RE.match(gate.token_address):
        raise ValueError(f"Invalid token address: {gate.token_address!r}")
    if gate.threshold < 0:
        raise ValueError("Threshold must be >= 0")
    if not gate.cid:
        raise ValueError("CID must be non-empty")
    preimage = (
        f"accessol:{gate.chain}:{gate.token_address}:{gate.threshold}:{gate.cid}"
    ).encode("utf-8")
    return hashlib.sha256(preimage).digest()


def encrypt_bytes(plaintext: bytes, private_key: str, gate: GateParams) -> dict[str, Any]:
    """Encrypt payload and return Haven-AOL metadata.

    Encryption uses Haven-AOL IBE (ICP-compatible) key wrapping only.
    """
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    derivation_input = compute_derivation_input(gate)

    aesgcm = AESGCM(aes_key)
    encrypted_payload = iv + aesgcm.encrypt(iv, plaintext, None)
    wrapped_key = _ibe_encrypt_aes_key(aes_key, derivation_input)

    return {
        "ciphertext_bytes": encrypted_payload,
        "data_to_encrypt_hash": derivation_input.hex(),
        "encrypted_key_b64": base64.b64encode(wrapped_key).decode("ascii"),
        "key_hash": hashlib.sha256(aes_key).hexdigest(),
        "iv_b64": base64.b64encode(iv).decode("ascii"),
        "gate": {
            "version": 1,
            "cid": gate.cid,
            "chain": gate.chain,
            "tokenAddress": gate.token_address,
            "threshold": str(gate.threshold),
        },
    }


def _load_transport_secret_key() -> bytearray:
    """Load and validate the transport secret key from environment.

    Returns the key as a mutable bytearray so callers can zero it after use.
    CPython does not guarantee immediate collection of ``bytes`` objects, so
    using ``bytearray`` + explicit zeroing provides best-effort protection
    against memory-residue attacks.

    Returns:
        Mutable bytearray containing the transport secret key (32 bytes).

    Raises:
        RuntimeError: If env var is missing or invalid.
    """
    secret_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", "").strip()
    if not secret_b64:
        raise RuntimeError(
            "HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 is required for decryption. "
            "Generate a transport keypair with vetkd_py and set both "
            "HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 and HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64."
        )
    try:
        return bytearray(base64.b64decode(secret_b64))
    except Exception as exc:
        raise RuntimeError(
            "HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 is not valid base64"
        ) from exc


def _validate_transport_keypair_consistency() -> None:
    """Validate that transport public key matches secret key (if both set).

    Raises:
        RuntimeError: If keys are inconsistent.
    """
    secret_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", "").strip()
    public_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", "").strip()
    if not secret_b64 or not public_b64:
        return  # Validation will fail elsewhere if needed

    try:
        import vetkd_py
    except ImportError:
        return  # Can't validate without vetkd_py; will fail later at use

    secret_bytes = base64.b64decode(secret_b64)
    public_bytes = base64.b64decode(public_b64)
    derived_public = vetkd_py.transport_public_key_from_secret(secret_bytes)
    if derived_public != public_bytes:
        raise RuntimeError(
            "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64 does not match "
            "HAVEN_AOL_TRANSPORT_SECRET_KEY_B64. The keypair is inconsistent. "
            "Regenerate with vetkd_py.generate_transport_secret_key() and "
            "vetkd_py.transport_public_key_from_secret()."
        )


def _vetkd_unwrap_aes_key(
    encrypted_canister_key: bytes,
    verification_key_b64: str,
    derivation_input: bytes,
    encrypted_aes_key_b64: str,
) -> bytes:
    """Perform full VetKD unwrap chain to recover the AES key.

    Chain: EncryptedVetKey -> VetKey -> IBE decrypt -> AES key

    Args:
        encrypted_canister_key: Raw bytes from requestDecryptionKey canister call.
        verification_key_b64: Base64-encoded DerivedPublicKey from getVetKDPublicKey.
        derivation_input: 32-byte SHA-256 derivation hash.
        encrypted_aes_key_b64: Base64-encoded IBE ciphertext of the AES key.

    Returns:
        32-byte AES key.

    Raises:
        RuntimeError: On any cryptographic failure (fail-closed).
    """
    try:
        import vetkd_py
    except ImportError as exc:
        raise RuntimeError(
            "vetkd_py package is required for VetKD transport-key unwrapping. "
            "Install it from the vetkd_py/ directory: pip install ./vetkd_py"
        ) from exc

    transport_secret_key = _load_transport_secret_key()
    _validate_transport_keypair_consistency()

    verification_key_bytes = base64.b64decode(verification_key_b64)
    ibe_ciphertext_bytes = base64.b64decode(encrypted_aes_key_b64)

    try:
        aes_key = vetkd_py.unwrap_and_derive(
            encrypted_key_bytes=encrypted_canister_key,
            transport_secret_key_bytes=bytes(transport_secret_key),
            verification_key_bytes=verification_key_bytes,
            derivation_input=derivation_input,
            ibe_ciphertext_bytes=ibe_ciphertext_bytes,
        )
    except ValueError as exc:
        raise RuntimeError(
            f"VetKD unwrap failed (fail-closed): {exc}"
        ) from exc
    finally:
        # Best-effort zeroing of transport secret key in Python memory
        for i in range(len(transport_secret_key)):
            transport_secret_key[i] = 0

    if len(aes_key) != 32:
        raise RuntimeError(
            f"VetKD unwrap produced {len(aes_key)}-byte key, expected 32. "
            "This indicates a protocol mismatch."
        )

    return bytes(aes_key)


def decrypt_bytes(
    ciphertext_bytes: bytes,
    private_key: str,
    encrypted_key_b64: str,
    gate: GateParams,
) -> bytes:
    """Decrypt payload from Haven-AOL metadata.

    Performs the full ICP VetKD decrypt chain:
      1. Request encrypted derived key + verification key from canister (bundled)
      2. Transport-unwrap the encrypted key using local secret
      3. IBE-decrypt the AES key
      4. AES-GCM decrypt the payload

    Args:
        ciphertext_bytes: Encrypted payload ([12-byte IV][ciphertext+tag]).
        private_key: Unused (kept for API compat); transport key from env.
        encrypted_key_b64: Base64-encoded IBE ciphertext of the AES key.
        gate: Gate parameters for derivation.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        RuntimeError: On any failure (fail-closed, no fallback).
    """
    derivation_input = compute_derivation_input(gate)

    # Step 1: Request encrypted derived key + verification key (bundled response)
    response = request_decryption_key(
        chain=gate.chain,
        token_address=gate.token_address,
        threshold=gate.threshold,
        cid=gate.cid,
    )

    # Use bundled verification key (no separate getVetKDPublicKey call needed)
    verification_key_b64 = base64.b64encode(response.verification_key).decode("ascii")

    # Steps 2-3: Transport unwrap + IBE decrypt to get AES key
    aes_key = _vetkd_unwrap_aes_key(
        encrypted_canister_key=response.encrypted_key,
        verification_key_b64=verification_key_b64,
        derivation_input=derivation_input,
        encrypted_aes_key_b64=encrypted_key_b64,
    )

    # Step 4: AES-GCM decrypt
    if len(ciphertext_bytes) < 12:
        raise RuntimeError("Ciphertext too short (missing IV)")

    iv = ciphertext_bytes[:12]
    ct_and_tag = ciphertext_bytes[12:]

    aesgcm = AESGCM(aes_key)
    try:
        plaintext = aesgcm.decrypt(iv, ct_and_tag, None)
    except Exception as exc:
        raise RuntimeError(
            "AES-GCM decryption failed. Key mismatch or corrupted ciphertext."
        ) from exc

    return plaintext


def serialize_gate_metadata(gate: dict[str, Any]) -> str:
    """Serialize gate metadata as compact JSON."""
    return json.dumps(gate, separators=(",", ":"))


def _derive_chunk_iv(base_iv: bytes, chunk_index: int) -> bytes:
    """Derive a unique per-chunk IV from a base IV and chunk index."""
    if len(base_iv) != 12:
        raise ValueError("Base IV must be 12 bytes")
    per_iv = bytearray(base_iv)
    idx_bytes = struct.pack(">Q", chunk_index)
    for i, idx_byte in enumerate(idx_bytes):
        per_iv[i + 4] ^= idx_byte
    return bytes(per_iv)


def encrypt_file_streaming(
    input_path: str | Path,
    output_path: str | Path,
    private_key: str,
    gate: GateParams,
    chunk_size: int = 1024 * 1024,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Encrypt a file using chunked streaming to avoid OOM on large files.

    Args:
        progress_callback: If set, invoked after each chunk with
            ``(chunk_index, source_bytes_processed)`` (0-based chunk index,
            cumulative bytes read from the source file).
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    src_path = Path(input_path)
    dst_path = Path(output_path)
    file_size = src_path.stat().st_size
    est_chunks = max(1, (file_size + chunk_size - 1) // chunk_size)
    log_interval = max(1, est_chunks // 10)

    logger.debug(
        "Streaming encrypt start input=%s output=%s bytes=%s chunk_size=%s",
        src_path,
        dst_path,
        file_size,
        chunk_size,
    )

    aes_key = os.urandom(32)
    base_iv = os.urandom(12)
    derivation_input = compute_derivation_input(gate)
    wrapped_key = _ibe_encrypt_aes_key(aes_key, derivation_input)
    aesgcm = AESGCM(aes_key)

    with src_path.open("rb") as src, dst_path.open("wb") as dst:
        dst.write(base_iv)
        chunk_index = 0
        source_bytes_processed = 0
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            encrypted_chunk = aesgcm.encrypt(per_iv, chunk, None)
            dst.write(struct.pack("<I", chunk_index))
            dst.write(struct.pack("<I", len(encrypted_chunk)))
            dst.write(encrypted_chunk)
            source_bytes_processed += len(chunk)
            if progress_callback is not None:
                progress_callback(chunk_index, source_bytes_processed)
            if logger.isEnabledFor(logging.DEBUG) and (
                chunk_index == 0
                or chunk_index == est_chunks - 1
                or (chunk_index + 1) % log_interval == 0
            ):
                logger.debug(
                    "Streaming encrypt progress chunk=%s/%s bytes=%s/%s",
                    chunk_index + 1,
                    est_chunks,
                    source_bytes_processed,
                    file_size,
                )
            chunk_index += 1

    logger.debug("Streaming encrypt done chunks=%s path=%s", chunk_index, dst_path)

    return {
        "data_to_encrypt_hash": derivation_input.hex(),
        "encrypted_key_b64": base64.b64encode(wrapped_key).decode("ascii"),
        "key_hash": hashlib.sha256(aes_key).hexdigest(),
        "iv_b64": base64.b64encode(base_iv).decode("ascii"),
        "gate": {
            "version": 1,
            "cid": gate.cid,
            "chain": gate.chain,
            "tokenAddress": gate.token_address,
            "threshold": str(gate.threshold),
        },
    }


def decrypt_file_streaming(
    input_path: str | Path,
    output_path: str | Path,
    private_key: str,
    encrypted_key_b64: str,
    gate: GateParams,
    chunk_size: int = 1024 * 1024,
) -> None:
    """Decrypt a file encrypted by encrypt_file_streaming.

    Performs the full ICP VetKD decrypt chain:
      1. Request encrypted derived key + verification key from canister (bundled)
      2. Transport-unwrap the encrypted key using local secret
      3. IBE-decrypt the AES key
      4. AES-GCM decrypt each chunk

    Args:
        input_path: Path to encrypted file.
        output_path: Path to write decrypted file.
        private_key: Unused (kept for API compat); transport key from env.
        encrypted_key_b64: Base64-encoded IBE ciphertext of the AES key.
        gate: Gate parameters for derivation.
        chunk_size: Ignored (chunk sizes are stored in the file).

    Raises:
        RuntimeError: On any failure (fail-closed, no fallback).
    """
    derivation_input = compute_derivation_input(gate)

    # Step 1: Request encrypted derived key + verification key (bundled response)
    response = request_decryption_key(
        chain=gate.chain,
        token_address=gate.token_address,
        threshold=gate.threshold,
        cid=gate.cid,
    )

    # Use bundled verification key (no separate getVetKDPublicKey call needed)
    verification_key_b64 = base64.b64encode(response.verification_key).decode("ascii")

    # Steps 2-3: Transport unwrap + IBE decrypt to get AES key
    aes_key = _vetkd_unwrap_aes_key(
        encrypted_canister_key=response.encrypted_key,
        verification_key_b64=verification_key_b64,
        derivation_input=derivation_input,
        encrypted_aes_key_b64=encrypted_key_b64,
    )

    # Step 5: AES-GCM decrypt chunks
    src_path = Path(input_path)
    dst_path = Path(output_path)
    aesgcm = AESGCM(aes_key)

    with src_path.open("rb") as src, dst_path.open("wb") as dst:
        # Read base IV (first 12 bytes)
        base_iv = src.read(12)
        if len(base_iv) != 12:
            raise RuntimeError("Encrypted file too short (missing base IV)")

        expected_chunk_index = 0

        while True:
            # Read chunk index (4 bytes, little-endian)
            idx_data = src.read(4)
            if not idx_data:
                break  # EOF
            if len(idx_data) != 4:
                raise RuntimeError("Truncated chunk index in encrypted file")
            chunk_index = struct.unpack("<I", idx_data)[0]

            # Verify sequential ordering — prevents reordering/duplication attacks
            if chunk_index != expected_chunk_index:
                raise RuntimeError(
                    f"Chunk ordering violation: expected chunk {expected_chunk_index}, "
                    f"got {chunk_index}. File may have been tampered with."
                )

            # Read chunk length (4 bytes, little-endian)
            len_data = src.read(4)
            if len(len_data) != 4:
                raise RuntimeError("Truncated chunk length in encrypted file")
            chunk_len = struct.unpack("<I", len_data)[0]

            # Sanity check chunk length to avoid OOM on malformed files
            if chunk_len > 64 * 1024 * 1024:  # 64 MiB max per chunk
                raise RuntimeError(
                    f"Chunk {chunk_index} claims {chunk_len} bytes — "
                    "exceeds maximum allowed chunk size (64 MiB). "
                    "File may be corrupted."
                )

            # Read encrypted chunk
            encrypted_chunk = src.read(chunk_len)
            if len(encrypted_chunk) != chunk_len:
                raise RuntimeError(
                    f"Truncated chunk data: expected {chunk_len} bytes, "
                    f"got {len(encrypted_chunk)}"
                )

            # Derive per-chunk IV and decrypt
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            try:
                plaintext_chunk = aesgcm.decrypt(per_iv, encrypted_chunk, None)
            except Exception as exc:
                raise RuntimeError(
                    f"AES-GCM decryption failed on chunk {chunk_index}. "
                    "Key mismatch or corrupted data."
                ) from exc

            dst.write(plaintext_chunk)
            expected_chunk_index += 1


def _ibe_encrypt_aes_key(aes_key: bytes, derivation_input: bytes) -> bytes:
    """Encrypt AES key using Haven-AOL IBE verification key.

    Uses vetkd_py (unified Rust package) for all VetKD cryptographic operations,
    replacing the previous dependency on haven_aol + haven_aol_vetkeys.

    The verification key is fetched from the canister via ``getVetKDPublicKey``,
    which returns the fully-derived DerivedPublicKey. We validate it via
    deserialization (round-trip check on the BLS12-381 G2 point) and then use
    it directly for IBE encryption.
    """
    try:
        import vetkd_py
    except ImportError as exc:
        raise RuntimeError(
            "vetkd_py package is required for Haven-AOL IBE encryption. "
            "Install it from the vetkd_py/ directory: pip install ./vetkd_py"
        ) from exc

    verification_key_b64 = get_vetkd_public_key_b64()
    verification_key_bytes = base64.b64decode(verification_key_b64)

    # Validate the verification key from the canister and use it as the
    # derived public key. This is the normal runtime path — the canister
    # returns the fully-derived key so we just validate it.
    derived_public_key = vetkd_py.deserialize_derived_public_key(verification_key_bytes)

    return vetkd_py.ibe_encrypt(
        derived_public_key_bytes=derived_public_key,
        identity_bytes=derivation_input,
        plaintext=aes_key,
    )
