from __future__ import annotations

import asyncio

import pytest

from olympus import registry as registry_module
from olympus.domain import (
    AgentDefinition,
    AgentProfile,
    AgentRuntime,
    ConfigurationField,
)
from olympus.ports import ProviderError
from olympus.registry import AgentRegistry
from olympus.services import AgentService, fresh_profile
from olympus.store import OlympusStore


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, agent_id, key):
        return self.values.get((agent_id, key))

    def set(self, agent_id, key, value):
        self.values[(agent_id, key)] = value

    def delete_agent(self, agent_id, keys=()):
        del keys
        self.values = {
            pair: value for pair, value in self.values.items() if pair[0] != agent_id
        }

    def location(self, agent_id, key):
        return "memory" if (agent_id, key) in self.values else None


class FakeProvider:
    definition = AgentDefinition(
        "fake",
        "Oracle",
        "Answers deterministic questions",
        "O",
        (
            ConfigurationField("endpoint", "Endpoint", default="http://fake.test"),
            ConfigurationField("token", "Token", secret=True),
        ),
    )

    def validate(self, profile, secrets):
        return {} if secrets.get("token") else {"token": "A token is required."}

    async def probe(self, profile, secrets, defaults):
        if secrets["token"] == "bad":
            raise ProviderError("Connection refused.")

    def open(self, profile, secrets, defaults):
        return AgentRuntime(object(), profile.model or defaults.model, "Fake")


def test_registry_and_service_manage_installed_agent_instances(tmp_path):
    store = OlympusStore(tmp_path)
    secrets = MemorySecrets()
    registry = AgentRegistry((FakeProvider(),))
    service = AgentService(store, registry, secrets)
    draft = service.draft("fake")
    assert draft.name == "Oracle"
    assert draft.settings["endpoint"] == "http://fake.test"
    with pytest.raises(ProviderError, match="token"):
        service.save(draft, {})

    saved = service.save(draft, {"token": "good"})
    renamed = service.save(fresh_profile(saved, name="Oracle 2"), {})
    assert renamed.name == "Oracle 2"
    assert secrets.get(saved.id, "token") == "good"
    asyncio.run(service.probe(saved, {"token": "good"}, store.ollama_defaults()))
    with pytest.raises(ProviderError, match="Connection refused"):
        asyncio.run(service.probe(saved, {"token": "bad"}, store.ollama_defaults()))

    assert service.secret_location(saved) == "memory"
    assert service.conversation_count(saved) == 0
    with pytest.raises(ProviderError, match="Ollama URL"):
        service.save(fresh_profile(saved, ollama_url="localhost:11434"), {})
    with pytest.raises(ProviderError, match="not a valid URL"):
        service.save(fresh_profile(saved, ollama_url="http://host:1143x"), {})
    assert service.save(fresh_profile(saved, ollama_url="http://gpu:11434"), {})

    service.remove(saved)
    assert store.agents() == []
    assert secrets.get(saved.id, "token") == "good"
    service.restore(saved)
    assert [agent.id for agent in store.agents()] == [saved.id]
    service.remove(saved, delete_history=True)
    assert store.agents(include_archived=True) == []
    assert secrets.get(saved.id, "token") is None
    store.close()


def test_agents_of_an_uninstalled_type_can_still_be_removed(tmp_path):
    store = OlympusStore(tmp_path)
    secrets = MemorySecrets()
    service = AgentService(store, AgentRegistry(()), secrets)
    ghost = store.save_agent(AgentProfile("ghost", "missing-kind", "Ghost"))
    secrets.set(ghost.id, "token", "left over")
    assert service.secret_location(ghost) is None
    with pytest.raises(ProviderError, match="not installed"):
        service.save(ghost, {})
    service.remove(ghost, delete_history=True)
    assert store.agents(include_archived=True) == []
    assert secrets.get(ghost.id, "token") is None
    store.close()


def test_registry_rejects_duplicates_and_unknown_types():
    with pytest.raises(ValueError, match="unique"):
        AgentRegistry((FakeProvider(), FakeProvider()))
    with pytest.raises(ProviderError, match="not installed"):
        AgentRegistry(()).provider("missing")


def test_registry_discovers_installed_provider_entry_points(monkeypatch):
    class EntryPoint:
        @staticmethod
        def load():
            return FakeProvider

    monkeypatch.setattr(
        registry_module, "entry_points", lambda **kwargs: (EntryPoint(),)
    )
    registry = AgentRegistry.discover()
    assert registry.provider("fake").definition.name == "Oracle"
    assert registry.failures == ()


def test_a_broken_plugin_is_reported_without_blocking_the_rest(monkeypatch):
    class Broken:
        name = "broken"

        @staticmethod
        def load():
            raise ImportError("needs a missing dependency")

    class Working:
        name = "fake"

        @staticmethod
        def load():
            return FakeProvider

    monkeypatch.setattr(
        registry_module, "entry_points", lambda **kwargs: (Broken(), Working())
    )
    registry = AgentRegistry.discover()
    assert registry.installed("fake")
    assert registry.failures == (
        "Agent plugin 'broken' failed: needs a missing dependency",
    )
