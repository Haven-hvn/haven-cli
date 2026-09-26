"""Usenet backends: SABnzbd and NZBGet.

Both receive the NZB *content* (not a URL), so indexer credentials in the
NZB link never reach the Usenet client.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from haven_cli.acquisition.clients import ClientError, ClientStatus, DownloadClient, NzbPayload
from haven_cli.acquisition.importer import apply_path_mappings
from haven_cli.services.url_safety import redact_text


class SABnzbdClient(DownloadClient):
    name = "sabnzbd"
    protocol = "usenet"

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        category: str = "",
        priority: int | None = None,
        path_mappings: tuple[str, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not url or not api_key:
            raise ClientError("sabnzbd_url and the SABnzbd API key are required", config=True)
        url = url.rstrip("/")
        self._api = url if url.endswith("/api") else f"{url}/api"
        self._key = api_key
        self._category = category
        self._priority = priority
        self._mappings = path_mappings
        self._http = httpx.AsyncClient(
            timeout=timeout, verify=verify, follow_redirects=False, transport=transport
        )

    async def _call(self, params: dict[str, Any], *, files: Any = None) -> dict[str, Any]:
        query = {**params, "apikey": self._key, "output": "json"}
        try:
            if files is not None:
                response = await self._http.post(self._api, params=query, files=files)
            else:
                response = await self._http.get(self._api, params=query)
        except httpx.HTTPError as exc:
            raise ClientError(redact_text(f"SABnzbd request failed: {exc}", (self._key,))) from exc
        if response.status_code in (401, 403):
            raise ClientError("SABnzbd rejected the API key", config=True)
        if response.status_code >= 400:
            raise ClientError(f"SABnzbd HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ClientError("SABnzbd returned invalid JSON") from exc
        if isinstance(data, dict) and data.get("status") is False:
            raise ClientError(
                redact_text(f"SABnzbd error: {data.get('error')}", (self._key,)), permanent=True
            )
        return data if isinstance(data, dict) else {}

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        if not isinstance(payload, NzbPayload):
            raise ClientError("SABnzbd accepts NZBs only", permanent=True)
        params: dict[str, Any] = {"mode": "addfile", "nzbname": title[:200]}
        if self._category:
            params["cat"] = self._category
        if self._priority is not None:
            params["priority"] = self._priority
        data = await self._call(
            params, files={"name": (payload.filename, payload.nzb, "application/x-nzb")}
        )
        ids = data.get("nzo_ids") or []
        if not ids:
            raise ClientError("SABnzbd did not return an nzo_id", permanent=True)
        return {"nzo_id": ids[0], "title": title, "label": label}

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        nzo_id = handle["nzo_id"]
        queue = (await self._call({"mode": "queue", "nzo_ids": nzo_id})).get("queue", {})
        for slot in queue.get("slots", []) or []:
            if slot.get("nzo_id") == nzo_id:
                try:
                    pct = float(slot.get("percentage", 0)) / 100.0
                except (TypeError, ValueError):
                    pct = 0.0
                return ClientStatus(state="downloading", progress=pct)
        history = (await self._call({"mode": "history", "nzo_ids": nzo_id})).get("history", {})
        for slot in history.get("slots", []) or []:
            if slot.get("nzo_id") != nzo_id:
                continue
            status = str(slot.get("status", ""))
            if status == "Completed":
                path = slot.get("storage") or slot.get("path") or ""
                return ClientStatus(
                    state="completed",
                    progress=1.0,
                    content_path=Path(apply_path_mappings(path, self._mappings)),
                )
            if status == "Failed":
                return ClientStatus(state="failed", error=str(slot.get("fail_message") or "failed"))
            return ClientStatus(state="downloading", progress=0.99)  # post-processing
        return ClientStatus(state="missing", error="job not found in SABnzbd queue or history")

    async def health(self) -> str:
        data = await self._call({"mode": "version"})
        return f"SABnzbd {data.get('version', '?')}"

    async def aclose(self) -> None:
        await self._http.aclose()


class NZBGetClient(DownloadClient):
    name = "nzbget"
    protocol = "usenet"

    def __init__(
        self,
        url: str,
        *,
        username: str = "",
        password: str = "",
        category: str = "",
        priority: int = 0,
        path_mappings: tuple[str, ...] = (),
        timeout: float = 30.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not url:
            raise ClientError("nzbget_url is not configured", config=True)
        url = url.rstrip("/")
        self._rpc_url = url if url.endswith("/jsonrpc") else f"{url}/jsonrpc"
        self._password = password
        self._category = category
        self._priority = priority
        self._mappings = path_mappings
        auth = httpx.BasicAuth(username, password) if username or password else None
        self._http = httpx.AsyncClient(
            timeout=timeout, verify=verify, auth=auth, follow_redirects=False, transport=transport
        )

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        try:
            response = await self._http.post(
                self._rpc_url, json={"method": method, "params": params, "id": 1}
            )
        except httpx.HTTPError as exc:
            raise ClientError(
                redact_text(f"NZBGet request failed: {exc}", (self._password,))
            ) from exc
        if response.status_code == 401:
            raise ClientError("NZBGet rejected credentials", config=True)
        if response.status_code >= 400:
            raise ClientError(f"NZBGet HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ClientError("NZBGet returned invalid JSON") from exc
        if data.get("error"):
            raise ClientError(f"NZBGet {method}: {data['error']}", permanent=method == "append")
        return data.get("result")

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        if not isinstance(payload, NzbPayload):
            raise ClientError("NZBGet accepts NZBs only", permanent=True)
        name = title[:200] if title.lower().endswith(".nzb") else f"{title[:196]}.nzb"
        content = base64.b64encode(payload.nzb).decode("ascii")
        # append(NZBFilename, Content, Category, Priority, AddToTop, AddPaused,
        #        DupeKey, DupeScore, DupeMode, PPParameters)
        nzb_id = await self._rpc(
            "append",
            [name, content, self._category, self._priority, False, False, label, 0, "FORCE", []],
        )
        if not isinstance(nzb_id, int) or nzb_id <= 0:
            raise ClientError("NZBGet refused the NZB", permanent=True)
        return {"nzb_id": nzb_id, "title": title, "label": label}

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        nzb_id = handle["nzb_id"]
        for group in await self._rpc("listgroups", [0]) or []:
            if group.get("NZBID") == nzb_id:
                total = group.get("FileSizeMB") or 0
                remaining = group.get("RemainingSizeMB") or 0
                progress = 1.0 - (remaining / total) if total else 0.0
                return ClientStatus(state="downloading", progress=max(0.0, min(progress, 0.99)))
        for item in await self._rpc("history", [False]) or []:
            if item.get("NZBID") != nzb_id:
                continue
            status = str(item.get("Status", ""))
            if status.startswith("SUCCESS"):
                path = item.get("FinalDir") or item.get("DestDir") or ""
                return ClientStatus(
                    state="completed",
                    progress=1.0,
                    content_path=Path(apply_path_mappings(path, self._mappings)),
                )
            if status.startswith(("FAILURE", "DELETED")):
                return ClientStatus(state="failed", error=f"NZBGet status {status}")
            return ClientStatus(state="downloading", progress=0.99)
        return ClientStatus(state="missing", error="job not found in NZBGet queue or history")

    async def health(self) -> str:
        return f"NZBGet {await self._rpc('version', [])}"

    async def aclose(self) -> None:
        await self._http.aclose()
