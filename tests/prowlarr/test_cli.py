"""`haven prowlarr` CLI commands against the in-memory fake (no sockets)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from haven_cli.main import app
from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin
from tests.prowlarr.fake_server import FakeProwlarr
from tests.prowlarr.fixtures import API_KEY, indexer, release


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PROWLARR_API_KEY", API_KEY)
    monkeypatch.setenv("COLUMNS", "200")
    fake = FakeProwlarr(
        indexers=[indexer(1, "Public Docs", categories=[(7000, "Books")], book=True), indexer(2, "Private", privacy="private")],
        releases=[release(1, title="First Thing"), release(2, indexer_id=2, indexer_name="Private", title="Second Thing")],
    )

    def factory():
        plugin = ProwlarrPlugin(
            {
                "base_url": fake.base,
                "state_file": str(tmp_path / "state.json"),
                "download_dir": str(tmp_path / "dl"),
                "searches": [{"name": "things", "query": "thing", "categories": ["books"]}],
            }
        )
        plugin.http_transport = fake.transport
        return plugin

    with patch.object(ProwlarrPlugin, "from_haven_config", side_effect=factory):
        yield fake, CliRunner()


def test_status(cli):
    fake, runner = cli
    result = runner.invoke(app, ["prowlarr", "status"])
    assert result.exit_code == 0, result.output
    assert "Prowlarr 2.6.5.0" in result.output and "2/2 indexers" in result.output


def test_indexers_json(cli):
    _, runner = cli
    result = runner.invoke(app, ["prowlarr", "indexers", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["search_types"] == ["search", "book"]


def test_search_passes_filters(cli):
    fake, runner = cli
    result = runner.invoke(app, ["prowlarr", "search", "thing", "-i", "Public Docs", "-i", "2", "-c", "books", "--json"])
    assert result.exit_code == 0, result.output
    req = fake.paths("/api/v1/search")[-1]
    assert sorted(req.query["indexerIds"]) == ["1", "2"] and req.query["categories"] == ["7000"]
    assert API_KEY not in result.output
    assert {r["title"] for r in json.loads(result.output)} == {"First Thing", "Second Thing"}


def test_searches_and_preview(cli):
    _, runner = cli
    listed = runner.invoke(app, ["prowlarr", "searches"])
    assert listed.exit_code == 0 and "things" in listed.output
    preview = runner.invoke(app, ["prowlarr", "preview"])
    assert preview.exit_code == 0, preview.output
    assert "First Thing" in preview.output and "(withheld)" in preview.output  # private indexer
    assert API_KEY not in preview.output


def test_bad_key_exit_code(cli, monkeypatch):
    _, runner = cli
    monkeypatch.setenv("PROWLARR_API_KEY", "wrong-key-value-123")
    result = runner.invoke(app, ["prowlarr", "indexers"])
    assert result.exit_code == 1
    assert "401" in result.output and API_KEY not in result.output


def test_pending_and_retry(cli, tmp_path):
    import asyncio

    from haven_cli.acquisition.state import AcquisitionStore

    _, runner = cli
    asyncio.run(AcquisitionStore(tmp_path / "state.json").record_failure("k1", "boom", permanent=True, max_attempts=3, base_backoff=1))
    shown = runner.invoke(app, ["prowlarr", "pending"])
    assert shown.exit_code == 0 and "k1" in shown.output and "failed" in shown.output
    cleared = runner.invoke(app, ["prowlarr", "retry", "all-failed"])
    assert cleared.exit_code == 0 and "Cleared 1" in cleared.output


def test_schedule_inline(cli):
    _, runner = cli
    with patch("haven_cli.scheduler.job_scheduler.get_scheduler") as get_scheduler:
        get_scheduler.return_value.add_job.side_effect = lambda job: job
        result = runner.invoke(
            app,
            ["prowlarr", "schedule", "--schedule", "0 * * * *", "--query", "x", "-i", "Public Docs", "--max-age-hours", "6", "-o", "vlm_enabled=false"],
        )
        assert result.exit_code == 0, result.output
        job = get_scheduler.return_value.add_job.call_args.args[0]
        assert job.plugin_name == "ProwlarrPlugin"
        assert job.metadata["prowlarr_search"]["indexers"] == ["Public Docs"]
        assert job.metadata["vlm_enabled"] is False
        bad = runner.invoke(app, ["prowlarr", "schedule", "--schedule", "0 * * * *", "--search", "nope"])
        assert bad.exit_code == 1 and "nope" in bad.output
