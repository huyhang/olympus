"""The keyboard-first Olympus application shell."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Coroutine, Iterable
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

import httpx
from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Button, Footer, Header, ListView, Markdown, Static

from olympus.commands import HELP, Command, parse_command
from olympus.config import Settings
from olympus.domain import (
    AgentEvent,
    AgentProfile,
    AgentRuntime,
    Candidate,
    Conversation,
    Message,
    Proposal,
    SuggestedAction,
)
from olympus.ports import (
    ConversationRepository,
    FolderWorkflow,
    OlympusError,
    StoreError,
)
from olympus.presentation import format_timestamp, plain_text
from olympus.runtimes import RuntimePool
from olympus.services import AgentService, fresh_profile
from olympus.ui.folders import FolderPickerScreen, FolderRequest
from olympus.ui.proposals import summary_evidence, summary_markdown
from olympus.ui.review import ReviewBoardScreen
from olympus.ui.screens import (
    AgentManagerResult,
    AgentManagerScreen,
    ConfirmScreen,
    HistoryScreen,
    IdentityScreen,
)
from olympus.ui.state import AgentChat, Reply
from olympus.ui.theme import OLYMPUS
from olympus.ui.transcript import (
    choice_markdown,
    draft_markdown,
    evidence_panel,
    message_markdown,
)
from olympus.ui.widgets import AgentListItem, Composer

MAX_QUESTION_LENGTH = 4_000
# What an agent's transport can raise mid-reply. These are shown in the reply;
# anything else is reported when the reply's task is collected.
AGENT_FAILURES = (RuntimeError, OSError, ValueError, httpx.HTTPError, httpx.InvalidURL)
NARROW_WIDTH = 88
EMPTY_REPLY = "The agent finished without an answer."

# Shortcuts that act on the chat workspace. They are priority bindings, so the
# focused composer cannot swallow them (its editor claims Ctrl+A and Ctrl+K),
# and `check_action` switches them off whenever another screen is on top.
WORKSPACE_ACTIONS = frozenset(
    {
        "command_palette",
        "agents",
        "focus_agents",
        "toggle_sidebar",
        "new",
        "history",
        "identity",
        "switch_agent",
        "previous_input",
        "next_input",
        "cancel_reply",
        "review_folder",
    }
)


def _agent_shortcuts() -> list[Binding]:
    """Ctrl+1–5. Only terminals with the kitty keyboard protocol send these."""
    return [
        Binding(f"ctrl+{n}", f"switch_agent({n})", f"Agent {n}", show=False)
        for n in range(1, 6)
    ]


class OfferButton(Button):
    """An agent's suggested action under its reply. Only a press starts it."""

    def __init__(self, action: SuggestedAction) -> None:
        super().__init__(action.label, classes="offer", variant="primary")
        self.offer = action


