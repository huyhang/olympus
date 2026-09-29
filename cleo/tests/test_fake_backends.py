"""The scripted fakes behind `scripts/fake_backends.py`.

They are only worth having while Cleo's real adapters still understand them.
So these drive the demo scenario through the real Ollama and Nineveh clients
over HTTP, and hold every fake catalog response to the vendored contract.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fake_backends import DEFAULT_SCENARIO, FakeBackends, Scenario

from cleo.agent import UNSUPPORTED_REPLY, Librarian
from cleo.config import Settings
from cleo.domain import AgentEvent, Identity
from cleo.nineveh import NinevehCatalogClient
from cleo.ollama import OllamaChatModel
from cleo.tools import ReadOnlyToolRegistry

CONTRACT = (
    Path(__file__).resolve().parent.parent / "contracts" / "librarian-openapi.json"
)


@pytest.fixture(scope="module")
def server():
    backends = FakeBackends(Scenario.load(DEFAULT_SCENARIO), port=0, delay=0)
    threading.Thread(target=backends.serve_forever, daemon=True).start()
    yield backends
    backends.shutdown()
    backends.server_close()


def settings(server: FakeBackends) -> Settings:
    return Settings(nineveh_url=server.url, token="nvh_fake", ollama_url=server.url)


def ask(server: FakeBackends, question: str) -> list[AgentEvent]:
    """One turn of Cleo's real agent and adapters, against the fakes."""

    async def collect():
        model = OllamaChatModel(server.url, "fake")
        catalog = NinevehCatalogClient(settings(server))
        agent = Librarian(model, ReadOnlyToolRegistry(catalog))
        try:
            return [event async for event in agent.respond([], question, Identity())]
        finally:
            await model.aclose()
            await catalog.aclose()

    return asyncio.run(collect())


def evidence(events: list[AgentEvent]) -> list[Any]:
    return [event.evidence for event in events if event.evidence]


def test_a_title_question_streams_an_answer_read_from_the_fake_catalog(server):
    events = ask(server, "What's the latest Vinland Saga volume I have?")
    found = evidence(events)
    assert [item.tool for item in found] == ["search_series", "get_series"]
    assert found[-1].payload["inventory"]["latest"]["filename"] == (
        "Vinland Saga 012.cbz"
    )
    assert len([event for event in events if event.kind == "token"]) > 1
    assert events[-1].text.startswith("The latest Vinland Saga volume you have")


def test_an_ambiguous_title_offers_a_choice_and_either_pick_is_scripted(server):
    choice = ask(server, "latest Saga?")[-1]
    assert choice.kind == "choice"
    assert [candidate.title for candidate in choice.candidates] == [
        "Saga",
        "Vinland Saga",
    ]
    for candidate in choice.candidates:
        # The question CleoApp sends when the user answers with a number.
        picked = ask(server, f'Use the series "{candidate.title}" (id {candidate.id}).')
        assert evidence(picked)[-1].arguments == {"series_id": candidate.id}
        assert picked[-1].kind == "done"


def test_an_author_question_filters_the_fake_catalog(server):
    [found] = evidence(ask(server, "Which series by Urasawa do I have?"))
    assert found.tool == "find_series_by_author"
    assert [item["localName"] for item in found.payload["candidates"]] == [
        "Pluto",
        "20th Century Boys",
    ]


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        ("berserk", "Nineveh denied access to that catalog data. Nineveh said:"),
        ("delete Saga", UNSUPPORTED_REPLY),
        ("broken", "Ollama: model runner has unexpectedly stopped"),
        ("crash", "Ollama returned HTTP 500."),
        ("garbled", "Ollama returned an invalid streaming response."),
        ("tell me a joke", UNSUPPORTED_REPLY),
    ],
)
def test_each_scripted_failure_reaches_cleo_as_its_own_message(server, question, reply):
    events = ask(server, question)
    assert reply in events[-1].text
    assert evidence(events) == []


def missing(value: Any, schema: dict, schemas: dict, path: str = "$") -> list[str]:
    """Paths to the fields the contract requires and `value` lacks."""
    if "$ref" in schema:
        return missing(value, schemas[schema["$ref"].rsplit("/", 1)[1]], schemas, path)
    if "anyOf" in schema:
        return min(
            (missing(value, option, schemas, path) for option in schema["anyOf"]),
            key=len,
        )
    if isinstance(value, dict):
        gaps = [
            f"{path}.{key}" for key in schema.get("required", []) if key not in value
        ]
        for key, child in schema.get("properties", {}).items():
            if key in value:
                gaps += missing(value[key], child, schemas, f"{path}.{key}")
        return gaps
    if isinstance(value, list) and "items" in schema:
        return [
            gap
            for index, item in enumerate(value)
            for gap in missing(item, schema["items"], schemas, f"{path}[{index}]")
        ]
    return []


def test_every_fake_catalog_response_has_the_fields_the_contract_requires(server):
    async def fetch():
        catalog = NinevehCatalogClient(settings(server))
        try:
            return [
                ("LibraryList", await catalog.list_libraries()),
                ("SeriesCandidates", await catalog.search_series(query="saga")),
                ("SeriesCandidates", await catalog.search_series(author="urasawa")),
                *[
                    ("SeriesInventoryDetail", await catalog.get_series(series_id))
                    for series_id in ("vinland-saga", "saga", "pluto")
                ],
            ]
        finally:
            await catalog.aclose()

    schemas = json.loads(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    for name, payload in asyncio.run(fetch()):
        assert missing(payload, schemas[name], schemas) == [], name
