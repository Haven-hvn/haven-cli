"""Regression tests for B10 (archive every video in a multi-video torrent).

Covers the selection helper, per-file titles/result metadata, and the
archive path end to end with a fake libtorrent files object.
"""

import concurrent.futures
import sys
from types import SimpleNamespace

import pytest

sys.modules.setdefault(
    "libtorrent",
    __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
)

from haven_cli.plugins.base import MediaSource
from haven_cli.plugins.builtin.bittorrent.plugin import BitTorrentPlugin


class _FakeFiles:
    def __init__(self, entries):
        self._entries = entries  # [(path, size)]

    def num_files(self):
        return len(self._entries)

    def file_path(self, i):
        return self._entries[i][0]

    def file_size(self, i):
        return self._entries[i][1]


class TestSelectVideoFiles:
    def test_three_videos_all_selected(self):
        files = _FakeFiles([("a.mp4", 100), ("b.mkv", 200), ("c.mov", 300)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4", ".mkv", ".mov"], 10, 1000
        )
        assert [i for i, _, _ in eligible] == [0, 1, 2]
        assert skipped == []

    def test_oversize_skipped_but_siblings_kept(self):
        files = _FakeFiles(
            [("big.mp4", 5000), ("ok1.mp4", 100), ("ok2.mkv", 200), ("tiny.mp4", 1)]
        )
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4", ".mkv"], 10, 1000
        )
        assert [i for i, _, _ in eligible] == [1, 2]
        assert [s[0] for s in skipped] == [0, 3]

    def test_non_video_ignored(self):
        files = _FakeFiles([("notes.txt", 100), ("v.mp4", 100)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4"], 10, 1000
        )
        assert [i for i, _, _ in eligible] == [1]
        assert skipped == []

    def test_all_skipped(self):
        files = _FakeFiles([("big.mp4", 5000)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4"], 10, 1000
        )
        assert eligible == []
        assert len(skipped) == 1

    def test_largest_mode_keeps_only_biggest(self):
        files = _FakeFiles([("a.mp4", 100), ("b.mkv", 300), ("c.mov", 200)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4", ".mkv", ".mov"], 10, 1000, mode="largest"
        )
        assert [i for i, _, _ in eligible] == [1]
        assert [s[0] for s in skipped] == [0, 2]
        assert all("select_mode=largest" in s[3] for s in skipped)

    def test_largest_mode_single_eligible(self):
        files = _FakeFiles([("only.mp4", 100), ("notes.txt", 5000)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4"], 10, 1000, mode="largest"
        )
        assert [i for i, _, _ in eligible] == [0]
        assert skipped == []

    def test_largest_mode_oversize_not_chosen(self):
        files = _FakeFiles([("big.mp4", 5000), ("ok.mp4", 100)])
        eligible, skipped = BitTorrentPlugin._select_video_files(
            files, [".mp4"], 10, 1000, mode="largest"
        )
        assert [i for i, _, _ in eligible] == [1]
        assert [s[0] for s in skipped] == [0]


class TestTitlesAndMetadata:
    def test_single_file_no_titles(self):
        source = MediaSource(
            source_id="x", media_type="bittorrent", uri="m", title="T",
            metadata={"title": "Torrent Title"},
        )
        assert BitTorrentPlugin._torrent_titles(source, ["/tmp/a.mp4"]) == {}

    def test_multi_file_titles(self):
        source = MediaSource(
            source_id="x", media_type="bittorrent", uri="m", title="T",
            metadata={"title": "Torrent Title"},
        )
        titles = BitTorrentPlugin._torrent_titles(
            source, ["/tmp/a.mp4", "/tmp/sub/b.mkv"]
        )
        assert titles["/tmp/a.mp4"] == "Torrent Title - a.mp4"
        assert titles["/tmp/sub/b.mkv"] == "Torrent Title - b.mkv"

    def test_result_metadata_carries_all_paths(self):
        source = MediaSource(
            source_id="x", media_type="bittorrent", uri="m", title="T",
            metadata={"title": "Torrent Title", "k": 1},
        )
        meta = BitTorrentPlugin._result_metadata(
            source, ["/a.mp4", "/b.mp4"], {"/a.mp4": "A", "/b.mp4": "B"}
        )
        assert meta["output_paths"] == ["/a.mp4", "/b.mp4"]
        assert meta["output_titles"] == {"/a.mp4": "A", "/b.mp4": "B"}
        assert meta["k"] == 1


def _make_plugin(tmp_path):
    plugin = BitTorrentPlugin(config={"download_dir": str(tmp_path), "sources": []})
    plugin._initialized = True
    plugin._download_dir = tmp_path
    plugin._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    return plugin


@pytest.mark.asyncio
async def test_archive_harness_multi_file(tmp_path, monkeypatch):
    """Full archive() with fakes: 3 videos -> 3 output_paths + titles."""
    from haven_cli.plugins.base import ArchiveResult

    plugin = _make_plugin(tmp_path)
    plugin._bt_config.enable_background_downloads = False

    files = _FakeFiles([
        ("pack/a.mp4", 20 * 1024 * 1024),
        ("pack/b.mkv", 30 * 1024 * 1024),
        ("pack/c.mov", 40 * 1024 * 1024),
        ("pack/notes.txt", 100),
    ])

    class _Handle:
        def __init__(self):
            self.priorities = {}

        def has_metadata(self):
            return True

        def get_torrent_info(self):
            return SimpleNamespace(files=lambda: files)

        def file_priority(self, i, p):
            self.priorities[i] = p

        def status(self):
            return SimpleNamespace(is_finished=True)

    handle = _Handle()
    plugin._session = SimpleNamespace(
        add_torrent=lambda p: handle,
        remove_torrent=lambda h: None,
    )

    # Persist the files where _download_with_tracking looks for them.
    for rel, _size in files._entries:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1024)

    async def fake_download(**kwargs):
        assert kwargs["selected_file_indexes"] == [0, 1, 2]
        assert kwargs["selected_file_index"] == 0
        return ArchiveResult(
            success=True,
            output_path=str(tmp_path / "pack/a.mp4"),
            file_size=3072,
            metadata=plugin._result_metadata(
                kwargs["source"],
                [str(tmp_path / "pack/a.mp4"), str(tmp_path / "pack/b.mkv"),
                 str(tmp_path / "pack/c.mov")],
                plugin._torrent_titles(
                    kwargs["source"],
                    [str(tmp_path / "pack/a.mp4"), str(tmp_path / "pack/b.mkv"),
                     str(tmp_path / "pack/c.mov")],
                ),
            ),
        )

    plugin._download_with_tracking = fake_download  # type: ignore[method-assign]

    source = MediaSource(
        source_id="h" * 40, media_type="bittorrent", uri="magnet:?x",
        title="Pack", metadata={"title": "Pack"},
    )
    result = await plugin.archive(source)

    assert result.success
    assert len(result.metadata["output_paths"]) == 3
    assert len(result.metadata["output_titles"]) == 3
    assert handle.priorities == {0: 4, 1: 4, 2: 4, 3: 0}
