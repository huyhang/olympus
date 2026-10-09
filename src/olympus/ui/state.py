"""What the shell remembers while the user moves between agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from olympus.domain import (
    AgentProfile,
    AgentRuntime,
    Candidate,
    Conversation,
    Evidence,
    SuggestedAction,
)


@dataclass
class AgentChat:
    """One agent's place in the shell: its open conversation and unsent text."""

    conversation: Conversation
    draft: str = ""


@dataclass
class Reply:
    """An answer in flight, kept so it can follow the user wherever they go.

    The widget is set only while the reply's conversation is on screen; the
    accumulated content lets a fresh widget pick up where the last one was.
    """

    profile: AgentProfile
    conversation_id: str
    runtime: AgentRuntime
    task: asyncio.Task[None] | None = None
    content: str = ""
    status: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    choices: tuple[Candidate, ...] = ()
    action: SuggestedAction | None = None
    widget: Any = None
    finished: bool = False

    @property
    def agent_id(self) -> str:
        return self.profile.id

    @property
    def running(self) -> bool:
        """Until the answer is filed; the task itself ends a moment later."""
        return self.task is not None and not self.task.done() and not self.finished
