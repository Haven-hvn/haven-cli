"""Construct download-client backends from :class:`ProwlarrSettings`."""

from __future__ import annotations

from haven_cli.acquisition.clients import ClientError, DownloadClient
from haven_cli.acquisition.clients.internal_torrent import InternalTorrentClient
from haven_cli.acquisition.clients.qbittorrent import QBittorrentClient
from haven_cli.acquisition.clients.transmission import TransmissionClient
from haven_cli.acquisition.clients.usenet import NZBGetClient, SABnzbdClient
from haven_cli.acquisition.clients.watch import WatchDirectory
from haven_cli.acquisition.selection import SelectionPolicy
from haven_cli.plugins.builtin.prowlarr.settings import ProwlarrSettings, read_secret

BACKENDS = ("internal", "qbittorrent", "transmission", "sabnzbd", "nzbget", "watch")


def _mappings(settings: ProwlarrSettings, client: dict[str, object]) -> tuple[str, ...]:
    own = client.get("path_mappings")
    if isinstance(own, (list, tuple)):
        return tuple(str(m) for m in own)
    return settings.remote_path_mappings


def make_backend(
    name: str, settings: ProwlarrSettings, selection: SelectionPolicy
) -> DownloadClient:
    """Instantiate backend *name* (raises :class:`ClientError` if unusable)."""
    verify = settings.verify
    if name == "internal":
        return InternalTorrentClient(
            settings.download_dir / "torrents",
            selection=selection,
            listen_interfaces=settings.torrent_listen,
            dht=settings.torrent_dht,
            download_rate_limit=settings.torrent_download_rate,
            upload_rate_limit=settings.torrent_upload_rate,
            seed=settings.torrent_seed,
        )
    if name == "qbittorrent":
        cfg = settings.qbittorrent
        return QBittorrentClient(
            str(cfg.get("url", "")),
            username=str(cfg.get("username", "")),
            password=read_secret(cfg, "password"),
            category=str(cfg.get("category", "")),
            save_path=str(cfg.get("save_path", "")),
            path_mappings=_mappings(settings, cfg),
            verify=verify,
        )
    if name == "transmission":
        cfg = settings.transmission
        return TransmissionClient(
            str(cfg.get("url", "")),
            username=str(cfg.get("username", "")),
            password=read_secret(cfg, "password"),
            download_dir=str(cfg.get("download_dir", "")),
            labels=bool(cfg.get("labels", True)),
            path_mappings=_mappings(settings, cfg),
            verify=verify,
        )
    if name == "sabnzbd":
        cfg = settings.sabnzbd
        priority = cfg.get("priority")
        return SABnzbdClient(
            str(cfg.get("url", "")),
            read_secret(cfg, "api_key", default_env="SABNZBD_API_KEY"),
            category=str(cfg.get("category", "")),
            priority=int(priority) if priority is not None else None,
            path_mappings=_mappings(settings, cfg),
            verify=verify,
        )
    if name == "nzbget":
        cfg = settings.nzbget
        return NZBGetClient(
            str(cfg.get("url", "")),
            username=str(cfg.get("username", "")),
            password=read_secret(cfg, "password"),
            category=str(cfg.get("category", "")),
            priority=int(cfg.get("priority", 0) or 0),
            path_mappings=_mappings(settings, cfg),
            verify=verify,
        )
    if name == "watch":
        if settings.watch_dir is None:
            raise ClientError("watch_dir is not configured", config=True)
        return WatchDirectory(settings.watch_dir, settle_seconds=settings.watch_settle)
    raise ClientError(f"unknown download backend {name!r}", config=True)
