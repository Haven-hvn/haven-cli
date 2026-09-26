"""Integration tests for ProwlarrPlugin against an in-memory fake Prowlarr.

All HTTP goes through ``httpx.MockTransport`` (no sockets, no loopback);
download clients are replaced by an in-process fake backend.
"""

from __future__ import annotations

import json
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from haven_cli.acquisition.clients import ClientStatus, DownloadClient
from haven_cli.acquisition.state import AcquisitionStore
from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin
from haven_cli.plugins.builtin.prowlarr.plugin import release_from_dict
from tests.prowlarr.fake_server import PDF, FakeProwlarr
from tests.prowlarr.fixtures import API_KEY, indexer, release


@pytest.fixture
def fake():
    server = FakeProwlarr(
        indexers=[
            indexer(1, "Public Docs", protocol="torrent", privacy="public", tags=[5], categories=[(7000, "Books")], book=True),
            indexer(2, "Private Tracker", protocol="torrent", privacy="private"),
            indexer(3, "Usenet Indexer", protocol="usenet", privacy="private"),
            indexer(4, "Disabled", enable=False),
        ],
        tags=[{"id": 5, "label": "docs"}],
    )
    yield server


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("PROWLARR_API_KEY", API_KEY)


def make_plugin(fake: FakeProwlarr, tmp_path: Path, searches: list[dict[str, Any]], **settings: Any) -> ProwlarrPlugin:
    config = {
        "base_url": fake.base,
        "download_dir": str(tmp_path / "dl"),
        "state_file": str(tmp_path / "state.json"),
        "allow_private_hosts": True,
        "poll_interval_s": 0.01,
        "wait_timeout_s": 2,
        "retry_backoff_s": 60,
        "searches": searches,
        **settings,
    }
    plugin = ProwlarrPlugin(config)
    plugin.http_transport = fake.transport
    return plugin


def assert_no_key_leak(fake: FakeProwlarr, *values: Any) -> None:
    for value in values:
        assert API_KEY not in json.dumps(value, default=str)
    for req in fake.requests:
        assert "apikey" not in {k.lower() for k in req.query}, req.path
        if req.path.startswith("/files/"):
            assert "x-api-key" not in req.headers, "API key sent to a non-Prowlarr origin"


class FakeBackend(DownloadClient):
    name = "fake"
    protocol = "torrent"

    def __init__(self, content: Path | None, *, states: list[str] | None = None):
        self.content = content
        self.states = list(states or ["completed"])
        self.submitted: list[Any] = []
        self.released = 0

    async def submit(self, payload, *, label, title):
        self.submitted.append(payload)
        return {"id": len(self.submitted), "title": title}

    async def status(self, handle):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == "completed":
            return ClientStatus(state="completed", progress=1.0, content_path=self.content)
        return ClientStatus(state=state, progress=0.5, error="boom" if state == "failed" else "")

    async def release(self, handle):
        self.released += 1

    async def health(self):
        return "fake"


# ── discovery ────────────────────────────────────────────────────────────


