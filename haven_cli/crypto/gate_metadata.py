"""Haven-AOL gate metadata v1 — canonical Arkiv encryption format."""

from __future__ import annotations

import json
from typing import Any

GATE_METADATA_VERSION = 1

REQUIRED_GATE_KEYS = frozenset(
    {"version", "cid", "chain", "tokenAddress", "threshold", "encryptedAesKey"}
)


def build_gate_metadata(
    *,
    cid: str,
    chain: str,
    token_address: str,
    threshold: int | str,
    encrypted_aes_key_b64: str,
) -> dict[str, Any]:
    """Build a Haven-AOL gate metadata object (version 1)."""
    if not cid:
        raise ValueError("gate cid is required")
    if not token_address:
        raise ValueError("gate tokenAddress is required")
    if not encrypted_aes_key_b64:
        raise ValueError("gate encryptedAesKey is required")

    return {
        "version": GATE_METADATA_VERSION,
        "cid": cid,
        "chain": chain,
        "tokenAddress": token_address,
        "threshold": str(threshold),
        "encryptedAesKey": encrypted_aes_key_b64,
    }


def merge_encrypt_result_gate(
    gate_partial: dict[str, Any],
    encrypted_aes_key_b64: str,
) -> dict[str, Any]:
    """Merge encrypt_bytes/streaming gate stub with the IBE-wrapped AES key."""
    return build_gate_metadata(
        cid=str(gate_partial["cid"]),
        chain=str(gate_partial["chain"]),
        token_address=str(gate_partial["tokenAddress"]),
        threshold=str(gate_partial.get("threshold", "1")),
        encrypted_aes_key_b64=encrypted_aes_key_b64,
    )


def is_gate_metadata(data: Any) -> bool:
    """Return True if data is a valid gate v1 metadata object."""
    if not isinstance(data, dict):
        return False
    if data.get("version") != GATE_METADATA_VERSION:
        return False
    return REQUIRED_GATE_KEYS.issubset(data.keys())


def parse_gate_metadata(raw: Any) -> dict[str, Any] | None:
    """Parse gate v1 metadata from JSON string or dict."""
    if not raw:
        return None

    parsed: Any
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        parsed = raw

    if not is_gate_metadata(parsed):
        return None
    return dict(parsed)


def gate_metadata_to_json(gate: dict[str, Any]) -> str:
    """Serialize gate metadata for Arkiv payload fields."""
    if not is_gate_metadata(gate):
        raise ValueError("Invalid gate metadata: expected version 1 with all required fields")
    return json.dumps(gate, separators=(",", ":"))
