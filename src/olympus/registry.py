"""Registry of installed agent types."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points

from olympus.ports import AgentProvider, ProviderError

# How a broken or incompatible plugin package fails to load. The failure is
# reported to the user rather than taking the whole shell down.
PLUGIN_FAILURES = (ImportError, AttributeError, TypeError, ValueError, RuntimeError)


class AgentRegistry:
    def __init__(
        self, providers: Iterable[AgentProvider], failures: Iterable[str] = ()
    ) -> None:
        provided = tuple(providers)
        self._providers = {provider.definition.kind: provider for provider in provided}
        if len(self._providers) != len(provided):
            raise ValueError("Agent provider kinds must be unique.")
        self.failures = tuple(failures)

    def providers(self) -> tuple[AgentProvider, ...]:
        return tuple(self._providers.values())

    @classmethod
    def discover(cls, builtins: Iterable[AgentProvider] = ()) -> AgentRegistry:
        """Load installed providers while retaining explicit built-in fallbacks.

        A plugin that fails to load is left out and reported in `failures`,
        so one broken package cannot keep every other agent from starting.
        """
        providers = {provider.definition.kind: provider for provider in builtins}
        failures: list[str] = []
        for entry_point in entry_points(group="olympus.agents"):
            try:
                provider = entry_point.load()()
                kind = provider.definition.kind
            except PLUGIN_FAILURES as error:
                failures.append(f"Agent plugin {entry_point.name!r} failed: {error}")
                continue
            providers.setdefault(kind, provider)
        return cls(providers.values(), failures)

    def provider(self, kind: str) -> AgentProvider:
        try:
            return self._providers[kind]
        except KeyError as error:
            raise ProviderError(f"Agent type {kind!r} is not installed.") from error

    def installed(self, kind: str) -> bool:
        return kind in self._providers
