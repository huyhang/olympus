"""Recognising volumes Nineveh already holds in its approval queue.

Nineveh lists each staged upload with its size and sha256, so a file is
matched to its upload by content: never by name, which the user may change.
"""

from __future__ import annotations

import hashlib
import posixpath
from typing import Any, BinaryIO

from olympus.domain import Candidate
from olympus.presentation import format_timestamp, plain_text

CHUNK = 1024 * 1024


class Fingerprint:
    """A file that hashes what is read from it, as an upload reads it."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._hash = hashlib.sha256()
        self.length = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self._hash.update(chunk)
        self.length += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self._stream.seek(offset, whence)
        if position == 0:
            # A transport that rewinds reads the file again from the start.
            self._hash = hashlib.sha256()
            self.length = 0
        return position

    def tell(self) -> int:
        return self._stream.tell()

    def fileno(self) -> int:
        return self._stream.fileno()

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


def sha256_of(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def records(payload: Any) -> list[dict[str, Any]]:
    """The staged uploads in Nineveh's answer to a pending-queue request."""
    found = payload.get("pending") if isinstance(payload, dict) else None
    items = found if isinstance(found, list) else []
    return [item for item in items if isinstance(item, dict) and item.get("ingestId")]


def find(
    queue: list[dict[str, Any]],
    sha256: str,
    size: int,
    series_id: str | None = None,
    taken: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """The first upload with this content, optionally under one series."""
    return next(
        (
            item
            for item in queue
            if item.get("sha256") == sha256
            and item.get("size") == size
            and (series_id is None or item.get("seriesId") == series_id)
            and str(item["ingestId"]) not in taken
        ),
        None,
    )


def queued_subject(record: dict[str, Any]) -> Candidate:
    """The series an upload waits under: its target folder, as Nineveh named it."""
    series_id = str(record.get("seriesId") or "")
    folder = posixpath.basename(posixpath.dirname(str(record.get("targetPath") or "")))
    return Candidate(series_id, plain_text(folder) or series_id)


def queued_note(record: dict[str, Any]) -> str:
    target = plain_text(str(record.get("targetPath") or ""))
    library = target.split("/", 1)[0] if "/" in target else ""
    created = str(record.get("createdAt") or "")
    when = f"uploaded {format_timestamp(created)}" if created else "uploaded earlier"
    return " · ".join(filter(None, (library, when, "waiting in Nineveh's queue")))
