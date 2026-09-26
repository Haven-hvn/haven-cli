"""Transmission RPC backend."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from haven_cli.acquisition.bencode import BencodeError, magnet_info_hash, parse_torrent
from haven_cli.acquisition.clients import ClientError, ClientStatus, DownloadClient, TorrentPayload
from haven_cli.acquisition.importer import apply_path_mappings
from haven_cli.services.url_safety import redact_text

_SESSION_HEADER = "X-Transmission-Session-Id"
# torrent-get "status": 0 stopped, 1 check-wait, 2 check, 3 download-wait,
# 4 download, 5 seed-wait, 6 seed.


class TransmissionClient(DownloadClient):
    name = "transmission"
    protocol = "torrent"

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        download_dir: str = "",
        labels: bool = True,
        path_mappings: tuple[str, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not url:
            raise ClientError("transmission_url is not configured", config=True)
        url = url.rstrip("/")
        self._url = url if url.endswith("/rpc") else f"{url}/transmission/rpc"
        self._password = password
        self._download_dir = download_dir
        self._labels = labels
        self._mappings = path_mappings
        auth = httpx.BasicAuth(username, password) if username or password else None
        self._http = httpx.AsyncClient(
            timeout=timeout, verify=verify, auth=auth, follow_redirects=False, transport=transport
        )
        self._session_id = ""

    async def _rpc(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {"method": method, "arguments": arguments}
        for _ in range(2):
            try:
                response = await self._http.post(
                    self._url, json=body, headers={_SESSION_HEADER: self._session_id}
                )
            except httpx.HTTPError as exc:
                raise ClientError(
                    redact_text(f"Transmission RPC failed: {exc}", (self._password,))
                ) from exc
            if response.status_code == 409:
                self._session_id = response.headers.get(_SESSION_HEADER, "")
                continue
            if response.status_code == 401:
                raise ClientError("Transmission rejected credentials", config=True)
            if response.status_code >= 400:
                raise ClientError(f"Transmission RPC HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise ClientError("Transmission returned invalid JSON") from exc
            result = str(data.get("result", ""))
            if method == "torrent-add" and "duplicate" in result.lower():
                return data.get("arguments") or {}
            if result != "success":
                raise ClientError(
                    f"Transmission {method}: {data.get('result')}",
                    permanent=method == "torrent-add",
                )
            return data.get("arguments") or {}
        raise ClientError("Transmission session negotiation failed")

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        if not isinstance(payload, TorrentPayload):
            raise ClientError("Transmission accepts torrents only", permanent=True)
        args: dict[str, Any] = {"paused": False}
        if self._download_dir:
            args["download-dir"] = self._download_dir
        if self._labels:
            args["labels"] = [label]
        info_hash = payload.info_hash
        if payload.torrent:
            try:
                info_hash = info_hash or parse_torrent(payload.torrent).info_hash or None
            except BencodeError as exc:
                raise ClientError(f"invalid .torrent: {exc}", permanent=True) from exc
            args["metainfo"] = base64.b64encode(payload.torrent).decode("ascii")
        else:
            args["filename"] = payload.magnet
            info_hash = info_hash or magnet_info_hash(payload.magnet or "")
        result = await self._rpc("torrent-add", args)
        added = result.get("torrent-added") or result.get("torrent-duplicate") or {}
        info_hash = str(added.get("hashString") or info_hash or "").lower()
        if not info_hash:
            raise ClientError("Transmission did not report an info-hash", permanent=True)
        return {"info_hash": info_hash, "title": title, "label": label}

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        fields = [
            "hashString",
            "status",
            "percentDone",
            "downloadDir",
            "name",
            "error",
            "errorString",
            "leftUntilDone",
        ]
        result = await self._rpc("torrent-get", {"ids": [handle["info_hash"]], "fields": fields})
        torrents = result.get("torrents") or []
        if not torrents:
            return ClientStatus(state="missing", error="torrent not found in Transmission")
        item = torrents[0]
        progress = float(item.get("percentDone") or 0.0)
        if item.get("error"):
            return ClientStatus(
                state="failed", progress=progress, error=str(item.get("errorString") or "error")
            )
        if (
            progress >= 1.0
            and item.get("leftUntilDone", 0) == 0
            and item.get("status") not in (1, 2)
        ):
            path = Path(
                apply_path_mappings(
                    str(Path(item.get("downloadDir", "")) / item.get("name", "")), self._mappings
                )
            )
            return ClientStatus(state="completed", progress=1.0, content_path=path)
        return ClientStatus(
            state="queued" if item.get("status") in (0, 1, 2, 3) else "downloading",
            progress=progress,
        )

    async def health(self) -> str:
        result = await self._rpc("session-get", {"fields": ["version"]})
        return f"Transmission {result.get('version', '?')}"

    async def aclose(self) -> None:
        await self._http.aclose()
