"""Async client for the Prowlarr v1 REST API.

Read operations (system status, indexers, tags, download clients, search)
plus the two write-ish operations an archiver needs:

* :meth:`ProwlarrClient.open_download` — fetch a release through Prowlarr's
  download proxy (``/{indexerId}/download?link=…``).
* :meth:`ProwlarrClient.grab` — ask Prowlarr to send a release to one of
  *its* configured download clients (``POST /api/v1/search``).

Security properties:

* The API key is sent only in the ``X-Api-Key`` header and only to the
  configured Prowlarr origin. Prowlarr embeds ``apikey=<key>`` in every
  proxy link it returns; those are stripped at parse time, so no URL held
  by a :class:`ProwlarrRelease` contains the key.
* Redirects are never followed with the key attached. API calls refuse
  redirects outright; proxy downloads return the redirect to the caller.
* Error messages are redacted before they are raised.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from haven_cli.services.url_safety import redact_text, strip_sensitive_params

USER_AGENT = "haven-cli-prowlarr/1.0"

#: Prowlarr search modes (``type`` query parameter).
SEARCH_TYPES = ("search", "tvsearch", "movie", "music", "book")

#: Pseudo indexer ids understood by Prowlarr's search dispatcher.
ALL_USENET_INDEXERS = -1
ALL_TORRENT_INDEXERS = -2

#: Standard Newznab top-level categories (name → id), for config convenience.
STANDARD_CATEGORIES: dict[str, int] = {
    "console": 1000,
    "movies": 2000,
    "audio": 3000,
    "pc": 4000,
    "tv": 5000,
    "xxx": 6000,
    "books": 7000,
    "other": 8000,
}

_PROXY_PATH = re.compile(r"/(?P<indexer>\d+)/download$")


class ProwlarrError(Exception):
    """A classified Prowlarr failure. ``str(err)`` never contains the API key.

    Attributes:
        code: One of ``not_configured``, ``unauthorized``, ``not_found``,
            ``rate_limited``, ``http_error``, ``network_error``, ``timeout``,
            ``bad_response``, ``invalid_request``.
        status: HTTP status code when the failure came from a response.
        retry_after: Seconds to wait, when Prowlarr/indexer supplied it.
    """

    def __init__(
        self,
        message: str,
        code: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        """Whether retrying later may succeed."""
        if self.code in ("rate_limited", "network_error", "timeout"):
            return True
        return self.code == "http_error" and (self.status or 0) >= 500


@dataclass(frozen=True)
class ProwlarrCategory:
    id: int
    name: str


@dataclass(frozen=True)
class ProwlarrIndexer:
    id: int
    name: str
    enabled: bool
    protocol: str
    privacy: str = ""
    definition_name: str = ""
    description: str = ""
    priority: int = 25
    tags: tuple[int, ...] = ()
    supports_search: bool = True
    search_types: tuple[str, ...] = ()
    categories: tuple[ProwlarrCategory, ...] = ()
    #: Supported query tokens per mode, e.g. {"book": ("q", "author")}.
    search_params: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_private(self) -> bool:
        return self.privacy.lower() in ("private", "semiprivate", "semi-private")

    def category_ids(self) -> set[int]:
        return {c.id for c in self.categories}


@dataclass(frozen=True)
class ProwlarrRelease:
    """A normalized search result. No field contains the API key."""

    guid: str
    title: str
    indexer_id: int
    indexer: str
    protocol: str
    publish_date: datetime | None = None
    age_hours: float | None = None
    size: int | None = None
    files: int | None = None
    grabs: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    info_url: str | None = None
    comment_url: str | None = None
    download_url: str | None = None
    magnet_url: str | None = None
    info_hash: str | None = None
    poster_url: str | None = None
    categories: tuple[ProwlarrCategory, ...] = ()
    indexer_flags: tuple[str, ...] = ()
    imdb_id: int | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None

    def category_ids(self) -> set[int]:
        return {c.id for c in self.categories}

    def as_template_fields(self) -> dict[str, Any]:
        """Flat, secret-free fields for title/creator templates."""
        return {
            "title": self.title,
            "guid": self.guid,
            "indexer": self.indexer,
            "indexer_id": self.indexer_id,
            "protocol": self.protocol,
            "publish_date": self.publish_date.isoformat() if self.publish_date else "",
            "year": self.publish_date.year if self.publish_date else "",
            "size": self.size or "",
            "info_hash": self.info_hash or "",
            "categories": ",".join(c.name for c in self.categories),
            "category_ids": ",".join(str(c.id) for c in self.categories),
            "info_url": self.info_url or "",
            "comment_url": self.comment_url or "",
            "imdb_id": self.imdb_id or "",
            "tmdb_id": self.tmdb_id or "",
            "tvdb_id": self.tvdb_id or "",
        }


@dataclass(frozen=True)
class ProwlarrTag:
    id: int
    label: str


@dataclass(frozen=True)
class ProwlarrDownloadClient:
    id: int
    name: str
    protocol: str
    enabled: bool
    implementation: str = ""


# ── Normalization ────────────────────────────────────────────────────────


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _nonneg_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _positive_id(value: Any) -> int | None:
    number = _nonneg_int(value)
    return number if number else None


def _parse_date(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    # Prowlarr uses 0001-01-01 for "unknown".
    return None if parsed.year < 1971 else parsed


def _categories(raw: Any) -> tuple[ProwlarrCategory, ...]:
    out: list[ProwlarrCategory] = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), int)
            and isinstance(item.get("name"), str)
        ):
            out.append(ProwlarrCategory(item["id"], item["name"]))
    return tuple(out)


def parse_indexer(raw: dict[str, Any]) -> ProwlarrIndexer | None:
    """Normalize an ``IndexerResource``; ``None`` if it lacks id or name."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("id"), int)
        or not _text(raw.get("name"))
    ):
        return None
    raw_caps = raw.get("capabilities")
    caps: dict[str, Any] = raw_caps if isinstance(raw_caps, dict) else {}
    supports_search = raw.get("supportsSearch") is not False
    params: dict[str, tuple[str, ...]] = {}
    for mode, key in (
        ("search", "searchParams"),
        ("tvsearch", "tvSearchParams"),
        ("movie", "movieSearchParams"),
        ("music", "musicSearchParams"),
        ("book", "bookSearchParams"),
    ):
        values = caps.get(key)
        if isinstance(values, list) and values:
            params[mode] = tuple(str(v).lower() for v in values)
    search_types = tuple(
        mode
        for mode in SEARCH_TYPES
        if (mode == "search" and supports_search) or mode in params and mode != "search"
    )
    raw_tags = raw.get("tags")
    tags: list[Any] = raw_tags if isinstance(raw_tags, list) else []
    raw_priority = raw.get("priority")
    return ProwlarrIndexer(
        id=raw["id"],
        name=raw["name"],
        enabled=raw.get("enable") is True,
        protocol=(_text(raw.get("protocol")) or "unknown").lower(),
        privacy=(_text(raw.get("privacy")) or "").lower(),
        definition_name=_text(raw.get("definitionName")) or "",
        description=_text(raw.get("description")) or "",
        priority=raw_priority if isinstance(raw_priority, int) else 25,
        tags=tuple(t for t in tags if isinstance(t, int)),
        supports_search=supports_search,
        search_types=search_types,
        categories=_categories(caps.get("categories")),
        search_params=params,
    )


