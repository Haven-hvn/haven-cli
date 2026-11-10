"""Tests for the repin service (plan item 11) with fake JS/Arkiv clients."""

from pathlib import Path
from typing import Any

import pytest

from haven_cli.services.repin import RepinResult, RepinTarget, repin


def _target(**overrides) -> RepinTarget:
    fields: dict[str, Any] = dict(
        piece_cid="bafk-original-piece",
        root_cid="bafy-root",
        gate={"version": 3, "epoch": 685},
        title="Seed Video",
        file_size=1024,
        dataset_id="52242",
        source="local DB",
    )
    fields.update(overrides)
    return RepinTarget(**fields)


def _fakes(tmp_path: Path, target: RepinTarget, retrievable: bool = True):
    calls = {"download": 0, "upload": 0, "status": 0, "publish": 0}

    def db_lookup(key: str) -> RepinTarget | None:
        return target if key == target.piece_cid else None

    def entity_lookup(key: str):
        calls["entity"] = calls.get("entity", 0) + 1
        return None

    def downloader(piece: str, out: str):
        calls["download"] += 1
        assert piece == target.piece_cid
        Path(out).write_bytes(b"ciphertext-bytes")
        return {"success": True}

    def uploader(path: str):
        calls["upload"] += 1
        assert Path(path).read_bytes() == b"ciphertext-bytes"
        return {"pieceCid": "bafk-new-piece", "cid": "bafy-new-root", "dataSetId": "52242"}

    def status_probe(piece: str, root: str | None):
        calls["status"] += 1
        assert piece == "bafk-new-piece"
        return {"retrievable": retrievable, "status": "active" if retrievable else "pending"}

    def publisher(t: RepinTarget, piece: str, root: str):
        calls["publish"] += 1
        # Gate is preserved verbatim — including the original epoch.
        assert t.gate == target.gate
        assert t.gate["epoch"] == 685
        return {"entity_key": "0xnewentity", "transaction_hash": "0xtx"}

    return calls, dict(
        db_lookup=db_lookup,
        entity_lookup=entity_lookup,
        downloader=downloader,
        uploader=uploader,
        status_probe=status_probe,
        publisher=publisher,
    )


class TestRepin:
    @pytest.mark.asyncio
    async def test_full_repin_preserves_gate_epoch(self, tmp_path):
        target = _target()
        calls, fakes = _fakes(tmp_path, target)

        result = await repin(target.piece_cid, output_dir=tmp_path, **fakes)

        assert result.error == ""
        assert result.piece_cid == "bafk-new-piece"
        assert result.entity_key == "0xnewentity"
        assert result.retrievable is True
        assert calls.get("entity", 0) == 0
        assert calls["download"] == 1 and calls["upload"] == 1
        assert calls["status"] == 1 and calls["publish"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_touches_nothing(self, tmp_path):
        target = _target()
        calls, fakes = _fakes(tmp_path, target)

        result = await repin(target.piece_cid, output_dir=tmp_path, dry_run=True, **fakes)

        assert result.dry_run is True
        assert result.error == ""
        assert calls["download"] == 0 and calls["upload"] == 0
        assert calls["status"] == 0 and calls["publish"] == 0

    @pytest.mark.asyncio
    async def test_unretrievable_new_piece_refuses_publish(self, tmp_path):
        target = _target()
        calls, fakes = _fakes(tmp_path, target, retrievable=False)

        result = await repin(target.piece_cid, output_dir=tmp_path, **fakes)

        assert result.error != ""
        assert "not retrievable" in result.error
        assert calls["publish"] == 0

    @pytest.mark.asyncio
    async def test_unknown_target_fails_closed(self, tmp_path):
        target = _target()
        calls, fakes = _fakes(tmp_path, target)

        with pytest.raises(ValueError, match="Cannot repin"):
            await repin("bafk-unknown", output_dir=tmp_path, **fakes)

    @pytest.mark.asyncio
    async def test_no_gate_refuses_repin(self, tmp_path):
        target = _target(gate=None)
        calls, fakes = _fakes(tmp_path, target)

        result = await repin(target.piece_cid, output_dir=tmp_path, **fakes)

        assert result.error != ""
        assert "no gate metadata" in result.error
        assert calls["download"] == 0


@pytest.mark.asyncio
async def test_repin_result_is_dataclass_with_plan(tmp_path):
    """Dry-run result carries the plan target for the CLI table."""
    target = _target()
    _, fakes = _fakes(tmp_path, target)
    result = await repin(target.piece_cid, output_dir=tmp_path, dry_run=True, **fakes)
    assert isinstance(result, RepinResult)
    assert result.target.piece_cid == "bafk-original-piece"
