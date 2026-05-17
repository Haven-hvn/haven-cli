"""
Filecoin Pin piece CID validation (Synapse / PDP download).

Haven-dapp requires Arkiv payload ``piece_cid`` (``bafkzcib…``) for all playback.
"""

from __future__ import annotations

PIECE_CID_PREFIX = "bafkzcib"


def is_filecoin_piece_cid(cid: str) -> bool:
    """Return True if *cid* looks like a Filecoin piece CID from filecoin-pin."""
    normalized = cid.strip()
    return normalized.startswith(PIECE_CID_PREFIX) and len(normalized) >= 59


def require_piece_cid(piece_cid: str, *, context: str = "upload") -> str:
    """
    Require a non-empty Filecoin piece CID.

    Raises:
        ValueError: If missing or not a ``bafkzcib…`` piece CID.
    """
    normalized = piece_cid.strip()
    if not normalized:
        raise ValueError(
            f"{context}: piece_cid is required — Filecoin Pin upload must return "
            "pieceCid from executeUpload (check js-services/synapse-wrapper.ts)."
        )
    if not is_filecoin_piece_cid(normalized):
        raise ValueError(
            f"{context}: invalid piece_cid (expected {PIECE_CID_PREFIX}…, got {normalized[:24]}…)"
        )
    return normalized
