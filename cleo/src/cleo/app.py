"""Cleo's keyboard-first Textual interface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Select,
    Static,
    TextArea,
)

from cleo.agent import Librarian
from cleo.commands import HELP, Command, parse_command
from cleo.config import DEFAULT_MODEL, ConfigurationError, Settings
from cleo.domain import Candidate, Conversation, Evidence, Identity, Message
from cleo.nineveh import NinevehCatalogClient
from cleo.ollama import OllamaChatModel
from cleo.ports import ConversationRepository, StoreError
from cleo.presentation import format_timestamp, plain_text
from cleo.store import ConversationStore
from cleo.tools import ReadOnlyToolRegistry

MAX_QUESTION_LENGTH = 4_000


class Composer(TextArea):
    """A multiline editor where Enter sends and Shift+Enter inserts a line."""

    BINDINGS: ClassVar = [
        Binding("enter", "submit", "Send", show=False, priority=True),
        Binding("shift+enter", "newline", "New line", show=False, priority=True),
    ]

    class Submitted(TextualMessage):
        pass

    def action_submit(self) -> None:
        self.post_message(self.Submitted())

    def action_newline(self) -> None:
        self.insert("\n")


class ConfirmScreen(ModalScreen[bool]):
    """Require a human click or keypress before clearing history."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._prompt)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Delete", id="confirm", variant="error")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class HistoryScreen(Screen[Conversation | None]):
    """Searchable, resumable conversation history."""

    BINDINGS: ClassVar = [
        Binding("escape", "close", "Back"),
        Binding("enter", "open", "Open"),
        Binding("d", "delete", "Delete"),
        Binding("e", "export", "Export"),
    ]

    def __init__(self, store: ConversationRepository, query: str = "") -> None:
        super().__init__()
        self._store = store
        self._initial_query = query
        self._conversations: dict[str, Conversation] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            value=self._initial_query,
            placeholder="Search titles and transcripts",
            id="history-search",
        )
        yield ListView(id="history-list")
        with Horizontal(id="history-actions"):
            yield Button("Open", id="history-open", variant="primary")
            yield Button("Delete", id="history-delete", variant="error")
            yield Button("Export", id="history-export")
            yield Button("Back", id="history-back")
        yield Label("Enter open · d delete · e export · Esc back", id="history-help")
        yield Footer()

    async def on_mount(self) -> None:
        await self._populate(self._initial_query)
        self.query_one("#history-search", Input).focus()

    @on(Input.Changed, "#history-search")
    async def search_changed(self, event: Input.Changed) -> None:
        await self._populate(event.value)

    @on(ListView.Selected, "#history-list")
    def selected(self, event: ListView.Selected) -> None:
        conversation = self._conversation_for(event.item)
        if conversation:
            self.dismiss(conversation)

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "history-open": self.action_open,
            "history-delete": self.action_delete,
            "history-export": self.action_export,
            "history-back": self.action_close,
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    async def _populate(self, query: str) -> None:
        view = self.query_one("#history-list", ListView)
        await view.clear()
        self._conversations.clear()
        for conversation in self._store.conversations(query):
            item_id = f"conversation-{conversation.id}"
            self._conversations[item_id] = conversation
            label = (
                f"{conversation.title}\n"
                f"{format_timestamp(conversation.updated_at)} · {conversation.id[:8]}"
            )
            await view.append(ListItem(Label(label), id=item_id))

    def _highlighted(self) -> Conversation | None:
        view = self.query_one("#history-list", ListView)
        return self._conversation_for(view.highlighted_child)

    def _conversation_for(self, item: ListItem | None) -> Conversation | None:
        return self._conversations.get(item.id or "") if item else None

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open(self) -> None:
        conversation = self._highlighted()
        if conversation:
            self.dismiss(conversation)

    def action_delete(self) -> None:
        conversation = self._highlighted()
        if conversation is None:
            return

        def confirmed(approved: bool | None) -> None:
            if approved:
                self._store.delete_conversation(conversation.id)
                self.run_worker(
                    self._populate(self.query_one("#history-search", Input).value)
                )

        self.app.push_screen(
            ConfirmScreen(f'Delete "{conversation.title}" from Cleo history?'),
            confirmed,
        )

    def action_export(self) -> None:
        conversation = self._highlighted()
        if conversation is None:
            return
        try:
            target = self._store.export_markdown(conversation.id)
        except StoreError as error:
            self.notify(str(error), severity="error")
        else:
            self.notify(f"Exported to {target}")


