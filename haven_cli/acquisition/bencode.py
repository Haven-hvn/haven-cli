"""Minimal bencode decoder plus torrent and magnet helpers.

Only what acquisition needs: validate a ``.torrent``, compute its v1
info-hash from the raw ``info`` bytes, list its files, and parse the
info-hash out of a magnet link.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit


class BencodeError(ValueError):
    """Malformed bencoded data."""


_MAX_DEPTH = 64


def _decode(
    data: bytes, pos: int, depth: int, spans: dict[str, tuple[int, int]] | None
) -> tuple[Any, int]:
    if depth > _MAX_DEPTH:
        raise BencodeError("nesting too deep")
    if pos >= len(data):
        raise BencodeError("unexpected end of data")
    lead = data[pos : pos + 1]
    if lead == b"i":
        end = data.index(b"e", pos)
        raw = data[pos + 1 : end]
        if not re.fullmatch(rb"-?\d+", raw) or raw.startswith((b"-0", b"0")) and raw != b"0":
            raise BencodeError(f"invalid integer {raw!r}")
        return int(raw), end + 1
    if lead == b"l":
        pos += 1
        items: list[Any] = []
        while data[pos : pos + 1] != b"e":
            item, pos = _decode(data, pos, depth + 1, None)
            items.append(item)
        return items, pos + 1
    if lead == b"d":
        pos += 1
        result: dict[bytes, Any] = {}
        while data[pos : pos + 1] != b"e":
            key, pos = _decode(data, pos, depth + 1, None)
            if not isinstance(key, bytes):
                raise BencodeError("dictionary key is not a string")
            start = pos
            value, pos = _decode(data, pos, depth + 1, None)
            if spans is not None and depth == 0:
                spans[key.decode("latin-1")] = (start, pos)
            result[key] = value
        return result, pos + 1
    if lead.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("string runs past end of data")
        return data[start:end], end
    raise BencodeError(f"unexpected byte {lead!r} at {pos}")


def decode(data: bytes) -> Any:
    """Decode a complete bencoded value."""
    try:
        value, end = _decode(data, 0, 0, None)
    except (IndexError, ValueError) as exc:
        if isinstance(exc, BencodeError):
            raise
        raise BencodeError(str(exc)) from exc
    return value


@dataclass(frozen=True)
class TorrentFile:
    path: str
    size: int


@dataclass(frozen=True)
class TorrentMeta:
    """Facts parsed from a ``.torrent`` file."""

    info_hash: str  # v1 SHA-1 hex; "" for v2-only torrents
    name: str
    files: tuple[TorrentFile, ...]
    private: bool

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def parse_torrent(data: bytes) -> TorrentMeta:
    """Validate a ``.torrent`` and extract hash, name and file list.

    Raises:
        BencodeError: when *data* is not a torrent.
    """
    spans: dict[str, tuple[int, int]] = {}
    try:
        root, _ = _decode(data, 0, 0, spans)
    except (IndexError, ValueError) as exc:
        if isinstance(exc, BencodeError):
            raise
        raise BencodeError(str(exc)) from exc
    if not isinstance(root, dict) or b"info" not in root or not isinstance(root[b"info"], dict):
        raise BencodeError("not a torrent: missing info dictionary")
    info = root[b"info"]
    start, end = spans["info"]
    has_v1 = b"pieces" in info
    info_hash = hashlib.sha1(data[start:end]).hexdigest() if has_v1 else ""  # noqa: S324 - BitTorrent v1 id
    name = _utf8(info.get(b"name.utf-8", info.get(b"name", b"")))
    files: list[TorrentFile] = []
    if isinstance(info.get(b"files"), list):
        for entry in info[b"files"]:
            if not isinstance(entry, dict):
                continue
            parts = entry.get(b"path.utf-8", entry.get(b"path", []))
            if not isinstance(parts, list):
                continue
            rel = "/".join(_utf8(p) for p in parts if isinstance(p, bytes))
            size = entry.get(b"length", 0)
            files.append(
                TorrentFile(f"{name}/{rel}" if name else rel, size if isinstance(size, int) else 0)
            )
    elif isinstance(info.get(b"length"), int):
        files.append(TorrentFile(name, info[b"length"]))
    elif isinstance(info.get(b"file tree"), dict):
        _walk_v2_tree(info[b"file tree"], [name] if name else [], files)
    return TorrentMeta(
        info_hash=info_hash,
        name=name,
        files=tuple(files),
        private=info.get(b"private") == 1,
    )


def _walk_v2_tree(tree: dict[bytes, Any], prefix: list[str], out: list[TorrentFile]) -> None:
    for key, value in tree.items():
        if key == b"" and isinstance(value, dict):
            out.append(TorrentFile("/".join(prefix), int(value.get(b"length", 0))))
        elif isinstance(value, dict):
            _walk_v2_tree(value, [*prefix, _utf8(key)], out)


def _utf8(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def is_torrent(data: bytes) -> bool:
    """Whether *data* parses as a ``.torrent``."""
    try:
        parse_torrent(data)
    except BencodeError:
        return False
    return True


def magnet_info_hash(uri: str) -> str | None:
    """Lower-case hex v1 info-hash from a magnet URI (hex or base32 ``btih``)."""
    if not uri.lower().startswith("magnet:"):
        return None
    try:
        query = parse_qs(urlsplit(uri).query)
    except ValueError:
        return None
    for xt in query.get("xt", []):
        if not xt.lower().startswith("urn:btih:"):
            continue
        value = xt[9:]
        if re.fullmatch(r"[0-9a-fA-F]{40}", value):
            return value.lower()
        if re.fullmatch(r"[A-Za-z2-7]{32}", value):
            return base64.b32decode(value.upper()).hex()
    return None


def magnet_display_name(uri: str) -> str | None:
    try:
        names = parse_qs(urlsplit(uri).query).get("dn")
    except ValueError:
        return None
    return names[0] if names else None
