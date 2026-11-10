"""Tests for plan item 9 — dead storage provider must fail the upload.

When synapse.getStatus never reaches active/confirmed, the upload must be
marked FAILED (retryable), never completed, and the verdict must be logged.
"""

import pytest
from unittest.mock import AsyncMock, patch

from haven_cli.pipeline.steps.upload_step import UploadStep

PIECE = "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"

FOC_UPLOAD_RESULT = {
    "cid": "bafybeigtest123",
    "pieceCid": PIECE,
    "complete": True,
    "copies": [{"dataSetId": "42", "providerId": "0x1111111111111111111111111111111111111111"}],
    "catalogOwner": "0xb24ca10fb6907a2d94b0dc5dbea6b5e379d19ffd",
    "txHash": "0xabcdef123456",
}


def _js_dispatch(getstatus_result):
    """Build a side_effect that dispatches by RPC method name."""
    async def call(method, params=None, **kwargs):
        if method == "synapse.connect":
            return None
        if method == "synapse.upload":
            return FOC_UPLOAD_RESULT
        if method == "synapse.verifyPieceRetrieval":
            return {"retrievable": False}
        if method == "synapse.getStatus":
            return getstatus_result
        return {}
    return call


class TestDeadProviderFailsUpload:
    @pytest.mark.asyncio
    async def test_non_retrievable_piece_raises(self, tmp_path, caplog):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"data")
        step = UploadStep(config={"network_mode": "testnet"})

        async def on_progress(*a, **k):
            pass

        js = AsyncMock(side_effect=_js_dispatch(
            {"status": "pending", "retrievable": False}
        ))
        with patch.object(step, "_js_call_with_retry", js), \
             patch("haven_cli.pipeline.steps.upload_step.asyncio.sleep",
                   new_callable=AsyncMock):
            with pytest.raises(RuntimeError) as exc:
                await step._upload_to_filecoin(
                    str(video),
                    {"data_set_id": 1, "wait_for_deal": True},
                    None,
                    on_progress,
                )

        assert "retrievability NOT confirmed" in str(exc.value)
        assert "dead storage provider" in str(exc.value)

    @pytest.mark.asyncio
    async def test_getstatus_exception_fails_closed(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"data")
        step = UploadStep(config={"network_mode": "testnet"})

        async def on_progress(*a, **k):
            pass

        async def js(method, params=None, **kwargs):
            if method == "synapse.connect":
                return None
            if method == "synapse.upload":
                return FOC_UPLOAD_RESULT
            if method == "synapse.verifyPieceRetrieval":
                return {}
            if method == "synapse.getStatus":
                raise RuntimeError("provider unreachable")
            return {}

        with patch.object(step, "_js_call_with_retry", AsyncMock(side_effect=js)), \
             patch("haven_cli.pipeline.steps.upload_step.asyncio.sleep",
                   new_callable=AsyncMock):
            with pytest.raises(RuntimeError) as exc:
                await step._upload_to_filecoin(
                    str(video),
                    {"data_set_id": 1, "wait_for_deal": True},
                    None,
                    on_progress,
                )
        assert "Could not confirm FOC retrieval status" in str(exc.value)

    @pytest.mark.asyncio
    async def test_retrievable_piece_succeeds_with_verdict(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"data")
        step = UploadStep(config={"network_mode": "testnet"})

        async def on_progress(*a, **k):
            pass

        js = AsyncMock(side_effect=_js_dispatch(
            {"status": "active", "retrievable": True}
        ))
        with patch.object(step, "_js_call_with_retry", js), \
             patch("haven_cli.pipeline.steps.upload_step.asyncio.sleep",
                   new_callable=AsyncMock):
            result = await step._upload_to_filecoin(
                str(video),
                {"data_set_id": 1, "wait_for_deal": True},
                None,
                on_progress,
            )
        assert result["foc_retrievable"] is True
        assert result["root_cid"] == "bafybeigtest123"
