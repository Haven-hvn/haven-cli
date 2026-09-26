"""Download-client backends.

Each backend accepts a torrent (magnet URI or ``.torrent`` bytes) or an
NZB, returns a JSON-serializable *handle*, and later reports status for
that handle. Handles are persisted by
:class:`~haven_cli.acquisition.state.AcquisitionStore`, so every backend
must be able to answer :meth:`DownloadClient.status` in a fresh process.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ClientError(Exception):
    """Backend failure.

    ``permanent``: retrying the same submission cannot help.
    ``config``: the backend itself is misconfigured or unreachable by
    credentials — not the release's fault.
    """

    def __init__(self, message: str, *, permanent: bool = False, config: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent or config
        self.config = config


@dataclass(frozen=True)
class TorrentPayload:
    magnet: str | None = None
    torrent: bytes | None = None
    info_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.magnet and not self.torrent:
            raise ValueError("TorrentPayload needs a magnet URI or torrent bytes")


@dataclass(frozen=True)
class NzbPayload:
    nzb: bytes
    filename: str = "release.nzb"


@dataclass(frozen=True)
class ClientStatus:
    #: queued | downloading | completed | failed | missing
    state: str
    progress: float = 0.0
    content_path: Path | None = None
    #: Exact content files when the backend knows them (e.g. selected torrent files).
    files: tuple[Path, ...] = ()
    error: str = ""

    @property
    def done(self) -> bool:
        return self.state == "completed"

    @property
    def terminal(self) -> bool:
        return self.state in ("completed", "failed", "missing")


class DownloadClient(ABC):
    """A torrent or Usenet download backend."""

    #: Stable identifier stored with handles (e.g. ``"qbittorrent"``).
    name: str = ""
    #: ``"torrent"`` or ``"usenet"``.
    protocol: str = ""

    @abstractmethod
    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        """Queue *payload*; return a persistent handle."""

    @abstractmethod
    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        """Current state of a previously submitted download."""

    async def release(self, handle: dict[str, Any]) -> None:  # noqa: B027 - optional hook
        """Called once files were imported (stop seeding, clear history...)."""

    @abstractmethod
    async def health(self) -> str:
        """Short description of the backend when reachable; raise ClientError otherwise."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""
