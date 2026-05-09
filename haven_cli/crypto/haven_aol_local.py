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
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
    """Encrypt payload and return standalone Haven-AOL metadata."""
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    derivation_input = compute_derivation_input(gate)

    aesgcm = AESGCM(aes_key)
    encrypted_payload = iv + aesgcm.encrypt(iv, plaintext, None)
    wrapped_key = _wrap_aes_key(aes_key, private_key, derivation_input)

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
    """Decrypt payload from Haven-AOL metadata."""
    if len(ciphertext_bytes) < 13:
        raise ValueError("Ciphertext payload too short")
    derivation_input = compute_derivation_input(gate)
    wrapped_key = base64.b64decode(encrypted_key_b64)
    aes_key = _unwrap_aes_key(wrapped_key, private_key, derivation_input)
    iv = ciphertext_bytes[:12]
    ct = ciphertext_bytes[12:]
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(iv, ct, None)


def serialize_gate_metadata(gate: dict[str, Any]) -> str:
    """Serialize gate metadata as compact JSON."""
    return json.dumps(gate, separators=(",", ":"))
