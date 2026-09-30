"""Cleo-specific domain constants.

Shared conversation and model values are owned by Olympus and re-exported here
temporarily for source compatibility with Cleo integrations.
"""

from __future__ import annotations

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


__all__ = [
    "SEARCH_FIELD_LIMITS",
    "AgentEvent",
    "Candidate",
    "Conversation",
    "Evidence",
    "Identity",
    "Message",
    "ModelChunk",
    "ToolCall",
    "utc_now",
]
