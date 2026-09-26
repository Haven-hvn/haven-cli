"""An in-memory fake Prowlarr (+ file host) served through ``httpx.MockTransport``.

No sockets are opened: requests are answered by :meth:`FakeProwlarr.handle`
inside the process. Point code at it with ``transport=fake.transport``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from tests.prowlarr.fixtures import API_KEY, make_torrent

PDF = b"%PDF-1.7\n" + b"0" * 512
NZB = b'<?xml version="1.0"?>\n<!DOCTYPE nzb>\n<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>'
BASE = "http://prowlarr.test:9696"


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes = b""


@dataclass
class FakeProwlarr:
    indexers: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    requests: list[Seen] = field(default_factory=list)
    torrent: bytes = field(default_factory=make_torrent)
    base: str = BASE

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self, path: str) -> list[Seen]:
        return [r for r in self.requests if r.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        seen = Seen(
            request.method,
            request.url.path,
            parse_qs(request.url.query.decode()),
            {k.lower(): v for k, v in request.headers.items()},
            request.content,
        )
        self.requests.append(seen)

        def js(status: int, value: Any) -> httpx.Response:
            return httpx.Response(status, json=value)

        if seen.path.startswith("/files/"):
            return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
        if seen.headers.get("x-api-key") != API_KEY:
            return js(401, {"message": "Unauthorized"})
        if seen.method == "POST":
            return js(200, json.loads(seen.body)) if seen.path == "/api/v1/search" else js(404, {})
        if seen.path == "/api/v1/system/status":
            return js(200, {"version": "2.6.5.0"})
        if seen.path == "/api/v1/indexer":
            return js(200, self.indexers)
        if seen.path == "/api/v1/tag":
            return js(200, self.tags)
        if seen.path == "/api/v1/search":
            return js(
                200,
                [dict(r, downloadUrl=r["downloadUrl"].replace("http://127.0.0.1:9696", self.base)) for r in self.releases],
            )
        if seen.path.endswith("/download"):
            link = (seen.query.get("link") or [""])[0]
            responses = {
                "pdf": lambda: httpx.Response(200, content=PDF, headers={"Content-Type": "application/x-bittorrent"}),
                "redirect": lambda: httpx.Response(301, headers={"Location": f"{self.base}/files/doc.pdf"}),
                "torrent": lambda: httpx.Response(200, content=self.torrent, headers={"Content-Type": "application/x-bittorrent"}),
                "nzb": lambda: httpx.Response(200, content=NZB, headers={"Content-Type": "application/x-nzb"}),
                "magnet": lambda: httpx.Response(301, headers={"Location": "magnet:?xt=urn:btih:" + "ef" * 20}),
                "html": lambda: httpx.Response(200, content=b"<!DOCTYPE html><html>login</html>", headers={"Content-Type": "text/html"}),
                "e429": lambda: httpx.Response(
                    429,
                    content=b'<?xml version="1.0"?><error code="429" description="Grab limit reached"/>',
                    headers={"Retry-After": "120"},
                ),
                "gone": lambda: httpx.Response(410, content=b'<error code="410" description="Indexer is disabled"/>'),
            }
            if link in responses:
                return responses[link]()
        return js(404, {"message": "not found"})