class TestDiscovery:
    async def test_search_parameters_filters_and_privacy(self, fake, tmp_path):
        fake.releases = [
            release(1, indexer_id=1, age_hours=2, title="Neural Networks Survey"),
            release(2, indexer_id=1, age_hours=200, title="Old Survey"),  # too old
            release(3, indexer_id=2, indexer_name="Private Tracker", title="Neural Networks Book", info_url="https://tracker.example/details?id=3&passkey=zz"),
            release(4, indexer_id=1, title="Neural Networks Survey"),  # duplicate title+size
            release(5, indexer_id=1, title="Neural Sample Networks"),  # excluded
            release(6, indexer_id=1, title="Neural Nets low seeders", seeders=0),
        ]
        plugin = make_plugin(
            fake,
            tmp_path,
            [
                {
                    "name": "nn",
                    "query": "neural networks",
                    "type": "book",
                    "author": "Doe",
                    "indexers": ["Public Docs"],
                    "indexer_tags": ["docs"],
                    "indexer_ids": [2],
                    "categories": ["books", 7020],
                    "max_age_hours": 48,
                    "include": ["neural"],
                    "exclude": ["sample"],
                    "min_seeders": 1,
                    "max_results": 10,
                    "title_template": "{title} [{indexer}]",
                    "pipeline_options": {"vlm_enabled": False},
                }
            ],
        )
        sources = await plugin.discover_sources()
        search = fake.paths("/api/v1/search")[0]
        assert search.query["type"] == ["book"]
        assert search.query["query"] == ["neural networks {author:Doe}"]
        assert sorted(search.query["indexerIds"]) == ["1", "2"]
        assert search.query["categories"] == ["7000", "7020"]
        titles = {s.title for s in sources}
        assert titles == {"Neural Networks Survey [Public Docs]", "Neural Networks Book [Private Tracker]"}
        by_title = {s.title: s for s in sources}
        public = by_title["Neural Networks Survey [Public Docs]"]
        private = by_title["Neural Networks Book [Private Tracker]"]
        assert public.uri == "https://docs.example.org/item/4"  # newest duplicate wins
        assert private.uri == ""  # private indexer: nothing published by default
        assert "arkiv_payload_extra" in public.metadata and "arkiv_payload_extra" not in private.metadata
        assert public.metadata["vlm_enabled"] is False
        assert public.metadata["generic_files_enabled"] is True
        assert_no_key_leak(fake, [s.metadata for s in sources], [s.uri for s in sources])

    async def test_protocol_pseudo_ids_and_empty_query(self, fake, tmp_path):
        plugin = make_plugin(fake, tmp_path, [{"name": "latest", "protocol": "usenet"}])
        await plugin.discover_sources()
        req = fake.paths("/api/v1/search")[0]
        assert req.query["indexerIds"] == ["-1"] and "query" not in req.query

    async def test_job_options_select_and_inline(self, fake, tmp_path):
        fake.releases = [release(1)]
        plugin = make_plugin(fake, tmp_path, [{"name": "a", "query": "alpha"}, {"name": "b", "query": "beta", "enabled": False}])
        await plugin.discover_sources_for({"prowlarr_searches": "b"})
        assert fake.paths("/api/v1/search")[-1].query["query"] == ["beta"]
        await plugin.discover_sources_for({"prowlarr_search": json.dumps({"query": "gamma", "indexer_ids": [1]})})
        assert fake.paths("/api/v1/search")[-1].query["query"] == ["gamma"]
        await plugin.discover_sources()
        assert fake.paths("/api/v1/search")[-1].query["query"] == ["alpha"]
        assert len(fake.paths("/api/v1/search")) == 3  # disabled 'b' not run by default

    async def test_unknown_indexer_name_skips_search(self, fake, tmp_path):
        plugin = make_plugin(fake, tmp_path, [{"name": "x", "indexers": ["Nope"]}])
        assert await plugin.discover_sources() == []
        assert fake.paths("/api/v1/search") == []

    async def test_paging(self, fake, tmp_path):
        fake.releases = [release(i) for i in range(3)]
        plugin = make_plugin(fake, tmp_path, [{"name": "p", "limit": 3, "pages": 2, "dedupe": "none", "max_results": 50}])
        await plugin.discover_sources()
        offsets = [r.query.get("offset") for r in fake.paths("/api/v1/search")]
        assert offsets == [None, ["3"]]

    def test_invalid_config_reported(self, tmp_path, fake):
        plugin = make_plugin(fake, tmp_path, [{"name": "bad", "type": "nope"}])
        assert any("type" in e for e in plugin.validate_config())
        plugin = ProwlarrPlugin({"api_key": "inline", "state_file": str(tmp_path / "s.json")})
        assert any("api_key_env" in e for e in plugin.validate_config())


# ── archiving ────────────────────────────────────────────────────────────


async def discover_one(plugin: ProwlarrPlugin):
    sources = await plugin.discover_sources()
    assert len(sources) == 1, sources
    return sources[0]


