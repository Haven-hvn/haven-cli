"""Acquisition layer: turn a release pointer into local files.

Given something an indexer returned — a direct URL, a ``.torrent``, a
magnet link or an ``.nzb`` — this package fetches or hands it to a
download client and reports the resulting local files. It knows nothing
about Prowlarr search semantics or the Haven pipeline; the Prowlarr
plugin composes it.

Modules:
    bencode       minimal bencode decoder + torrent/magnet helpers
    http_fetch    SSRF-guarded, size-capped streaming downloads
    selection     choosing content files inside a completed download
    importer      hardlink/copy/move completed files into Haven's workspace
    state         persistent store for in-flight acquisitions
    clients/      torrent + usenet download-client backends
"""
