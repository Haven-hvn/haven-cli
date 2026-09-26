"""Tests for bencode, selection, importer, state and guarded HTTP fetch."""

from __future__ import annotations

import hashlib
import os
import stat
import time

import httpx
import pytest

from haven_cli.acquisition.bencode import (
    BencodeError,
    decode,
    is_torrent,
    magnet_info_hash,
    parse_torrent,
)
from haven_cli.acquisition.http_fetch import FetchError, FetchPolicy, fetch_to_file, safe_filename
from haven_cli.acquisition.importer import apply_path_mappings, import_files
from haven_cli.acquisition.selection import SelectionPolicy, select_files, select_torrent_indices
from haven_cli.acquisition.state import AcquisitionRecord, AcquisitionStore
from tests.prowlarr.fixtures import make_torrent

PDF = b"%PDF-1.5\n" + b"x" * 200


class TestBencode:
    def test_decode_basic(self):
        assert decode(b"d3:bar4:spam3:fooi42ee") == {b"bar": b"spam", b"foo": 42}
        assert decode(b"l4:spami-3ee") == [b"spam", -3]

    @pytest.mark.parametrize("bad", [b"", b"i03e", b"5:abc", b"d3:fooe", b"x", b"l" * 100 + b"e" * 100])
    def test_decode_rejects(self, bad):
        with pytest.raises(BencodeError):
            decode(bad)

    def test_parse_torrent(self):
        data = make_torrent("pack", [("a/paper.pdf", 1000), ("readme.nfo", 10)])
        meta = parse_torrent(data)
        info_start = data.index(b"4:info") + len(b"4:info")
        assert meta.info_hash == hashlib.sha1(data[info_start:-1]).hexdigest()
        assert [f.path for f in meta.files] == ["pack/a/paper.pdf", "pack/readme.nfo"]
        assert meta.total_size == 1010 and not meta.private
        assert is_torrent(data) and not is_torrent(PDF)

    def test_magnet_hash(self):
        hexhash = "c12fe1c06bba254a9dc9f519b335aa7c1367a88a"
        assert magnet_info_hash(f"magnet:?xt=urn:btih:{hexhash.upper()}&dn=x") == hexhash
        b32 = "YEX6DQDLXISUVHOJ6UM3GNNKPQJWPKEK"
        assert magnet_info_hash(f"magnet:?xt=urn:btih:{b32}") == hexhash
        assert magnet_info_hash("https://x") is None
        assert magnet_info_hash("magnet:?xt=urn:btmh:1220abcd") is None


