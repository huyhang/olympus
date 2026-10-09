"""Screens for reviewing an agent's proposals for the files in a folder.

The agent owns the work: it discovers files, prepares each proposal, and
carries out what the user decides. These screens only show its snapshots and
pass decisions back through `olympus.ports.ReviewSession`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import httpx
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
)

from olympus.domain import Candidate, Proposal
from olympus.ports import FolderWorkflow, ReviewError, ReviewSession
from olympus.presentation import plain_text
from olympus.ui import proposals as view
from olympus.ui.folders import TYPING_PAUSE, FolderRequest
from olympus.ui.screens import ConfirmScreen

# What a session's transport can raise from an action. Shown, never fatal.
ACTION_FAILURES = (RuntimeError, OSError, ValueError, httpx.HTTPError)
# Worker groups. Leaving cancels preparation but lets a placement finish, so
# the summary never calls a volume undecided that the agent was placing.
PREPARING = "preparing"
PLACING = "placing"

Search = Callable[[str], Awaitable[tuple[Candidate, ...]]]


class ProposalRow(ListItem):
    def __init__(self, proposal: Proposal) -> None:
        super().__init__(Static(view.row_text(proposal)))
        self.proposal_id = proposal.id

    def show(self, proposal: Proposal) -> None:
        self.query_one(Static).update(view.row_text(proposal))


class ReviewBoardScreen(Screen[list[Proposal]]):
    """Every proposal at a glance, the highlighted one in detail."""

    DEFAULT_CSS = """
    #board { width: 1fr; height: 1fr; padding: 1 2; }
    #board-heading { height: 1; color: #8bd5ff; text-style: bold; }
    #board-folder { height: 1; color: #8b9bab; }
    #board-tally { height: 1; margin-top: 1; }
    #board-progress { height: 1; margin-bottom: 1; }
    #board-progress Bar { width: 1fr; }
    #board-body { height: 1fr; }
    #board-files {
        width: 2fr; min-width: 30; height: 1fr; background: #101821;
        border: round #243447;
    }
    #board-files ListItem { height: 1; padding: 0 1; }
    #board-files ListItem.--highlight { background: #1d3044; }
    #board-detail {
        width: 3fr; height: 1fr; margin-left: 1; padding: 1 2;
        background: #0f1720; border: round #2f81f7;
    }
    #board-card { height: 1fr; }
    #board-actions { height: 3; }
    #board-actions Button { margin-right: 1; min-width: 10; }
    """
    BINDINGS: ClassVar = [
        Binding("enter", "place", "Place"),
        Binding("s", "skip", "Skip"),
        Binding("c", "choose", "Series"),
        Binding("r", "rename", "Rename"),
        Binding("A", "place_all", "Place all ready"),
        Binding("escape", "leave", "Done"),
    ]

    def __init__(
        self, workflow: FolderWorkflow, request: FolderRequest, agent_name: str
    ) -> None:
        super().__init__()
        self._workflow = workflow
        self._request = request
        self._agent_name = agent_name
        self._proposals: dict[str, Proposal] = {}
        self._proposal_rows: dict[str, ProposalRow] = {}
        self._prepared = False
        self._leaving = False
        self._session: ReviewSession | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="board"):
            yield Label(
                f"{self._agent_name} · {self._workflow.title}",
                id="board-heading",
                markup=False,
            )
            yield Static(
                plain_text(str(self._request.folder)), id="board-folder", markup=False
            )
            yield Static("Looking through the folder…", id="board-tally")
            yield ProgressBar(
                show_eta=False, show_percentage=False, id="board-progress"
            )
            with Horizontal(id="board-body"):
                yield ListView(id="board-files")
                with Vertical(id="board-detail"):
                    yield Static(view.detail_renderable(None), id="board-card")
                    with Horizontal(id="board-actions"):
                        yield Button("Place ⏎", id="board-place", variant="success")
                        yield Button("Skip", id="board-skip")
                        yield Button("Series…", id="board-choose")
                        yield Button("Rename", id="board-rename")
        yield Footer()

    def on_mount(self) -> None:
        request = self._request
        self._session = self._workflow.open(
            request.folder, request.recursive, self.show_proposal
        )
        self.query_one("#board-files", ListView).focus()
        self._refresh()
        self.run_worker(self._run(), group=PREPARING)

    @property
    def proposals(self) -> list[Proposal]:
        return list(self._proposals.values())

    # -- updates from the session --------------------------------------------

    def show_proposal(self, proposal: Proposal) -> None:
        """The session's listener: one fresh snapshot of one proposal."""
        if self._leaving or not self.is_mounted:
            return
        self._proposals[proposal.id] = proposal
        row = self._proposal_rows.get(proposal.id)
        if row is None:
            row = self._proposal_rows[proposal.id] = ProposalRow(proposal)
            files = self.query_one("#board-files", ListView)
            files.append(row)
            if files.index is None:
                files.index = 0
        else:
            row.show(proposal)
        self._refresh()

    async def _run(self) -> None:
        try:
            notice = await self._require_session().run()
        except ReviewError as error:
            self.notify(str(error), severity="warning")
        except ACTION_FAILURES as error:
            self.notify(_failure(error), severity="error")
        else:
            if notice:
                self.notify(plain_text(notice), severity="warning", timeout=10)
        self._prepared = True
        self._refresh()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        proposals = self.proposals
        self.query_one("#board-tally", Static).update(self._tally(proposals))
        progress = self.query_one("#board-progress", ProgressBar)
        if proposals:
            settled = sum(1 for item in proposals if item.settled)
            progress.update(total=len(proposals), progress=settled)
        self._show_detail()

    def _tally(self, proposals: list[Proposal]) -> Text:
        if proposals:
            return view.tally_text(proposals)
        if self._prepared:
            return Text("Nothing to file in this folder.", style="#d29922")
        return Text("Looking through the folder…", style="#8b9bab")

    def _show_detail(self) -> None:
        current = self._highlighted()
        card = self.query_one("#board-card", Static)
        if current is None and self._prepared and not self._proposals:
            card.update(Text("Nothing here. Press Esc to go back.", style="#8b9bab"))
        else:
            card.update(view.detail_renderable(current))
        for action in ("place", "skip", "choose", "rename"):
            button = self.query_one(f"#board-{action}", Button)
            button.disabled = not view.allowed(current, action)

    def _highlighted(self) -> Proposal | None:
        row = self.query_one("#board-files", ListView).highlighted_child
        if not isinstance(row, ProposalRow):
            return None
        return self._proposals.get(row.proposal_id)

    def _require_session(self) -> ReviewSession:
        if self._session is None:
            raise ReviewError("The review has not started.")
        return self._session

    # -- the user's decisions -------------------------------------------------

    @on(ListView.Highlighted, "#board-files")
    def highlighted(self) -> None:
        self._show_detail()

    @on(ListView.Selected, "#board-files")
    def selected(self) -> None:
        """Enter does the one useful thing: place, or else choose a series."""
        if view.allowed(self._highlighted(), "place"):
            self.action_place()
        else:
            self.action_choose()

    @on(Button.Pressed, "#board-actions Button")
    def button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "board-place": self.action_place,
            "board-skip": self.action_skip,
            "board-choose": self.action_choose,
            "board-rename": self.action_rename,
        }
        actions[event.button.id or ""]()

    def action_place(self) -> None:
        current = self._permitted("place")
        if current:
            self._act(self._require_session().place, current.id, group=PLACING)

    def action_skip(self) -> None:
        current = self._permitted("skip")
        if current:
            self._act(self._require_session().skip, current.id)

    def action_choose(self) -> None:
        current = self._permitted("choose")
        if current is None:
            return
        picker = SeriesPickerScreen(
            current.source, current.alternatives, self._require_session().search
        )
        self.app.push_screen(picker, lambda choice: self._chosen(current.id, choice))

    def _chosen(self, proposal_id: str, choice: Candidate | None) -> None:
        if choice is not None:
            self._act(self._require_session().choose, proposal_id, choice)

    def action_rename(self) -> None:
        current = self._permitted("rename")
        if current is None:
            return
        self.app.push_screen(
            RenameScreen(current.filename),
            lambda name: self._renamed(current.id, name),
        )

    def _renamed(self, proposal_id: str, filename: str | None) -> None:
        if filename:
            self._act(self._require_session().rename, proposal_id, filename)

    def action_place_all(self) -> None:
        ready = view.placeable(self.proposals)
        if not ready:
            self.notify("Nothing is ready to place without a check.")
            return
        self.run_worker(self._place_all(ready), group=PLACING)

    async def _place_all(self, proposal_ids: list[str]) -> None:
        for proposal_id in proposal_ids:
            await self._attempt(self._require_session().place, proposal_id)

    def _permitted(self, action: str) -> Proposal | None:
        current = self._highlighted()
        return current if view.allowed(current, action) else None

    def _act(
        self,
        method: Callable[..., Awaitable[None]],
        *arguments: Any,
        group: str = PREPARING,
    ) -> None:
        self.run_worker(self._attempt(method, *arguments), group=group)

    async def _attempt(
        self, method: Callable[..., Awaitable[None]], *arguments: Any
    ) -> None:
        try:
            await method(*arguments)
        except ReviewError as error:
            self.notify(str(error), severity="warning")
        except ACTION_FAILURES as error:
            self.notify(_failure(error), severity="error")

    # -- leaving --------------------------------------------------------------

    def action_leave(self) -> None:
        open_count = view.unsettled(self.proposals)
        if not open_count:
            self._leave()
            return
        noun = "file" if open_count == 1 else "files"
        self.app.push_screen(
            ConfirmScreen(
                f"Leave {open_count} {noun} undecided? Their uploads are withdrawn.",
                "Leave",
            ),
            lambda confirmed: self._leave() if confirmed else None,
        )

    def _leave(self) -> None:
        if not self._leaving:
            self.run_worker(self._close(), group="closing")

    async def _close(self) -> None:
        self.workers.cancel_group(self, PREPARING)
        placing = [w for w in self.workers if w.group == PLACING and w.node is self]
        await asyncio.gather(
            *(worker.wait() for worker in placing), return_exceptions=True
        )
        result = self.proposals
        self._leaving = True
        try:
            await self._require_session().close()
        except ACTION_FAILURES as error:
            self.app.notify(_failure(error), severity="error")
        self.dismiss(result)


