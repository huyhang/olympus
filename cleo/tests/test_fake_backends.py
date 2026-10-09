"""The scripted fakes behind `scripts/fake_backends.py`.

They are only worth having while Cleo's real adapters still understand them.
So these drive the demo scenario through the real Ollama and Nineveh clients
over HTTP, and hold every fake catalog response to the vendored contract.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from cleo.agent import UNSUPPORTED_REPLY, Librarian
from cleo.config import CleoSettings
from cleo.domain import AgentEvent, Identity
from cleo.ingest import NinevehIngestClient
from cleo.nineveh import NinevehCatalogClient
from cleo.ollama import OllamaChatModel
from cleo.ports import IngestError
from cleo.provider import CleoProvider
from cleo.tools import ReadOnlyToolRegistry
from fake_backends import DEFAULT_INBOX, DEFAULT_SCENARIO, FakeBackends, Scenario

from olympus.domain import AgentProfile, OllamaDefaults, Proposal

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


def settings(server: FakeBackends) -> CleoSettings:
    return CleoSettings(nineveh_url=server.url, token="nvh_fake", ollama_url=server.url)


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
        # The question Olympus sends when the user answers with a number.
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


@pytest.fixture
def fresh():
    """A server of one's own, for tests that leave uploads behind."""
    backends = FakeBackends(Scenario.load(DEFAULT_SCENARIO), port=0, delay=0)
    threading.Thread(target=backends.serve_forever, daemon=True).start()
    yield backends
    backends.shutdown()
    backends.server_close()


def fixture_bytes(name: str) -> io.BytesIO:
    return io.BytesIO((DEFAULT_INBOX / name).read_bytes())


