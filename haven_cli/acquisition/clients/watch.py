"""Completion detection by watching a directory.

For download clients Haven has no API integration for (Deluge, rTorrent,
Aria2, Download Station, ...), the archiver asks Prowlarr to grab the
release to *its* configured client and then waits for a matching entry to
appear, and stop changing, in the client's completed-downloads directory.

Matching is by normalized release title (case/punctuation-insensitive
prefix match) among entries modified after the grab. This is a heuristic:
point ``watch_dir`` at a directory (or category folder) used only for
Haven grabs to avoid mismatches.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from haven_cli.acquisition.clients import ClientError, ClientStatus, DownloadClient

_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
_TEMP_SUFFIXES = (".part", ".!qb", ".!ut", ".bc!", ".crdownload", ".tmp", ".partial")

# path → (signature, first_seen_stable_at); process-local debounce.
_OBSERVED: dict[str, tuple[tuple[int, int, float], float]] = {}


def normalize_title(title: str) -> str:
    return _NON_ALNUM.sub(" ", title.lower()).strip()


def _signature(path: Path) -> tuple[int, int, float] | None:
    """(file count, total bytes, newest mtime) or ``None`` while incomplete."""
    count = 0
    total = 0
    newest = 0.0
    entries = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    for entry in entries:
        if entry.name.lower().endswith(_TEMP_SUFFIXES):
            return None
        try:
            stat = entry.stat()
        except OSError:
            return None
        count += 1
        total += stat.st_size
        newest = max(newest, stat.st_mtime)
    return (count, total, newest) if count else None


class WatchDirectory(DownloadClient):
    """Pseudo-client: the submission already happened (a Prowlarr grab)."""

    name = "watch"
    protocol = "any"

    def __init__(
        self, watch_dir: Path, *, settle_seconds: float = 60.0, clock_slack: float = 300.0
    ) -> None:
        self._dir = Path(watch_dir)
        self._settle = settle_seconds
        self._slack = clock_slack

    async def submit(self, payload: Any, *, label: str, title: str) -> dict[str, Any]:
        return {"title": title, "label": label, "since": time.time(), "watch_dir": str(self._dir)}

    def _match(self, handle: dict[str, Any]) -> Path | None:
        wanted = normalize_title(str(handle.get("title", "")))
        if not wanted or not self._dir.is_dir():
            return None
        since = float(handle.get("since", 0.0)) - self._slack
        best: tuple[float, Path] | None = None
        for entry in self._dir.iterdir():
            if entry.name.startswith("."):
                continue
            name = normalize_title(entry.stem if entry.is_file() else entry.name)
            if not (
                name == wanted
                or name.startswith(wanted)
                or wanted.startswith(name)
                and len(name) >= 8
            ):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < since:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, entry)
        return best[1] if best else None

    async def status(self, handle: dict[str, Any]) -> ClientStatus:
        if not self._dir.is_dir():
            raise ClientError(f"watch_dir {self._dir} does not exist", permanent=True)
        entry = self._match(handle)
        if entry is None:
            return ClientStatus(state="downloading")
        signature = _signature(entry)
        key = str(entry)
        now = time.monotonic()
        if signature is None:
            _OBSERVED.pop(key, None)
            return ClientStatus(state="downloading")
        previous = _OBSERVED.get(key)
        if previous is None or previous[0] != signature:
            _OBSERVED[key] = (signature, now)
            return ClientStatus(state="downloading", progress=0.99)
        if now - previous[1] < self._settle:
            return ClientStatus(state="downloading", progress=0.99)
        _OBSERVED.pop(key, None)
        return ClientStatus(state="completed", progress=1.0, content_path=entry)

    async def health(self) -> str:
        if not self._dir.is_dir():
            raise ClientError(f"watch_dir {self._dir} does not exist", permanent=True)
        return f"watching {self._dir}"
