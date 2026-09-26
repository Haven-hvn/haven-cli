"""Built-in torrent backend using libtorrent directly.

Independent of :class:`BitTorrentPlugin` (which is specialised to
single-largest-video downloads): any content type, file selection by
:class:`~haven_cli.acquisition.selection.SelectionPolicy`, ``.torrent``
files and magnets. One libtorrent session per process and settings is
shared across plugin instances, so downloads keep progressing in the
daemon between scheduler ticks; in a fresh process, :meth:`status`
re-adds the torrent and libtorrent resumes from data already on disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from haven_cli.acquisition.bencode import BencodeError, magnet_info_hash, parse_torrent
from haven_cli.acquisition.clients import ClientError, ClientStatus, DownloadClient, TorrentPayload
from haven_cli.acquisition.selection import SelectionPolicy, select_torrent_indices

logger = logging.getLogger(__name__)

_SESSIONS: dict[tuple[str, ...], Any] = {}
_SESSION_LOCK = threading.Lock()


def _libtorrent() -> Any:
    try:
        import libtorrent as lt
    except ImportError as exc:  # pragma: no cover - libtorrent is a core dependency
        raise ClientError("libtorrent is not installed", permanent=True) from exc
    return lt


def _session(listen: str, dht: bool, download_rate: int, upload_rate: int) -> Any:
    key = (listen, str(dht), str(download_rate), str(upload_rate))
    with _SESSION_LOCK:
        if key not in _SESSIONS:
            lt = _libtorrent()
            _SESSIONS[key] = lt.session(
                {
                    "listen_interfaces": listen,
                    "enable_dht": dht,
                    "enable_lsd": dht,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                    "download_rate_limit": max(0, download_rate),
                    "upload_rate_limit": max(0, upload_rate),
                    "alert_mask": 0,
                    "user_agent": "haven-cli",
                }
            )
        return _SESSIONS[key]


class InternalTorrentClient(DownloadClient):
    """libtorrent-backed downloads into ``download_dir/<info_hash>/``."""

    name = "internal"
    protocol = "torrent"

    def __init__(
        self,
        download_dir: Path,
        *,
        selection: SelectionPolicy | None = None,
        listen_interfaces: str = "0.0.0.0:0,[::]:0",
        dht: bool = True,
        download_rate_limit: int = 0,
        upload_rate_limit: int = 0,
        seed: bool = False,
    ) -> None:
        self._dir = Path(download_dir)
        self._selection = selection or SelectionPolicy()
        self._listen = listen_interfaces
        self._dht = dht
        self._down = download_rate_limit
        self._up = upload_rate_limit
        self._seed = seed

    def _ses(self) -> Any:
        return _session(self._listen, self._dht, self._down, self._up)

    def _torrent_store(self) -> Path:
        path = self._dir / ".torrents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _add(self, handle: dict[str, Any]) -> Any:
        lt = _libtorrent()
        save_path = Path(handle["save_path"])
        save_path.mkdir(parents=True, exist_ok=True)
        torrent_file = handle.get("torrent_file")
        if torrent_file and Path(torrent_file).is_file():
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(str(torrent_file))
        elif handle.get("magnet"):
            params = lt.parse_magnet_uri(handle["magnet"])
        else:
            raise ClientError(
                "torrent handle has neither a .torrent file nor a magnet URI", permanent=True
            )
        params.save_path = str(save_path)
        return self._ses().add_torrent(params)

    def _find(self, info_hash: str) -> Any:
        lt = _libtorrent()
        found = self._ses().find_torrent(lt.sha1_hash(bytes.fromhex(info_hash)))
        return found if found.is_valid() else None

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        if not isinstance(payload, TorrentPayload):
            raise ClientError("internal torrent client accepts torrents only", permanent=True)
        handle: dict[str, Any] = {"title": title, "label": label}
        if payload.torrent:
            try:
                meta = parse_torrent(payload.torrent)
            except BencodeError as exc:
                raise ClientError(f"invalid .torrent: {exc}", permanent=True) from exc
            if not meta.info_hash:
                raise ClientError("BitTorrent v2-only torrents are not supported", permanent=True)
            info_hash = meta.info_hash
            path = self._torrent_store() / f"{info_hash}.torrent"
            path.write_bytes(payload.torrent)
            handle["torrent_file"] = str(path)
        else:
            info_hash = payload.info_hash or magnet_info_hash(payload.magnet or "") or ""
            if not info_hash:
                raise ClientError("magnet URI has no BitTorrent v1 info-hash", permanent=True)
            handle["magnet"] = payload.magnet
        handle["info_hash"] = info_hash
        handle["save_path"] = str(self._dir / info_hash)
        existing = self._find(info_hash)
        if existing is None:
            await asyncio.to_thread(self._add, handle)
        logger.info("Queued torrent %s (%s)", info_hash, title)
        return handle

    def _apply_selection(self, lt_handle: Any, handle: dict[str, Any]) -> list[int] | None:
        info = lt_handle.torrent_file()
        if info is None:
            return None
        files = info.files()
        entries = [(files.file_path(i), files.file_size(i)) for i in range(files.num_files())]
        chosen = select_torrent_indices(entries, self._selection)
        if not chosen:
            return []
        wanted = set(chosen)
        priorities = [4 if i in wanted else 0 for i in range(len(entries))]
        if list(lt_handle.get_file_priorities()) != priorities:
            lt_handle.prioritize_files(priorities)
        return chosen

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        lt = _libtorrent()
        info_hash = handle.get("info_hash", "")
        lt_handle = self._find(info_hash) if info_hash else None
        if lt_handle is None:
            try:
                lt_handle = await asyncio.to_thread(self._add, handle)
            except ClientError:
                raise
            except Exception as exc:  # noqa: BLE001 - libtorrent raises RuntimeError variants
                raise ClientError(f"cannot resume torrent {info_hash}: {exc}") from exc
            return ClientStatus(state="queued")
        status = lt_handle.status()
        if status.errc.value() != 0:
            return ClientStatus(state="failed", error=status.errc.message())
        if not status.has_metadata:
            return ClientStatus(state="queued")
        chosen = self._apply_selection(lt_handle, handle)
        if chosen == []:
            return ClientStatus(
                state="failed", error="no files in the torrent match the accepted types/sizes"
            )
        finished = (
            status.state in (lt.torrent_status.finished, lt.torrent_status.seeding)
            or status.is_finished
        )
        if not finished:
            return ClientStatus(state="downloading", progress=float(status.progress))
        info = lt_handle.torrent_file()
        save_path = Path(handle["save_path"])
        files = info.files()
        paths = tuple(save_path / files.file_path(i) for i in (chosen or range(files.num_files())))
        return ClientStatus(
            state="completed",
            progress=1.0,
            content_path=save_path / info.name(),
            files=tuple(p for p in paths if p.is_file()),
        )

    async def release(self, handle: dict[str, Any]) -> None:
        if self._seed:
            return
        lt_handle = self._find(handle.get("info_hash", "")) if handle.get("info_hash") else None
        if lt_handle is not None:
            self._ses().remove_torrent(lt_handle)

    async def health(self) -> str:
        # Deliberately does not start a session: a health check must not open
        # listening sockets or join the DHT.
        lt = _libtorrent()
        return f"libtorrent {getattr(lt, '__version__', 'available')}"


def torrent_label(key: str) -> str:
    """Short, stable, client-safe label for a source key."""
    return "haven-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - label only
