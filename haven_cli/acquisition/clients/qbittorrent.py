"""qBittorrent Web API (v2) backend.

Torrents are added with a unique tag (``haven-<hash>``) and an optional
category/save path; status is looked up by info-hash when known, else by
tag. Paths reported by qBittorrent are translated through
``path_mappings`` for containerized setups.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from haven_cli.acquisition.bencode import BencodeError, magnet_info_hash, parse_torrent
from haven_cli.acquisition.clients import ClientError, ClientStatus, DownloadClient, TorrentPayload
from haven_cli.acquisition.importer import apply_path_mappings
from haven_cli.services.url_safety import redact_text

_COMPLETE_STATES = {
    "uploading",
    "stalledUP",
    "pausedUP",
    "stoppedUP",
    "queuedUP",
    "forcedUP",
    "checkingUP",
}
_FAILED_STATES = {"error", "missingFiles"}


class QBittorrentClient(DownloadClient):
    name = "qbittorrent"
    protocol = "torrent"

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        category: str = "",
        save_path: str = "",
        path_mappings: tuple[str, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not url:
            raise ClientError("qbittorrent_url is not configured", config=True)
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._category = category
        self._save_path = save_path
        self._mappings = path_mappings
        self._http = httpx.AsyncClient(
            timeout=timeout,
            verify=verify,
            follow_redirects=False,
            transport=transport,
            # qBittorrent's CSRF protection checks Referer/Origin.
            headers={"Referer": self._url, "Origin": self._url},
        )
        self._logged_in = False

    def _redact(self, text: str) -> str:
        return redact_text(text, (self._password,))

    async def _login(self) -> None:
        if not self._username and not self._password:
            self._logged_in = True  # auth bypass for whitelisted subnets / localhost
            return
        try:
            response = await self._http.post(
                f"{self._url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as exc:
            raise ClientError(self._redact(f"qBittorrent login failed: {exc}")) from exc
        if response.status_code == 403:
            raise ClientError(
                "qBittorrent refused login (IP banned after failed attempts?)", config=True
            )
        if response.status_code != 200 or response.text.strip() != "Ok.":
            raise ClientError(
                "qBittorrent login rejected: check qbittorrent_username/password", config=True
            )
        self._logged_in = True

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._logged_in:
            await self._login()
        for attempt in range(2):
            try:
                response = await self._http.request(method, f"{self._url}{path}", **kwargs)
            except httpx.HTTPError as exc:
                raise ClientError(
                    self._redact(f"qBittorrent request {path} failed: {exc}")
                ) from exc
            if response.status_code == 403 and attempt == 0:
                self._logged_in = False
                await self._login()
                continue
            if response.status_code >= 400:
                raise ClientError(
                    self._redact(
                        f"qBittorrent {path} returned HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    ),
                    permanent=response.status_code in (400, 415),
                )
            return response
        raise ClientError(f"qBittorrent {path}: authentication failed")

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        if not isinstance(payload, TorrentPayload):
            raise ClientError("qBittorrent accepts torrents only", permanent=True)
        data: dict[str, str] = {"tags": label, "paused": "false", "stopped": "false"}
        if self._category:
            data["category"] = self._category
        if self._save_path:
            data["savepath"] = self._save_path
        info_hash = payload.info_hash
        files = None
        if payload.torrent:
            try:
                info_hash = info_hash or parse_torrent(payload.torrent).info_hash or None
            except BencodeError as exc:
                raise ClientError(f"invalid .torrent: {exc}", permanent=True) from exc
            files = {"torrents": ("release.torrent", payload.torrent, "application/x-bittorrent")}
        else:
            data["urls"] = payload.magnet or ""
            info_hash = info_hash or magnet_info_hash(payload.magnet or "")
        response = await self._call("POST", "/api/v2/torrents/add", data=data, files=files)
        # qBittorrent answers "Fails." for duplicates too; the lookup in
        # status() finds an existing torrent when there is one.
        if response.text.strip().lower().startswith("fails") and not info_hash:
            raise ClientError("qBittorrent rejected the torrent", permanent=True)
        return {"info_hash": info_hash or "", "tag": label, "title": title}

    async def _find(self, handle: dict[str, Any]) -> dict[str, Any] | None:
        params = (
            {"hashes": handle["info_hash"]}
            if handle.get("info_hash")
            else {"tag": handle.get("tag", "")}
        )
        response = await self._call("GET", "/api/v2/torrents/info", params=params)
        try:
            items = response.json()
        except ValueError as exc:
            raise ClientError("qBittorrent returned invalid JSON") from exc
        if not isinstance(items, list) or not items:
            return None
        return items[0] if isinstance(items[0], dict) else None

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        item = await self._find(handle)
        if item is None:
            return ClientStatus(state="missing", error="torrent not found in qBittorrent")
        state = str(item.get("state", ""))
        progress = float(item.get("progress", 0.0) or 0.0)
        if state in _FAILED_STATES:
            return ClientStatus(
                state="failed", progress=progress, error=f"qBittorrent state {state}"
            )
        if state in _COMPLETE_STATES or progress >= 1.0:
            raw_path = item.get("content_path") or str(
                Path(item.get("save_path", "")) / item.get("name", "")
            )
            return ClientStatus(
                state="completed",
                progress=1.0,
                content_path=Path(apply_path_mappings(str(raw_path), self._mappings)),
            )
        if state in (
            "metaDL",
            "queuedDL",
            "checkingDL",
            "checkingResumeData",
            "allocating",
            "moving",
        ):
            return ClientStatus(state="queued", progress=progress)
        return ClientStatus(state="downloading", progress=progress)

    async def health(self) -> str:
        response = await self._call("GET", "/api/v2/app/version")
        return f"qBittorrent {response.text.strip()}"

    async def aclose(self) -> None:
        await self._http.aclose()
