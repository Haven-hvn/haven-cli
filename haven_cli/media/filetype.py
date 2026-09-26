"""Content-based file type detection for arbitrary (non-video) files.

The video pipeline historically trusted file extensions. Files acquired
from third-party indexers often have missing or misleading extensions,
so this module sniffs magic bytes first, then falls back to optional
``python-magic`` and finally to the extension via :mod:`mimetypes`.

It also classifies a MIME type into a coarse *media kind* used for
pipeline routing (skip VLM for non-video, Arkiv group selection) and for
acquisition (a ``.torrent`` or ``.nzb`` is a pointer to content, not the
content itself).
"""

from __future__ import annotations

import fnmatch
import mimetypes
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path

#: Coarse kinds, in the order checked by :func:`media_kind`.
MEDIA_KINDS = (
    "video",
    "audio",
    "image",
    "document",
    "text",
    "archive",
    "torrent",
    "nzb",
    "other",
)

#: Kinds that describe *where* content is, not content. Never archived as-is.
POINTER_KINDS = frozenset({"torrent", "nzb"})

TORRENT_MIME = "application/x-bittorrent"
NZB_MIME = "application/x-nzb"

_DOCUMENT_MIMES = frozenset(
    {
        "application/pdf",
        "application/epub+zip",
        "application/x-mobipocket-ebook",
        "application/vnd.amazon.ebook",
        "image/vnd.djvu",
        "application/postscript",
        "application/rtf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/x-fictionbook+xml",
        "application/vnd.comicbook+zip",
        "application/vnd.comicbook-rar",
        "application/x-cbr",
        "application/x-cbz",
    }
)

_ARCHIVE_MIMES = frozenset(
    {
        "application/zip",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/zstd",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/x-rar-compressed",
        "application/x-iso9660-image",
    }
)

_TEXT_APPLICATION_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-ndjson",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/x-tex",
        "application/x-bibtex",
    }
)

# Extension → MIME for types :mod:`mimetypes` gets wrong or lacks on some
# platforms. Consulted before the stdlib table.
_EXTENSION_MIMES = {
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
    ".azw": "application/vnd.amazon.ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".djvu": "image/vnd.djvu",
    ".djv": "image/vnd.djvu",
    ".fb2": "application/x-fictionbook+xml",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".mkv": "video/x-matroska",
    ".mka": "audio/x-matroska",
    ".flac": "audio/flac",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".ts": "video/mp2t",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".tex": "application/x-tex",
    ".bib": "application/x-bibtex",
    ".ndjson": "application/x-ndjson",
    ".jsonl": "application/x-ndjson",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".zst": "application/zstd",
    ".xz": "application/x-xz",
    ".iso": "application/x-iso9660-image",
    ".torrent": TORRENT_MIME,
    ".nzb": NZB_MIME,
}

_SNIFF_BYTES = 4096

# A bencoded dict whose first (sorted) key is one a .torrent file can start with.
_TORRENT_START = re.compile(
    rb"d(8:announce|13:announce-list|7:comment|10:created by|13:creation date"
    rb"|8:encoding|4:info|8:url-list|5:nodes|9:httpseeds)"
)


