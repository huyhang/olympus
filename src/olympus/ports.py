"""Interfaces at Olympus's persistence and provider boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from olympus.domain import (
    AgentDefinition,
    AgentEvent,
    AgentProfile,
    AgentRuntime,
    Candidate,
    Conversation,
    Evidence,
    Identity,
    Message,
    OllamaDefaults,
    Proposal,
)


class OlympusError(RuntimeError):
    """A user-actionable Olympus failure."""


class StoreError(OlympusError):
    """Local state could not be safely read or written."""


class ProviderError(OlympusError):
    """An agent configuration or connection is invalid."""


class ReviewError(OlympusError):
    """A review action the agent refused, explained for the user."""


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


ProposalListener = Callable[[Proposal], None]


class ReviewSession(Protocol):
    """One batch of proposals under review, owned by the agent that made them.

    Every change, including those an action causes, reaches the listener the
    session was opened with as a fresh `Proposal` snapshot. Actions raise
    `ReviewError` when they do not apply to a proposal in its current state.
    """

    async def run(self) -> str:
        """Discover and prepare every proposal; returns once all are prepared.

        The result is a note about the review as a whole, such as a folder
        too large to read in full, or "" when there is nothing to say.
        """
        ...

    async def place(self, proposal_id: str) -> None: ...

    async def skip(self, proposal_id: str) -> None: ...

    async def rename(self, proposal_id: str, filename: str) -> None: ...

    async def choose(self, proposal_id: str, choice: Candidate) -> None: ...

    async def search(self, query: str) -> tuple[Candidate, ...]: ...

    async def close(self) -> None:
        """Withdraw whatever was prepared and not settled.

        Work still in flight is stopped, or withdrawn as soon as it lands.
        """
        ...


class FolderWorkflow(Protocol):
    """An agent's offer to turn the files in a folder into proposals."""

    @property
    def title(self) -> str: ...

    def describe(self, folder: Path, recursive: bool) -> str:
        """A one-line preview of what a review of this folder would cover."""
        ...

    def open(
        self, folder: Path, recursive: bool, listener: ProposalListener
    ) -> ReviewSession: ...
