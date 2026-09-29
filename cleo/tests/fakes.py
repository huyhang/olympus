"""In-process stand-ins for Ollama and Nineveh, shared by the test modules.

`scripts/fake_backends.py` is the out-of-process counterpart, for driving the
real application by hand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from cleo.domain import ModelChunk


class ScriptedModel:
    """Plays one list of chunks per request, and records every request."""

    def __init__(self, turns: list[list[ModelChunk]]) -> None:
        self.turns = turns
        self.requests: list[
            tuple[Sequence[dict[str, Any]], Sequence[dict[str, Any]]]
        ] = []

    async def stream_chat(self, messages, tools) -> AsyncIterator[ModelChunk]:
        self.requests.append((list(messages), list(tools)))
        for chunk in self.turns.pop(0):
            yield chunk

    async def aclose(self):
        pass


class FakeCatalog:
    async def list_libraries(self):
        return {"libraries": ["Manga"]}

    async def search_series(self, **filters):
        return {"items": [{"id": "pluto", "title": "Pluto"}], "filters": filters}

    async def get_series(self, series_id):
        return {"id": series_id, "title": "Pluto", "volumes": 8}


class AmbiguousCatalog(FakeCatalog):
    """Nineveh's real title-search shape, with no confident match."""

    async def search_series(self, **filters):
        return {
            "candidates": [
                {"seriesId": "s1", "localName": "Saga"},
                {"seriesId": "s2", "localName": "Vinland Saga"},
            ],
            "confidentMatch": None,
            "ambiguous": True,
        }
