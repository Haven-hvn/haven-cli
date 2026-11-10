"""Regression tests for B5 (absolute output_path for torrent downloads)."""

import logging
from pathlib import Path

import pytest

from haven_cli.plugins.builtin.bittorrent.plugin import BitTorrentPlugin


class TestAbsoluteDownloadDir:
    def test_download_dir_is_resolved_absolute(self):
        plugin = BitTorrentPlugin(config={"download_dir": "downloads/bittorrent"})
        assert plugin._download_dir.is_absolute(), (
            "download_dir must be resolved to an absolute path (B5)"
        )

    def test_absolute_config_stays_absolute(self, tmp_path):
        absolute = str(tmp_path / "torrents")
        plugin = BitTorrentPlugin(config={
            "plugins": {"plugin_settings": {"bittorrent": {"download_dir": absolute}}}
        })
        assert plugin._download_dir == Path(absolute).resolve()


class TestValidateDownloadPaths:
    @pytest.mark.asyncio
    async def test_relative_and_missing_paths_logged_as_errors(self, tmp_path, caplog):
        """Startup validation must loudly flag relative/missing paths (B5)."""
        import concurrent.futures

        plugin = BitTorrentPlugin(config={"download_dir": str(tmp_path / "dl")})
        plugin._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        good = tmp_path / "good.mp4"
        good.write_bytes(b"x")
        rows = [
            (1, "a" * 40, "completed", "downloads/bittorrent/rel.mp4"),  # relative
            (2, "b" * 40, "completed", str(tmp_path / "missing.mp4")),   # missing
            (3, "c" * 40, "completed", str(good)),                       # ok
        ]

        class FakeRow:
            def __init__(self, t):
                self.id, self.infohash, self.status, self.output_path = t

        class FakeQuery:
            def all(self):
                return [FakeRow(r) for r in rows]

        class FakeSession:
            def query(self, model):
                return FakeQuery()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        plugin._db_session_factory = lambda: FakeSession()

        async def call(fn):
            return fn()
        plugin._run_db_operation = call

        with caplog.at_level(logging.ERROR):
            await plugin._validate_download_paths()

        text = caplog.text
        assert "RELATIVE output_path" in text
        assert "MISSING output_path" in text
        assert "downloads/bittorrent/rel.mp4" in text
        assert "2 stored torrent download path(s)" in text
