"""Tests for FOC upload verification."""

import pytest

from haven_cli.services.foc_upload import FocUploadError, verify_foc_upload_js_result

PIECE = "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
OWNER = "0xb24ca10fb6907a2d94b0dc5dbea6b5e379d19ffd"


class TestVerifyFocUploadJsResult:
    def test_accepts_complete_upload_with_copies(self) -> None:
        foc = verify_foc_upload_js_result(
            {
                "cid": "bafybeigtest",
                "pieceCid": PIECE,
                "complete": True,
                "copies": [{"dataSetId": "99", "providerId": "0xprov"}],
                "catalogOwner": OWNER,
            }
        )
        assert foc.piece_cid == PIECE
        assert foc.copy_count == 1
        assert foc.data_set_id == "99"
        assert foc.catalog_owner == OWNER

    def test_rejects_incomplete_upload(self) -> None:
        with pytest.raises(FocUploadError, match="incomplete"):
            verify_foc_upload_js_result(
                {
                    "pieceCid": PIECE,
                    "complete": False,
                    "copies": [{"dataSetId": "1"}],
                }
            )

    def test_rejects_empty_copies(self) -> None:
        with pytest.raises(FocUploadError, match="no copies"):
            verify_foc_upload_js_result(
                {
                    "pieceCid": PIECE,
                    "complete": True,
                    "copies": [],
                }
            )

    def test_rejects_missing_piece_cid(self) -> None:
        with pytest.raises(FocUploadError, match="invalid pieceCid"):
            verify_foc_upload_js_result(
                {
                    "complete": True,
                    "copies": [{"dataSetId": "1"}],
                }
            )
