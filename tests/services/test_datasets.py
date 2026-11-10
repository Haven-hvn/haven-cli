"""Tests for the dataset-per-epoch service + ``haven dataset`` CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from haven_cli.services.datasets import (
    account_dataset,
    choose_epoch_dataset,
    epoch_from_gate,
    load_epoch_map,
    record_epoch_dataset,
)


class TestEpochMap:
    def test_roundtrip(self, tmp_path: Path):
        record_epoch_dataset(tmp_path, 685, "52242")
        assert load_epoch_map(tmp_path) == {685: "52242"}
        record_epoch_dataset(tmp_path, 686, "52244")
        assert load_epoch_map(tmp_path) == {685: "52242", 686: "52244"}

    def test_ignores_none(self, tmp_path: Path):
        record_epoch_dataset(tmp_path, None, "52242")
        record_epoch_dataset(tmp_path, 685, None)
        assert load_epoch_map(tmp_path) == {}

    def test_corrupt_file_is_empty(self, tmp_path: Path):
        (tmp_path / "dataset_epochs.json").write_text("{not json")
        assert load_epoch_map(tmp_path) == {}


class TestChoose:
    def test_first_epoch_uses_default_reuse(self, tmp_path: Path):
        choice = choose_epoch_dataset(tmp_path, 685)
        assert choice.epoch == 685 and choice.dataset_id is None and choice.fresh is False

    def test_known_epoch_reuses_its_dataset(self, tmp_path: Path):
        record_epoch_dataset(tmp_path, 685, "52242")
        choice = choose_epoch_dataset(tmp_path, 685)
        assert choice.dataset_id == "52242" and choice.fresh is False

    def test_new_epoch_gets_fresh(self, tmp_path: Path):
        record_epoch_dataset(tmp_path, 685, "52242")
        choice = choose_epoch_dataset(tmp_path, 686)
        assert choice.fresh is True and choice.dataset_id is None

    def test_no_epoch_no_grouping(self, tmp_path: Path):
        choice = choose_epoch_dataset(tmp_path, None)
        assert choice.epoch is None and choice.fresh is False

    def test_epoch_from_gate(self):
        assert epoch_from_gate({"epoch": "685"}) == 685
        assert epoch_from_gate({"epoch": 685}) == 685
        assert epoch_from_gate({"epoch": "abc"}) is None
        assert epoch_from_gate(None) is None
        assert epoch_from_gate({}) is None


def _row(piece: str, dataset: str, epoch: int = 685):
    row = MagicMock()
    row.piece_cid = piece
    row.filecoin_data_set_id = dataset
    row.encryption_metadata = json.dumps({"version": 3, "epoch": epoch})
    row.filecoin_uploaded_at = None
    return row


def _fake_session(rows):
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = rows
    session.query.return_value = query
    return session


class TestAccounting:
    def test_sole_copy_blocks(self):
        session = _fake_session([_row("piece-a", "52242"), _row("piece-b", "52242")])
        with patch("haven_cli.database.connection.get_db_session") as factory:
            factory.return_value.__enter__.return_value = session
            accounting = account_dataset("52242")
        assert accounting.pieces == ["piece-a", "piece-b"]
        assert accounting.sole_copy_pieces == ["piece-a", "piece-b"]
        assert accounting.safe_to_terminate is False

    def test_shared_piece_is_safe(self):
        session = _fake_session([
            _row("piece-a", "52242"),
            _row("piece-a", "52244"),
        ])
        with patch("haven_cli.database.connection.get_db_session") as factory:
            factory.return_value.__enter__.return_value = session
            accounting = account_dataset("52242")
        assert accounting.sole_copy_pieces == []
        assert accounting.safe_to_terminate is True
        assert accounting.epochs == [685]


class TestDatasetCli:
    def test_list_json_merges(self, tmp_path: Path, monkeypatch):
        from typer.testing import CliRunner

        from haven_cli.main import app
        from haven_cli.services.datasets import DatasetView

        monkeypatch.setenv("HAVEN_CONFIG_DIR", str(tmp_path))
        views = [DatasetView(dataset_id="52242", piece_count=3, epochs=[685])]
        with patch("haven_cli.services.datasets.list_local_datasets", return_value=views), patch(
            "haven_cli.cli.dataset._js_call", return_value=[]
        ):
            result = CliRunner().invoke(app, ["dataset", "list", "--json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert rows[0]["dataset_id"] == "52242"
        assert rows[0]["piece_count"] == 3

    def test_terminate_refuses_without_yes(self, monkeypatch):
        from typer.testing import CliRunner

        from haven_cli.main import app
        from haven_cli.services.datasets import DatasetAccounting

        with patch("haven_cli.services.datasets.account_dataset", return_value=DatasetAccounting(
            dataset_id="52242", pieces=["p1"], epochs=[685]
        )):
            result = CliRunner().invoke(app, ["dataset", "terminate", "52242"])
        assert result.exit_code == 0, result.output
        assert "--yes" in result.output
