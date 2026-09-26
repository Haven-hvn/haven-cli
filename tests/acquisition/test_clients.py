"""Tests for download-client backends against mock APIs."""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from haven_cli.acquisition.clients import ClientError, NzbPayload, TorrentPayload
from haven_cli.acquisition.clients.qbittorrent import QBittorrentClient
from haven_cli.acquisition.clients.transmission import TransmissionClient
from haven_cli.acquisition.clients.usenet import NZBGetClient, SABnzbdClient
from haven_cli.acquisition.clients.watch import WatchDirectory
from haven_cli.acquisition.bencode import parse_torrent
from tests.prowlarr.fixtures import make_torrent

MAGNET = "magnet:?xt=urn:btih:" + "ab" * 20 + "&dn=Pack"


class TestQBittorrent:
    def _client(self, state):
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path == "/api/v2/auth/login":
                form = parse_qs(req.content.decode())
                if form.get("password") == ["pw"]:
                    state["cookie"] = True
                    return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=abc; path=/"})
                return httpx.Response(200, text="Fails.")
            if "SID=abc" not in req.headers.get("cookie", ""):
                return httpx.Response(403)
            if path == "/api/v2/torrents/add":
                state["added"] = req
                return httpx.Response(200, text="Ok.")
            if path == "/api/v2/torrents/info":
                return httpx.Response(200, json=state.get("info", []))
            if path == "/api/v2/app/version":
                return httpx.Response(200, text="v5.0.0")
            return httpx.Response(404)

        return QBittorrentClient(
            "http://qbit:8080",
            username="admin",
            password="pw",
            category="haven",
            path_mappings=("/downloads=/mnt/dl",),
            transport=httpx.MockTransport(handler),
        )

    async def test_add_magnet_and_complete(self):
        state: dict = {}
        client = self._client(state)
        handle = await client.submit(TorrentPayload(magnet=MAGNET), label="haven-x", title="Pack")
        assert handle["info_hash"] == "ab" * 20
        body = state["added"].content.decode()
        assert "haven-x" in body and "haven" in body and "magnet" in body
        state["info"] = [{"state": "downloading", "progress": 0.4}]
        assert (await client.status(handle)).state == "downloading"
        state["info"] = [{"state": "stalledUP", "progress": 1.0, "content_path": "/downloads/Pack"}]
        done = await client.status(handle)
        assert done.done and str(done.content_path) == "/mnt/dl/Pack"
        state["info"] = []
        assert (await client.status(handle)).state == "missing"
        assert await client.health() == "qBittorrent v5.0.0"
        await client.aclose()

    async def test_add_torrent_file(self):
        state: dict = {}
        client = self._client(state)
        data = make_torrent()
        handle = await client.submit(TorrentPayload(torrent=data), label="l", title="t")
        assert handle["info_hash"] == parse_torrent(data).info_hash
        assert b"application/x-bittorrent" in state["added"].content
        await client.aclose()

    async def test_bad_login_is_config_error(self):
        client = QBittorrentClient(
            "http://qbit:8080",
            username="admin",
            password="wrong",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="Fails.")),
        )
        with pytest.raises(ClientError) as info:
            await client.health()
        assert info.value.config and "wrong" not in str(info.value)
        await client.aclose()


class TestTransmission:
    async def test_session_negotiation_and_status(self):
        calls = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.headers.get("X-Transmission-Session-Id") != "sess":
                return httpx.Response(409, headers={"X-Transmission-Session-Id": "sess"})
            body = json.loads(req.content)
            calls.append(body)
            if body["method"] == "torrent-add":
                return httpx.Response(200, json={"result": "success", "arguments": {"torrent-added": {"hashString": "CD" * 20}}})
            if body["method"] == "torrent-get":
                return httpx.Response(
                    200,
                    json={
                        "result": "success",
                        "arguments": {"torrents": [{"percentDone": 1.0, "leftUntilDone": 0, "status": 6, "downloadDir": "/data", "name": "Pack", "error": 0}]},
                    },
                )
            return httpx.Response(200, json={"result": "success", "arguments": {"version": "4.0.6"}})

        client = TransmissionClient("http://tr:9091", transport=httpx.MockTransport(handler))
        handle = await client.submit(TorrentPayload(torrent=make_torrent()), label="haven-1", title="Pack")
        assert handle["info_hash"] == "cd" * 20
        assert calls[0]["arguments"]["labels"] == ["haven-1"]
        assert base64.b64decode(calls[0]["arguments"]["metainfo"]) == make_torrent()
        status = await client.status(handle)
        assert status.done and str(status.content_path) == "/data/Pack"
        assert await client.health() == "Transmission 4.0.6"
        await client.aclose()


