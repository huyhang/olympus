"""In-process stand-ins for Ollama and Nineveh, shared by the test modules.

`scripts/fake_backends.py` is the out-of-process counterpart, for driving the
real application by hand.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from cleo.domain import FolderScan, LocalVolume, ModelChunk


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


class MemoryInbox:
    """A folder of volumes held in memory: name → (bytes, ComicInfo.xml or None)."""

    def __init__(self, files=None, problems=None, limit=""):
        self.files = files or {}
        self.problems = problems or {}
        self.limit = limit
        self.scanned = []

    def scan(self, folder, recursive):
        self.scanned.append((folder, recursive))
        volumes = tuple(
            LocalVolume(
                Path(folder) / name, name, len(data), self.problems.get(name, "")
            )
            for name, (data, _) in self.files.items()
        )
        return FolderScan(volumes, self.limit)

    def comic_info(self, volume):
        return self.files[volume.relative][1]

    def open(self, volume):
        if volume.relative.startswith("unreadable"):
            raise PermissionError(13, "Permission denied")
        return io.BytesIO(self.files[volume.relative][0])


class RecordingIngest:
    """Stages under predictable IDs; `refuse` maps a call to the IngestError it raises."""

    def __init__(self, refuse=None):
        self.calls = []
        self.refuse = refuse or {}
        self.counter = 0

    async def pending(self):
        return {"pending": []}

    async def stage(self, series_id, filename, content, progress=None):
        self.calls.append(("stage", series_id, filename))
        self._maybe_refuse("stage", series_id)
        data = content.read()
        if progress:
            progress(0.5)
            progress(1.0)
        self.counter += 1
        return {
            "ingestId": f"up-{self.counter}",
            "state": "staged",
            "seriesId": series_id,
            "filename": filename,
            "suggestedFilename": f"{series_id.title()} 001.cbz",
            "siblingPattern": f"{series_id.title()} NNN.cbz",
            "targetPath": f"Manga/{series_id.title()}/{series_id.title()} 001.cbz",
            "size": len(data),
            "pageCount": 3,
            "duplicateOf": None,
        }

    async def commit(self, ingest_id, filename=None):
        self.calls.append(("commit", ingest_id, filename))
        self._maybe_refuse("commit", ingest_id)
        return {
            "ingestId": ingest_id,
            "state": "placed",
            "relativePath": f"Manga/Placed/{filename or 'Suggested 001.cbz'}",
        }

    async def discard(self, ingest_id):
        self.calls.append(("discard", ingest_id))
        self._maybe_refuse("discard", ingest_id)

    def _maybe_refuse(self, call, key):
        failure = self.refuse.get(call)
        if failure is not None:
            raise failure


class TitleCatalog(FakeCatalog):
    """Answers title searches from a table: query (casefolded) → payload."""

    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    async def search_series(self, **filters):
        self.queries.append(filters["query"])
        answer = self.answers.get(filters["query"].casefold())
        if isinstance(answer, Exception):
            raise answer
        return answer or {"candidates": [], "confidentMatch": None, "ambiguous": False}


def title_match(series_id, title, library="Manga", score=1.0, count=8):
    return {
        "seriesId": series_id,
        "localName": title,
        "library": library,
        "score": score,
        "matchedOn": "localName",
        "publicationCount": count,
    }
