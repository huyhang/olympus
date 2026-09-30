"""How messages, pick lists, and evidence are rendered in a transcript.

Everything here renders text Olympus did not author, so it is always passed
through `plain_text`: the terminal displays it and never acts on it.
"""

from __future__ import annotations

import json

from textual.widgets import Collapsible, Markdown

from olympus.domain import Candidate, Evidence, Message
from olympus.presentation import format_timestamp, plain_text


def message_markdown(message: Message, agent_name: str) -> str:
    label = "You" if message.role == "user" else agent_name
    return (
        f"**{label}** · {format_timestamp(message.created_at)}\n\n"
        f"{plain_text(message.content)}"
    )


def draft_markdown(agent_name: str, content: str) -> str:
    return f"**{agent_name}**\n\n{plain_text(content)}"


def choice_markdown(prompt: str, choices: tuple[Candidate, ...]) -> str:
    lines = [prompt, ""]
    lines += [f"{number}. {item.title}" for number, item in enumerate(choices, 1)]
    lines += ["", "Reply with a number."]
    return "\n".join(lines)


def evidence_panel(item: Evidence) -> Collapsible:
    details = {"request": item.arguments, "response": item.payload}
    payload = plain_text(json.dumps(details, indent=2, ensure_ascii=False))
    title = f"Evidence · {item.tool} · {format_timestamp(item.retrieved_at)}"
    return Collapsible(
        Markdown(f"```json\n{payload}\n```"), title=title, classes="evidence"
    )
