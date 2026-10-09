"""Interfaces at Cleo's I/O boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from cleo.domain import FolderScan, LocalVolume, MatchResult, SeriesOption
from olympus.domain import ModelChunk


class CatalogError(RuntimeError):
    """The catalog gateway could not satisfy a read."""


class ModelError(RuntimeError):
    """The chat model could not complete a turn."""


class IngestError(RuntimeError):
    """Nineveh would not stage, place, or withdraw a volume.

    `status` is Nineveh's HTTP status, when it answered at all.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


Progress = Callable[[float], None]


class IngestGateway(Protocol):
    async def pending(self) -> dict[str, Any]: ...

    async def stage(
        self,
        series_id: str,
        filename: str,
        content: BinaryIO,
        progress: Progress | None = None,
    ) -> dict[str, Any]: ...

    async def commit(
        self, ingest_id: str, filename: str | None = None
    ) -> dict[str, Any]: ...

    async def discard(self, ingest_id: str) -> None: ...


class Inbox(Protocol):
    """The local files a filing session may read: one folder, chosen by the user."""

    def scan(self, folder: Path, recursive: bool) -> FolderScan: ...

    def comic_info(self, volume: LocalVolume) -> str | None: ...

    def open(self, volume: LocalVolume) -> BinaryIO: ...


class TitleGuesser(Protocol):
    async def guess(self, filename: str) -> str | None: ...


class SeriesFinder(Protocol):
    async def match(self, volume: LocalVolume) -> MatchResult: ...

    async def search(self, query: str) -> tuple[SeriesOption, ...]: ...
