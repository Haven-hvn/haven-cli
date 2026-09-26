"""Unit tests for URL hygiene helpers."""

import socket

import pytest

from haven_cli.services.url_safety import (
    UnsafeURLError,
    check_fetch_target,
    describe_url,
    host_matches,
    public_url,
    redact_text,
    strip_sensitive_params,
)
from tests.prowlarr.conftest import no_network  # noqa: F401 - offline guard

pytestmark = pytest.mark.usefixtures("no_network")

KEY = "0123456789abcdef0123456789abcdef"


class TestRedaction:
    def test_redact_text_replaces_every_occurrence(self):
        assert redact_text(f"a {KEY} b {KEY}", [KEY]) == "a [REDACTED] b [REDACTED]"

    def test_short_or_empty_secrets_ignored(self):
        assert redact_text("abc", ["", None, "ab"]) == "abc"

    def test_strip_sensitive_params(self):
        url = f"http://u:p@host:9696/1/download?apikey={KEY}&link=abc&file=x&PassKey=zz"
        out = strip_sensitive_params(url, [KEY])
        assert "apikey" not in out.lower() and "passkey" not in out.lower()
        assert "u:p@" not in out
        assert "link=abc" in out and "file=x" in out
        assert KEY not in out

    def test_strip_keeps_magnet_but_drops_secrets(self):
        out = strip_sensitive_params(f"magnet:?xt=urn:btih:{'a' * 40}&tr=http://t/{KEY}/announce", [KEY])
        assert KEY not in out and out.startswith("magnet:")


class TestPublicUrl:
    def test_accepts_plain_https(self):
        assert public_url("https://Example.org/abs/1?x=1#frag") == "https://Example.org/abs/1?x=1"

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "magnet:?xt=urn:btih:" + "a" * 40,
            "ftp://host/file",
            "javascript:alert(1)",
            "https:///nohost",
        ],
    )
    def test_rejects_non_http(self, url):
        assert public_url(url) is None

    def test_strips_credentials(self):
        out = public_url("https://user:pw@tracker.example/details?id=5&passkey=secret&token=t")
        assert out == "https://tracker.example/details?id=5"

    def test_literal_secret_in_path_disqualifies(self):
        assert public_url(f"https://tracker.example/rss/{KEY}/feed", [KEY]) is None


class TestHostMatching:
    @pytest.mark.parametrize(
        "host,patterns,expected",
        [
            ("example.org", ["example.org"], True),
            ("cdn.example.org", ["example.org"], True),
            ("badexample.org", ["example.org"], False),
            ("example.org", ["*.example.org"], False),
            ("a.example.org", ["*.example.org"], True),
            ("EXAMPLE.org.", ["example.ORG"], True),
        ],
    )
    def test_host_matches(self, host, patterns, expected):
        assert host_matches(host, patterns) is expected


class TestFetchTarget:
    @pytest.mark.parametrize(
        "url",
        ["http://127.0.0.1/x", "http://10.0.0.5/x", "http://[::1]/x", "http://169.254.169.254/latest", "http://0.0.0.0/"],
    )
    async def test_blocks_private_literals(self, url):
        with pytest.raises(UnsafeURLError):
            await check_fetch_target(url)

    async def test_allow_private(self):
        await check_fetch_target("http://127.0.0.1:1/x", allow_private=True)

    async def test_scheme_and_host_allowlist(self):
        with pytest.raises(UnsafeURLError, match="scheme"):
            await check_fetch_target("file:///etc/passwd")
        with pytest.raises(UnsafeURLError, match="allowed_hosts"):
            await check_fetch_target("https://93.184.216.34/", allowed_hosts=["example.org"])

    async def test_hostname_resolving_to_private_is_blocked(self, monkeypatch):
        async def fake_getaddrinfo(self, host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]

        monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", fake_getaddrinfo)
        with pytest.raises(UnsafeURLError, match="non-public"):
            await check_fetch_target("https://internal.example/")

    async def test_hostname_resolving_public_allowed(self, monkeypatch):
        async def fake_getaddrinfo(self, host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", fake_getaddrinfo)
        await check_fetch_target("https://public.example/")


def test_describe_url_hides_path_and_query():
    assert describe_url(f"https://h.example/a/b?apikey={KEY}") == "https://h.example/…"
    assert describe_url("magnet:?xt=urn:btih:abc") == "magnet:<…>"


def test_token_like_path_segments_rejected_when_requested():
    url = "https://tracker.example/rss/a1b2c3d4e5f6a7b8c9d0e1f2/feed"
    assert public_url(url) == url
    assert public_url(url, reject_token_paths=True) is None
    assert public_url("https://docs.example.org/abs/2609.00001v2", reject_token_paths=True)
