"""Standalone Haven-AOL encryption/decryption helpers for haven-cli.

This module is intentionally self-contained so haven-cli does not depend on
publishing haven-aol to PyPI/npm.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from haven_cli.services.haven_aol_icp import get_vetkd_public_key_b64, request_decryption_key

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


def _normalize_private_key(private_key: str) -> bytes:
    key = private_key.strip()
    if key.startswith("0x"):
        key = key[2:]
    if not key:
        raise ValueError("Private key is empty")
    if len(key) % 2 != 0:
        raise ValueError("Private key hex must have even length")
    return bytes.fromhex(key)


def _keystream(private_key_bytes: bytes, derivation_input: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(
            private_key_bytes + derivation_input + counter.to_bytes(4, "big")
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _wrap_aes_key(aes_key: bytes, private_key: str, derivation_input: bytes) -> bytes:
    stream = _keystream(_normalize_private_key(private_key), derivation_input, len(aes_key))
    return bytes(a ^ b for a, b in zip(aes_key, stream))


def _unwrap_aes_key(wrapped_key: bytes, private_key: str, derivation_input: bytes) -> bytes:
    # XOR wrapping is symmetric.
    return _wrap_aes_key(wrapped_key, private_key, derivation_input)


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


def decrypt_bytes(
    ciphertext_bytes: bytes,
    private_key: str,
    encrypted_key_b64: str,
    gate: GateParams,
) -> bytes:
    """Decrypt payload from Haven-AOL metadata.

    Runtime decryption for IBE-wrapped keys requires canister key retrieval and
    transport-key unwrapping. This path is intentionally disabled in haven-cli.
    """
    _ = get_vetkd_public_key_b64()
    _ = request_decryption_key(
        chain=gate.chain,
        token_address=gate.token_address,
        threshold=gate.threshold,
        cid=gate.cid,
    )
    raise RuntimeError(
        "decrypt_bytes is disabled for ICP-only Haven-AOL mode. "
        "Derived-key transport unwrapping is not yet wired."
    )


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
) -> dict[str, Any]:
    """Encrypt a file using chunked streaming to avoid OOM on large files."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    src_path = Path(input_path)
    dst_path = Path(output_path)

    aes_key = os.urandom(32)
    base_iv = os.urandom(12)
    derivation_input = compute_derivation_input(gate)
    wrapped_key = _ibe_encrypt_aes_key(aes_key, derivation_input)
    aesgcm = AESGCM(aes_key)

    with src_path.open("rb") as src, dst_path.open("wb") as dst:
        dst.write(base_iv)
        chunk_index = 0
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            encrypted_chunk = aesgcm.encrypt(per_iv, chunk, None)
            dst.write(struct.pack("<I", chunk_index))
            dst.write(struct.pack("<I", len(encrypted_chunk)))
            dst.write(encrypted_chunk)
            chunk_index += 1

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

    Runtime decryption for IBE-wrapped keys requires canister key retrieval and
    transport-key unwrapping. This path is intentionally disabled in haven-cli.
    """
    _ = get_vetkd_public_key_b64()
    _ = request_decryption_key(
        chain=gate.chain,
        token_address=gate.token_address,
        threshold=gate.threshold,
        cid=gate.cid,
    )
    raise RuntimeError(
        "decrypt_file_streaming is disabled for ICP-only Haven-AOL mode. "
        "Derived-key transport unwrapping is not yet wired."
    )


def _ibe_encrypt_aes_key(aes_key: bytes, derivation_input: bytes) -> bytes:
    """Encrypt AES key using Haven-AOL IBE verification key."""
    try:
        from haven_aol.core import derive_verification_key, ibe_encrypt_aes_key
    except ImportError as exc:
        raise RuntimeError(
            "haven_aol package is required for ICP-only Haven-AOL encryption."
        ) from exc

    key_name = os.environ.get("HAVEN_AOL_VETKD_KEY_NAME", "key_1").strip() or "key_1"
    context = os.environ.get("HAVEN_AOL_VETKD_CONTEXT", "accessol_v1").encode("utf-8")
    verification_key_b64 = get_vetkd_public_key_b64()
    verification_key_bytes: bytes | None = base64.b64decode(verification_key_b64)

    derived_public_key = derive_verification_key(
        canister_id="bkyz2-fmaaa-aaaaa-qaaaq-cai",
        context=context,
        key_name=key_name,
        verification_key_bytes=verification_key_bytes,
    )
    return ibe_encrypt_aes_key(
        aes_key=aes_key,
        derived_public_key=derived_public_key,
        derivation_input=derivation_input,
    )