class OlympusApp(App[None]):
    """Agent navigation surrounding one focused conversation."""

    TITLE = "Olympus"
    SUB_TITLE = "Local agents, one place"
    COMMAND_PALETTE_BINDING = "ctrl+k"
    CSS = """
    Screen { background: $background; color: $foreground; }
    Header { background: $panel; color: $foreground; }
    Footer { background: $panel; }
    #body { height: 1fr; }
    #sidebar {
        width: 30; min-width: 24; height: 1fr; background: $surface;
        border-right: solid $border-blurred;
    }
    #brand { height: 3; padding: 1 2; color: $text-primary; text-style: bold; }
    #agent-list { height: 1fr; background: transparent; padding: 0 1; }
    #agent-list ListItem { height: 3; padding: 0 1; margin-bottom: 1; }
    #agent-list ListItem.--highlight { background: $primary-muted; color: $text; }
    #sidebar-actions { height: 4; padding: 0 1; }
    #sidebar-actions Button { width: 1fr; min-width: 8; margin: 0 1 0 0; }
    #workspace { width: 1fr; height: 1fr; }
    #identity { height: 3; padding: 1 2; background: $surface; color: $text-muted; }
    #transcript { height: 1fr; padding: 1 3; scrollbar-color: $primary; }
    #empty-state { width: 70%; margin: 4 8; padding: 2 3; border: round $primary; }
    .message { margin: 0 0 1 0; padding: 1 2; }
    .user { background: $primary-muted; border-left: thick $primary; }
    .assistant { background: $surface; border-left: thick $success; }
    .evidence { margin: 0 2 1 4; color: $text-muted; }
    .offer { margin: 0 2 1 4; }
    #status { height: 1; padding: 0 3; color: $text-muted; }
    #compose-row { height: 7; margin: 0 2 1 2; }
    #composer { width: 1fr; height: 6; border: tall $primary; background: $surface; }
    #composer:focus { border: tall $primary-lighten-2; }
    #send { width: 10; height: 6; margin-left: 1; }
    .screen-panel { width: 90%; height: 88%; margin: 2 5; padding: 1 2; }
    .form-panel { width: 80%; height: 90%; margin: 1 10; padding: 1 3; }
    .screen-title { height: 2; color: $text-primary; text-style: bold; }
    .screen-subtitle { height: 2; color: $text-muted; }
    .screen-actions { height: 4; margin-top: 1; }
    .screen-actions Button { margin-right: 1; }
    .hint { height: auto; color: $text-muted; }
    #manager-actions {
        grid-size: 4; grid-gutter: 0 1; grid-rows: 3; height: 7; margin-top: 1;
    }
    #manager-actions Button { width: 1fr; min-width: 8; }
    #history-search { margin-bottom: 1; }
    #history-list, #manager-agent-list { height: 1fr; }
    #identity-persona, #agent-persona { height: 8; min-height: 5; }
    .toggle-row { height: 3; margin-top: 1; }
    .toggle-row Label { padding: 1 1; }
    #agent-form { overflow-y: auto; }
    #agent-form-status, #defaults-status { height: 2; margin-top: 1; }
    .dialog {
        width: 68; height: auto; max-height: 80%; padding: 2;
        background: $panel; border: round $primary;
    }
    .dialog-title { height: 2; color: $text; text-style: bold; }
    .dialog-copy { height: auto; color: $text-muted; }
    .dialog-buttons { height: 4; align-horizontal: right; margin-top: 1; }
    .dialog-buttons Button { margin-left: 1; }
    ConfirmScreen, RemoveAgentScreen, AgentTypeScreen {
        align: center middle; background: $background 70%;
    }
    #type-dialog { height: 20; }
    #type-list { height: 1fr; }
    #remove-agent-dialog { border: round $warning; }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+a", "agents", "Agents", priority=True),
        Binding("ctrl+g", "focus_agents", "Switch", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", priority=True),
        Binding("ctrl+n", "new", "New chat", priority=True),
        Binding("ctrl+r", "history", "History", priority=True),
        Binding("ctrl+h", "history", "History", show=False, priority=True),
        Binding("ctrl+s", "identity", "Style", priority=True),
        Binding("ctrl+o", "review_folder", "File folder", priority=True),
        Binding("ctrl+up", "previous_input", "Previous input", show=False),
        Binding("ctrl+down", "next_input", "Next input", show=False),
        Binding("escape", "cancel_reply", "Cancel reply"),
        *_agent_shortcuts(),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        service: AgentService,
        settings: Settings,
        start_folder: Path | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._settings = settings
        self._start_folder = start_folder or Path.home()
        self._defaults = service.store.ollama_defaults(settings)
        self._profile: AgentProfile | None = None
        self._repository: ConversationRepository | None = None
        self._runtimes = RuntimePool(service.registry, service.secrets)
        self._chats: dict[str, AgentChat] = {}
        self._replies: dict[str, Reply] = {}
        self._pending_choices: dict[str, tuple[Candidate, ...]] = {}
        self._input_history: list[str] = []
        self._input_index = 0
        self._sidebar_forced = False
        self._sidebar_lock = asyncio.Lock()
        self.register_theme(OLYMPUS)
        saved = service.store.theme()
        self.theme = saved if saved in self.available_themes else OLYMPUS.name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("OLYMPUS", id="brand")
                yield ListView(id="agent-list")
                with Horizontal(id="sidebar-actions"):
                    yield Button("＋ Add", id="quick-add", variant="primary")
                    yield Button("Manage", id="manage-agents")
            with Vertical(id="workspace"):
                yield Static("No agent selected", id="identity")
                yield VerticalScroll(id="transcript")
                yield Static("Choose an agent to begin", id="status")
                with Horizontal(id="compose-row"):
                    yield Composer(
                        id="composer", show_line_numbers=False, disabled=True
                    )
                    yield Button("Send", id="send", variant="primary", disabled=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.theme_changed_signal.subscribe(self, self._remember_theme)
        for failure in self._service.registry.failures:
            self.notify(failure, severity="error", timeout=15)
        await self._populate_agents()
        first = self._first_openable()
        if first is None or not await self._activate(first):
            await self._show_empty_state()

    async def on_unmount(self) -> None:
        tasks = [reply.task for reply in self._replies.values() if reply.running]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._runtimes.close_all()
        self._service.store.close()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action not in WORKSPACE_ACTIONS or not self.screen_stack:
            return True
        return self.screen is self._main

    def on_resize(self, event: events.Resize) -> None:
        sidebar = self._sidebar()
        if event.size.width >= NARROW_WIDTH:
            sidebar.display = True
            self._sidebar_forced = False
        elif not self._sidebar_forced:
            sidebar.display = False

    @on(ListView.Selected, "#agent-list")
    async def agent_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, AgentListItem):
            return
        await self._activate(event.item.profile)
        if self.size.width < NARROW_WIDTH:
            self._sidebar().display = False
            self._sidebar_forced = False

    @on(Composer.Submitted)
    async def composer_submitted(self) -> None:
        await self._submit()

    @on(Button.Pressed)
    async def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            await self._submit()
        elif event.button.id in {"quick-add", "manage-agents"}:
            self.action_agents()
        elif isinstance(event.button, OfferButton):
            self._review_folder(event.button.offer.argument)

    # -- the main workspace -------------------------------------------------

    @property
    def _main(self) -> Screen[Any]:
        """The workspace screen, even while another screen is shown over it."""
        return self.screen_stack[0]

    def _sidebar(self) -> Vertical:
        return self._main.query_one("#sidebar", Vertical)

    def _transcript(self) -> VerticalScroll:
        return self._main.query_one("#transcript", VerticalScroll)

    def _status(self) -> Static:
        return self._main.query_one("#status", Static)

    def _composer(self) -> Composer:
        return self._main.query_one("#composer", Composer)

    @property
    def _chat(self) -> AgentChat | None:
        return self._chats.get(self._profile.id) if self._profile else None

    @property
    def _conversation(self) -> Conversation | None:
        return self._chat.conversation if self._chat else None

    def _show_conversation_later(self, conversation: Conversation) -> None:
        if self._chat:
            self._chat.conversation = conversation
            self.run_worker(self._show_conversation())

    # -- agents and runtimes ------------------------------------------------

    def _first_openable(self) -> AgentProfile | None:
        installed = self._service.registry.installed
        return next(
            (item for item in self._service.store.agents() if installed(item.kind)),
            None,
        )

    def _runtime(self, profile: AgentProfile) -> AgentRuntime | None:
        try:
            return self._runtimes.open(profile, self._defaults)
        except OlympusError as error:
            self.notify(str(error), severity="error")
            return None

    def _busy_runtimes(self) -> list[AgentRuntime]:
        return [reply.runtime for reply in self._replies.values() if reply.running]

    async def _activate(self, profile: AgentProfile) -> bool:
        if self._runtime(profile) is None:
            return False
        self._stash_draft()
        self._profile = profile
        self._repository = self._service.store.for_agent(profile.id)
        chat = self._chat_for(profile)
        composer = self._composer()
        composer.disabled = False
        self._main.query_one("#send", Button).disabled = False
        composer.text = chat.draft
        self._refresh_identity()
        await self._show_conversation()
        await self._populate_agents()
        composer.focus()
        return True

    def _stash_draft(self) -> None:
        if self._chat:
            self._chat.draft = self._composer().text

    def _chat_for(self, profile: AgentProfile) -> AgentChat:
        """The agent's open conversation, kept across switches while it exists."""
        chat = self._chats.get(profile.id)
        repository = self._service.store.for_agent(profile.id)
        if chat is None:
            chat = AgentChat(repository.draft_or_create())
            self._chats[profile.id] = chat
        elif repository.conversation(chat.conversation.id) is None:
            chat.conversation = repository.draft_or_create()
        return chat

    def _agent_by_reference(self, reference: str) -> AgentProfile | None:
        """A sidebar number, or one unambiguous name or ID prefix."""
        profiles = self._service.store.agents()
        if reference.isdigit() and 0 < int(reference) <= len(profiles):
            return profiles[int(reference) - 1]
        return self._service.store.agent(reference)

    async def _populate_agents(self) -> None:
        async with self._sidebar_lock:
            view = self._main.query_one("#agent-list", ListView)
            profiles = self._service.store.agents()
            rows = [
                self._agent_row(number, item) for number, item in enumerate(profiles, 1)
            ]
            current = [item.id for item in view.children]
            if current == [f"agent-{item.id}" for item in profiles]:
                for item, row in zip(view.children, rows, strict=True):
                    item.show(*row)
            else:
                await view.clear()
                for row in rows:
                    await view.append(AgentListItem(*row))
            self._highlight_active(view, profiles)

    def _agent_row(
        self, number: int, profile: AgentProfile
    ) -> tuple[AgentProfile, int, str, bool]:
        busy = any(
            reply.running and reply.agent_id == profile.id
            for reply in self._replies.values()
        )
        return profile, number, profile.model or self._defaults.model, busy

    def _highlight_active(self, view: ListView, profiles: list[AgentProfile]) -> None:
        for index, profile in enumerate(profiles):
            if self._profile and profile.id == self._profile.id:
                view.index = index

    def _refresh_identity(self) -> None:
        if self._profile is None:
            return
        runtime = self._runtimes.cached(self._profile.id)
        detail = f"{runtime.summary}  •  {runtime.model}" if runtime else "unavailable"
        self._main.query_one("#identity", Static).update(
            f"{self._profile.name}  •  {detail}"
        )

    # -- asking and answering -----------------------------------------------

    async def _submit(self) -> None:
        if self._profile is None or self._chat is None:
            self.action_agents()
            return
        composer = self._composer()
        text = composer.text.strip()
        if self._refuse_input(text):
            return
        composer.text = ""
        self._input_history.append(text)
        self._input_index = len(self._input_history)
        command = parse_command(text)
        if command:
            self._pending_choices.pop(self._chat.conversation.id, None)
            await self._handle_command(command)
        else:
            await self._ask(self._profile, self._chat, text)

    def _refuse_input(self, text: str) -> bool:
        if not text:
            return True
        if len(text) > MAX_QUESTION_LENGTH:
            self.notify(
                f"Messages may contain at most {MAX_QUESTION_LENGTH} characters.",
                severity="error",
            )
            return True
        reply = self._replies.get(self._conversation.id) if self._conversation else None
        if reply and reply.running:
            self.notify("Cancel or wait for this response.", severity="warning")
            return True
        return False

    async def _ask(self, profile: AgentProfile, chat: AgentChat, text: str) -> None:
        runtime = self._runtime(profile)
        if runtime is None or self._repository is None:
            return
        question = self._resolve_choice(chat.conversation.id, text)
        if question is None:
            choices = self._pending_choices[chat.conversation.id]
            await self._local_message(f"Pick a number between 1 and {len(choices)}.")
            return
        repository = self._repository
        if repository.conversation(chat.conversation.id) is None:
            chat.conversation = repository.draft_or_create()
        conversation_id = chat.conversation.id
        history = repository.messages(conversation_id)
        grounded = repository.has_evidence(conversation_id)
        message = Message("user", text)
        repository.add_message(conversation_id, message)
        await self._mount_message(message, profile.name)
        reply = Reply(profile, conversation_id, runtime)
        self._replies[conversation_id] = reply
        await self._mount_reply(reply)
        reply.task = asyncio.create_task(
            self._answer(reply, history, question, grounded)
        )
        reply.task.add_done_callback(
            lambda task: self.call_later(self._reply_finished, reply, task)
        )
        await self._populate_agents()

    def _resolve_choice(self, conversation_id: str, text: str) -> str | None:
        choices = self._pending_choices.get(conversation_id, ())
        if not choices:
            return text
        if not text.isdigit():
            self._pending_choices.pop(conversation_id, None)
            return text
        index = int(text) - 1
        if not 0 <= index < len(choices):
            return None
        chosen = choices[index]
        self._pending_choices.pop(conversation_id, None)
        return f'The user selected "{chosen.title}" (id {chosen.id}).'

    async def _answer(
        self, reply: Reply, history: list[Message], question: str, grounded: bool
    ) -> None:
        try:
            async for event in reply.runtime.session.respond(
                history, question, reply.profile.identity, grounded
            ):
                await self._apply_event(reply, event)
        except asyncio.CancelledError:
            reply.content = "Response cancelled. Nothing was changed."
            reply.choices = ()
        except AGENT_FAILURES as error:
            detail = plain_text(str(error)) or type(error).__name__
            reply.content = f"Something went wrong while answering: {detail}"
            reply.choices = ()
        finally:
            await self._file_reply(reply)

    async def _apply_event(self, reply: Reply, event: AgentEvent) -> None:
        if event.kind == "status":
            reply.status = event.text
            if self._is_current(reply):
                self._status().update(event.text)
        elif event.kind == "token":
            reply.content += event.text
            await self._render_reply(reply)
        elif event.kind == "evidence" and event.evidence:
            reply.evidence.append(event.evidence)
        elif event.kind == "choice":
            reply.choices = event.candidates
            reply.content = choice_markdown(event.text, event.candidates)
            await self._render_reply(reply)
        elif event.kind == "action" and event.action:
            reply.action = event.action
            reply.content = event.text
            await self._render_reply(reply)
        elif event.kind == "done":
            reply.content = event.text

    async def _render_reply(self, reply: Reply) -> None:
        if reply.widget is None:
            return
        await reply.widget.update(draft_markdown(reply.profile.name, reply.content))
        if self._is_current(reply):
            self._scroll_to_end()

    async def _file_reply(self, reply: Reply) -> None:
        """Store the answer with its question, then show it if still in view."""
        reply.finished = True
        message = Message("assistant", reply.content or EMPTY_REPLY)
        repository = self._service.store.for_agent(reply.agent_id)
        stored = repository.add_message(
            reply.conversation_id, message, tuple(reply.evidence)
        )
        if stored and reply.choices:
            self._pending_choices[reply.conversation_id] = reply.choices
        if self._is_current(reply):
            await self._finish_on_screen(reply, message)
        elif stored:
            self.notify(f"{reply.profile.name} finished replying.")
        reply.widget = None

    async def _finish_on_screen(self, reply: Reply, message: Message) -> None:
        if reply.widget is not None and reply.widget.is_mounted:
            await reply.widget.update(message_markdown(message, reply.profile.name))
            for item in reply.evidence:
                await self._transcript().mount(evidence_panel(item))
            if reply.action:
                await self._transcript().mount(OfferButton(reply.action))
        else:
            await self._show_conversation()
        self._status().update("Ready")
        self._scroll_to_end()

    def _reply_finished(self, reply: Reply, task: asyncio.Task[None]) -> None:
        if self._replies.get(reply.conversation_id) is reply:
            del self._replies[reply.conversation_id]
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            self.notify(f"{reply.profile.name} failed: {failure!r}", severity="error")
        self.run_worker(self._after_reply())

    async def _after_reply(self) -> None:
        await self._runtimes.close_unused(self._busy_runtimes())
        await self._populate_agents()

    def _is_current(self, reply: Reply) -> bool:
        return bool(
            self._profile
            and self._conversation
            and self._profile.id == reply.agent_id
            and self._conversation.id == reply.conversation_id
        )

    def _cancel_reply(self, conversation_id: str) -> None:
        reply = self._replies.get(conversation_id)
        if reply and reply.running and reply.task:
            reply.task.cancel()

    # -- rendering ----------------------------------------------------------

    async def _show_empty_state(self) -> None:
        self._stash_draft()
        self._profile = None
        self._repository = None
        self._main.query_one("#identity", Static).update("Welcome to Olympus")
        self._status().update("Add an agent to begin")
        self._composer().disabled = True
        self._main.query_one("#send", Button).disabled = True
        transcript = self._transcript()
        self._detach_replies()
        await transcript.remove_children()
        await transcript.mount(
            Markdown(
                "# Your agents, one place\n\n"
                "Choose **＋ Add**, or press **Ctrl+A**, to configure an installed "
                "agent. Olympus keeps each agent's conversations and credentials "
                "isolated.",
                id="empty-state",
            )
        )

    async def _show_conversation(self) -> None:
        if self._profile is None or self._repository is None or self._chat is None:
            await self._show_empty_state()
            return
        transcript = self._transcript()
        self._detach_replies()
        await transcript.remove_children()
        conversation_id = self._chat.conversation.id
        messages = self._repository.messages(conversation_id)
        evidence_rows = self._repository.message_evidence(conversation_id)
        if not messages:
            await transcript.mount(Markdown(self._welcome_markdown(self._profile)))
        for message, evidence in zip(messages, evidence_rows, strict=True):
            await self._mount_message(message, self._profile.name)
            for item in evidence:
                await transcript.mount(evidence_panel(item))
        reply = self._replies.get(conversation_id)
        if reply and reply.running:
            await self._mount_reply(reply)
            self._status().update(reply.status or "Thinking…")
        else:
            self._status().update("Ready")
        self._scroll_to_end()

    def _welcome_markdown(self, profile: AgentProfile) -> str:
        definition = self._service.registry.provider(profile.kind).definition
        return (
            f"# {profile.name}\n\n{definition.description}. "
            "Ask a question, or press **Ctrl+K** for commands."
        )

    def _detach_replies(self) -> None:
        """Forget reply widgets before the transcript they live in is cleared."""
        for reply in self._replies.values():
            reply.widget = None

    async def _mount_reply(self, reply: Reply) -> None:
        widget = Markdown(
            draft_markdown(reply.profile.name, reply.content),
            classes="message assistant",
        )
        await self._transcript().mount(widget)
        reply.widget = widget
        self._scroll_to_end()

    async def _mount_message(self, message: Message, agent_name: str) -> None:
        css_class = "user" if message.role == "user" else "assistant"
        await self._transcript().mount(
            Markdown(
                message_markdown(message, agent_name), classes=f"message {css_class}"
            )
        )
        self._scroll_to_end()

    async def _local_message(self, content: str) -> None:
        await self._transcript().mount(
            Markdown(plain_text(content), classes="message assistant")
        )
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        self._transcript().scroll_end(animate=False)

    # -- local commands -----------------------------------------------------

    async def _handle_command(self, command: Command) -> None:
        handlers: dict[str, Callable[[str], Coroutine[Any, Any, None]]] = {
            "new": self._command_new,
            "agents": self._command_agents,
            "agent": self._command_agent,
            "history": self._command_history,
            "open": self._command_open,
            "name": self._command_name,
            "persona": self._command_persona,
            "style": self._command_style,
            "export": self._command_export,
            "clear": self._command_clear,
            "file": self._command_file,
            "help": self._command_help,
        }
        handler = handlers.get(command.name)
        if handler is None:
            await self._local_message(f"Unknown command: `/{command.name}`\n\n{HELP}")
        else:
            await handler(command.argument)

    async def _command_new(self, argument: str) -> None:
        if argument or self._repository is None or self._chat is None:
            await self._local_message("Usage: `/new`")
            return
        conversation = self._repository.draft_or_create()
        if self._repository.messages(conversation.id):
            conversation = self._repository.create_conversation()
        self._chat.conversation = conversation
        await self._show_conversation()

    async def _command_agents(self, argument: str) -> None:
        if argument:
            await self._local_message("Usage: `/agents`")
        else:
            self.action_agents()

    async def _command_agent(self, argument: str) -> None:
        profile = self._agent_by_reference(argument) if argument else None
        if profile is None:
            await self._local_message(
                "Provide a sidebar number, or one unambiguous agent name or ID prefix."
            )
        else:
            await self._activate(profile)

    async def _command_history(self, argument: str) -> None:
        if self._repository and self._profile:
            self.push_screen(
                HistoryScreen(self._repository, self._profile.name, argument),
                self._history_closed,
            )

    async def _command_open(self, argument: str) -> None:
        if self._repository is None or self._chat is None:
            return
        conversation = self._repository.conversation(argument) if argument else None
        if conversation is None:
            await self._local_message("Provide one unambiguous conversation ID prefix.")
            return
        self._chat.conversation = conversation
        await self._show_conversation()

    async def _command_name(self, argument: str) -> None:
        if not argument:
            self.action_identity()
            return
        await self._save_identity(name=argument)

    async def _command_persona(self, argument: str) -> None:
        if not argument:
            self.action_identity()
            return
        await self._save_identity(persona=argument)

    async def _command_style(self, argument: str) -> None:
        await self._save_identity(response_style=argument)

    async def _save_identity(self, **changes: object) -> None:
        if self._profile is None:
            return
        try:
            profile = self._service.store.save_agent(
                fresh_profile(self._profile, **changes)
            )
        except StoreError as error:
            await self._local_message(str(error))
            return
        self._profile = profile
        self._refresh_identity()
        await self._populate_agents()
        await self._local_message("Agent preferences updated.")

    async def _command_export(self, argument: str) -> None:
        if self._repository is None or self._conversation is None:
            return
        target = self._conversation
        if argument:
            target = self._repository.conversation(argument)
            if target is None:
                await self._local_message("Conversation ID is missing or ambiguous.")
                return
        try:
            exported = self._repository.export_markdown(target.id)
        except StoreError as error:
            await self._local_message(str(error))
        else:
            await self._local_message(f"Exported to `{exported}`.")

    async def _command_clear(self, argument: str) -> None:
        if self._repository is None or self._profile is None:
            return
        if argument == "all":
            self.push_screen(
                ConfirmScreen(f"Delete every {self._profile.name} conversation?"),
                self._clear_all_confirmed,
            )
            return
        target = (
            self._repository.conversation(argument) if argument else self._conversation
        )
        if target is None:
            await self._local_message("Conversation ID is missing or ambiguous.")
            return
        self.push_screen(
            ConfirmScreen(f'Delete "{target.title}"?'),
            lambda approved: self._clear_one_confirmed(approved, target.id),
        )

    async def _command_help(self, argument: str) -> None:
        await self._local_message(HELP if not argument else "Usage: `/help`")

    async def _command_file(self, argument: str) -> None:
        self._review_folder(argument)

    # -- reviewing a folder -------------------------------------------------

    def _workflow(self) -> FolderWorkflow | None:
        runtime = self._runtime(self._profile) if self._profile else None
        return runtime.workflow if runtime else None

    def _review_folder(self, initial: str) -> None:
        """Pick a folder, review the agent's proposals, then file a summary."""
        if self._profile is None or self._conversation is None:
            return
        workflow = self._workflow()
        if workflow is None:
            self.notify(
                f"{self._profile.name} cannot file from a folder. Turn filing on "
                "in its settings under Ctrl+A.",
                severity="warning",
            )
            return
        if self._refuse_input("/file"):
            return
        target = (self._profile, self._conversation.id)
        self.push_screen(
            FolderPickerScreen(
                workflow, initial or f"{self._start_folder}/", self._start_folder
            ),
            lambda request: self._folder_chosen(workflow, target, request),
        )

    def _folder_chosen(
        self,
        workflow: FolderWorkflow,
        target: tuple[AgentProfile, str],
        request: FolderRequest | None,
    ) -> None:
        if request is None:
            return
        self.push_screen(
            ReviewBoardScreen(workflow, request, target[0].name),
            lambda proposals: self.run_worker(
                self._file_review(workflow, target, request, proposals or [])
            ),
        )

    async def _file_review(
        self,
        workflow: FolderWorkflow,
        target: tuple[AgentProfile, str],
        request: FolderRequest,
        proposals: list[Proposal],
    ) -> None:
        """Record the review in the conversation it was started from."""
        if not proposals:
            return
        profile, conversation_id = target
        repository = self._service.store.for_agent(profile.id)
        asked = Message("user", f"{workflow.title}: {request.folder}")
        summary = Message(
            "assistant", summary_markdown(workflow.title, request.folder, proposals)
        )
        if repository.add_message(conversation_id, asked):
            repository.add_message(
                conversation_id, summary, summary_evidence(request.folder, proposals)
            )
        if (
            self._profile
            and self._profile.id == profile.id
            and self._conversation
            and self._conversation.id == conversation_id
        ):
            await self._show_conversation()

    # -- actions ------------------------------------------------------------

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        """The Ctrl+K palette: every agent and recent chat, fuzzy-searchable."""
        yield SystemCommand(
            "Manage agents", "Add, edit, remove, or restore agents", self.action_agents
        )
        for number, profile in enumerate(self._service.store.agents(), 1):
            yield SystemCommand(
                f"Switch to {profile.name}",
                f"Agent {number} · {profile.model or self._defaults.model}",
                partial(self._switch_to, profile),
            )
        yield from self._conversation_commands()
        yield from self._workflow_commands()
        yield SystemCommand(
            "Toggle agent sidebar",
            "Show or hide navigation",
            self.action_toggle_sidebar,
        )
        # Textual's own: Theme, Keys, Maximize, Screenshot. Its Quit would
        # duplicate ours, which also closes the agents.
        for command in super().get_system_commands(screen):
            if command.title != "Quit":
                yield command
        yield SystemCommand("Quit Olympus", "Close agents and exit", self.exit)

    def _remember_theme(self, theme: Theme) -> None:
        try:
            self._service.store.save_theme(theme.name)
        except (OlympusError, sqlite3.Error) as error:
            self.notify(f"Could not save the theme: {error}", severity="warning")

    def _conversation_commands(self) -> Iterable[SystemCommand]:
        if self._profile is None or self._repository is None:
            return
        name = self._profile.name
        yield SystemCommand(
            "New conversation", f"Start fresh with {name}", self.action_new
        )
        yield SystemCommand(
            "Conversation history", "Search and resume", self.action_history
        )
        yield SystemCommand(
            "Conversation style",
            "Name, persona, and answer depth",
            self.action_identity,
        )
        for conversation in self._repository.conversations()[:10]:
            yield SystemCommand(
                f"Open: {conversation.title}",
                f"{name} · {format_timestamp(conversation.updated_at)}",
                partial(self._show_conversation_later, conversation),
            )

    def _workflow_commands(self) -> Iterable[SystemCommand]:
        if self._profile is None:
            return
        runtime = self._runtimes.cached(self._profile.id)
        if runtime and runtime.workflow:
            yield SystemCommand(
                f"{runtime.workflow.title}…",
                f"{self._profile.name} proposes, you approve · Ctrl+O",
                self.action_review_folder,
            )

    def _switch_to(self, profile: AgentProfile) -> None:
        self.run_worker(self._activate(profile))

    def action_new(self) -> None:
        if self._profile:
            self.run_worker(self._command_new(""))

    def action_history(self) -> None:
        self.run_worker(self._command_history(""))

    def action_identity(self) -> None:
        if self._profile:
            self.push_screen(IdentityScreen(self._profile), self._identity_closed)

    def action_agents(self) -> None:
        self.push_screen(
            AgentManagerScreen(
                self._service,
                self._defaults,
                self._settings,
                self._profile.id if self._profile else "",
            ),
            self._agents_closed,
        )

    def action_focus_agents(self) -> None:
        """Portable keyboard switching: Ctrl+G, then arrows and Enter."""
        sidebar = self._sidebar()
        if not sidebar.display:
            sidebar.display = True
            self._sidebar_forced = True
        self._main.query_one("#agent-list", ListView).focus()

    def action_toggle_sidebar(self) -> None:
        sidebar = self._sidebar()
        sidebar.display = not sidebar.display
        self._sidebar_forced = sidebar.display

    def action_switch_agent(self, number: int) -> None:
        profile = self._agent_by_reference(str(number))
        if profile:
            self._switch_to(profile)

    def action_previous_input(self) -> None:
        if not self._input_history:
            return
        self._input_index = max(0, self._input_index - 1)
        self._composer().text = self._input_history[self._input_index]

    def action_next_input(self) -> None:
        if not self._input_history:
            return
        self._input_index = min(len(self._input_history), self._input_index + 1)
        self._composer().text = (
            self._input_history[self._input_index]
            if self._input_index < len(self._input_history)
            else ""
        )

    def action_review_folder(self) -> None:
        self._review_folder("")

    def action_cancel_reply(self) -> None:
        if self._conversation:
            self._cancel_reply(self._conversation.id)

    # -- screen results -----------------------------------------------------

    def _history_closed(self, conversation: Conversation | None) -> None:
        if conversation:
            self._show_conversation_later(conversation)
        elif (
            self._repository
            and self._conversation
            and self._repository.conversation(self._conversation.id) is None
        ):
            self._show_conversation_later(self._repository.draft_or_create())

    def _identity_closed(self, profile: AgentProfile | None) -> None:
        if profile:
            self.run_worker(self._save_profile_from_screen(profile))

    async def _save_profile_from_screen(self, profile: AgentProfile) -> None:
        try:
            self._profile = self._service.save(profile, {})
        except OlympusError as error:
            self.notify(str(error), severity="error")
            return
        self._refresh_identity()
        await self._populate_agents()

    def _agents_closed(self, result: AgentManagerResult | None) -> None:
        self.run_worker(self._apply_agent_changes(result or AgentManagerResult()))

    async def _apply_agent_changes(self, result: AgentManagerResult) -> None:
        self._defaults = self._service.store.ollama_defaults(self._settings)
        if result.changed:
            self._forget_departed_agents()
            self._runtimes.retire_all()
            await self._runtimes.close_unused(self._busy_runtimes())
        store = self._service.store
        candidates = (
            store.agent(result.selected.id) if result.selected else None,
            store.agent(self._profile.id) if self._profile else None,
            self._first_openable(),
        )
        for target in candidates:
            if target is not None and await self._activate(target):
                return
        await self._show_empty_state()
        await self._populate_agents()

    def _forget_departed_agents(self) -> None:
        """Stop replies and drop state for agents that were archived or deleted."""
        active = {profile.id for profile in self._service.store.agents()}
        for conversation_id, reply in self._replies.items():
            if reply.agent_id not in active:
                self._cancel_reply(conversation_id)
        for agent_id in [key for key in self._chats if key not in active]:
            del self._chats[agent_id]
        if self._profile and self._profile.id not in active:
            self._profile = None
            self._repository = None

    def _clear_one_confirmed(self, approved: bool | None, conversation_id: str) -> None:
        if not approved or self._repository is None:
            return
        self._cancel_reply(conversation_id)
        self._repository.delete_conversation(conversation_id)
        if self._conversation and conversation_id == self._conversation.id:
            self._show_conversation_later(self._repository.draft_or_create())
        self.notify("Conversation deleted.")

    def _clear_all_confirmed(self, approved: bool | None) -> None:
        if not approved or self._repository is None:
            return
        for conversation in self._repository.conversations():
            self._cancel_reply(conversation.id)
        count = self._repository.clear_conversations()
        self._show_conversation_later(self._repository.draft_or_create())
        self.notify(f"Deleted {count} conversations.")