class TestArchive:
    async def test_proxy_download_of_content(self, fake, tmp_path):
        fake.releases = [release(1, link="pdf", title="A Paper")]
        plugin = make_plugin(fake, tmp_path, [{"name": "s", "accept": ["document"]}])
        result = await plugin.archive(await discover_one(plugin))
        assert result.success, result.error
        path = Path(result.output_path)
        assert path.read_bytes() == PDF and path.suffix == ".pdf"
        assert result.metadata["output_paths"] == [str(path)]
        assert result.metadata["output_titles"][str(path)] == "A Paper"
        dl = fake.paths("/1/download")[0]
        assert dl.headers["x-api-key"] == API_KEY and dl.query["link"] == ["pdf"]
        assert_no_key_leak(fake, result.metadata)
        record = await AcquisitionStore(tmp_path / "state.json").get(plugin.settings().searches and (await plugin.discover_sources())[0].source_id)
        assert record is not None and record.status == "done"

    async def test_redirect_fetched_without_key(self, fake, tmp_path):
        fake.releases = [release(1, link="redirect")]
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        result = await plugin.archive(await discover_one(plugin))
        assert result.success, result.error
        assert fake.paths("/files/doc.pdf")
        assert_no_key_leak(fake)

    async def test_direct_link_rewrite(self, fake, tmp_path):
        fake.releases = [release(7, link="gone")]
        plugin = make_plugin(
            fake,
            tmp_path,
            [
                {
                    "name": "s",
                    "link_field": "info_url",
                    "link_pattern": r"^https://docs\.example\.org/item/(\d+)$",
                    "link_replacement": fake.base + r"/files/\1.pdf",
                    "allowed_hosts": ["prowlarr.test"],
                }
            ],
        )
        result = await plugin.archive(await discover_one(plugin))
        assert result.success, result.error
        assert fake.paths("/files/7.pdf") and not fake.paths("/1/download")

    async def test_direct_host_not_allowed(self, fake, tmp_path):
        fake.releases = [release(7)]
        plugin = make_plugin(
            fake,
            tmp_path,
            [{"name": "s", "fetch": "direct", "link_pattern": "^(.*)$", "link_replacement": fake.base + "/files/x.pdf", "allowed_hosts": ["files.example"]}],
        )
        result = await plugin.archive(await discover_one(plugin))
        assert not result.success and "allowed_hosts" in result.error

    async def test_rejected_type_is_permanent(self, fake, tmp_path):
        fake.releases = [release(1, link="html")]
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        source = await discover_one(plugin)
        result = await plugin.archive(source)
        assert not result.success and "text/html" in result.error
        assert (await AcquisitionStore(tmp_path / "state.json").get(source.source_id)).status == "failed"
        assert await plugin.discover_sources() == []  # not surfaced again
        assert list((tmp_path / "dl").rglob("*.html")) == []

    async def test_rate_limit_backs_off(self, fake, tmp_path):
        fake.releases = [release(1, link="e429")]
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        source = await discover_one(plugin)
        result = await plugin.archive(source)
        assert not result.success and "Grab limit reached" in result.error
        record = await AcquisitionStore(tmp_path / "state.json").get(source.source_id)
        assert record.status == "retry" and record.next_attempt_at >= time.time() + 100
        assert await plugin.discover_sources() == []

    async def test_nzb_without_usenet_client(self, fake, tmp_path):
        fake.releases = [release(1, indexer_id=3, protocol="usenet", link="nzb")]
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        result = await plugin.archive(await discover_one(plugin))
        assert not result.success and "usenet_client" in result.error

    async def test_unauthorized_is_config_error_not_failure(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1)]
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        source = await discover_one(plugin)
        monkeypatch.setenv("PROWLARR_API_KEY", "wrong-key-value")
        plugin.configure({})
        result = await plugin.archive(source)
        assert not result.success and result.error.startswith("configuration")
        assert await AcquisitionStore(tmp_path / "state.json").get(source.source_id) is None


