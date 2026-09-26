"""Choose the content files inside a completed download.

Download clients leave behind extras (``.nfo``, samples, par2 sets,
``.torrent`` copies). :func:`select_files` filters a file or directory
down to what should be archived, by type, size and name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from haven_cli.media.filetype import (
    POINTER_KINDS,
    detect_mime,
    media_kind,
    mime_from_extension,
    mime_matches,
)

#: Names skipped by default: samples, par2 repair sets, client temp files.
DEFAULT_EXCLUDE_PATTERNS = (
    r"(^|[\W_])sample([\W_]|$)",
    r"\.par2$",
    r"\.(nfo|sfv|srr|url|lnk|db|ds_store)$",
    r"^\.",
    r"\.(part|!qb|!ut|bc!|crdownload|tmp)$",
)


@dataclass
class SelectionPolicy:
    accept: tuple[str, ...] = ("*",)
    reject: tuple[str, ...] = ()
    min_size: int = 0
    max_size: int = 0  # 0 = unlimited
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS
    #: "all" keeps every match; "largest" keeps only the biggest one.
    mode: str = "all"
    max_files: int = 1000
    _compiled: list[re.Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.exclude_patterns]

    def excluded_name(self, name: str) -> bool:
        return any(p.search(name) for p in self._compiled)

    def accepts_mime(self, mime: str | None) -> bool:
        if media_kind(mime) in POINTER_KINDS:
            return False
        return mime_matches(mime, self.accept) and not mime_matches(mime, self.reject)

    def accepts_name(self, name: str) -> bool | None:
        """Decide from the file name alone; ``None`` when the extension is unknown."""
        if self.excluded_name(Path(name).name):
            return False
        mime = mime_from_extension(name)
        if mime is None:
            return None
        return self.accepts_mime(mime)


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def select_files(root: Path, policy: SelectionPolicy) -> list[Path]:
    """Content files under *root* (a file or directory) that satisfy *policy*.

    Types are sniffed from content, not trusted from extensions.
    """
    chosen: list[tuple[int, Path]] = []
    for path in _iter_files(root):
        if policy.excluded_name(path.name):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < policy.min_size or (policy.max_size and size > policy.max_size):
            continue
        if not policy.accepts_mime(detect_mime(path)):
            continue
        chosen.append((size, path))
        if len(chosen) >= policy.max_files:
            break
    if policy.mode == "largest" and chosen:
        return [max(chosen, key=lambda item: item[0])[1]]
    return [path for _, path in chosen]


def select_torrent_indices(files: Iterable[tuple[str, int]], policy: SelectionPolicy) -> list[int]:
    """Indices of torrent files to download, decided from names and sizes.

    Files whose extension is unknown are included (content is checked
    again after download by :func:`select_files`).
    """
    candidates: list[tuple[int, int]] = []
    for index, (name, size) in enumerate(files):
        if size < policy.min_size or (policy.max_size and size > policy.max_size):
            continue
        verdict = policy.accepts_name(name)
        if verdict is False:
            continue
        candidates.append((size, index))
    if policy.mode == "largest" and candidates:
        return [max(candidates)[1]]
    return sorted(index for _, index in candidates)[: policy.max_files]