def parse_release(raw: dict[str, Any], api_key: str) -> ProwlarrRelease | None:
    """Normalize a ``ReleaseResource``; ``None`` if it has no title or guid."""
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"))
    guid = _text(raw.get("guid"))
    if not title or not guid:
        return None
    secrets = (api_key,)

    def clean(value: Any) -> str | None:
        text = _text(value)
        return strip_sensitive_params(text, secrets) if text else None

    info_hash = _text(raw.get("infoHash"))
    raw_flags = raw.get("indexerFlags")
    flags: list[Any] = raw_flags if isinstance(raw_flags, list) else []
    raw_indexer_id = raw.get("indexerId")
    return ProwlarrRelease(
        guid=redact_text(guid, secrets),
        title=title,
        indexer_id=raw_indexer_id if isinstance(raw_indexer_id, int) else -1,
        indexer=_text(raw.get("indexer")) or "unknown",
        protocol=(_text(raw.get("protocol")) or "unknown").lower(),
        publish_date=_parse_date(raw.get("publishDate")),
        age_hours=float(raw["ageHours"]) if isinstance(raw.get("ageHours"), (int, float)) else None,
        size=_positive_id(raw.get("size")),
        files=_nonneg_int(raw.get("files")),
        grabs=_nonneg_int(raw.get("grabs")),
        seeders=_nonneg_int(raw.get("seeders")),
        leechers=_nonneg_int(raw.get("leechers")),
        info_url=clean(raw.get("infoUrl")),
        comment_url=clean(raw.get("commentUrl")),
        download_url=clean(raw.get("downloadUrl")),
        magnet_url=clean(raw.get("magnetUrl")),
        info_hash=info_hash.lower() if info_hash else None,
        poster_url=clean(raw.get("posterUrl")),
        categories=_categories(raw.get("categories")),
        indexer_flags=tuple(str(f) for f in flags),
        imdb_id=_positive_id(raw.get("imdbId")),
        tmdb_id=_positive_id(raw.get("tmdbId")),
        tvdb_id=_positive_id(raw.get("tvdbId")),
    )