class TestTorrentAndUsenet:
    def _content(self, tmp_path: Path) -> Path:
        root = tmp_path / "client" / "Pack"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "one.pdf").write_bytes(PDF)
        (root / "two.pdf").write_bytes(PDF + b"2")
        (root / "readme.nfo").write_text("nfo")
        return root

    async def test_torrent_submitted_and_imported(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1, link="torrent", title="Pack")]
        backend = FakeBackend(self._content(tmp_path))
        monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
        plugin = make_plugin(fake, tmp_path, [{"name": "s", "torrent_client": "qbittorrent"}], qbittorrent_url="http://unused")
        result = await plugin.archive(await discover_one(plugin))
        assert result.success, result.error
        assert backend.submitted[0].torrent == fake.torrent and backend.released == 1
        names = sorted(Path(p).name for p in result.metadata["output_paths"])
        assert names == ["one.pdf", "two.pdf"]
        imported = Path(result.metadata["output_paths"][0])
        assert "imports" in imported.parts
        assert (tmp_path / "client" / "Pack" / "two.pdf").exists()  # hardlinked, not moved
        titles = result.metadata["output_titles"]
        assert all(t.startswith("Pack - ") for t in titles.values())

    async def test_multi_file_tar(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1, link="torrent", title="Pack")]
        backend = FakeBackend(self._content(tmp_path))
        monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
        plugin = make_plugin(fake, tmp_path, [{"name": "s", "multi_file": "tar"}])
        result = await plugin.archive(await discover_one(plugin))
        assert result.success, result.error
        with tarfile.open(result.output_path) as archive:
            assert sorted(archive.getnames()) == ["docs/one.pdf", "two.pdf"]

    async def test_magnet_redirect(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1, link="magnet")]
        backend = FakeBackend(self._content(tmp_path))
        monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
        plugin = make_plugin(fake, tmp_path, [{"name": "s", "select_mode": "largest"}])
        result = await plugin.archive(await discover_one(plugin))
        assert result.success
        assert backend.submitted[0].magnet.startswith("magnet:?xt=urn:btih:efef")
        assert [Path(p).name for p in result.metadata["output_paths"]] == ["two.pdf"]

    async def test_pending_resumes_across_runs(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1, link="nzb", indexer_id=3, protocol="usenet")]
        backend = FakeBackend(self._content(tmp_path), states=["downloading"])
        monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
        plugin = make_plugin(fake, tmp_path, [{"name": "s", "usenet_client": "sabnzbd"}], wait_timeout_s=0, sabnzbd_url="http://unused")
        source = await discover_one(plugin)
        first = await plugin.archive(source)
        assert not first.success and first.metadata.get("pending") and first.error.startswith("pending")
        assert isinstance(backend.submitted[0].nzb, bytes)

        fake.releases = []  # release fell out of the feed
        resurfaced = await plugin.discover_sources()
        assert [s.source_id for s in resurfaced] == [source.source_id]
        backend.states = ["completed"]
        second = await plugin.archive(resurfaced[0])
        assert second.success, second.error
        assert len(backend.submitted) == 1  # not re-submitted

    async def test_backend_failure(self, fake, tmp_path, monkeypatch):
        fake.releases = [release(1, link="torrent")]
        backend = FakeBackend(None, states=["failed"])
        monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
        plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
        result = await plugin.archive(await discover_one(plugin))
        assert not result.success and "boom" in result.error

    async def test_grab_via_prowlarr_and_watch_dir(self, fake, tmp_path):
        watch = tmp_path / "complete"
        watch.mkdir()
        fake.releases = [release(1, link="torrent", title="Grabbed Release")]
        plugin = make_plugin(
            fake, tmp_path, [{"name": "s", "torrent_client": "prowlarr", "download_client_id": 3}], watch_dir=str(watch), watch_settle_s=0
        )
        source = await discover_one(plugin)
        (watch / "Grabbed.Release").mkdir()
        (watch / "Grabbed.Release" / "file.pdf").write_bytes(PDF)
        result = await plugin.archive(source)
        assert result.success, result.error
        grab = fake.paths("/api/v1/search")
        posted = [r for r in grab if r.method == "POST"]
        assert json.loads(posted[0].body) == {"guid": "https://docs.example.org/item/1", "indexerId": 1, "downloadClientId": 3}
        # fetch='auto' with torrent_client='prowlarr' grabs instead of downloading the .torrent
        assert not fake.paths("/1/download")


def test_release_roundtrip():
    from haven_cli.plugins.builtin.prowlarr.plugin import release_to_dict
    from haven_cli.services.prowlarr import parse_release

    rel = parse_release(release(1, info_hash="a" * 40), API_KEY)
    assert release_from_dict(json.loads(json.dumps(release_to_dict(rel)))) == rel


async def test_private_path_passkey_never_published(fake, tmp_path):
    fake.releases = [
        release(3, indexer_id=2, indexer_name="Private Tracker", info_url="https://tracker.example/t/9f8e7d6c5b4a39281706aabbccdd/details")
    ]
    plugin = make_plugin(fake, tmp_path, [{"name": "s", "publish_source": True}])
    (source,) = await plugin.discover_sources()
    assert source.uri == "https://docs.example.org/item/3"  # guid fallback, info_url withheld


async def test_concurrent_archive_of_same_release_submits_once(fake, tmp_path, monkeypatch):
    import asyncio

    fake.releases = [release(1, link="torrent")]
    content = tmp_path / "c" / "x.pdf"
    content.parent.mkdir()
    content.write_bytes(PDF)
    backend = FakeBackend(content, states=["downloading", "downloading", "completed"])
    monkeypatch.setattr("haven_cli.plugins.builtin.prowlarr.backends.make_backend", lambda *a, **k: backend)
    plugin = make_plugin(fake, tmp_path, [{"name": "s"}])
    source = await discover_one(plugin)
    first, second = await asyncio.gather(plugin.archive(source), plugin.archive(source))
    assert len(backend.submitted) == 1
    assert sorted([first.success, second.success]) == [False, True]
