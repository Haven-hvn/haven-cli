"""Guarded HTTP downloads for URLs that come from third-party data.

Every hop (including each redirect) is checked with
:func:`~haven_cli.services.url_safety.check_fetch_target`; bodies are
streamed to a ``.part`` file with a hard byte cap and atomically renamed
once complete; the saved file gets an extension matching its sniffed
content so the pipeline's type checks work even when the server lied.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from haven_cli.media.filetype import detect_mime, extension_for_mime, mime_from_extension
from haven_cli.services.url_safety import UnsafeURLError, check_fetch_target, describe_url

DEFAULT_USER_AGENT = "haven-cli/1.0 (+https://github.com/Haven-hvn/haven-cli)"
_CHUNK = 256 * 1024


class FetchError(Exception):
    """Download failure.

    Attributes:
        permanent: ``True`` when retrying cannot help (too large, blocked
            host, 404, ...); ``False`` for network errors, 5xx and 429.
        retry_after: Suggested wait in seconds, when known.
    """

    def __init__(self, message: str, *, permanent: bool, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


@dataclass
class FetchPolicy:
    """Limits applied to one download."""

    max_bytes: int = 2 * 1024**3
    timeout: float = 120.0
    max_redirects: int = 5
    allowed_hosts: tuple[str, ...] = ()
    allow_private_hosts: bool = False
    user_agent: str = DEFAULT_USER_AGENT
    verify: bool | str = True
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Optional httpx transport (in-memory transports in tests).
    transport: httpx.AsyncBaseTransport | None = None


class HostRateLimiter:
    """Enforce a minimum interval between requests to the same host.

    Shared process-wide so parallel archive calls respect it together.
    """

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        host = (urlsplit(url).hostname or "").lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            delay = self._last.get(host, 0.0) + min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


RATE_LIMITER = HostRateLimiter()


@dataclass(frozen=True)
class FetchedFile:
    path: Path
    size: int
    mime: str
    final_url: str
    content_type: str = ""


_UNSAFE_NAME = re.compile(r"[^\w.\- ()\[\]+,]+", re.UNICODE)


def safe_filename(name: str, default: str = "download", max_len: int = 180) -> str:
    """A filesystem-safe single path component derived from *name*."""
    name = unquote(name).replace("/", " ").replace("\\", " ").strip()
    name = _UNSAFE_NAME.sub("_", name).strip(" ._")
    if not name:
        name = default
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def _filename_from_disposition(value: str) -> str | None:
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;]+)", value)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', value)
    return match.group(1).strip() if match else None


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(1, 10_000):
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    raise FetchError(f"cannot find a free file name near {path}", permanent=True)


def _status_error(response: httpx.Response, url: str) -> FetchError:
    status = response.status_code
    retry_after: float | None = None
    header = response.headers.get("retry-after")
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            retry_after = None
    transient = status == 429 or status >= 500 or status == 408
    return FetchError(
        f"HTTP {status} from {describe_url(url)}", permanent=not transient, retry_after=retry_after
    )


async def save_response(
    response: httpx.Response,
    dest_dir: Path,
    *,
    filename_hint: str | None,
    max_bytes: int,
    final_url: str,
) -> FetchedFile:
    """Stream an open (successful) response to *dest_dir* and close it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(
                f"{describe_url(final_url)} is {int(declared)} bytes, "
                f"over the {max_bytes}-byte limit",
                permanent=True,
            )
        disposition = response.headers.get("content-disposition", "")
        name = (
            (_filename_from_disposition(disposition) if disposition else None)
            or filename_hint
            or Path(urlsplit(final_url).path).name
            or "download"
        )
        name = safe_filename(name)
        part = dest_dir / f".{name}.{os.getpid()}.{id(response)}.part"
        written = 0
        try:
            with open(part, "wb") as handle:
                async for chunk in response.aiter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise FetchError(
                            f"{describe_url(final_url)} exceeded the {max_bytes}-byte limit",
                            permanent=True,
                        )
                    handle.write(chunk)
            if written == 0:
                raise FetchError(
                    f"{describe_url(final_url)} returned an empty body", permanent=False
                )
            mime = detect_mime(part)
            wanted_ext = extension_for_mime(mime)
            current = Path(name)
            if (
                wanted_ext
                and mime not in ("application/octet-stream", "text/plain")
                and mime_from_extension(current) != mime
            ):
                name = f"{current.stem if current.suffix else current.name}{wanted_ext}"
            target = _unique(dest_dir / name)
            os.replace(part, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return FetchedFile(
            path=target,
            size=written,
            mime=mime,
            final_url=final_url,
            content_type=response.headers.get("content-type", ""),
        )
    except httpx.HTTPError as exc:
        raise FetchError(
            f"transfer from {describe_url(final_url)} failed: {exc}", permanent=False
        ) from exc
    finally:
        await response.aclose()


async def open_guarded(
    url: str,
    policy: FetchPolicy,
    *,
    client: httpx.AsyncClient | None = None,
    min_interval: float = 0.0,
    stop_at: Iterable[str] = ("magnet",),
) -> tuple[httpx.Response | None, str]:
    """Open a GET, following redirects manually with a check on every hop.

    Returns ``(response, final_url)``. When a redirect points at a scheme
    in *stop_at* (e.g. ``magnet:``), returns ``(None, target)`` instead so
    the caller can hand it to a torrent client. The caller owns and must
    close the returned response.
    """
    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(policy.timeout, connect=min(policy.timeout, 20.0)),
        follow_redirects=False,
        verify=policy.verify,
        headers={"User-Agent": policy.user_agent, **policy.extra_headers},
        transport=policy.transport,
    )
    stop = {s.lower() for s in stop_at}
    current = url
    try:
        for _hop in range(policy.max_redirects + 1):
            if urlsplit(current).scheme.lower() in stop:
                return None, current
            try:
                await check_fetch_target(
                    current,
                    allowed_hosts=policy.allowed_hosts,
                    allow_private=policy.allow_private_hosts,
                )
            except UnsafeURLError as exc:
                raise FetchError(
                    f"blocked fetch to {describe_url(current)}: {exc}", permanent=True
                ) from exc
            await RATE_LIMITER.wait(current, min_interval)
            try:
                response = await http.send(http.build_request("GET", current), stream=True)
            except httpx.TimeoutException as exc:
                raise FetchError(
                    f"timed out fetching {describe_url(current)}", permanent=False
                ) from exc
            except httpx.HTTPError as exc:
                raise FetchError(
                    f"request to {describe_url(current)} failed: {exc}", permanent=False
                ) from exc
            if response.is_redirect:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise FetchError(
                        f"redirect without Location from {describe_url(current)}", permanent=True
                    )
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                error = _status_error(response, current)
                await response.aclose()
                raise error
            if owns:
                # Tie the client's lifetime to the response.
                async def _close_both(
                    original_close: Any = response.aclose, owned: httpx.AsyncClient = http
                ) -> None:
                    try:
                        await original_close()
                    finally:
                        await owned.aclose()

                response.aclose = _close_both  # type: ignore[method-assign]
                owns = False
            return response, current
        raise FetchError(f"too many redirects starting at {describe_url(url)}", permanent=True)
    finally:
        if owns:
            await http.aclose()


async def fetch_to_file(
    url: str,
    dest_dir: Path,
    policy: FetchPolicy,
    *,
    filename_hint: str | None = None,
    min_interval: float = 0.0,
    client: httpx.AsyncClient | None = None,
) -> FetchedFile | str:
    """Download *url* into *dest_dir*.

    Returns the saved :class:`FetchedFile`, or a ``magnet:`` URI string if
    the chain redirected to one.
    """
    response, final_url = await open_guarded(url, policy, client=client, min_interval=min_interval)
    if response is None:
        return final_url
    return await save_response(
        response,
        dest_dir,
        filename_hint=filename_hint,
        max_bytes=policy.max_bytes,
        final_url=final_url,
    )
