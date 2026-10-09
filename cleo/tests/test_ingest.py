from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest
from cleo.config import CleoSettings
from cleo.ingest import MAX_RESPONSE_BYTES, NinevehIngestClient, ProgressReader
from cleo.ports import IngestError


def run(awaitable):
    return asyncio.run(awaitable)


def make_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = CleoSettings("http://nineveh.test", "nvh_secret")
    return NinevehIngestClient(settings, http)


def answering(status=200, **response):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, **response)

    return handler, requests


def test_staging_uploads_the_file_with_its_series_and_name():
    handler, requests = answering(201, json={"ingestId": "i1", "state": "staged"})
    client = make_client(handler)
    reported: list[float] = []
    content = io.BytesIO(b"PK" + b"x" * 200_000)

    staged = run(client.stage("pluto", "Pluto v1.cbz", content, reported.append))

    assert staged == {"ingestId": "i1", "state": "staged"}
    request = requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/v1/librarian/ingest")
    assert request.headers["authorization"] == "Bearer nvh_secret"
    body = request.content
    assert b'name="series_id"\r\n\r\npluto' in body
    assert b'name="filename"\r\n\r\nPluto v1.cbz' in body
    assert b'filename="Pluto v1.cbz"' in body and b"PKxxx" in body
    assert reported and reported[-1] == 1.0


def test_commit_sends_a_rename_only_when_there_is_one():
    handler, requests = answering(200, json={"state": "placed"})
    client = make_client(handler)
    run(client.commit("id/1"))
    run(client.commit("id/1", "Pluto 001.cbz"))
    assert requests[0].url.raw_path.endswith(b"/ingest/id%2F1/commit")
    assert json.loads(requests[0].content) == {"filename": None}
    assert json.loads(requests[1].content) == {"filename": "Pluto 001.cbz"}


def test_discard_and_pending_use_their_own_routes():
    handler, requests = answering(204)
    client = make_client(handler)
    assert run(client.discard("abc")) is None
    assert run(client.pending()) == {}
    assert [(r.method, r.url.path) for r in requests] == [
        ("DELETE", "/api/v1/librarian/ingest/abc"),
        ("GET", "/api/v1/librarian/ingest"),
    ]


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {}, "rejected Cleo's credential"),
        (
            403,
            {"detail": "Comics only."},
            "may not do that. Nineveh said: Comics only.",
        ),
        (404, {}, "no longer has that upload"),
        (409, {}, "conflict"),
        (413, {}, "too large"),
        (422, {"detail": [{"msg": "bad"}]}, "could not accept"),
        (500, {}, "HTTP 500"),
    ],
)
def test_refusals_are_explained_and_keep_their_status(status, body, message):
    handler, _ = answering(status, json=body)
    with pytest.raises(IngestError, match=message) as raised:
        run(make_client(handler).commit("abc"))
    assert raised.value.status == status


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.ConnectError("down"), "unavailable"),
        (httpx.ReadTimeout("slow"), "did not respond in time"),
        (httpx.RemoteProtocolError("garbled"), "invalid response"),
    ],
)
def test_transport_failures_become_ingest_errors(failure, message):
    def handler(request):
        raise failure

    with pytest.raises(IngestError, match=message) as raised:
        run(make_client(handler).pending())
    assert raised.value.status is None


@pytest.mark.parametrize(
    "response",
    [
        {"content": b"not json"},
        {"json": ["a", "list"]},
        {"content": b'"' + b"x" * MAX_RESPONSE_BYTES + b'"'},
    ],
)
def test_malformed_answers_are_refused(response):
    handler, _ = answering(200, **response)
    with pytest.raises(IngestError):
        run(make_client(handler).pending())


@pytest.mark.parametrize(
    ("call", "arguments"),
    [
        ("stage", ("", "v.cbz", io.BytesIO(b"PK"))),
        ("stage", ("x" * 65, "v.cbz", io.BytesIO(b"PK"))),
        ("stage", ("pluto", "", io.BytesIO(b"PK"))),
        ("commit", ("",)),
        ("commit", ("abc", "x" * 256)),
        ("discard", ("x" * 201,)),
    ],
)
def test_invalid_arguments_never_reach_nineveh(call, arguments):
    handler, requests = answering(200, json={})
    client = make_client(handler)
    with pytest.raises(IngestError, match="requires a valid"):
        run(getattr(client, call)(*arguments))
    assert requests == []


def test_an_owned_client_is_closed_and_a_borrowed_one_is_not():
    owned = NinevehIngestClient(CleoSettings("http://nineveh.test", "t"))
    run(owned.aclose())
    assert owned._client.is_closed
    handler, _ = answering()
    borrowed = make_client(handler)
    run(borrowed.aclose())
    assert not borrowed._client.is_closed


def test_progress_follows_the_read_position():
    reported: list[float] = []
    reader = ProgressReader(io.BytesIO(b"abcd"), 4, reported.append)
    assert reader.read(2) == b"ab"
    reader.seek(0)
    assert reader.tell() == 0
    reader.read()
    assert reported == [0.5, 1.0]
    with pytest.raises(io.UnsupportedOperation):
        reader.fileno()
