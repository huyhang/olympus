"""The lifecycle of opened agent runtimes."""

from __future__ import annotations

from collections.abc import Iterable

from olympus.domain import AgentProfile, AgentRuntime, OllamaDefaults
from olympus.ports import SecretStore
from olympus.registry import AgentRegistry


class RuntimePool:
    """One open runtime per agent, plus retired ones still finishing replies.

    After a configuration change every cached runtime is retired: the next
    question opens a fresh one with the new settings, while a reply already
    streaming keeps its runtime until it ends and only then closes it.
    """

    def __init__(self, registry: AgentRegistry, secrets: SecretStore) -> None:
        self._registry = registry
        self._secrets = secrets
        self._open: dict[str, AgentRuntime] = {}
        self._retired: list[AgentRuntime] = []

    def cached(self, agent_id: str) -> AgentRuntime | None:
        return self._open.get(agent_id)

    def open(self, profile: AgentProfile, defaults: OllamaDefaults) -> AgentRuntime:
        """The agent's runtime, opening it on first use. Raises OlympusError."""
        runtime = self._open.get(profile.id)
        if runtime is None:
            provider = self._registry.provider(profile.kind)
            runtime = provider.open(profile, self._secrets, defaults)
            self._open[profile.id] = runtime
        return runtime

    def retire_all(self) -> None:
        self._retired.extend(self._open.values())
        self._open.clear()

    async def close_unused(self, busy: Iterable[AgentRuntime]) -> None:
        """Close retired runtimes that no reply is still using."""
        in_use = {id(runtime) for runtime in busy}
        retired, self._retired = self._retired, []
        for runtime in retired:
            if id(runtime) in in_use:
                self._retired.append(runtime)
            else:
                await _close(runtime)

    async def close_all(self) -> None:
        for runtime in (*self._open.values(), *self._retired):
            await _close(runtime)
        self._open.clear()
        self._retired.clear()


async def _close(runtime: AgentRuntime) -> None:
    for close in runtime.close_async:
        await close()
