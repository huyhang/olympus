from __future__ import annotations

import asyncio

import pytest
from shell_fakes import FakeProvider, MemorySecrets

from olympus.domain import AgentProfile, OllamaDefaults
from olympus.ports import ProviderError
from olympus.registry import AgentRegistry
from olympus.runtimes import RuntimePool

DEFAULTS = OllamaDefaults("http://ollama", "default-model")


def pool_with_provider():
    provider = FakeProvider()
    return RuntimePool(AgentRegistry((provider,)), MemorySecrets()), provider


def agent(identifier: str, model: str = "") -> AgentProfile:
    return AgentProfile(identifier, "fake", identifier.title(), model=model)


def test_a_runtime_is_opened_once_and_cached():
    pool, provider = pool_with_provider()
    first = pool.open(agent("a"), DEFAULTS)
    assert pool.open(agent("a"), DEFAULTS) is first
    assert pool.cached("a") is first
    assert pool.cached("b") is None
    assert provider.opened == [("a", "default-model")]


def test_retired_runtimes_close_only_once_their_replies_end():
    pool, provider = pool_with_provider()
    busy = pool.open(agent("a", "old"), DEFAULTS)
    pool.open(agent("b"), DEFAULTS)
    pool.retire_all()
    assert pool.cached("a") is None
    asyncio.run(pool.close_unused([busy]))
    assert provider.closed == [("b", "default-model")]

    fresh = pool.open(agent("a", "new"), DEFAULTS)
    assert fresh is not busy and fresh.model == "new"
    asyncio.run(pool.close_unused([]))
    assert ("a", "old") in provider.closed
    assert ("a", "new") not in provider.closed

    asyncio.run(pool.close_all())
    assert ("a", "new") in provider.closed
    assert pool.cached("a") is None


def test_an_uninstalled_type_cannot_be_opened():
    pool, _ = pool_with_provider()
    with pytest.raises(ProviderError, match="not installed"):
        pool.open(AgentProfile("x", "missing", "X"), DEFAULTS)
