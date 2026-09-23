from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from cleo.ollama import ModelError, OllamaChatModel


def run(awaitable):
    return asyncio.run(awaitable)


def test_streaming_text_and_tool_calls_are_normalized():
    seen_body = None

    def handler(request):
        nonlocal seen_body
        seen_body = json.loads(request.content)
        body = (
            b'{"message":{"content":"Hello "}}\n'
            b'{"message":{"content":"there","tool_calls":[{"function":'
            b'{"name":"search_series","arguments":{"query":"Pluto"}}}]}}\n'
        )
        return httpx.Response(200, content=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaChatModel("http://ollama.test", "granite4.2:8b", http)

    async def collect():
        return [
            chunk
            async for chunk in model.stream_chat(
                [{"role": "user", "content": "hi"}], [{"type": "function"}]
            )
        ]

    chunks = run(collect())
    assert "".join(chunk.content for chunk in chunks) == "Hello there"
    assert chunks[-1].tool_calls[0].name == "search_series"
    assert chunks[-1].tool_calls[0].arguments == {"query": "Pluto"}
    assert seen_body["stream"] is True
    assert seen_body["think"] is False
    assert seen_body["model"] == "granite4.2:8b"
    assert seen_body["tools"] == [{"type": "function"}]
    assert seen_body["options"]["num_predict"] == 1_024
    run(http.aclose())


def drain(handler, match):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaChatModel("http://ollama.test", "model", http)

    async def collect():
        with pytest.raises(ModelError, match=match):
            async for _chunk in model.stream_chat([], []):
                pass
        await http.aclose()

    run(collect())


def test_invalid_stream_is_reported_as_a_model_error():
    drain(lambda request: httpx.Response(200, content=b"not json\n"), "invalid")


def test_a_tool_call_without_an_object_of_arguments_is_refused():
    body = b'{"message":{"tool_calls":[{"function":{"name":"x","arguments":"[]"}}]}}\n'
    drain(lambda request: httpx.Response(200, content=body), "invalid")


def test_an_error_inside_the_stream_is_surfaced():
    drain(
        lambda request: httpx.Response(200, content=b'{"error":"model not found"}\n'),
        "model not found",
    )


def test_blank_keepalive_lines_are_skipped():
    body = b'\n{"message":{"content":"hi"}}\n\n'
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    model = OllamaChatModel("http://ollama.test", "model", http)

    async def collect():
        chunks = [chunk async for chunk in model.stream_chat([], [])]
        await http.aclose()
        return chunks

    assert "".join(chunk.content for chunk in run(collect())) == "hi"


def raises(error):
    def handler(request):
        raise error

    return handler


def test_an_unreachable_ollama_says_how_to_start_it():
    drain(raises(httpx.ConnectError("refused")), "Start it")


def test_a_slow_ollama_is_named_as_a_timeout():
    drain(raises(httpx.ReadTimeout("slow")), "did not respond in time")


def test_an_http_error_from_ollama_reports_its_status():
    drain(lambda request: httpx.Response(500), "HTTP 500")


def test_any_other_transport_failure_still_reads_cleanly():
    drain(raises(httpx.ProtocolError("bad frame")), "connection to Ollama failed")


def test_the_model_closes_only_the_client_it_created():
    borrowed = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    model = OllamaChatModel("http://ollama.test", "model", borrowed)
    run(model.aclose())
    assert not borrowed.is_closed
    run(borrowed.aclose())

    owned = OllamaChatModel("http://ollama.test", "model")
    run(owned.aclose())
