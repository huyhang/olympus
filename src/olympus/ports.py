"""Interfaces at Olympus's persistence and provider boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from olympus.domain import (
    AgentDefinition,
    AgentEvent,
    AgentProfile,
    AgentRuntime,
    Conversation,
    Evidence,
    Identity,
    Message,
    OllamaDefaults,
)


class OlympusError(RuntimeError):
    """A user-actionable Olympus failure."""


class StoreError(OlympusError):
    """Local state could not be safely read or written."""


class ProviderError(OlympusError):
    """An agent configuration or connection is invalid."""


class SecretStore(Protocol):
    def get(self, agent_id: str, key: str) -> str | None: ...

    def set(self, agent_id: str, key: str, value: str) -> None: ...

    def delete_agent(self, agent_id: str, keys: Sequence[str] = ()) -> None: ...

    def location(self, agent_id: str, key: str) -> str | None:
        """Where a stored value lives, for display; None when it is unset."""
        ...


class AgentSession(Protocol):
    def respond(
        self,
        history: Sequence[Message],
        question: str,
        identity: Identity,
        prior_evidence: bool = False,
    ) -> AsyncIterator[AgentEvent]: ...


class AgentProvider(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    def validate(
        self, profile: AgentProfile, secrets: Mapping[str, str]
    ) -> dict[str, str]: ...

    async def probe(
        self,
        profile: AgentProfile,
        secrets: Mapping[str, str],
        defaults: OllamaDefaults,
    ) -> None: ...

    def open(
        self,
        profile: AgentProfile,
        secrets: SecretStore,
        defaults: OllamaDefaults,
    ) -> AgentRuntime: ...


class ConversationRepository(Protocol):
    def create_conversation(self, title: str = "New conversation") -> Conversation: ...

    def draft_or_create(self) -> Conversation: ...

    def add_message(
        self,
        conversation_id: str,
        message: Message,
        evidence: tuple[Evidence, ...] = (),
    ) -> bool: ...

    def has_evidence(self, conversation_id: str) -> bool: ...

    def messages(self, conversation_id: str) -> list[Message]: ...

    def message_evidence(self, conversation_id: str) -> list[tuple[Evidence, ...]]: ...

    def conversations(self, query: str = "") -> list[Conversation]: ...

    def conversation(self, prefix: str) -> Conversation | None: ...

    def delete_conversation(self, conversation_id: str) -> bool: ...

    def clear_conversations(self) -> int: ...

    def export_markdown(self, conversation_id: str) -> Path: ...