def _error_detail(body: Any) -> str:
    if isinstance(body, list):
        return "; ".join(
            str(item.get("errorMessage"))
            for item in body
            if isinstance(item, dict) and item.get("errorMessage")
        )
    if isinstance(body, dict):
        parts = [body.get("message"), body.get("description")]
        return " — ".join(str(p) for p in parts if isinstance(p, str) and p)
    return ""


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


# ── Client ───────────────────────────────────────────────────────────────


class ProwlarrClient:
    """Async Prowlarr v1 client. Use as ``async with ProwlarrClient(...)``.

    Args:
        base_url: Prowlarr origin plus any URL base, e.g.
            ``http://localhost:9696`` or ``https://host/prowlarr``.
        api_key: Prowlarr API key (Settings → General → Security).
        timeout: Per-request timeout in seconds. Searches fan out to every
            selected indexer, so allow generous values.
        verify: TLS verification: ``True``, ``False`` or a CA bundle path.
        transport: Optional httpx transport (tests).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 90.0,
        verify: bool | str = True,
        user_agent: str = USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not urlsplit(base_url).scheme or not urlsplit(base_url).netloc:
            raise ProwlarrError(f"Invalid Prowlarr base_url: {base_url!r}", "not_configured")
        if not api_key:
            raise ProwlarrError(
                "Prowlarr API key is not configured (set the api_key_env variable or api_key_file)",
                "not_configured",
            )
        self._base = base_url.rstrip("/")
        parts = urlsplit(self._base)
        self._origin = (parts.scheme.lower(), parts.netloc.lower())
        self._api_key = api_key
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
            verify=verify,
            follow_redirects=False,
            headers={"X-Api-Key": api_key, "Accept": "application/json", "User-Agent": user_agent},
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return self._base

    async def __aenter__(self) -> ProwlarrClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def redact(self, text: str) -> str:
        """Remove the API key from arbitrary text."""
        return redact_text(text, (self._api_key,))

    # ── low level ──

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str | int | float | bool | None]] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            response = await self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            raise ProwlarrError(f"Prowlarr request timed out: {method} {path}", "timeout") from exc
        except httpx.HTTPError as exc:
            raise ProwlarrError(
                self.redact(f"Prowlarr request failed: {method} {path}: {exc}"), "network_error"
            ) from exc

        if response.is_redirect:
            raise ProwlarrError(
                f"Prowlarr answered {method} {path} with a redirect (HTTP {response.status_code}); "
                "check base_url (scheme, host and URL base)",
                "bad_response",
                status=response.status_code,
            )
        if response.status_code >= 400:
            try:
                detail = _error_detail(response.json())
            except ValueError:
                detail = ""
            status = response.status_code
            code = {
                401: "unauthorized",
                403: "unauthorized",
                404: "not_found",
                429: "rate_limited",
            }.get(status, "invalid_request" if status in (400, 409, 422) else "http_error")
            message = f"Prowlarr API error (HTTP {status}) for {method} {path}"
            if detail:
                message += f": {detail}"
            raise ProwlarrError(
                self.redact(message), code, status=status, retry_after=_retry_after(response)
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProwlarrError(
                f"Prowlarr returned a non-JSON body for {method} {path}", "bad_response"
            ) from exc

    # ── read API ──

    async def system_status(self) -> dict[str, Any]:
        """``GET /api/v1/system/status`` (version, URL base, ...)."""
        body = await self._request("GET", "/api/v1/system/status", timeout=min(self._timeout, 20.0))
        if not isinstance(body, dict):
            raise ProwlarrError("Unexpected system/status response", "bad_response")
        return body

    async def indexers(self) -> list[ProwlarrIndexer]:
        """``GET /api/v1/indexer`` — every configured indexer, enabled or not."""
        body = await self._request("GET", "/api/v1/indexer")
        if not isinstance(body, list):
            raise ProwlarrError("Unexpected indexer list response", "bad_response")
        return [ix for ix in (parse_indexer(item) for item in body) if ix is not None]

    async def tags(self) -> list[ProwlarrTag]:
        """``GET /api/v1/tag``."""
        body = await self._request("GET", "/api/v1/tag")
        if not isinstance(body, list):
            raise ProwlarrError("Unexpected tag list response", "bad_response")
        return [
            ProwlarrTag(item["id"], item["label"])
            for item in body
            if isinstance(item, dict)
            and isinstance(item.get("id"), int)
            and isinstance(item.get("label"), str)
        ]

    async def download_clients(self) -> list[ProwlarrDownloadClient]:
        """``GET /api/v1/downloadclient`` — clients Prowlarr can grab to."""
        body = await self._request("GET", "/api/v1/downloadclient")
        if not isinstance(body, list):
            raise ProwlarrError("Unexpected download client list response", "bad_response")
        return [
            ProwlarrDownloadClient(
                id=item["id"],
                name=str(item.get("name", "")),
                protocol=str(item.get("protocol", "")).lower(),
                enabled=item.get("enable") is True,
                implementation=str(item.get("implementation", "")),
            )
            for item in body
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        ]

    async def search(
        self,
        query: str,
        *,
        search_type: str = "search",
        indexer_ids: Iterable[int] = (),
        categories: Iterable[int] = (),
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ProwlarrRelease]:
        """``GET /api/v1/search``.

        An empty *query* asks indexers for their latest releases (RSS mode).
        Newznab-style tokens in the query (``{imdbid:tt123}``,
        ``{season:1}``, ``{author:…}``) are parsed by Prowlarr per mode.
        """
        if search_type not in SEARCH_TYPES:
            raise ProwlarrError(f"Unknown search type {search_type!r}", "invalid_request")
        params: list[tuple[str, str | int | float | bool | None]] = [("type", search_type)]
        if query:
            params.append(("query", query))
        params.extend(("indexerIds", str(i)) for i in indexer_ids)
        params.extend(("categories", str(c)) for c in categories)
        if limit is not None:
            params.append(("limit", str(limit)))
        if offset is not None:
            params.append(("offset", str(offset)))
        body = await self._request("GET", "/api/v1/search", params=params)
        if not isinstance(body, list):
            raise ProwlarrError("Unexpected search response", "bad_response")
        return [r for r in (parse_release(item, self._api_key) for item in body) if r is not None]

    # ── actions ──

    async def grab(self, release: ProwlarrRelease, download_client_id: int | None = None) -> None:
        """Send *release* to a Prowlarr download client (``POST /api/v1/search``).

        Prowlarr only keeps search results for 30 minutes, so grab soon
        after searching (a 404 means "search again").
        """
        body: dict[str, Any] = {"guid": release.guid, "indexerId": release.indexer_id}
        if download_client_id is not None:
            body["downloadClientId"] = download_client_id
        await self._request("POST", "/api/v1/search", json_body=body)

    def proxy_url(self, url: str) -> str | None:
        """Map a Prowlarr download-proxy link onto the configured origin.

        Prowlarr builds proxy links from the Host it was reached by, which
        may differ from ``base_url`` behind Docker or a reverse proxy. The
        path (``{urlBase}/{id}/download``) and query are kept; the origin is
        replaced. Returns ``None`` when *url* is not a proxy link.
        """
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        if not _PROXY_PATH.search(parts.path):
            return None
        base = urlsplit(self._base)
        query = (
            strip_sensitive_params("?" + parts.query, (self._api_key,))[1:] if parts.query else ""
        )
        return urlunsplit((base.scheme, base.netloc, parts.path, query, ""))

    def is_prowlarr_origin(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.scheme.lower(), parts.netloc.lower()) == self._origin

    async def open_download(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        """Open a streaming GET to a Prowlarr proxy link (key in header only).

        Redirects are *not* followed; the caller inspects ``status_code`` /
        ``headers["location"]`` and fetches external targets itself without
        credentials. The caller must ``await response.aclose()``.

        Raises:
            ProwlarrError: when *url* is not on the Prowlarr origin, or the
                request fails at the transport level.
        """
        target = self.proxy_url(url) or url
        if not self.is_prowlarr_origin(target):
            raise ProwlarrError(
                "Refusing to send the Prowlarr API key to a non-Prowlarr origin", "invalid_request"
            )
        try:
            request = self._client.build_request(
                "GET",
                target,
                headers={"Accept": "*/*"},
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
            return await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise ProwlarrError("Prowlarr download request timed out", "timeout") from exc
        except httpx.HTTPError as exc:
            raise ProwlarrError(
                self.redact(f"Prowlarr download request failed: {exc}"), "network_error"
            ) from exc


def resolve_redirect(base: str, location: str) -> str:
    """Absolute redirect target for a ``Location`` header."""
    return urljoin(base, location)


async def sleep_for_retry(error: ProwlarrError, default: float = 5.0, cap: float = 300.0) -> None:
    """Sleep for ``error.retry_after`` (bounded) or *default* seconds."""
    await asyncio.sleep(min(cap, error.retry_after if error.retry_after is not None else default))
