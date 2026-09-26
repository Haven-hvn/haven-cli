"""URL hygiene helpers for anything that leaves the process.

Three distinct concerns, kept separate on purpose:

* :func:`redact_text` — replace known secret values (API keys, passwords)
  in free text such as log lines and error messages.
* :func:`public_url` — turn a URL into something safe to *publish*
  (e.g. the ``src`` field of an on-chain Arkiv record): http(s) only, no
  userinfo, no credential-like query parameters, no known secrets.
  Magnet links and other schemes are refused because tracker URLs inside
  them routinely embed per-user passkeys.
* :func:`check_fetch_target` — decide whether an outbound fetch to a URL
  that came from third-party data (an indexer, a redirect) is allowed:
  scheme, optional host allowlist, and a block on loopback / private /
  link-local / reserved addresses to prevent SSRF.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

#: Query parameters treated as credentials (compared case-insensitively,
#: with ``-`` and ``_`` ignored). Covers Prowlarr/*arr API keys and the
#: usual private-tracker passkey spellings.
SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "apikey",
        "key",
        "token",
        "accesstoken",
        "auth",
        "authkey",
        "authtoken",
        "passkey",
        "torrentpass",
        "rsskey",
        "secret",
        "password",
        "pass",
        "sig",
        "signature",
        "uid",
        "session",
        "sessionid",
        "sid",
    }
)


def _norm_param(name: str) -> str:
    return name.replace("-", "").replace("_", "").lower()


def is_sensitive_param(name: str) -> bool:
    """Whether a query parameter name looks like a credential."""
    return _norm_param(name) in SENSITIVE_QUERY_PARAMS


def redact_text(text: str, secrets: Iterable[str | None] = ()) -> str:
    """Replace every occurrence of each non-trivial secret in *text*."""
    out = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, REDACTED)
    return out


def strip_sensitive_params(url: str, secrets: Iterable[str | None] = ()) -> str:
    """Drop credential-like query params and userinfo; keep everything else.

    Unlike :func:`public_url` this accepts any scheme and never returns
    ``None``; it is meant for URLs we still need to *use* (minus secrets).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url, secrets)
    netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not is_sensitive_param(k)
        ]
    )
    rebuilt = urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    return redact_text(rebuilt, secrets)


_TOKEN_SEGMENT = re.compile(r"[A-Za-z0-9_-]{20,}")


def has_token_like_segment(url: str) -> bool:
    """Whether a URL path segment looks like an embedded credential.

    Heuristic: a path segment of 20+ ``[A-Za-z0-9_-]`` characters containing
    both letters and digits (typical passkeys, RSS keys, session ids).
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        return True
    for segment in path.split("/"):
        if (
            _TOKEN_SEGMENT.fullmatch(segment)
            and re.search(r"[A-Za-z]", segment)
            and re.search(r"\d", segment)
        ):
            return True
    return False


def public_url(
    url: str | None,
    secrets: Iterable[str | None] = (),
    *,
    reject_token_paths: bool = False,
) -> str | None:
    """A version of *url* safe to publish, or ``None`` if there is none.

    Only ``http``/``https`` URLs with a host qualify. Userinfo, credential
    query parameters and fragments are removed, and any literal secret left
    anywhere in the URL (e.g. a passkey in the path) disqualifies it.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    secret_values = [s for s in secrets if s and len(s) >= 4]
    netloc = parts.netloc.rsplit("@", 1)[-1]
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not is_sensitive_param(k)
        ]
    )
    rebuilt = urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))
    if any(secret in rebuilt for secret in secret_values):
        return None
    if reject_token_paths and has_token_like_segment(rebuilt):
        return None
    return rebuilt


def describe_url(url: str) -> str:
    """Short, secret-free description of a URL for logs: ``scheme://host/…``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    if parts.scheme == "magnet":
        return "magnet:<…>"
    host = parts.hostname or ""
    return (
        f"{parts.scheme}://{host}/…" if parts.path not in ("", "/") else f"{parts.scheme}://{host}/"
    )


class UnsafeURLError(ValueError):
    """Raised when an outbound fetch target is not allowed."""


def host_matches(host: str, patterns: Iterable[str]) -> bool:
    """Case-insensitive host match; ``example.org`` also matches subdomains.

    A pattern starting with ``*.`` matches subdomains only.
    """
    host = host.lower().rstrip(".")
    for raw in patterns:
        pattern = raw.lower().strip().rstrip(".")
        if not pattern:
            continue
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern or host.endswith("." + pattern):
            return True
    return False


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def check_fetch_target(
    url: str,
    *,
    allowed_hosts: Iterable[str] = (),
    allow_private: bool = False,
    allowed_schemes: Iterable[str] = ("https", "http"),
) -> None:
    """Validate an outbound fetch target derived from untrusted data.

    Raises:
        UnsafeURLError: when the scheme, host or resolved address is not allowed.

    Note:
        Resolution happens here and again inside the HTTP client, so a
        hostile DNS server could still rebind between the two. Combine
        with ``allowed_hosts`` when that matters.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError(f"invalid URL: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in {s.lower() for s in allowed_schemes}:
        raise UnsafeURLError(f"scheme {scheme!r} is not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    allowed = [h for h in allowed_hosts if h]
    if allowed and not host_matches(host, allowed):
        raise UnsafeURLError(f"host {host!r} is not in allowed_hosts")
    if allow_private:
        return
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_is_public(literal):
            raise UnsafeURLError(
                f"address {host} is not public (set allow_private_hosts to permit)"
            )
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise UnsafeURLError(f"cannot resolve {host!r}: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not _ip_is_public(address):
            raise UnsafeURLError(
                f"host {host!r} resolves to non-public address {address} "
                "(set allow_private_hosts to permit)"
            )
