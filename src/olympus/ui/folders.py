"""Choosing the folder an agent should review.

A path field with completion, a tree of folders, and a live preview of what
the agent would find there. The agent is asked only to describe the folder;
nothing is read beyond listing it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.timer import Timer
from textual.widgets import Button, DirectoryTree, Input, Label, Static, Switch

from olympus.ports import FolderWorkflow
from olympus.presentation import plain_text

# How long typing must pause before a lookup starts.
TYPING_PAUSE = 0.25

ChildFolders = Callable[[Path], Iterable[str]]


@dataclass(frozen=True, slots=True)
class FolderRequest:
    folder: Path
    recursive: bool = False


def child_folders(parent: Path) -> list[str]:
    try:
        with os.scandir(parent) as entries:
            return [entry.name for entry in entries if entry.is_dir()]
    except OSError:
        return []


def complete_folder(value: str, children: ChildFolders = child_folders) -> str | None:
    """Complete the last path segment of `value` to a folder, keeping `~`."""
    if not value:
        return None
    expanded = Path(value).expanduser()
    parent, prefix = (
        (expanded, "")
        if value.endswith("/")
        else (
            expanded.parent,
            expanded.name,
        )
    )
    for name in sorted(children(parent)):
        if name.startswith(".") and not prefix.startswith("."):
            continue
        if name.startswith(prefix) and name != prefix:
            return f"{value}{name[len(prefix) :]}/"
    return None


def resolve_folder(value: str) -> Path | None:
    """The folder `value` names, or None when it is not an existing folder."""
    if not value.strip():
        return None
    folder = Path(value.strip()).expanduser()
    return folder.resolve() if folder.is_dir() else None


class FolderSuggester(Suggester):
    def __init__(self, children: ChildFolders = child_folders) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._children = children

    async def get_suggestion(self, value: str) -> str | None:
        return await asyncio.to_thread(complete_folder, value, self._children)


class FolderTree(DirectoryTree):
    """Folders only: the picker chooses where to look, not what to file."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [
            path for path in paths if not path.name.startswith(".") and _is_dir(path)
        ]


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


class FolderPickerScreen(ModalScreen[FolderRequest | None]):
    DEFAULT_CSS = """
    FolderPickerScreen { align: center middle; background: #05080c 70%; }
    #folder-dialog { width: 84; height: 34; }
    #folder-tree {
        height: 1fr; margin: 1 0; background: #0e1621; border: round #243447;
    }
    #folder-options { height: 3; }
    #folder-options Label { padding: 1 1; color: #aab8c5; }
    #folder-preview { height: 1; color: #8bd5ff; }
    """
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, workflow: FolderWorkflow, initial: str, root: Path) -> None:
        super().__init__()
        self._workflow = workflow
        self._initial_path = initial
        self._root = root
        self._preview_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="folder-dialog"):
            yield Label(self._workflow.title, classes="dialog-title", markup=False)
            yield Input(
                value=self._initial_path,
                placeholder="Folder to look in, e.g. ~/Downloads",
                suggester=FolderSuggester(),
                id="folder-path",
            )
            yield FolderTree(
                resolve_folder(self._initial_path) or self._root, id="folder-tree"
            )
            with Horizontal(id="folder-options"):
                yield Switch(id="folder-recursive")
                yield Label("Include subfolders")
            yield Static("", id="folder-preview")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="folder-cancel")
                yield Button("Start review", id="folder-start", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#folder-path", Input).focus()
        self._schedule_preview()

    @property
    def _recursive(self) -> bool:
        return self.query_one("#folder-recursive", Switch).value

    @on(Input.Changed, "#folder-path")
    def path_changed(self) -> None:
        self._schedule_preview()

    @on(Switch.Changed, "#folder-recursive")
    def recursion_changed(self) -> None:
        self._schedule_preview()

    @on(DirectoryTree.DirectorySelected, "#folder-tree")
    def folder_picked(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#folder-path", Input).value = str(event.path)

    @on(Input.Submitted, "#folder-path")
    @on(Button.Pressed, "#folder-start")
    def start(self) -> None:
        folder = resolve_folder(self.query_one("#folder-path", Input).value)
        if folder is None:
            self._preview(Text("Choose an existing folder.", style="#f85149"))
            return
        self.dismiss(FolderRequest(folder, self._recursive))

    @on(Button.Pressed, "#folder-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _schedule_preview(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(TYPING_PAUSE, self._start_preview)

    def _start_preview(self) -> None:
        folder = resolve_folder(self.query_one("#folder-path", Input).value)
        if folder is None:
            self._preview(Text(""))
            return
        self.run_worker(
            self._describe(folder, self._recursive), exclusive=True, group="preview"
        )

    async def _describe(self, folder: Path, recursive: bool) -> None:
        self._preview(Text("Looking…", style="#8b9bab"))
        try:
            line = await asyncio.to_thread(self._workflow.describe, folder, recursive)
        except OSError as error:
            line = f"Cannot read that folder: {error.strerror or error}"
        self._preview(Text(plain_text(line), style="#8bd5ff"))

    def _preview(self, text: Text) -> None:
        self.query_one("#folder-preview", Static).update(text)
