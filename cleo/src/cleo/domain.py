"""Cleo-specific domain constants.

Shared conversation and model values are owned by Olympus and re-exported here
temporarily for source compatibility with Cleo integrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from olympus.domain import (
    AgentEvent,
    Candidate,
    Conversation,
    Evidence,
    Identity,
    Message,
    ModelChunk,
    ToolCall,
    utc_now,
)

# The metadata fields Nineveh's series search accepts, and how long each may
# be. Declared once: the catalog adapter and the model-facing tool registry
# both narrow this set, and they must not drift apart while doing so.
SEARCH_FIELD_LIMITS: dict[str, int] = {
    "query": 200,
    "library": 200,
    "author": 200,
    "artist": 200,
    "publisher": 200,
    "status": 64,
    "tag": 64,
    "title": 200,
}


@dataclass(frozen=True, slots=True)
class LocalVolume:
    """A volume file found in the folder under review.

    `relative` is its path from that folder, as the user sees it. A volume
    with a `problem` is shown but never uploaded.
    """

    path: Path
    relative: str
    size: int
    problem: str = ""


@dataclass(frozen=True, slots=True)
class FolderScan:
    """What a scan found, and, when it stopped early, the limit it hit."""

    volumes: tuple[LocalVolume, ...]
    limit: str = ""


@dataclass(frozen=True, slots=True)
class VolumeHint:
    """What a volume says about itself: a series title and maybe a number."""

    title: str
    number: str | None
    source: Literal["comicinfo", "filename", "model"]


@dataclass(frozen=True, slots=True)
class SeriesOption:
    """A catalog series a volume might belong to, as Nineveh described it."""

    series_id: str
    title: str
    library: str = ""
    score: float | None = None
    matched_on: str = ""
    volumes: int | None = None

    @property
    def candidate(self) -> Candidate:
        """How the series is offered in a pick list."""
        details = [self.library] if self.library else []
        if self.volumes is not None:
            details.append(f"{self.volumes} volume{'' if self.volumes == 1 else 's'}")
        suffix = f" — {' · '.join(details)}" if details else ""
        return Candidate(self.series_id, f"{self.title}{suffix}")

    @property
    def subject(self) -> Candidate:
        """How the series is named once chosen."""
        return Candidate(self.series_id, self.title)


@dataclass(frozen=True, slots=True)
class MatchResult:
    """The series a volume belongs to, or the options when that is unclear.

    `reason` says how the series was settled, or why it was not.
    """

    series: SeriesOption | None
    alternatives: tuple[SeriesOption, ...] = ()
    reason: str = ""


__all__ = [
    "SEARCH_FIELD_LIMITS",
    "AgentEvent",
    "Candidate",
    "Conversation",
    "Evidence",
    "FolderScan",
    "Identity",
    "LocalVolume",
    "MatchResult",
    "Message",
    "ModelChunk",
    "SeriesOption",
    "ToolCall",
    "VolumeHint",
    "utc_now",
]
