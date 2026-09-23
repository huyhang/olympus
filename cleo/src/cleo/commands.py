"""Deterministic local commands, parsed before text reaches the model."""

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

- `/new` — start a new conversation
- `/history [search]` — browse and resume past conversations
- `/open ID` — open a conversation by its displayed ID prefix
- `/name NAME` — change the librarian's name
- `/persona [INSTRUCTIONS]` — edit or replace presentation preferences
- `/style compact|detailed` — choose the default answer depth
- `/export [ID]` — export a conversation as Markdown
- `/clear [ID|all]` — request confirmed removal of local history
- `/help` — show this list

These commands are handled by the TUI and are never sent to the model.
"""
