"""Tests for the Prowlarr API client (mock transport)."""

from __future__ import annotations

import json

import httpx
import pytest

from haven_cli.services.prowlarr import (
    ProwlarrClient,
    ProwlarrError,
    parse_indexer,
    parse_release,
)
from tests.prowlarr.fixtures import API_KEY, indexer, release
from tests.prowlarr.conftest import no_network  # noqa: F401 - offline guard

pytestmark = pytest.mark.usefixtures("no_network")


def make_client(handler, base="http://prowlarr.local:9696/prowlarr"):
    return ProwlarrClient(base, API_KEY, timeout=5, transport=httpx.MockTransport(handler))


class TestParsing:
    def test_indexer_capabilities(self):
        ix = parse_indexer(indexer(3, "Books", book=True, tags=[7], privacy="private"))
        assert ix is not None
        assert ix.search_types == ("search", "book")
        assert ix.search_params["book"] == ("q", "author", "title")
        assert ix.tags == (7,) and ix.is_private
        assert parse_indexer({"name": "no id"}) is None

    def test_release_is_key_free_and_typed(self):
        raw = release(1, info_hash="ABCDEF" + "0" * 34, magnet=f"magnet:?xt=urn:btih:{'a' * 40}&tr=x?apikey={API_KEY}")
        rel = parse_release(raw, API_KEY)
        assert rel is not None
        assert API_KEY not in json.dumps(rel.__dict__, default=str)
        assert "apikey" not in (rel.download_url or "").lower()
        assert "link=pdf" in (rel.download_url or "")
        assert rel.info_hash == "abcdef" + "0" * 34
        assert rel.publish_date is not None and rel.size == 2048
        assert rel.grabs is None and rel.imdb_id is None

    def test_unknown_publish_date_and_missing_title(self):
        raw = release(2)
        raw["publishDate"] = "0001-01-01T00:00:00Z"
        rel = parse_release(raw, API_KEY)
        assert rel is not None and rel.publish_date is None
        assert parse_release({"guid": "x"}, API_KEY) is None


class TestRequests:
    async def test_search_query_encoding_and_auth(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=[release(1), {"bad": "row"}])

        async with make_client(handler) as client:
            results = await client.search(
                "deep learning {author:Doe}",
                search_type="book",
                indexer_ids=[1, 2],
                categories=[7000, 7020],
                limit=50,
                offset=100,
            )
        assert len(results) == 1
        req = seen[0]
        assert req.url.path == "/prowlarr/api/v1/search"
        assert req.url.params.get_list("indexerIds") == ["1", "2"]
        assert req.url.params.get_list("categories") == ["7000", "7020"]
        assert req.url.params["type"] == "book"
        assert req.url.params["query"] == "deep learning {author:Doe}"
        assert req.url.params["offset"] == "100"
        assert req.headers["x-api-key"] == API_KEY
        assert "apikey" not in str(req.url).lower()

    async def test_empty_query_omitted(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=[])

        async with make_client(handler) as client:
            await client.search("")
        assert "query" not in seen[0].url.params

    @pytest.mark.parametrize(
        "status,code,transient",
        [(401, "unauthorized", False), (404, "not_found", False), (429, "rate_limited", True), (400, "invalid_request", False), (503, "http_error", True)],
    )
    async def test_error_classification(self, status, code, transient):
        def handler(request):
            return httpx.Response(status, json={"message": f"boom {API_KEY}"}, headers={"Retry-After": "30"})

        async with make_client(handler) as client:
            with pytest.raises(ProwlarrError) as info:
                await client.indexers()
        assert info.value.code == code
        assert info.value.transient is transient
        assert API_KEY not in str(info.value)
        assert info.value.retry_after == 30.0

    async def test_validation_error_detail(self):
        def handler(request):
            return httpx.Response(400, json=[{"propertyName": "q", "errorMessage": "bad query"}])

        async with make_client(handler) as client:
            with pytest.raises(ProwlarrError, match="bad query"):
                await client.search("x")

    async def test_redirect_is_an_error(self):
        def handler(request):
            return httpx.Response(301, headers={"Location": "https://elsewhere.example/api"})

        async with make_client(handler) as client:
            with pytest.raises(ProwlarrError, match="redirect"):
                await client.indexers()

    async def test_network_error_redacted(self):
        def handler(request):
            raise httpx.ConnectError(f"cannot connect {API_KEY}")

        async with make_client(handler) as client:
            with pytest.raises(ProwlarrError) as info:
                await client.system_status()
        assert info.value.code == "network_error" and API_KEY not in str(info.value)

    async def test_grab_body(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={})

        rel = parse_release(release(5, indexer_id=4), API_KEY)
        async with make_client(handler) as client:
            await client.grab(rel, download_client_id=2)
        body = json.loads(seen[0].content)
        assert seen[0].method == "POST"
        assert body == {"guid": rel.guid, "indexerId": 4, "downloadClientId": 2}

    def test_missing_config(self):
        with pytest.raises(ProwlarrError, match="API key"):
            ProwlarrClient("http://x", "")
        with pytest.raises(ProwlarrError, match="base_url"):
            ProwlarrClient("localhost:9696", API_KEY)


class TestProxy:
    def test_proxy_url_rewrites_origin_and_strips_key(self):
        client = ProwlarrClient("https://prowlarr.internal/pr", API_KEY)
        url = f"http://172.17.0.2:9696/pr/12/download?apikey={API_KEY}&link=abc&file=Name"
        mapped = client.proxy_url(url)
        assert mapped == "https://prowlarr.internal/pr/12/download?link=abc&file=Name"
        assert client.proxy_url("https://other.example/file.pdf") is None

    async def test_open_download_refuses_foreign_origin(self):
        client = ProwlarrClient("http://prowlarr.local:9696", API_KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        with pytest.raises(ProwlarrError, match="non-Prowlarr"):
            await client.open_download("https://evil.example/steal")
        await client.aclose()

    async def test_open_download_does_not_follow_redirects(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(301, headers={"Location": "https://files.example/doc.pdf"})

        async with make_client(handler, base="http://prowlarr.local:9696") as client:
            response = await client.open_download(f"http://prowlarr.local:9696/1/download?apikey={API_KEY}&link=x&file=y")
            assert response.status_code == 301
            await response.aclose()
        assert len(seen) == 1
        assert seen[0].headers["x-api-key"] == API_KEY
        assert "apikey" not in str(seen[0].url).lower()
