"""Haven-AOL gate metadata — v1 (original) + v3 (corpus-scoped) surfaces.

Sprint 4 · Task 01 — additive v3 extension. Every v1 public symbol below
(``GATE_METADATA_VERSION``, ``REQUIRED_GATE_KEYS``, ``build_gate_metadata``,
``merge_encrypt_result_gate``, ``is_gate_metadata``, ``gate_metadata_to_json``)
keeps its original signature and bit-for-bit behaviour. The v1 regression
test ``tests/crypto/test_gate_metadata.py`` is the byte-identity gate.

The single v1-behavior change is :func:`parse_gate_metadata`: it now
dispatches on ``metadata.version`` so v3 records parse cleanly into the
v3 shape. For v1 records the return value is **identical** to the
pre-Sprint-4 implementation — same keys, same string ``threshold``, same
``version`` integer. This is verified by a snapshot test added in
``tests/crypto/test_gate_metadata_v3.py``.

v3 builder/parser/serializer are re-exported from the shared Python SDK
(:mod:`haven_aol.v3`) so haven-cli never owns a second copy of the
derivation / metadata logic. The SDK is the source of truth — see
``tasking/sprint-3-shared-sdks/01-python-sdk-v3.md``.
"""

from __future__ import annotations

import json
from typing import Any

# ── v3 re-exports from the shared SDK ────────────────────────────────
#
# haven-cli imports the v3 metadata helpers from ``haven_aol.v3`` rather
# than re-implementing them. The SDK is pure Python and has no native
# dependency, so this import is always safe (no ImportError fallback
# needed). If the SDK is missing from the environment, that is a
# configuration error, not a runtime degradation — the v3 functions
# below would simply fail with ``ImportError`` at import time, which is
# the desired loud failure.
from haven_aol.v3 import (
    GATE_METADATA_VERSION_V3,
    build_gate_metadata_v3,
    gate_metadata_v3_to_json,
    parse_gate_metadata as _sdk_parse_gate_metadata,
    parse_gate_metadata_v3,
)

# ── v1 surface (unchanged) ───────────────────────────────────────────

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
    """Parse gate metadata (dispatching on ``version``).

    Pre-Sprint-4 behaviour: returned a v1 record dict on success, ``None``
    on any failure. Behaviour preserved for v1 — same record shape, same
    fields, same ``None`` on hybrid-v1 / unknown shapes.

    Sprint 4 extension: a v3 record (``version == 3``) is now parsed via
    the shared SDK's :func:`haven_aol.v3.parse_gate_metadata_v3` and
    returned as a v3-shaped dict. Decryptor entry points must dispatch on
    the returned ``version`` field — see
    :mod:`haven_cli.crypto.haven_aol_v3`.

    Returns ``None`` on any parse / validation failure.
    """
    if not raw:
        return None

    # Best-effort decode of strings. The SDK dispatcher does this too,
    # but doing it here lets us preserve the pre-Sprint-4 "unknown
    # version → None" contract without paying for two JSON parses.
    parsed: Any
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        parsed = raw

    if not isinstance(parsed, dict):
        return None

    version = parsed.get("version")

    # Pre-empt the ``bool`` is-a-subclass-of-``int`` ambiguity: ``True ==
    # 1`` in Python, so a literal ``true`` in JSON would otherwise route
    # to the v1 parser. The dispatcher rejects bool versions outright;
    # downstream parsers do the same check defensively.
    if isinstance(version, bool):
        return None

    # v1 path. The pre-Sprint-4 implementation called ``is_gate_metadata``
    # here, which checks ``version == 1`` AND ``REQUIRED_GATE_KEYS``
    # subset. Preserve that exact check rather than delegating to the
    # SDK's looser v1 path so the byte-identity snapshot test holds.
    if version == GATE_METADATA_VERSION:
        if not is_gate_metadata(parsed):
            return None
        return dict(parsed)

    # v3 path.
    if version == GATE_METADATA_VERSION_V3:
        return _sdk_parse_gate_metadata(parsed)

    return None


def gate_metadata_to_json(gate: dict[str, Any]) -> str:
    """Serialize gate metadata for Arkiv payload fields."""
    if not is_gate_metadata(gate):
        raise ValueError("Invalid gate metadata: expected version 1 with all required fields")
    return json.dumps(gate, separators=(",", ":"))


# ── v3 surface (re-exported for haven-cli callers) ───────────────────

__all__ = [
    # v1 surface (unchanged)
    "GATE_METADATA_VERSION",
    "REQUIRED_GATE_KEYS",
    "build_gate_metadata",
    "merge_encrypt_result_gate",
    "is_gate_metadata",
    "parse_gate_metadata",
    "gate_metadata_to_json",
    # v3 surface (re-exported from haven_aol.v3)
    "GATE_METADATA_VERSION_V3",
    "build_gate_metadata_v3",
    "gate_metadata_v3_to_json",
    "parse_gate_metadata_v3",
]
