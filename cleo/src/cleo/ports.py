"""Interfaces at Cleo's I/O boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from olympus.domain import ModelChunk


class CatalogError(RuntimeError):
    """The catalog gateway could not satisfy a read."""


class ModelError(RuntimeError):
    """The chat model could not complete a turn."""


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
