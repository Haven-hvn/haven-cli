"""Normalize interactive / CLI input for ``pipeline.access_pattern``."""

from __future__ import annotations


def parse_access_pattern_choice(raw: str) -> str:
    """Map user input to a normalized ``access_pattern`` value.

    Accepts numeric menu keys (1–4) or names such as ``token_gated``.

    Raises:
        ValueError: If the input does not match a supported pattern.
    """
    c = raw.strip().lower()
    if c in ("1", "public"):
        return "public"
    if c in ("2", "token_gated", "token-gated", "token"):
        return "token_gated"
    if c in ("3", "nft_gated", "nft-gated", "nft"):
        return "nft_gated"
    if c in ("4", "owner_only", "owner-only", "owner"):
        return "owner_only"
    raise ValueError(f"Unknown access pattern: {raw!r}")
