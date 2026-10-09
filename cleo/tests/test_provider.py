"""Cleo's plug-in boundary: validation, probing, and opening real adapters."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from cleo.config import CleoSettings
from cleo.ingest import NinevehIngestClient
from cleo.ports import IngestError
from cleo.provider import CleoProvider
from fake_backends import DEFAULT_SCENARIO, FakeBackends, Scenario

from olympus.domain import TOGGLE_OFF, TOGGLE_ON, AgentProfile, OllamaDefaults
from olympus.ports import ProviderError


class DictSecrets:
    def __init__(self, values):
        self.values = values

    def get(self, agent_id, key):
        return self.values.get((agent_id, key))


@pytest.fixture
def backend():
    server = FakeBackends(Scenario.load(DEFAULT_SCENARIO), port=0, delay=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def cleo_profile(url: str) -> AgentProfile:
    return AgentProfile(
        id="cleo-one", kind="cleo", name="Cleo", settings={"nineveh_url": url}
    )


def test_cleo_provider_validates_probes_and_opens_real_adapters(backend):
    provider = CleoProvider()
    profile = cleo_profile(backend.url)
    secrets = {"nineveh_token": "nvh_fake"}
    assert provider.validate(profile, secrets) == {}
    asyncio.run(provider.probe(profile, secrets, OllamaDefaults(backend.url, "fake")))
    with pytest.raises(ProviderError, match="not installed"):
        asyncio.run(
            provider.probe(profile, secrets, OllamaDefaults(backend.url, "missing"))
        )

    stored = DictSecrets({(profile.id, "nineveh_token"): "nvh_fake"})
    runtime = provider.open(profile, stored, OllamaDefaults(backend.url, "fake"))
    events = asyncio.run(_exercise(runtime))
    assert events[-1].text.startswith("The latest Vinland Saga")
    assert runtime.model == "fake"


def test_cleo_provider_rejects_bad_configuration_and_failed_probes():
    provider = CleoProvider()
    assert set(provider.validate(cleo_profile("not-a-url"), {})) == {
        "nineveh_url",
        "nineveh_token",
    }
    typo = provider.validate(
        cleo_profile("http://localhost:808o"), {"nineveh_token": "t"}
    )
    assert typo == {"nineveh_url": "Nineveh URL is not a valid URL."}
    with pytest.raises(ProviderError, match="token"):
        provider.open(cleo_profile("http://nineveh"), DictSecrets({}), OllamaDefaults())
    with pytest.raises(ProviderError, match="Nineveh"):
        asyncio.run(
            provider.probe(
                cleo_profile("http://127.0.0.1:1"),
                {"nineveh_token": "token"},
                OllamaDefaults("http://127.0.0.1:1", "fake"),
            )
        )


def test_each_cleo_uses_its_own_token_whatever_the_environment_says(monkeypatch):
    monkeypatch.setenv("NINEVEH_TOKEN", "shell-token")
    provider = CleoProvider()
    assert "nineveh_token" in provider.validate(cleo_profile("http://nineveh"), {})
    settings = CleoProvider._settings(
        cleo_profile("http://nineveh"),
        {"nineveh_token": "instance-token"},
        OllamaDefaults(),
    )
    assert settings.authorization == {"Authorization": "Bearer instance-token"}


def test_cleo_runtime_settings_never_reveal_the_token():
    settings = CleoSettings("http://nineveh", "top-secret")
    assert "top-secret" not in repr(settings)
    assert settings.authorization == {"Authorization": "Bearer top-secret"}


async def _exercise(runtime):
    try:
        return [
            event
            async for event in runtime.session.respond(
                [],
                "What's the latest Vinland Saga volume?",
                cleo_profile("").identity,
            )
        ]
    finally:
        await _close(runtime)


async def _close(runtime):
    for callback in runtime.close_async:
        await callback()


def filing_profile(url: str, filing: str = TOGGLE_ON) -> AgentProfile:
    return AgentProfile(
        id="cleo-files",
        kind="cleo",
        name="Cleo",
        settings={"nineveh_url": url, "filing": filing},
    )


@pytest.mark.parametrize(
    ("key", "default"), [("filing", TOGGLE_OFF), ("upload_up_front", TOGGLE_ON)]
)
def test_filing_settings_are_optional_toggles(key, default):
    field = next(f for f in CleoProvider().definition.fields if f.key == key)
    assert (field.kind, field.default, field.required) == ("toggle", default, False)


@pytest.mark.parametrize(
    ("settings", "upfront"),
    [
        ({}, True),
        ({"upload_up_front": TOGGLE_ON}, True),
        ({"upload_up_front": TOGGLE_OFF}, False),
    ],
)
def test_a_filing_cleo_uploads_up_front_unless_told_not_to(backend, settings, upfront):
    profile = filing_profile(backend.url)
    profile = replace(profile, settings={**profile.settings, **settings})
    stored = DictSecrets({("cleo-files", "nineveh_token"): "nvh_fake"})
    runtime = CleoProvider().open(profile, stored, OllamaDefaults(backend.url, "fake"))
    assert runtime.workflow._upfront is upfront
    asyncio.run(_close(runtime))


def test_a_filing_cleo_opens_with_the_filing_board(backend):
    provider = CleoProvider()
    stored = DictSecrets({("cleo-files", "nineveh_token"): "nvh_fake"})
    defaults = OllamaDefaults(backend.url, "fake")
    plain = provider.open(filing_profile(backend.url, TOGGLE_OFF), stored, defaults)
    filing = provider.open(filing_profile(backend.url), stored, defaults)
    assert plain.workflow is None
    assert filing.workflow.title == "File volumes from a folder"
    assert filing.summary.endswith("· filing on")
    # The board's uploads are withdrawn before any client it needs closes.
    assert [close.__qualname__ for close in filing.close_async] == [
        "FilingWorkflow.aclose",
        *(close.__qualname__ for close in plain.close_async),
        "NinevehIngestClient.aclose",
    ]
    asyncio.run(_close(plain))
    asyncio.run(_close(filing))


def test_probing_a_filing_cleo_checks_it_may_upload(backend, monkeypatch):
    provider = CleoProvider()
    secrets = {"nineveh_token": "nvh_fake"}
    defaults = OllamaDefaults(backend.url, "fake")
    asyncio.run(provider.probe(filing_profile(backend.url), secrets, defaults))

    for failure, message in (
        (IngestError("refused", 403), "needs ingest:stage"),
        (IngestError("Nineveh is unavailable."), "Filing is on, but Nineveh"),
    ):

        async def refuse(self, failure=failure):
            raise failure

        monkeypatch.setattr(NinevehIngestClient, "pending", refuse)
        with pytest.raises(ProviderError, match=message):
            asyncio.run(provider.probe(filing_profile(backend.url), secrets, defaults))