class SettingsScreen(Screen[Identity | None]):
    """Edit the safe, user-controlled portion of the librarian identity."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, store: ConversationRepository, identity: Identity) -> None:
        super().__init__()
        self._store = store
        self._identity = identity

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="settings-panel"):
            yield Label("Librarian name")
            yield Input(value=self._identity.name, id="settings-name", max_length=40)
            yield Label("Persona instructions (tone and presentation only)")
            yield TextArea(self._identity.persona, id="settings-persona")
            yield Label("Response style")
            yield Select(
                (("Compact", "compact"), ("Detailed", "detailed")),
                value=self._identity.response_style,
                allow_blank=False,
                id="settings-style",
            )
            yield Markdown(id="settings-preview")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Save", id="settings-save", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._update_preview()
        self.query_one("#settings-name", Input).focus()

    @on(Input.Changed, "#settings-name")
    @on(TextArea.Changed, "#settings-persona")
    def changed(self) -> None:
        self._update_preview()

    @on(Select.Changed, "#settings-style")
    def style_changed(self) -> None:
        self._update_preview()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-cancel":
            self.dismiss(None)
            return
        name = self.query_one("#settings-name", Input).value
        persona = self.query_one("#settings-persona", TextArea).text
        style = self.query_one("#settings-style", Select).value
        try:
            identity = self._store.save_identity(name, persona, str(style))
        except StoreError as error:
            self.notify(str(error), severity="error")
        else:
            self.dismiss(identity)

    def _update_preview(self) -> None:
        name = self.query_one("#settings-name", Input).value.strip() or "Cleo"
        persona = self.query_one("#settings-persona", TextArea).text.strip()
        style = str(self.query_one("#settings-style", Select).value)
        preview = f"Preview: **{name}**"
        if persona:
            preview += f" — {persona[:120]}"
        preview += f" · {style or 'compact'} answers"
        self.query_one("#settings-preview", Markdown).update(preview)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CleoApp(App[None]):
    """Single-pane chat with history and settings kept one gesture away."""

    TITLE = "Cleo"
    SUB_TITLE = "Read-only Nineveh librarian"
    CSS = """
    Screen { background: $surface; }
    #identity { height: 1; padding: 0 2; color: $text-muted; }
    #transcript { height: 1fr; padding: 1 2; }
    .message { margin: 0 0 1 0; padding: 1 2; }
    .user { background: $primary 15%; border-left: thick $primary; }
    .assistant { background: $boost; border-left: thick $success; }
    .evidence { margin: 0 2 1 4; color: $text-muted; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    #compose-row { height: 6; margin: 0 2 1 2; }
    #composer { width: 1fr; height: 5; border: tall $accent; }
    #send { width: 10; height: 5; margin-left: 1; }
    #history-search { margin: 1 2; }
    #history-list { height: 1fr; margin: 0 2; }
    #history-actions { height: 3; margin: 0 2; }
    #history-actions Button { margin-right: 1; }
    #history-help { height: 1; margin: 1 2; color: $text-muted; }
    #settings-panel { width: 80%; height: 80%; margin: 2 10; padding: 1 2; }
    #settings-persona { height: 1fr; min-height: 8; }
    #settings-preview { margin: 1 0; padding: 1; background: $boost; }
    #confirm-dialog {
        width: 64; height: auto; padding: 2; background: $panel;
        border: thick $error; align: center middle;
    }
    ConfirmScreen { align: center middle; background: $background 60%; }
    .dialog-buttons { height: 3; align-horizontal: right; margin-top: 1; }
    .dialog-buttons Button { margin-left: 1; }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+n", "new", "New"),
        Binding("ctrl+h", "history", "History"),
        Binding("ctrl+s", "settings", "Settings"),
        Binding("ctrl+up", "previous_input", "Previous input", show=False),
        Binding("ctrl+down", "next_input", "Next input", show=False),
        Binding("escape", "cancel_reply", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        agent: Librarian,
        store: ConversationRepository,
        close_async: tuple[Callable[[], Coroutine[Any, Any, None]], ...] = (),
        model: str = DEFAULT_MODEL,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._store = store
        self._close_async = close_async
        self._model = model
        self._identity = store.identity()
        self._conversation = store.draft_or_create()
        self._reply_task: asyncio.Task[None] | None = None
        self._pending_choice: tuple[Candidate, ...] = ()
        self._input_history: list[str] = []
        self._input_index = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="identity")
        yield VerticalScroll(id="transcript")
        yield Static("Ready", id="status")
        with Horizontal(id="compose-row"):
            yield Composer(id="composer", show_line_numbers=False)
            yield Button("Send", id="send", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self._refresh_identity()
        await self._show_conversation()
        self.query_one("#composer", TextArea).focus()

    async def on_unmount(self) -> None:
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            await self._reply_task
        for close in self._close_async:
            await close()
        self._store.close()

    @on(Composer.Submitted)
    async def composer_submitted(self) -> None:
        await self._submit()

    @on(Button.Pressed, "#send")
    async def send_pressed(self) -> None:
        await self._submit()

    def _refuse_input(self, text: str) -> bool:
        """Nothing worth sending, too much of it, or a reply already running."""
        if not text:
            return True
        if len(text) > MAX_QUESTION_LENGTH:
            self.notify(
                f"Messages may contain at most {MAX_QUESTION_LENGTH} characters.",
                severity="error",
            )
            return True
        if self._reply_task and not self._reply_task.done():
            self.notify("Cancel or wait for the current response.", severity="warning")
            return True
        return False

    async def _submit(self) -> None:
        composer = self.query_one("#composer", TextArea)
        text = composer.text.strip()
        if self._refuse_input(text):
            return
        composer.text = ""
        self._input_history.append(text)
        self._input_index = len(self._input_history)
        command = parse_command(text)
        if command:
            self._pending_choice = ()
            await self._handle_command(command)
            return
        question = self._resolve_choice(text)
        if question is None:
            await self._local_message(
                f"Pick a number between 1 and {len(self._pending_choice)}."
            )
            return
        if self._store.conversation(self._conversation.id) is None:
            self._conversation = self._store.draft_or_create()
        # Captured now: the reply may outlive the user's stay in this conversation.
        conversation_id = self._conversation.id
        history = self._store.messages(conversation_id)
        grounded = self._store.has_evidence(conversation_id)
        message = Message("user", text)
        self._store.add_message(conversation_id, message)
        await self._mount_message(message)
        self._reply_task = asyncio.create_task(
            self._answer(conversation_id, history, question, grounded)
        )

    def _resolve_choice(self, text: str) -> str | None:
        """Read a reply to a pending pick list. None means the number was invalid."""
        if not self._pending_choice:
            return text
        if not text.isdigit():
            self._pending_choice = ()
            return text
        index = int(text) - 1
        if not 0 <= index < len(self._pending_choice):
            return None
        chosen = self._pending_choice[index]
        self._pending_choice = ()
        return f'Use the series "{chosen.title}" (id {chosen.id}).'

    async def _answer(
        self,
        conversation_id: str,
        history: list[Message],
        question: str,
        grounded: bool,
    ) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        status = self.query_one("#status", Static)
        response = Markdown(
            self._assistant_draft_markdown(""), classes="message assistant"
        )
        await transcript.mount(response)
        content = ""
        evidence: list[Evidence] = []
        choices: tuple[Candidate, ...] = ()
        try:
            async for event in self._agent.respond(
                history, question, self._identity, grounded
            ):
                if event.kind == "status":
                    status.update(event.text)
                elif event.kind == "token":
                    content += event.text
                    await response.update(self._assistant_draft_markdown(content))
                    transcript.scroll_end(animate=False)
                elif event.kind == "evidence" and event.evidence:
                    evidence.append(event.evidence)
                elif event.kind == "choice":
                    choices = event.candidates
                    content = self._choice_markdown(event.text, choices)
                elif event.kind == "done":
                    content = event.text
        except asyncio.CancelledError:
            content = "Response cancelled. Nothing was changed."
            choices = ()
        finally:
            await self._file_reply(
                conversation_id, content, evidence, choices, transcript, response
            )
            status.update("Ready")
            transcript.scroll_end(animate=False)

    async def _file_reply(
        self,
        conversation_id: str,
        content: str,
        evidence: list[Evidence],
        choices: tuple[Candidate, ...],
        transcript: VerticalScroll,
        response: Markdown,
    ) -> None:
        """Keep the answer, then show it if there is still anywhere to show it.

        A reply outlives both the screen it was typed into (quit mid answer)
        and sometimes the conversation it belongs to (cleared mid answer).
        Writing to the widgets captured by the caller is harmless once they are
        detached; re-querying the DOM from here is not, and was how quitting
        mid-reply used to end in a traceback.
        """
        if not content:
            return
        message = Message("assistant", content)
        stored = self._store.add_message(conversation_id, message, tuple(evidence))
        if stored and conversation_id == self._conversation.id:
            self._pending_choice = choices
        await response.update(self._message_markdown(message))
        await self._mount_evidence(transcript, evidence)

    @staticmethod
    def _choice_markdown(prompt: str, choices: tuple[Candidate, ...]) -> str:
        lines = [prompt, ""]
        lines += [f"{number}. {item.title}" for number, item in enumerate(choices, 1)]
        lines += ["", "Reply with a number."]
        return "\n".join(lines)

    async def _mount_message(self, message: Message) -> None:
        css_class = "user" if message.role == "user" else "assistant"
        await self.query_one("#transcript", VerticalScroll).mount(
            Markdown(self._message_markdown(message), classes=f"message {css_class}")
        )
        self._scroll_to_end()

    async def _mount_evidence(
        self, transcript: VerticalScroll, evidence: list[Evidence]
    ) -> None:
        for item in evidence:
            details = {"request": item.arguments, "response": item.payload}
            # Catalog titles and filenames are untrusted; they are displayed,
            # never interpreted by the terminal.
            payload = plain_text(json.dumps(details, indent=2, ensure_ascii=False))
            title = f"Nineveh evidence · {item.tool} · {format_timestamp(item.retrieved_at)}"
            await transcript.mount(
                Collapsible(
                    Markdown(f"```json\n{payload}\n```"),
                    title=title,
                    classes="evidence",
                )
            )

    async def _show_conversation(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        self._pending_choice = ()
        messages = self._store.messages(self._conversation.id)
        evidence_rows = self._store.message_evidence(self._conversation.id)
        if not messages:
            await transcript.mount(
                Markdown(
                    f"# {self._identity.name}\n\n"
                    "Ask me about series recorded in Nineveh. Type `/help` for local commands."
                )
            )
        for message, evidence in zip(messages, evidence_rows, strict=True):
            await self._mount_message(message)
            await self._mount_evidence(transcript, list(evidence))
        self._scroll_to_end()

    async def _handle_command(self, command: Command) -> None:
        handlers: dict[str, Callable[[str], Coroutine[Any, Any, None]]] = {
            "new": self._command_new,
            "history": self._command_history,
            "open": self._command_open,
            "name": self._command_name,
            "persona": self._command_persona,
            "style": self._command_style,
            "export": self._command_export,
            "clear": self._command_clear,
            "help": self._command_help,
        }
        handler = handlers.get(command.name)
        if handler is None:
            await self._local_message(f"Unknown command: `/{command.name}`\n\n{HELP}")
            return
        await handler(command.argument)

    async def _command_new(self, argument: str) -> None:
        if argument:
            await self._local_message("Usage: `/new`")
            return
        self._conversation = self._store.draft_or_create()
        await self._show_conversation()

    async def _command_history(self, argument: str) -> None:
        self.push_screen(HistoryScreen(self._store, argument), self._history_closed)

    async def _command_open(self, argument: str) -> None:
        conversation = self._store.conversation(argument) if argument else None
        if conversation is None:
            await self._local_message("Provide one unambiguous conversation ID prefix.")
            return
        self._conversation = conversation
        await self._show_conversation()

    async def _command_name(self, argument: str) -> None:
        if not argument:
            self.action_settings()
            return
        try:
            self._identity = self._store.save_identity(
                argument, self._identity.persona, self._identity.response_style
            )
        except StoreError as error:
            await self._local_message(str(error))
        else:
            self._refresh_identity()
            await self._local_message(f"My name is now **{self._identity.name}**.")

    async def _command_persona(self, argument: str) -> None:
        if not argument:
            self.action_settings()
            return
        try:
            self._identity = self._store.save_identity(
                self._identity.name, argument, self._identity.response_style
            )
        except StoreError as error:
            await self._local_message(str(error))
        else:
            await self._local_message("Persona instructions updated.")

    async def _command_style(self, argument: str) -> None:
        try:
            self._identity = self._store.save_identity(
                self._identity.name, self._identity.persona, argument
            )
        except StoreError as error:
            await self._local_message(str(error))
        else:
            await self._local_message(
                f"Response style set to **{self._identity.response_style}**."
            )

    async def _command_export(self, argument: str) -> None:
        target = self._conversation
        if argument:
            target = self._store.conversation(argument)
            if target is None:
                await self._local_message("Conversation ID is missing or ambiguous.")
                return
        try:
            exported = self._store.export_markdown(target.id)
        except StoreError as error:
            await self._local_message(str(error))
        else:
            await self._local_message(f"Exported to `{exported}`.")

    async def _command_clear(self, argument: str) -> None:
        if argument == "all":
            self.push_screen(
                ConfirmScreen("Delete every conversation from Cleo history?"),
                self._clear_all_confirmed,
            )
            return
        target = self._store.conversation(argument) if argument else self._conversation
        if target is None:
            await self._local_message("Conversation ID is missing or ambiguous.")
            return
        self.push_screen(
            ConfirmScreen(f'Delete "{target.title}" from Cleo history?'),
            lambda approved: self._clear_one_confirmed(approved, target),
        )

    async def _command_help(self, argument: str) -> None:
        await self._local_message(HELP if not argument else "Usage: `/help`")

    async def _local_message(self, content: str) -> None:
        await self.query_one("#transcript", VerticalScroll).mount(
            Markdown(plain_text(content), classes="message assistant")
        )
        self._scroll_to_end()

    # Everything below renders text Cleo did not author. The terminal must
    # display it, never act on it, so the escape strip happens here rather
    # than being assumed of whatever produced the string.
    def _message_markdown(self, message: Message) -> str:
        label = "You" if message.role == "user" else self._identity.name
        return (
            f"**{label}** · {format_timestamp(message.created_at)}\n\n"
            f"{plain_text(message.content)}"
        )

    def _assistant_draft_markdown(self, content: str) -> str:
        return f"**{self._identity.name}**\n\n{plain_text(content)}"

    def _history_closed(self, conversation: Conversation | None) -> None:
        if conversation:
            self._conversation = conversation
        elif self._store.conversation(self._conversation.id) is None:
            self._conversation = self._store.draft_or_create()
        else:
            return
        self.run_worker(self._show_conversation())

    def _settings_closed(self, identity: Identity | None) -> None:
        if identity:
            self._identity = identity
            self._refresh_identity()

    def _clear_one_confirmed(
        self, approved: bool | None, conversation: Conversation
    ) -> None:
        if not approved:
            return
        self._store.delete_conversation(conversation.id)
        if conversation.id == self._conversation.id:
            self._conversation = self._store.draft_or_create()
            self.run_worker(self._show_conversation())
        self.notify("Conversation deleted.")

    def _clear_all_confirmed(self, approved: bool | None) -> None:
        if not approved:
            return
        count = self._store.clear_conversations()
        self._conversation = self._store.draft_or_create()
        self.run_worker(self._show_conversation())
        self.notify(f"Deleted {count} conversations.")

    def _refresh_identity(self) -> None:
        self.title = self._identity.name
        self.query_one("#identity", Static).update(
            f"{self._identity.name} · read-only Nineveh catalog · {self._model}"
        )

    def _scroll_to_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def action_new(self) -> None:
        self.run_worker(self._command_new(""))

    def action_history(self) -> None:
        self.push_screen(HistoryScreen(self._store), self._history_closed)

    def action_settings(self) -> None:
        self.push_screen(
            SettingsScreen(self._store, self._identity), self._settings_closed
        )

    def action_previous_input(self) -> None:
        if not self._input_history:
            return
        self._input_index = max(0, self._input_index - 1)
        self.query_one("#composer", TextArea).text = self._input_history[
            self._input_index
        ]

    def action_next_input(self) -> None:
        if not self._input_history:
            return
        self._input_index = min(len(self._input_history), self._input_index + 1)
        value = (
            self._input_history[self._input_index]
            if self._input_index < len(self._input_history)
            else ""
        )
        self.query_one("#composer", TextArea).text = value

    def action_cancel_reply(self) -> None:
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()


def build_app(settings: Settings) -> CleoApp:
    store = ConversationStore(settings.data_dir)
    catalog = NinevehCatalogClient(settings)
    model = OllamaChatModel(settings.ollama_url, settings.model)
    agent = Librarian(model, ReadOnlyToolRegistry(catalog))
    return CleoApp(agent, store, (model.aclose, catalog.aclose), settings.model)


def main() -> None:
    try:
        app = build_app(Settings.load())
    except (ConfigurationError, StoreError) as error:
        raise SystemExit(f"cleo: {error}") from error
    app.run()
