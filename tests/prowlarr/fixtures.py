"""Shared fixtures: fake Prowlarr JSON payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

API_KEY = "fakeprowlarrkey0123456789abcdef00"


def indexer(
    id_: int,
    name: str,
    *,
    protocol: str = "torrent",
    privacy: str = "public",
    enable: bool = True,
    tags: list[int] | None = None,
    categories: list[tuple[int, str]] | None = None,
    book: bool = False,
    tv: bool = False,
) -> dict[str, Any]:
    caps: dict[str, Any] = {
        "categories": [{"id": c, "name": n, "subCategories": []} for c, n in (categories or [(8000, "Other")])],
        "searchParams": ["q"],
    }
    if book:
        caps["bookSearchParams"] = ["q", "author", "title"]
    if tv:
        caps["tvSearchParams"] = ["q", "season", "ep", "imdbId"]
    return {
        "id": id_,
        "name": name,
        "definitionName": name.lower().replace(" ", ""),
        "enable": enable,
        "protocol": protocol,
        "privacy": privacy,
        "supportsSearch": True,
        "priority": 25,
        "tags": tags or [],
        "capabilities": caps,
    }


def release(
    n: int,
    *,
    indexer_id: int = 1,
    indexer_name: str = "Public Docs",
    protocol: str = "torrent",
    base: str = "http://127.0.0.1:9696",
    link: str = "pdf",
    age_hours: float = 1.0,
    size: int = 2048,
    seeders: int | None = 5,
    info_hash: str | None = None,
    title: str | None = None,
    info_url: str | None = "default",
    magnet: str | None = None,
    categories: list[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    published = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {
        "guid": f"https://docs.example.org/item/{n}",
        "title": title or f"Item {n}",
        "indexerId": indexer_id,
        "indexer": indexer_name,
        "protocol": protocol,
        "publishDate": published.isoformat().replace("+00:00", "Z"),
        "ageHours": age_hours,
        "size": size,
        "seeders": seeders,
        "leechers": 0,
        "grabs": None,
        "infoUrl": f"https://docs.example.org/item/{n}" if info_url == "default" else info_url,
        "downloadUrl": f"{base}/{indexer_id}/download?apikey={API_KEY}&link={link}&file=Item{n}",
        "magnetUrl": magnet,
        "infoHash": info_hash,
        "categories": [{"id": c, "name": nm, "subCategories": []} for c, nm in (categories or [(8000, "Other")])],
        "imdbId": 0,
        "tmdbId": 0,
        "tvdbId": 0,
    }


def make_torrent(name: str = "pack", files: list[tuple[str, int]] | None = None) -> bytes:
    """A syntactically valid multi-file .torrent (pieces are fake)."""

    def enc(value: Any) -> bytes:
        if isinstance(value, int):
            return b"i%de" % value
        if isinstance(value, str):
            value = value.encode()
        if isinstance(value, bytes):
            return b"%d:%s" % (len(value), value)
        if isinstance(value, list):
            return b"l" + b"".join(enc(v) for v in value) + b"e"
        if isinstance(value, dict):
            return b"d" + b"".join(enc(k) + enc(value[k]) for k in sorted(value)) + b"e"
        raise TypeError(value)

    entries = files or [("paper.pdf", 1000), ("readme.nfo", 10)]
    info = {
        "name": name,
        "piece length": 16384,
        "pieces": b"\x00" * 20,
        "files": [{"length": size, "path": path.split("/")} for path, size in entries],
    }
    return enc({"announce": "http://tracker.example/announce", "info": info})
