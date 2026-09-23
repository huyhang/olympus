from __future__ import annotations

import asyncio

import httpx
import pytest

from cleo.config import Settings
from cleo.nineveh import MAX_RESPONSE_BYTES, CatalogError, NinevehCatalogClient


def run(awaitable):
    return asyncio.run(awaitable)


def make_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings("http://nineveh.test", "nvh_secret")
    return NinevehCatalogClient(settings, http), http


def test_catalog_requests_are_authenticated_gets_to_fixed_paths():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client, http = make_client(handler)

    async def exercise():
        await client.list_libraries()
        await client.search_series(query="Vinland", limit=4)
        await client.get_series("id/with a slash")
        await http.aclose()

    run(exercise())
    assert [request.method for request in requests] == ["GET", "GET", "GET"]
    assert [request.url.path for request in requests] == [
        "/api/v1/librarian/libraries",
        "/api/v1/librarian/series",
        "/api/v1/librarian/series/id/with a slash",
    ]
    assert dict(requests[1].url.params) == {"query": "Vinland", "limit": "4"}
    assert requests[2].url.raw_path.endswith(b"/id%2Fwith%20a%20slash")
    assert all(
        request.headers["authorization"] == "Bearer nvh_secret" for request in requests
    )


def test_arbitrary_query_parameters_are_rejected_before_http():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    client, http = make_client(handler)

    async def exercise():
        with pytest.raises(CatalogError, match="Unsupported"):
            await client.search_series(url="https://example.com")
        await http.aclose()

    run(exercise())
    assert requests == []


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "credential"), (403, "denied"), (404, "find"), (500, "HTTP 500")],
)
def test_catalog_errors_are_actionable(status, message):
    client, http = make_client(lambda request: httpx.Response(status, request=request))

    async def exercise():
        with pytest.raises(CatalogError, match=message):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


def test_ninevehs_own_reason_for_a_refusal_is_passed_on():
    client, http = make_client(
        lambda request: httpx.Response(
            422, json={"detail": "Provide query, or at least one metadata filter"}
        )
    )

    async def exercise():
        with pytest.raises(CatalogError, match="at least one metadata filter"):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


def test_a_structured_reason_is_passed_on_rather_than_dropped():
    client, http = make_client(
        lambda request: httpx.Response(
            422, json={"detail": [{"loc": ["query", "query"], "msg": "too short"}]}
        )
    )

    async def exercise():
        with pytest.raises(CatalogError, match="too short"):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


@pytest.mark.parametrize(
    "body", [{"content": b"not json"}, {"json": ["not", "an", "object"]}, {"json": {}}]
)
def test_a_reasonless_refusal_still_reads_cleanly(body):
    client, http = make_client(lambda request: httpx.Response(403, **body))

    async def exercise():
        with pytest.raises(
            CatalogError, match=r"denied access to that catalog data\.$"
        ):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


def test_a_body_that_is_not_json_at_all_is_refused():
    client, http = make_client(lambda request: httpx.Response(200, content=b"<html>"))

    async def exercise():
        with pytest.raises(CatalogError, match="invalid"):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


def test_non_object_json_is_refused():
    client, http = make_client(lambda request: httpx.Response(200, json=[]))

    async def exercise():
        with pytest.raises(CatalogError, match="invalid"):
            await client.list_libraries()
        await http.aclose()

    run(exercise())


def test_an_unreachable_or_slow_nineveh_is_named_as_such():
    def unreachable(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    async def exercise():
        for handler, message in ((unreachable, "unavailable"), (slow, "in time")):
            client, http = make_client(handler)
            with pytest.raises(CatalogError, match=message):
                await client.list_libraries()
            await http.aclose()

    run(exercise())


def test_an_empty_series_id_never_reaches_the_network():
    requests = []
    client, http = make_client(
        lambda request: requests.append(request) or httpx.Response(200, json={})
    )

    async def exercise():
        for series_id in ("", "x" * 201):
            with pytest.raises(CatalogError, match="valid series identifier"):
                await client.get_series(series_id)
        await http.aclose()

    run(exercise())
    assert requests == []


def test_the_client_closes_only_the_transport_it_created():
    async def exercise():
        borrowed = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        NinevehCatalogClient(Settings("http://nineveh.test", "t"), borrowed)
        owned = NinevehCatalogClient(Settings("http://nineveh.test", "t"))
        await owned.aclose()
        assert not borrowed.is_closed
        await borrowed.aclose()

    run(exercise())


def test_oversized_response_is_not_sent_to_the_model():
    client, http = make_client(
        lambda request: httpx.Response(
            200, content=b'{"value":"' + b"x" * MAX_RESPONSE_BYTES + b'"}'
        )
    )

    async def exercise():
        with pytest.raises(CatalogError, match="unexpectedly large"):
            await client.list_libraries()
        await http.aclose()

    run(exercise())
