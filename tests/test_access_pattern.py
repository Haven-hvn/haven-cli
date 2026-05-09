"""Tests for access pattern parsing (config init wizard)."""

import pytest

from haven_cli.access_pattern import parse_access_pattern_choice


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "public"),
        ("public", "public"),
        ("PUBLIC", "public"),
        ("2", "token_gated"),
        ("token_gated", "token_gated"),
        ("token-gated", "token_gated"),
        ("token", "token_gated"),
        ("3", "nft_gated"),
        ("nft_gated", "nft_gated"),
        ("nft", "nft_gated"),
        ("4", "owner_only"),
        ("owner_only", "owner_only"),
        ("owner", "owner_only"),
    ],
)
def test_parse_access_pattern_choice_accepts_aliases(raw: str, expected: str) -> None:
    assert parse_access_pattern_choice(raw) == expected


def test_parse_access_pattern_choice_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown access pattern"):
        parse_access_pattern_choice("not-a-pattern")
