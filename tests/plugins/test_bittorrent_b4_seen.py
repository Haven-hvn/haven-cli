"""Regression tests for B4 (seen-infohash on download, not discovery).

The observed failure (2026-11-10): a discovery-only probe wrote discovered
infohashes into ``.bittorrent_seen.json`` at discovery time, so the daemon's
next ``discover_sources()`` returned 0 and the torrent was never downloaded.

These tests assert:
  * discovery is side-effect-free (two calls return the same set, and the
    seen set / seen file are untouched by a discovery-only probe);
  * the seen mark is written only when a download is enqueued (_mark_seen);
  * a failed download releases the mark so the next cycle can retry.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

# The plugin imports libtorrent at module import time. conftest.py mocks it,
# but these tests also construct the plugin directly, so ensure the mock.
sys.modules.setdefault("libtorrent", __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())

from haven_cli.plugins.builtin.bittorrent.plugin import BitTorrentPlugin
from haven_cli.plugins.builtin.bittorrent.sources.base import MagnetLink


def _make_plugin(tmp_path):
    """Build a minimally-initialized plugin without a libtorrent session."""
    plugin = BitTorrentPlugin(config={"download_dir": str(tmp_path), "sources": []})
    plugin._initialized = True
    plugin._download_dir = tmp_path
    # _persist_seen uses _run_in_executor -> needs an executor.
    import concurrent.futures
    plugin._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return plugin


def _magnet(infohash: str) -> MagnetLink:
    return MagnetLink(
        infohash=infohash,
        uri=f"magnet:?xt=urn:btih:{infohash}",
        title=f"torrent-{infohash[:6]}",
        size=1024,
        seeders=1,
        leechers=0,
        category="video",
        source_name="test",
    )


class _FakeSource:
    enabled = True
    name = "test-source"

    def __init__(self, magnets):
        self._magnets = magnets

    async def search(self, query):
        return list(self._magnets)

    async def shutdown(self):
        pass


class TestDiscoveryIsSideEffectFree:
    """A discovery-only probe must never consume a torrent (B4)."""

    @pytest.mark.asyncio
    async def test_two_discoveries_return_same_set(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        magnets = [_magnet("a" * 40), _magnet("b" * 40)]
        plugin._sources = [_FakeSource(magnets)]

        first = await plugin.discover_sources()
        second = await plugin.discover_sources()

        assert len(first) == 2
        assert [m.source_id for m in first] == ["a" * 40, "b" * 40]
        assert [m.source_id for m in second] == [m.source_id for m in first]
        # No infohash was marked seen by discovery.
        assert plugin._seen_infohashes == set()

    @pytest.mark.asyncio
    async def test_discovery_does_not_write_seen_file(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        plugin._sources = [_FakeSource([_magnet("c" * 40)])]

        await plugin.discover_sources()

        seen_file = tmp_path / ".bittorrent_seen.json"
        assert not seen_file.exists(), (
            "discovery-only probe must not create/persist the seen file"
        )

    @pytest.mark.asyncio
    async def test_probe_then_daemon_still_finds_sources(self, tmp_path):
        """Regression: probe-then-discover returns non-zero sources."""
        probe = _make_plugin(tmp_path)
        probe._sources = [_FakeSource([_magnet("d" * 40)])]
        await probe.discover_sources()  # discovery-only probe
        await probe.shutdown()          # would have persisted on the old code

        # Fresh daemon process (new plugin instance) loads seen state from disk.
        daemon = _make_plugin(tmp_path)
        # Mirror initialize()'s load of the seen file without a libtorrent session.
        seen_file = tmp_path / ".bittorrent_seen.json"
        if seen_file.exists():
            data = json.loads(seen_file.read_text())
            daemon._seen_infohashes = set(data.get("seen", []))
        daemon._sources = [_FakeSource([_magnet("d" * 40)])]

        sources = await daemon.discover_sources()
        assert len(sources) == 1, (
            "probe must not burn the torrent; daemon still needs to download it"
        )
        assert sources[0].source_id == "d" * 40


class TestMarkSeenOnEnqueue:
    """The seen mark is written at download enqueue, and released on failure."""

    @pytest.mark.asyncio
    async def test_mark_seen_persists_immediately(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        infohash = "e" * 40

        await plugin._mark_seen(infohash)

        assert infohash in plugin._seen_infohashes
        seen_file = tmp_path / ".bittorrent_seen.json"
        assert seen_file.exists()
        data = json.loads(seen_file.read_text())
        assert infohash in data["seen"]

        # After marking, discovery filters it out.
        plugin._sources = [_FakeSource([_magnet(infohash)])]
        assert await plugin.discover_sources() == []

    @pytest.mark.asyncio
    async def test_failed_download_releases_mark(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        infohash = "f" * 40
        await plugin._mark_seen(infohash)
        assert infohash in plugin._seen_infohashes

        await plugin._unmark_seen(infohash)

        assert infohash not in plugin._seen_infohashes
        data = json.loads((tmp_path / ".bittorrent_seen.json").read_text())
        assert infohash not in data["seen"]
        # Next discovery re-offers it.
        plugin._sources = [_FakeSource([_magnet(infohash)])]
        assert len(await plugin.discover_sources()) == 1
