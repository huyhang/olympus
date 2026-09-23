"""Small domain values shared by Cleo's adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Role = Literal["user", "assistant"]

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


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Identity:
    name: str = "Cleo"
    persona: str = ""
    response_style: Literal["compact", "detailed"] = "compact"


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""

    def as_message_value(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "function": {"name": self.name, "arguments": self.arguments}
        }
        if self.call_id:
            value["id"] = self.call_id
        return value


@dataclass(frozen=True, slots=True)
class ModelChunk:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    tool: str
    payload: Any
    retrieved_at: str = field(default_factory=utc_now)
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One series Nineveh offered when it could not pick a single match."""

    id: str
    title: str


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: Literal["status", "token", "evidence", "choice", "done"]
    text: str = ""
    evidence: Evidence | None = None
    candidates: tuple[Candidate, ...] = ()