class SeriesPickerScreen(ModalScreen[Candidate | None]):
    DEFAULT_CSS = """
    SeriesPickerScreen { align: center middle; background: #05080c 70%; }
    #series-dialog { width: 76; height: 26; }
    #series-list {
        height: 1fr; margin-top: 1; background: #0e1621; border: round #243447;
    }
    #series-status { height: 1; color: #8b9bab; }
    """
    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "focus_results", "Results", show=False),
    ]

    def __init__(
        self, source: str, alternatives: tuple[Candidate, ...], search: Search
    ) -> None:
        super().__init__()
        self._source = source
        self._search = search
        self._candidates = alternatives
        self._search_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="series-dialog"):
            yield Label(
                f"Which series is {plain_text(self._source)}?",
                classes="dialog-title",
                markup=False,
            )
            yield Input(placeholder="Search the catalog by title", id="series-search")
            yield ListView(id="series-list")
            yield Static(self._status_text(), id="series-status", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="series-cancel")
                yield Button("Choose", id="series-choose", variant="primary")

    async def on_mount(self) -> None:
        await self._show(self._candidates)
        self.query_one("#series-search", Input).focus()

    def _status_text(self) -> str:
        if self._candidates:
            return "Type to search · ↓ results · Enter choose"
        return "No suggestions yet. Type a title to search."

    async def _show(self, candidates: tuple[Candidate, ...]) -> None:
        self._candidates = candidates
        results = self.query_one("#series-list", ListView)
        await results.clear()
        for candidate in candidates:
            await results.append(
                ListItem(Label(plain_text(candidate.title), markup=False))
            )
        if candidates:
            results.index = 0

    @on(Input.Changed, "#series-search")
    def query_changed(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(TYPING_PAUSE, self._start_search)

    @on(Input.Submitted, "#series-search")
    def query_submitted(self) -> None:
        if self._candidates:
            self.action_focus_results()
        else:
            self._start_search()

    def _start_search(self) -> None:
        query = self.query_one("#series-search", Input).value.strip()
        if query:
            self.run_worker(self._run_search(query), exclusive=True, group="search")

    async def _run_search(self, query: str) -> None:
        status = self.query_one("#series-status", Static)
        status.update("Searching…")
        try:
            found = await self._search(query)
        except (ReviewError, *ACTION_FAILURES) as error:
            status.update(Text(_failure(error), style="#f85149"))
            return
        await self._show(found)
        status.update(f"{len(found)} match{'' if len(found) == 1 else 'es'}")

    def action_focus_results(self) -> None:
        self.query_one("#series-list", ListView).focus()

    @on(ListView.Selected, "#series-list")
    @on(Button.Pressed, "#series-choose")
    def choose(self) -> None:
        index = self.query_one("#series-list", ListView).index
        if index is not None and 0 <= index < len(self._candidates):
            self.dismiss(self._candidates[index])

    @on(Button.Pressed, "#series-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    RenameScreen { align: center middle; background: #05080c 70%; }
    """
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="rename-dialog"):
            yield Label("Place under which filename?", classes="dialog-title")
            yield Input(value=plain_text(self._filename), id="rename-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="rename-cancel")
                yield Button("Rename", id="rename-save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    @on(Input.Submitted, "#rename-input")
    @on(Button.Pressed, "#rename-save")
    def save(self) -> None:
        name = self.query_one("#rename-input", Input).value.strip()
        self.dismiss(name or None)

    @on(Button.Pressed, "#rename-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def _failure(error: BaseException) -> str:
    return plain_text(str(error)) or type(error).__name__
