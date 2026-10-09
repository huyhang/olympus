"""Values shared by the Olympus shell and its agent providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from olympus.ports import FolderWorkflow

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
class SuggestedAction:
    """Something an agent offers the user, which only the user can start.

    `review_folder` opens the folder picker with `argument` filled in.
    """

    kind: Literal["review_folder"]
    label: str
    argument: str = ""


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: Literal["status", "token", "evidence", "choice", "action", "done"]
    text: str = ""
    evidence: Evidence | None = None
    candidates: tuple[Candidate, ...] = ()
    action: SuggestedAction | None = None


# Where one proposal stands. `planned` knows its target but is prepared only
# when the user turns to it; `ready` waits for the user; `held` was prepared
# but must be approved elsewhere; `placed`, `held`, `skipped`, and `failed`
# are settled.
ProposalState = Literal[
    "pending",
    "working",
    "planned",
    "needs_choice",
    "ready",
    "placing",
    "placed",
    "held",
    "skipped",
    "failed",
]
SETTLED_STATES: frozenset[str] = frozenset({"placed", "held", "skipped", "failed"})


@dataclass(frozen=True, slots=True)
class Proposal:
    """An agent's suggestion for one file, as the review board shows it.

    Every text field comes from the agent or the service behind it, never
    from Olympus, so views pass it through `plain_text` before display.
    `retryable` is False for a file the agent will never act on, such as one
    too large to upload: choosing another target cannot help it.
    `suggestion` is the agent's best guess when it would not decide; the board
    offers it first and never acts on it. A `carried_over` proposal was
    prepared before this review began, so leaving the review keeps it and only
    an explicit withdrawal undoes it.
    """

    id: str
    source: str
    state: ProposalState = "pending"
    size: int = 0
    pages: int = 0
    activity: str = ""
    subject: Candidate | None = None
    subject_note: str = ""
    destination: str = ""
    filename: str = ""
    suggested_filename: str = ""
    pattern: str = ""
    warning: str = ""
    alternatives: tuple[Candidate, ...] = ()
    suggestion: Candidate | None = None
    evidence: tuple[Evidence, ...] = ()
    retryable: bool = True
    carried_over: bool = False

    @property
    def settled(self) -> bool:
        return self.state in SETTLED_STATES

    @property
    def renamed(self) -> bool:
        return bool(self.filename) and self.filename != self.source.rsplit("/", 1)[-1]


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
    """One provider setting. A `toggle` is stored as TOGGLE_ON or TOGGLE_OFF."""

    key: str
    label: str
    placeholder: str = ""
    default: str = ""
    secret: bool = False
    required: bool = True
    kind: Literal["text", "toggle"] = "text"


TOGGLE_ON = "on"
TOGGLE_OFF = "off"


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
    """An opened agent, and the folder workflow it offers, if any."""

    session: Any
    model: str
    summary: str
    close_async: tuple[Any, ...] = ()
    workflow: FolderWorkflow | None = None
