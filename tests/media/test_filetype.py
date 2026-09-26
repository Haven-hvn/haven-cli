"""Tests for content-based file type detection."""

import zipfile

import pytest

from haven_cli.media.filetype import (
    detect_mime,
    extension_for_mime,
    media_kind,
    mime_matches,
    sniff_bytes,
)
from tests.prowlarr.conftest import no_network  # noqa: F401 - offline guard

pytestmark = pytest.mark.usefixtures("no_network")

TORRENT = b"d8:announce14:http://t/annou4:infod6:lengthi3e4:name1:a12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"


@pytest.mark.parametrize(
    "head,mime",
    [
        (b"%PDF-1.7\n...", "application/pdf"),
        (TORRENT, "application/x-bittorrent"),
        (b'<?xml version="1.0"?>\n<!DOCTYPE nzb><nzb xmlns="x">', "application/x-nzb"),
        (b"<!DOCTYPE html><html>", "text/html"),
        (b"\x89PNG\r\n\x1a\n....", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"\x00\x00\x00\x18ftypisom", "video/mp4"),
        (b"\x00\x00\x00\x18ftypM4A ", "audio/mp4"),
        (b"\x1aE\xdf\xa3....webm", "video/webm"),
        (b"ID3\x04", "audio/mpeg"),
        (b"fLaC", "audio/flac"),
        (b"\x1f\x8b\x08", "application/gzip"),
        (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
        (b"Rar!\x1a\x07\x00", "application/vnd.rar"),
        (b"magnet:?xt=urn:btih:abc", "text/x-magnet"),
        (b"d4:spam4:eggse", None),  # bencoded, but not a torrent
        (b"plain words", None),
    ],
)
def test_sniff_bytes(head, mime):
    assert sniff_bytes(head) == mime


def test_detect_refines_zip_containers(tmp_path):
    epub = tmp_path / "book.bin"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", "<package/>")
    assert detect_mime(epub, use_magic=False) == "application/epub+zip"

    docx = tmp_path / "doc.zip"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w/>")
    assert detect_mime(docx, use_magic=False).endswith("wordprocessingml.document")

    cbz = tmp_path / "comic.zip"
    with zipfile.ZipFile(cbz, "w") as z:
        z.writestr("001.jpg", b"\xff\xd8\xff")
        z.writestr("002.png", b"\x89PNG")
    assert detect_mime(cbz, use_magic=False) == "application/vnd.comicbook+zip"

    plain = tmp_path / "data.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.csv", "1,2")
    assert detect_mime(plain, use_magic=False) == "application/zip"


def test_detect_content_beats_extension(tmp_path):
    lying = tmp_path / "paper.html"
    lying.write_bytes(b"%PDF-1.4 content")
    assert detect_mime(lying, use_magic=False) == "application/pdf"


def test_detect_falls_back_to_extension_then_text(tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("# Title\n")
    assert detect_mime(md, use_magic=False) == "text/markdown"
    unknown = tmp_path / "README"
    unknown.write_text("hello")
    assert detect_mime(unknown, use_magic=False) == "text/plain"
    binary = tmp_path / "blob"
    binary.write_bytes(b"\x00\x01\x02\x03")
    assert detect_mime(binary, use_magic=False) == "application/octet-stream"


@pytest.mark.parametrize(
    "mime,kind",
    [
        ("video/mp4", "video"),
        ("audio/flac", "audio"),
        ("image/png", "image"),
        ("application/pdf", "document"),
        ("application/epub+zip", "document"),
        ("text/plain; charset=utf-8", "text"),
        ("application/json", "text"),
        ("application/zip", "archive"),
        ("application/x-bittorrent", "torrent"),
        ("text/x-magnet", "torrent"),
        ("application/x-nzb", "nzb"),
        ("application/octet-stream", "other"),
        (None, "other"),
    ],
)
def test_media_kind(mime, kind):
    assert media_kind(mime) == kind


def test_mime_matches_kinds_and_globs():
    assert mime_matches("application/pdf", ["document"])
    assert mime_matches("image/webp", ["image/*"])
    assert mime_matches("anything/else", ["*"])
    assert not mime_matches("application/pdf", ["image/*", "video"])
    assert mime_matches("APPLICATION/PDF; x=y", ["application/pdf"])


def test_extension_for_mime():
    assert extension_for_mime("application/pdf") == ".pdf"
    assert extension_for_mime("application/epub+zip") == ".epub"
