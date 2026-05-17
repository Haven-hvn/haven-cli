"""Tests for Filecoin piece CID validation."""

import pytest

from haven_cli.services.piece_cid import is_filecoin_piece_cid, require_piece_cid

PIECE = "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
ROOT = "bafybeidounfsl4czdwgsodecmdzbe2vfac5mamr3k5vdpml2a6yrgwattu"


class TestIsFilecoinPieceCid:
    def test_valid_piece(self) -> None:
        assert is_filecoin_piece_cid(PIECE) is True

    def test_rejects_ipfs_root(self) -> None:
        assert is_filecoin_piece_cid(ROOT) is False


class TestRequirePieceCid:
    def test_returns_normalized(self) -> None:
        assert require_piece_cid(f"  {PIECE}  ") == PIECE

    def test_raises_when_empty(self) -> None:
        with pytest.raises(ValueError, match="piece_cid is required"):
            require_piece_cid("")

    def test_raises_when_not_piece_format(self) -> None:
        with pytest.raises(ValueError, match="invalid piece_cid"):
            require_piece_cid(ROOT)
