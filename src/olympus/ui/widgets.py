"""Reusable Olympus widgets."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.message import Message as TextualMessage
from textual.widgets import ListItem, Static, TextArea

from olympus.domain import AgentProfile


class Composer(TextArea):
    """Enter sends. Shift+Enter or Ctrl+J inserts a newline.

    Ctrl+J is the portable one: many terminals send Shift+Enter as a plain
    Enter, but every terminal sends Ctrl+J as its own key.
    """

    BINDINGS: ClassVar = [
        Binding("enter", "submit", "Send", show=False, priority=True),
        Binding("shift+enter", "newline", "New line", show=False, priority=True),
        Binding("ctrl+j", "newline", "New line", show=False, priority=True),
    ]

    class Submitted(TextualMessage):
        pass

    def action_submit(self) -> None:
        self.post_message(self.Submitted())

    def action_newline(self) -> None:
        self.insert("\n")


class AgentListItem(ListItem):
    """A sidebar row: shortcut number, reply marker, name, and model."""

    def __init__(
        self, profile: AgentProfile, number: int, model: str, busy: bool = False
    ) -> None:
        label = self.label(number, profile.name, model, busy)
        super().__init__(Static(label, markup=False))
        self.profile = profile
        self.id = f"agent-{profile.id}"

    def show(self, profile: AgentProfile, number: int, model: str, busy: bool) -> None:
        self.profile = profile
        self.query_one(Static).update(self.label(number, profile.name, model, busy))

    @staticmethod
    def label(number: int, name: str, model: str, busy: bool) -> str:
        marker = "●" if busy else "○"
        return f"{number} {marker} {name}\n    {model}"