class TestSelection:
    def _tree(self, tmp_path):
        root = tmp_path / "Release"
        (root / "sub").mkdir(parents=True)
        (root / "paper.pdf").write_bytes(PDF)
        (root / "sub" / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        (root / "sample.pdf").write_bytes(PDF)
        (root / "info.nfo").write_text("nfo")
        (root / "repair.par2").write_bytes(b"PAR2")
        (root / "misnamed.txt").write_bytes(PDF)
        (root / "copy.torrent").write_bytes(make_torrent())
        return root

    def test_select_by_content_and_names(self, tmp_path):
        root = self._tree(tmp_path)
        names = sorted(p.name for p in select_files(root, SelectionPolicy(accept=("document",))))
        assert names == ["misnamed.txt", "paper.pdf"]
        everything = sorted(p.name for p in select_files(root, SelectionPolicy()))
        assert "copy.torrent" not in everything and "info.nfo" not in everything
        assert "figure.png" in everything

    def test_largest_and_size_limits(self, tmp_path):
        root = self._tree(tmp_path)
        (root / "big.pdf").write_bytes(PDF + b"y" * 1000)
        assert [p.name for p in select_files(root, SelectionPolicy(accept=("document",), mode="largest"))] == ["big.pdf"]
        assert select_files(root, SelectionPolicy(accept=("document",), max_size=100)) == []

    def test_torrent_indices(self):
        files = [("pack/paper.pdf", 1000), ("pack/Sample/clip.mkv", 50), ("pack/readme.nfo", 1), ("pack/data.qqq9", 7)]
        assert select_torrent_indices(files, SelectionPolicy(accept=("document",))) == [0, 3]
        assert select_torrent_indices(files, SelectionPolicy(mode="largest")) == [0]


class TestImporter:
    def test_modes(self, tmp_path):
        src_root = tmp_path / "client" / "Rel"
        (src_root / "sub").mkdir(parents=True)
        a = src_root / "sub" / "a?.pdf"
        a.write_bytes(PDF)
        linked = import_files([a], tmp_path / "ws", mode="hardlink", root=src_root)
        assert linked[0].relative_to(tmp_path / "ws").parts[0] == "sub"
        assert os.stat(linked[0]).st_ino == os.stat(a).st_ino
        copied = import_files([a], tmp_path / "ws2", mode="copy")
        assert os.stat(copied[0]).st_ino != os.stat(a).st_ino
        assert import_files([a], tmp_path / "ws3", mode="inplace") == [a]
        moved = import_files([a], tmp_path / "ws4", mode="move")
        assert moved[0].exists() and not a.exists()
        with pytest.raises(ValueError):
            import_files([moved[0]], tmp_path, mode="teleport")

    def test_path_mappings(self):
        maps = ["/downloads=/mnt/nas/dl", "/downloads/complete=/fast/complete"]
        assert apply_path_mappings("/downloads/complete/x", maps) == "/fast/complete/x"
        assert apply_path_mappings("/downloads/other", maps) == "/mnt/nas/dl/other"
        assert apply_path_mappings("/downloadsX/y", maps) == "/downloadsX/y"
        assert apply_path_mappings("C:\\dl\\x", ["C:\\dl=/data"]) == "/data/x"


class TestState:
    async def test_roundtrip_backoff_and_permissions(self, tmp_path):
        store = AcquisitionStore(tmp_path / "state.json")
        await store.put(AcquisitionRecord(key="k1", backend="qbittorrent", handle={"h": 1}, status="pending"))
        assert (await store.get("k1")).handle == {"h": 1}
        assert stat.S_IMODE(os.stat(tmp_path / "state.json").st_mode) == 0o600

        rec = await store.record_failure("k2", "boom", permanent=False, max_attempts=3, base_backoff=10)
        assert rec.status == "retry" and rec.next_attempt_at > time.time()
        rec = await store.record_failure("k2", "boom", permanent=False, max_attempts=3, base_backoff=10)
        assert rec.attempts == 2
        rec = await store.record_failure("k2", "boom", permanent=False, max_attempts=3, base_backoff=10)
        assert rec.status == "failed"
        rec = await store.record_failure("k3", "no", permanent=True, max_attempts=9, base_backoff=10)
        assert rec.status == "failed"

    async def test_corrupt_file_recovers(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json")
        store = AcquisitionStore(path)
        assert await store.all() == []
        assert list(tmp_path.glob("state.corrupt-*"))


def _transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        return routes(request)

    return httpx.MockTransport(handler)


class TestHttpFetch:
    def _client(self, routes):
        return httpx.AsyncClient(transport=_transport(routes), follow_redirects=False)

    async def test_follows_redirect_and_fixes_extension(self, tmp_path):
        def routes(req):
            if req.url.path == "/abs/1":
                return httpx.Response(302, headers={"Location": "/pdf/1"})
            return httpx.Response(200, content=PDF, headers={"Content-Disposition": 'attachment; filename="1.bin"'})

        policy = FetchPolicy(allow_private_hosts=True)
        async with self._client(routes) as client:
            got = await fetch_to_file("https://files.example/abs/1", tmp_path, policy, client=client)
        assert got.path.name == "1.pdf" and got.mime == "application/pdf"
        assert got.final_url == "https://files.example/pdf/1"
        assert not list(tmp_path.glob(".*.part"))

    async def test_size_cap(self, tmp_path):
        policy = FetchPolicy(allow_private_hosts=True, max_bytes=100)
        async with self._client(lambda r: httpx.Response(200, content=PDF)) as client:
            with pytest.raises(FetchError) as info:
                await fetch_to_file("https://files.example/a", tmp_path, policy, client=client)
        assert info.value.permanent
        assert not list(tmp_path.iterdir())

    async def test_declared_length_cap(self, tmp_path):
        policy = FetchPolicy(allow_private_hosts=True, max_bytes=100)
        routes = lambda r: httpx.Response(200, content=b"x" * 10, headers={"Content-Length": "999999"})  # noqa: E731
        async with self._client(routes) as client:
            with pytest.raises(FetchError, match="limit"):
                await fetch_to_file("https://files.example/a", tmp_path, policy, client=client)

    async def test_redirect_to_magnet_returned(self, tmp_path):
        magnet = "magnet:?xt=urn:btih:" + "a" * 40
        async with self._client(lambda r: httpx.Response(301, headers={"Location": magnet})) as client:
            got = await fetch_to_file("https://t.example/dl", tmp_path, FetchPolicy(allow_private_hosts=True), client=client)
        assert got == magnet

    async def test_redirect_hop_is_checked(self, tmp_path):
        routes = lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})  # noqa: E731
        async with self._client(routes) as client:
            with pytest.raises(FetchError, match="blocked"):
                await fetch_to_file("http://93.184.216.34/x", tmp_path, FetchPolicy(), client=client)

    async def test_host_allowlist(self, tmp_path):
        async with self._client(lambda r: httpx.Response(200, content=PDF)) as client:
            with pytest.raises(FetchError, match="allowed_hosts"):
                await fetch_to_file(
                    "https://other.example/a",
                    tmp_path,
                    FetchPolicy(allow_private_hosts=True, allowed_hosts=("files.example",)),
                    client=client,
                )

    @pytest.mark.parametrize("status,permanent", [(404, True), (429, False), (503, False)])
    async def test_status_errors(self, tmp_path, status, permanent):
        async with self._client(lambda r: httpx.Response(status, headers={"Retry-After": "7"})) as client:
            with pytest.raises(FetchError) as info:
                await fetch_to_file("https://f.example/a", tmp_path, FetchPolicy(allow_private_hosts=True), client=client)
        assert info.value.permanent is permanent

    async def test_too_many_redirects(self, tmp_path):
        async with self._client(lambda r: httpx.Response(302, headers={"Location": "/loop"})) as client:
            with pytest.raises(FetchError, match="too many"):
                await fetch_to_file("https://f.example/loop", tmp_path, FetchPolicy(allow_private_hosts=True, max_redirects=2), client=client)


@pytest.mark.parametrize(
    "raw,expected",
    [("a/b\\c.pdf", "a b c.pdf"), ("..", "download"), ("%2e%2e%2fetc", "etc"), ("x" * 300 + ".pdf", "x" * 176 + ".pdf")],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
