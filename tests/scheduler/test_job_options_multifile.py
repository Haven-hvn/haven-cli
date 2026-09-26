"""Scheduler, CLI and config support added for multi-file / option-driven plugins."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haven_cli.cli.jobs import parse_job_options
from haven_cli.plugins.base import ArchiveResult, ArchiverPlugin, MediaSource, PluginInfo
from haven_cli.scheduler.job_executor import JobExecutor
from haven_cli.scheduler.job_scheduler import OnSuccessAction, RecurringJob
from tests.prowlarr.conftest import no_network  # noqa: F401 - offline guard

pytestmark = pytest.mark.usefixtures("no_network")


class OptionAwarePlugin(ArchiverPlugin):
    supports_concurrent_archive = True

    def __init__(self) -> None:
        super().__init__({})
        self.seen_options: list[dict[str, Any]] = []
        self.active = 0
        self.max_active = 0

    @property
    def info(self) -> PluginInfo:
        return PluginInfo(name="OptionAwarePlugin")

    async def discover_sources(self) -> list[MediaSource]:
        raise AssertionError("discover_sources_for should be used")

    async def discover_sources_for(self, options):
        self.seen_options.append(options)
        return [
            MediaSource(source_id=f"s{i}", media_type="x", uri=f"https://h/{i}", title=f"T{i}", metadata={"k": i})
            for i in range(3)
        ]

    async def archive(self, source):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        n = source.source_id[1:]
        return ArchiveResult(
            success=True,
            output_path=f"/tmp/{n}-a.pdf",
            metadata={
                "output_paths": [f"/tmp/{n}-a.pdf", f"/tmp/{n}-b.pdf"],
                "output_titles": {f"/tmp/{n}-b.pdf": f"file b of {n}"},
                "pipeline_options": {"generic_files_enabled": True},
            },
        )


class LegacyPlugin(ArchiverPlugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(name="LegacyPlugin")

    async def discover_sources(self):
        return [MediaSource(source_id="only", media_type="x", uri="u")]

    async def archive(self, source):
        return ArchiveResult(success=True, output_path="/tmp/v.mp4")


def make_executor(tmp_path: Path, plugin: ArchiverPlugin):
    pipeline = MagicMock()
    pipeline.process = AsyncMock(return_value=MagicMock(success=True))
    executor = JobExecutor(pipeline_manager=pipeline, config={"data_dir": str(tmp_path), "max_concurrent_archives": 2})
    executor._get_plugin = AsyncMock(return_value=plugin)  # type: ignore[method-assign]
    executor._save_execution = AsyncMock()  # type: ignore[method-assign]
    return executor, pipeline


async def drain():
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class TestExecutor:
    async def test_options_multi_file_titles_and_concurrency(self, tmp_path):
        plugin = OptionAwarePlugin()
        await plugin.initialize()
        executor, pipeline = make_executor(tmp_path, plugin)
        job = RecurringJob(name="j", plugin_name="OptionAwarePlugin", on_success=OnSuccessAction.ARCHIVE_NEW, metadata={"prowlarr_searches": ["x"], "vlm_enabled": False})
        result = await executor.execute(job)
        await drain()
        assert result.success and result.sources_archived == 3
        assert plugin.seen_options == [{"prowlarr_searches": ["x"], "vlm_enabled": False}]
        assert 1 < plugin.max_active <= 2
        contexts = [call.args[0] for call in pipeline.process.call_args_list]
        assert len(contexts) == 6
        by_path = {str(c.source_path): c for c in contexts}
        b0 = by_path["/tmp/0-b.pdf"]
        assert b0.options["title"] == "file b of 0"
        assert b0.options["generic_files_enabled"] is True and b0.options["vlm_enabled"] is False
        assert b0.options["source_uri"] == "https://h/0" and b0.options["k"] == 0
        assert "title" not in by_path["/tmp/0-a.pdf"].options

        again = await executor.execute(job)
        assert again.sources_archived == 0  # archive_new tracker

    async def test_legacy_plugin_unchanged(self, tmp_path):
        executor, pipeline = make_executor(tmp_path, LegacyPlugin())
        await executor._get_plugin.return_value.initialize()
        result = await executor.execute(RecurringJob(name="j", plugin_name="LegacyPlugin", metadata={"a": 1}))
        await drain()
        assert result.sources_archived == 1
        assert [str(c.args[0].source_path) for c in pipeline.process.call_args_list] == ["/tmp/v.mp4"]


class TestJobOptions:
    def test_parse(self):
        opts = parse_job_options(
            ["vlm_enabled=false", "limit=5", "names=[\"a\",\"b\"]", "query=deep learning", "empty="],
            '{"arkiv_expires_in": 60, "limit": 1}',
        )
        assert opts == {"arkiv_expires_in": 60, "vlm_enabled": False, "limit": 5, "names": ["a", "b"], "query": "deep learning", "empty": ""}

    @pytest.mark.parametrize("pairs,raw", [(["novalue"], None), (["=x"], None), ([], "[1]"), ([], "{bad")])
    def test_errors(self, pairs, raw):
        with pytest.raises(ValueError):
            parse_job_options(pairs, raw)

    def test_cli_rejects_unknown_saved_search(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from haven_cli.main import app

        monkeypatch.setenv("PROWLARR_API_KEY", "k" * 32)
        with patch("haven_cli.plugins.builtin.prowlarr.plugin.ProwlarrPlugin.from_haven_config") as factory, patch(
            "haven_cli.scheduler.job_scheduler.get_scheduler"
        ) as get_scheduler:
            from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin

            factory.return_value = ProwlarrPlugin({"searches": [{"name": "real"}], "state_file": str(tmp_path / "s.json")})
            result = CliRunner().invoke(
                app,
                ["jobs", "create", "--plugin", "ProwlarrPlugin", "--schedule", "0 * * * *", "-o", "prowlarr_searches=missing"],
            )
            assert result.exit_code == 1, result.output
            assert "missing" in result.output
            get_scheduler.return_value.add_job.assert_not_called()

            ok = CliRunner().invoke(
                app,
                ["jobs", "create", "--plugin", "ProwlarrPlugin", "--schedule", "0 * * * *", "-o", "prowlarr_searches=real", "-o", "arkiv_expires_in=60"],
            )
            assert ok.exit_code == 0, ok.output
            job = get_scheduler.return_value.add_job.call_args.args[0]
            assert job.metadata == {"prowlarr_searches": "real", "arkiv_expires_in": 60}


def test_plugin_settings_with_nested_values_roundtrip(tmp_path):
    from haven_cli.config import HavenConfig, load_config, save_config

    config = HavenConfig()
    config.config_dir = tmp_path
    config.data_dir = tmp_path
    config.plugins.plugin_settings["ProwlarrPlugin"] = {
        "base_url": "http://localhost:9696",
        "accept": ["document", "image/*"],
        "searches": [
            {
                "name": "s1",
                "indexer_ids": [1, 2],
                "include": ["neural\\s+net"],
                "pipeline_options": {"vlm_enabled": False, "arkiv_grp": "acme.docs.full"},
                "link_replacement": 'https://x/"\\1".pdf',
            }
        ],
    }
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.plugins.plugin_settings["ProwlarrPlugin"] == config.plugins.plugin_settings["ProwlarrPlugin"]
