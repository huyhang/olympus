"""Small application services used by the TUI and tests."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from olympus.domain import AgentProfile, OllamaDefaults, utc_now
from olympus.ports import ProviderError, SecretStore
from olympus.registry import AgentRegistry
from olympus.store import OlympusStore
from olympus.validation import http_url_problem


class AgentService:
    def __init__(
        self, store: OlympusStore, registry: AgentRegistry, secrets: SecretStore
    ) -> None:
        self.store = store
        self.registry = registry
        self.secrets = secrets

    def draft(self, kind: str) -> AgentProfile:
        definition = self.registry.provider(kind).definition
        settings = {
            field.key: field.default for field in definition.fields if not field.secret
        }
        return AgentProfile(
            id=str(uuid4()), kind=kind, name=definition.name, settings=settings
        )

    def save(
        self, profile: AgentProfile, supplied_secrets: Mapping[str, str]
    ) -> AgentProfile:
        self._validate(profile, supplied_secrets)
        saved = self.store.save_agent(profile)
        for key, value in supplied_secrets.items():
            if value:
                self.secrets.set(saved.id, key, value)
        return saved

    async def probe(
        self,
        profile: AgentProfile,
        supplied_secrets: Mapping[str, str],
        defaults: OllamaDefaults,
    ) -> None:
        effective_secrets = self._validate(profile, supplied_secrets)
        await self.registry.provider(profile.kind).probe(
            profile, effective_secrets, defaults
        )

    def remove(self, profile: AgentProfile, delete_history: bool = False) -> None:
        """Archive, or delete with history and credentials.

        The agent is gone from the store before credentials are touched, so a
        Keychain failure raised here never leaves a half-deleted agent behind.
        """
        if not delete_history:
            self.store.archive_agent(profile.id)
            return
        self.store.delete_agent(profile.id)
        self.secrets.delete_agent(profile.id, self._secret_keys(profile))

    def conversation_count(self, profile: AgentProfile) -> int:
        return len(self.store.for_agent(profile.id).conversations())

    def secret_location(self, profile: AgentProfile) -> str | None:
        """Where the agent's first credential lives, for display."""
        keys = self._secret_keys(profile)
        return self.secrets.location(profile.id, keys[0]) if keys else None

    def restore(self, profile: AgentProfile) -> None:
        self.store.restore_agent(profile.id)

    def _validate(
        self, profile: AgentProfile, supplied: Mapping[str, str]
    ) -> dict[str, str]:
        _check_ollama_override(profile)
        effective = self._effective_secrets(profile, supplied)
        errors = self.registry.provider(profile.kind).validate(profile, effective)
        if errors:
            raise ProviderError(next(iter(errors.values())))
        return effective

    def _effective_secrets(
        self, profile: AgentProfile, supplied: Mapping[str, str]
    ) -> dict[str, str]:
        return {
            key: supplied.get(key) or self.secrets.get(profile.id, key) or ""
            for key in self._secret_keys(profile)
        }

    def _secret_keys(self, profile: AgentProfile) -> tuple[str, ...]:
        """The provider's secret fields; none when its type is not installed."""
        if not self.registry.installed(profile.kind):
            return ()
        fields = self.registry.provider(profile.kind).definition.fields
        return tuple(field.key for field in fields if field.secret)


def _check_ollama_override(profile: AgentProfile) -> None:
    """A blank override inherits the default; anything else must be a URL."""
    if not profile.ollama_url.strip():
        return
    problem = http_url_problem(profile.ollama_url, "Ollama URL")
    if problem:
        raise ProviderError(problem)


def fresh_profile(profile: AgentProfile, **changes: object) -> AgentProfile:
    values = {
        "id": profile.id,
        "kind": profile.kind,
        "name": profile.name,
        "persona": profile.persona,
        "response_style": profile.response_style,
        "ollama_url": profile.ollama_url,
        "model": profile.model,
        "settings": profile.settings,
        "archived": profile.archived,
        "created_at": profile.created_at,
        "updated_at": utc_now(),
    }
    values.update(changes)
    return AgentProfile(**values)
