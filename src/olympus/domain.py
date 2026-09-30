"""Values shared by the Olympus shell and its agent providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Role = Literal["user", "assistant"]
ResponseStyle = Literal["compact", "detailed"]


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
    response_style: ResponseStyle = "compact"


@dataclass(frozen=True, slots=True)
class Evidence:
    tool: str
    payload: Any
    retrieved_at: str = field(default_factory=utc_now)
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: Literal["status", "token", "evidence", "choice", "done"]
    text: str = ""
    evidence: Evidence | None = None
    candidates: tuple[Candidate, ...] = ()


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
class ConfigurationField:
    key: str
    label: str
    placeholder: str = ""
    default: str = ""
    secret: bool = False
    required: bool = True


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    kind: str
    name: str
    description: str
    glyph: str
    fields: tuple[ConfigurationField, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentProfile:
    id: str
    kind: str
    name: str
    persona: str = ""
    response_style: ResponseStyle = "compact"
    ollama_url: str = ""
    model: str = ""
    settings: dict[str, str] = field(default_factory=dict)
    archived: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def identity(self) -> Identity:
        return Identity(self.name, self.persona, self.response_style)


@dataclass(frozen=True, slots=True)
class OllamaDefaults:
    url: str = "http://localhost:11434"
    model: str = "granite4.2:8b"


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    session: Any
    model: str
    summary: str
    close_async: tuple[Any, ...] = ()