def test_every_fake_ingest_response_has_the_fields_the_contract_requires(fresh):
    async def exercise():
        ingest = NinevehIngestClient(settings(fresh))
        try:
            seeded = await ingest.pending()
            staged = await ingest.stage(
                "vinland-saga",
                "vinland_saga_v12.cbz",
                fixture_bytes("vinland_saga_v12.cbz"),
            )
            spare = await ingest.stage(
                "pluto", "pluto_v08.cbz", fixture_bytes("extras/pluto_v08.cbz")
            )
            pending = await ingest.pending()
            placed = await ingest.commit(staged["ingestId"], "Vinland Saga 012 (2).cbz")
            await ingest.discard(spare["ingestId"])
            return seeded, staged, pending, placed, await ingest.pending()
        finally:
            await ingest.aclose()

    seeded, staged, pending, placed, after = asyncio.run(exercise())
    schemas = json.loads(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    for name, payload in (
        ("StagedIngest", staged),
        ("PendingIngests", pending),
        ("PlacedIngest", placed),
    ):
        assert missing(payload, schemas[name], schemas) == [], name
    assert staged["duplicateOf"]["filename"] == "Vinland Saga 012.cbz"
    assert placed["relativePath"] == "Manga/Vinland Saga/Vinland Saga 012 (2).cbz"
    assert len(pending["pending"]) == len(seeded["pending"]) + 2
    assert after == seeded


@pytest.mark.parametrize(
    ("series_id", "content", "status"),
    [
        ("pluto", b"not a zip", 422),
        ("no-such-series", None, 404),
        ("berserk", None, 403),
    ],
)
def test_the_fake_refuses_uploads_nineveh_would(fresh, series_id, content, status):
    async def exercise():
        ingest = NinevehIngestClient(settings(fresh))
        upload = io.BytesIO(content) if content else fixture_bytes("Berserk v42.cbz")
        try:
            await ingest.stage(series_id, "x.cbz", upload)
        finally:
            await ingest.aclose()

    with pytest.raises(IngestError) as raised:
        asyncio.run(exercise())
    assert raised.value.status == status


def test_a_stage_only_scenario_refuses_to_place(fresh):
    fresh.scenario.allow_commit = False

    async def exercise():
        ingest = NinevehIngestClient(settings(fresh))
        try:
            staged = await ingest.stage(
                "pluto", "pluto_v08.cbz", fixture_bytes("extras/pluto_v08.cbz")
            )
            with pytest.raises(IngestError) as raised:
                await ingest.commit(staged["ingestId"])
            with pytest.raises(IngestError, match="no longer has"):
                await ingest.discard("never-staged")
            with pytest.raises(IngestError, match="no longer has"):
                await ingest.commit("never-staged")
            return raised.value.status, await ingest.pending()
        finally:
            await ingest.aclose()

    seeded = len(fresh.scenario.pending()["pending"])
    status, pending = asyncio.run(exercise())
    assert status == 403 and len(pending["pending"]) == seeded + 1


def test_unknown_or_unauthenticated_ingest_requests_are_refused(fresh):
    with httpx.Client(base_url=fresh.url) as client:
        assert client.delete("/api/v1/librarian/ingest/x").status_code == 401
        auth = {"Authorization": "Bearer nvh_fake"}
        assert (
            client.delete("/api/v1/librarian/ingest/x/commit", headers=auth).status_code
            == 404
        )
        assert client.post("/api/v1/librarian/other", headers=auth).status_code == 404
        assert client.post("/api/v1/librarian/ingest").status_code == 401


def test_the_fixture_inbox_exercises_every_kind_of_proposal(fresh):
    async def exercise():
        profile = AgentProfile(
            id="cleo",
            kind="cleo",
            name="Cleo",
            settings={"nineveh_url": fresh.url, "filing": "on"},
        )
        secrets = type("S", (), {"get": lambda self, agent, key: "nvh_fake"})()
        runtime = CleoProvider().open(
            profile, secrets, OllamaDefaults(fresh.url, "fake")
        )
        latest: dict[str, Proposal] = {}
        session = runtime.workflow.open(
            DEFAULT_INBOX, True, lambda p: latest.update({p.id: p})
        )
        try:
            await session.run()
            await session.close()
        finally:
            for close in runtime.close_async:
                await close()
        return latest

    seeded = fresh.scenario.pending()
    latest = asyncio.run(exercise())
    queued = [item for name, item in latest.items() if name.startswith("queue:")]
    assert [(item.source, item.state, item.carried_over) for item in queued] == [
        ("vinland_saga_v15.cbz", "ready", True)
    ]
    states = {
        name: (item.state, bool(item.warning))
        for name, item in latest.items()
        if not name.startswith("queue:")
    }
    assert states == {
        "[Digital] Vinland.Saga.v14 (2024) (1r0n).cbz": ("ready", False),
        "Berserk v42.cbz": ("failed", False),
        "extras/pluto_v08.cbz": ("ready", True),
        "mystery_scan.cbz": ("needs_choice", False),
        "PLT_Urasawa_TZK_09.cbz": ("ready", False),
        "saga_v11.cbz": ("needs_choice", False),
        "scan_0042.cbz": ("ready", False),
        "vinland_saga_v12.cbz": ("ready", True),
        "Vinland_Saga_v13.cbz": ("ready", False),
    }
    assert latest["PLT_Urasawa_TZK_09.cbz"].subject_note.endswith(
        "suggested by the model"
    )
    assert latest["scan_0042.cbz"].subject.title == "20th Century Boys"
    # The volume Nineveh already held is taken over, not uploaded again.
    assert latest["Vinland_Saga_v13.cbz"].carried_over
    assert latest["saga_v11.cbz"].suggestion.title == "Saga"
    # Leaving withdrew what the review staged and kept what it found waiting.
    assert fresh.scenario.pending() == seeded


def test_asking_to_file_offers_the_board(server):
    async def collect():
        model = OllamaChatModel(server.url, "fake")
        catalog = NinevehCatalogClient(settings(server))
        agent = Librarian(model, ReadOnlyToolRegistry(catalog, filing=True))
        try:
            question = "Please file the volumes in my downloads"
            return [event async for event in agent.respond([], question, Identity())]
        finally:
            await model.aclose()
            await catalog.aclose()

    offer = asyncio.run(collect())[-1]
    assert offer.kind == "action"
    assert offer.action.argument == "cleo/scripts/scenarios/inbox"
