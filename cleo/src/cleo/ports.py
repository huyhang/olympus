"""Interfaces at Cleo's I/O boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Protocol

from cleo.domain import Conversation, Evidence, Identity, Message, ModelChunk


class CatalogError(RuntimeError):
    """The catalog gateway could not satisfy a read."""


class ModelError(RuntimeError):
    """The chat model could not complete a turn."""


class StoreError(RuntimeError):
    """Local state could not be safely read or written."""


class CatalogGateway(Protocol):
    async def list_libraries(self) -> dict[str, Any]: ...

    async def search_series(self, **filters: Any) -> dict[str, Any]: ...

    async def get_series(self, series_id: str) -> dict[str, Any]: ...


class ChatModel(Protocol):
    def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[ModelChunk]: ...

    async def aclose(self) -> None: ...


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

    def identity(self) -> Identity: ...

    def save_identity(
        self, name: str, persona: str, response_style: str = "compact"
    ) -> Identity: ...

    def export_markdown(self, conversation_id: str) -> Path: ...

    def close(self) -> None: ...
