"""Bring completed files into Haven's workspace.

Files produced by an external download client usually must stay where
they are (the client keeps seeding or tracks them). Import modes mirror
the *arr convention:

* ``hardlink`` (default) — hard link into the workspace, falling back to
  copy across filesystems. Haven's cleanup step then removes only the link.
* ``copy`` — always copy.
* ``move`` — move (the client loses the files).
* ``inplace`` — use the files where they are. Combine with
  ``cleanup_enabled = false`` or Haven's cleanup deletes the originals.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from haven_cli.acquisition.http_fetch import safe_filename

IMPORT_MODES = ("hardlink", "copy", "move", "inplace")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"no free name near {path}")


def import_files(
    files: Iterable[Path],
    dest_dir: Path,
    *,
    mode: str = "hardlink",
    root: Path | None = None,
) -> list[Path]:
    """Place *files* under *dest_dir* according to *mode*.

    Relative structure below *root* is preserved (each component made
    filesystem-safe). Returns the paths the pipeline should ingest.
    """
    if mode not in IMPORT_MODES:
        raise ValueError(f"import mode must be one of {IMPORT_MODES}, got {mode!r}")
    results: list[Path] = []
    for src in files:
        if mode == "inplace":
            results.append(src)
            continue
        if root is not None and root.is_dir():
            try:
                rel_parts = src.relative_to(root).parts
            except ValueError:
                rel_parts = (src.name,)
        else:
            rel_parts = (src.name,)
        target = dest_dir.joinpath(*(safe_filename(p) for p in rel_parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == src.stat().st_size and target.samefile(src):
            results.append(target)
            continue
        target = _unique(target)
        if mode == "move":
            shutil.move(str(src), str(target))
        elif mode == "hardlink":
            try:
                os.link(src, target)
            except OSError:
                shutil.copy2(src, target)
        else:
            shutil.copy2(src, target)
        results.append(target)
    return results


def apply_path_mappings(path: str, mappings: Iterable[str]) -> str:
    """Translate a download-client path to a local path.

    Each mapping is ``"remote=local"`` (like *arr remote path mappings),
    e.g. ``"/downloads=/mnt/nas/downloads"``. The longest matching remote
    prefix wins.
    """
    best: tuple[str, str] | None = None
    normalized = path.replace("\\", "/")
    for mapping in mappings:
        if "=" not in mapping:
            continue
        remote, local = (part.strip() for part in mapping.split("=", 1))
        remote_norm = remote.replace("\\", "/").rstrip("/")
        if not remote_norm:
            continue
        matches = normalized == remote_norm or normalized.startswith(remote_norm + "/")
        if matches and (best is None or len(remote_norm) > len(best[0])):
            best = (remote_norm, local.rstrip("/\\"))
    if best is None:
        return path
    return best[1] + normalized[len(best[0]) :]