class TestSABnzbd:
    async def test_flow(self):
        state = {"phase": "queue"}

        def handler(req: httpx.Request) -> httpx.Response:
            params = dict(req.url.params)
            assert params["apikey"] == "sabkey"
            mode = params.get("mode")
            if mode == "addfile":
                assert b"<nzb" in req.content
                return httpx.Response(200, json={"status": True, "nzo_ids": ["SABnzbd_nzo_1"]})
            if mode == "queue":
                slots = [{"nzo_id": "SABnzbd_nzo_1", "percentage": "40"}] if state["phase"] == "queue" else []
                return httpx.Response(200, json={"queue": {"slots": slots}})
            if mode == "history":
                return httpx.Response(200, json={"history": {"slots": [{"nzo_id": "SABnzbd_nzo_1", "status": "Completed", "storage": "/complete/Rel"}]}})
            return httpx.Response(200, json={"version": "4.3.0"})

        client = SABnzbdClient("http://sab:8080", "sabkey", category="haven", transport=httpx.MockTransport(handler))
        handle = await client.submit(NzbPayload(nzb=b"<nzb></nzb>"), label="l", title="Rel")
        status = await client.status(handle)
        assert status.state == "downloading" and status.progress == pytest.approx(0.4)
        state["phase"] = "history"
        status = await client.status(handle)
        assert status.done and str(status.content_path) == "/complete/Rel"
        await client.aclose()

    async def test_error_redacts_key(self):
        def handler(req):
            raise httpx.ConnectError(f"refused {req.url}")

        client = SABnzbdClient("http://sab:8080", "supersecretkey", transport=httpx.MockTransport(handler))
        with pytest.raises(ClientError) as info:
            await client.health()
        assert "supersecretkey" not in str(info.value)
        await client.aclose()


class TestNZBGet:
    async def test_flow(self):
        state = {"queued": True}

        def handler(req):
            body = json.loads(req.content)
            method = body["method"]
            if method == "append":
                assert body["params"][0].endswith(".nzb")
                assert base64.b64decode(body["params"][1]) == b"<nzb/>"
                return httpx.Response(200, json={"result": 17})
            if method == "listgroups":
                groups = [{"NZBID": 17, "FileSizeMB": 100, "RemainingSizeMB": 25}] if state["queued"] else []
                return httpx.Response(200, json={"result": groups})
            if method == "history":
                return httpx.Response(200, json={"result": [{"NZBID": 17, "Status": "SUCCESS/UNPACK", "FinalDir": "/done/Rel"}]})
            return httpx.Response(200, json={"result": "24.1"})

        client = NZBGetClient("http://nzbget:6789", transport=httpx.MockTransport(handler))
        handle = await client.submit(NzbPayload(nzb=b"<nzb/>"), label="l", title="Rel")
        assert handle["nzb_id"] == 17
        assert (await client.status(handle)).progress == pytest.approx(0.75)
        state["queued"] = False
        status = await client.status(handle)
        assert status.done and str(status.content_path) == "/done/Rel"
        await client.aclose()


class TestWatch:
    async def test_detects_settled_match(self, tmp_path):
        watch = WatchDirectory(tmp_path, settle_seconds=0.0)
        handle = await watch.submit(None, label="l", title="Some Release (2024) [PDF]")
        assert (await watch.status(handle)).state == "downloading"
        rel = tmp_path / "Some.Release.2024.PDF"
        rel.mkdir()
        (rel / "doc.pdf.part").write_bytes(b"%PDF")
        assert (await watch.status(handle)).state == "downloading"
        (rel / "doc.pdf.part").rename(rel / "doc.pdf")
        first = await watch.status(handle)
        assert first.state == "downloading"  # first sighting
        second = await watch.status(handle)
        assert second.done and second.content_path == rel

    async def test_ignores_old_entries(self, tmp_path):
        old = tmp_path / "Old Release"
        old.mkdir()
        (old / "a.pdf").write_bytes(b"%PDF")
        past = time.time() - 10_000
        import os

        os.utime(old, (past, past))
        watch = WatchDirectory(tmp_path, settle_seconds=0.0, clock_slack=0)
        handle = await watch.submit(None, label="l", title="Old Release")
        assert (await watch.status(handle)).state == "downloading"