def _sniff_zip(path: Path) -> str:
    """Refine a ZIP container into EPUB / OOXML / ODF / CBZ / plain ZIP."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                declared = archive.read("mimetype")[:128].decode("ascii", "ignore").strip()
                if declared:
                    return declared
            lowered = [n.lower() for n in names]
            if "[content_types].xml" in lowered:
                if any(n.startswith("word/") for n in lowered):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if any(n.startswith("xl/") for n in lowered):
                    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if any(n.startswith("ppt/") for n in lowered):
                    return (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    )
            image_ext = (".jpg", ".jpeg", ".png", ".gif", ".webp")
            files = [n for n in lowered if not n.endswith("/")]
            if files and all(n.endswith(image_ext) or n.endswith(".xml") for n in files):
                return "application/vnd.comicbook+zip"
    except (zipfile.BadZipFile, OSError, KeyError):
        pass
    return "application/zip"


def sniff_bytes(head: bytes) -> str | None:
    """MIME type from leading bytes alone, or ``None`` when unrecognized.

    Container formats that need more than the header (ZIP subtypes) are
    reported as their container; use :func:`detect_mime` for files.
    """
    if not head:
        return None
    h = head
    if h.startswith(b"%PDF-"):
        return "application/pdf"
    if h.startswith(b"magnet:?"):
        return "text/x-magnet"
    if _TORRENT_START.match(h):
        return TORRENT_MIME
    stripped = h.lstrip(b"\xef\xbb\xbf \t\r\n")
    if (
        stripped.startswith(b"<?xml")
        or stripped.startswith(b"<!DOCTYPE nzb")
        or stripped.startswith(b"<nzb")
    ):
        lower = stripped[:_SNIFF_BYTES].lower()
        if b"<nzb" in lower:
            return NZB_MIME
        if b"<svg" in lower:
            return "image/svg+xml"
        if b"<html" in lower:
            return "text/html"
        return "application/xml"
    lower_head = stripped[:512].lower()
    if lower_head.startswith(b"<!doctype html") or lower_head.startswith(b"<html"):
        return "text/html"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio/wav"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "video/x-msvideo"
    if h.startswith(b"AT&TFORM") and h[12:15] == b"DJV":
        return "image/vnd.djvu"
    if len(h) >= 68 and h[60:68] == b"BOOKMOBI":
        return "application/x-mobipocket-ebook"
    if h.startswith(b"PK\x03\x04") or h.startswith(b"PK\x05\x06"):
        return "application/zip"
    if h.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if h.startswith(b"BZh"):
        return "application/x-bzip2"
    if h.startswith(b"\xfd7zXZ\x00"):
        return "application/x-xz"
    if h.startswith(b"(\xb5/\xfd"):
        return "application/zstd"
    if h.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if h.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"
    if len(h) >= 262 and h[257:262] == b"ustar":
        return "application/x-tar"
    if h.startswith(b"fLaC"):
        return "audio/flac"
    if h.startswith(b"OggS"):
        return "audio/ogg"
    if h.startswith(b"ID3") or (
        len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0 and h[1] != 0xFF
    ):
        return "audio/mpeg"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm" if b"webm" in h[:64] else "video/x-matroska"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        if brand in (b"qt  ",):
            return "video/quicktime"
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"mif1"):
            return "image/heic"
        return "video/mp4"
    if h.startswith(b"{\\rtf"):
        return "application/rtf"
    if h.startswith(b"%!PS"):
        return "application/postscript"
    if h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"
    return None


def _looks_like_text(head: bytes) -> bool:
    if not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multibyte sequence cut at the sniff boundary is still text.
        if exc.start < len(head) - 4:
            return False
    return True


def mime_from_extension(path: Path | str) -> str | None:
    """MIME type implied by the file extension, or ``None``."""
    suffix = Path(path).suffix.lower()
    if suffix in _EXTENSION_MIMES:
        return _EXTENSION_MIMES[suffix]
    guessed, _ = mimetypes.guess_type(Path(path).name)
    return guessed


def detect_mime(path: Path, *, use_magic: bool = True) -> str:
    """Best-effort MIME type for an arbitrary file.

    Order: magic bytes → ZIP refinement → ``python-magic`` (optional) →
    extension → text heuristic → ``application/octet-stream``.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError:
        return "application/octet-stream"

    sniffed = sniff_bytes(head)
    if sniffed == "application/zip":
        return _sniff_zip(path)
    if sniffed == "application/x-ole-storage":
        by_ext = mime_from_extension(path)
        return by_ext or "application/x-ole-storage"
    if sniffed == "application/xml":
        by_ext = mime_from_extension(path)
        if by_ext and ("xml" in by_ext or by_ext.startswith("text/")):
            return by_ext
        return sniffed
    if sniffed:
        return sniffed

    if use_magic:
        try:
            import magic  # type: ignore[import-not-found]

            found = magic.from_file(str(path), mime=True)
            if isinstance(found, str) and found and found != "application/octet-stream":
                return found
        except Exception:  # noqa: BLE001 - optional dependency, any failure falls through
            pass

    by_ext = mime_from_extension(path)
    if by_ext:
        return by_ext
    if _looks_like_text(head):
        return "text/plain"
    return "application/octet-stream"


def media_kind(mime: str | None) -> str:
    """Coarse kind for a MIME type (one of :data:`MEDIA_KINDS`)."""
    if not mime:
        return "other"
    base = mime.split(";", 1)[0].strip().lower()
    if base == TORRENT_MIME or base == "text/x-magnet":
        return "torrent"
    if base == NZB_MIME:
        return "nzb"
    if base in _DOCUMENT_MIMES:
        return "document"
    if base in _ARCHIVE_MIMES:
        return "archive"
    major = base.split("/", 1)[0]
    if major in ("video", "audio", "image"):
        return major
    if (
        major == "text"
        or base in _TEXT_APPLICATION_MIMES
        or base.endswith("+json")
        or base.endswith("+xml")
    ):
        return "text"
    return "other"


def mime_matches(mime: str | None, patterns: Iterable[str]) -> bool:
    """Whether *mime* matches any pattern.

    A pattern is either a kind name from :data:`MEDIA_KINDS`, ``*``/``any``,
    or a MIME glob such as ``application/pdf`` or ``image/*``.
    """
    base = (mime or "").split(";", 1)[0].strip().lower()
    kind = media_kind(base)
    for raw in patterns:
        pattern = raw.strip().lower()
        if not pattern:
            continue
        if pattern in ("*", "any", "*/*"):
            return True
        if pattern in MEDIA_KINDS:
            if pattern == kind:
                return True
            continue
        if fnmatch.fnmatchcase(base, pattern):
            return True
    return False


def extension_for_mime(mime: str) -> str:
    """A reasonable file extension (with dot) for *mime*, or ``""``."""
    base = mime.split(";", 1)[0].strip().lower()
    for ext, known in _EXTENSION_MIMES.items():
        if known == base:
            return ext
    guessed = mimetypes.guess_extension(base)
    return guessed or ""
