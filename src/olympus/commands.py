"""Deterministic local commands, parsed before text reaches an agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    argument: str = ""


def parse_command(text: str) -> Command | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    name, separator, argument = stripped[1:].partition(" ")
    return Command(name.lower(), argument.strip() if separator else "")


HELP = """### Local commands

- `/new` — start a conversation with the current agent
- `/agents` — add, edit, remove, or switch agents
- `/agent NUMBER|NAME` — switch agent by sidebar number, name, or ID prefix
- `/history [search]` — browse this agent's conversations
- `/open ID` — open a conversation by ID prefix
- `/name NAME` — rename the current agent
- `/persona [INSTRUCTIONS]` — edit presentation preferences
- `/style compact|detailed` — choose the answer depth
- `/export [ID]` — export a conversation as Markdown
- `/clear [ID|all]` — request confirmed history removal
- `/help` — show this list

These commands are handled by Olympus and are never sent to an agent.
"""
