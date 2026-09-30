"""Focused screens for history, agent management, and configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from olympus.config import Settings
from olympus.domain import AgentProfile, Conversation, OllamaDefaults
from olympus.ports import ConversationRepository, OlympusError, StoreError
from olympus.presentation import format_timestamp
from olympus.services import AgentService, fresh_profile
from olympus.store import OlympusStore


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, confirm_label: str = "Delete") -> None:
        super().__init__()
        self._prompt = prompt
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="confirm-dialog"):
            yield Label(self._prompt, classes="dialog-title")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, id="confirm", variant="error")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class RemoveAgentScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, profile: AgentProfile, conversations: int = 0) -> None:
        super().__init__()
        self._profile = profile
        self._conversations = conversations

    def compose(self) -> ComposeResult:
        count = self._conversations
        chats = f"{count} conversation{'' if count == 1 else 's'}"
        with Vertical(classes="dialog", id="remove-agent-dialog"):
            yield Label(f"Remove {self._profile.name}?", classes="dialog-title")
            yield Static(
                f"Keep history archives the agent and its {chats}. "
                f"Delete everything also erases those {chats} and its credentials.",
                classes="dialog-copy",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="remove-cancel")
                yield Button("Keep history", id="remove-archive", variant="warning")
                yield Button("Delete everything", id="remove-delete", variant="error")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        result = {
            "remove-archive": "archive",
            "remove-delete": "delete",
        }.get(event.button.id or "")
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HistoryScreen(Screen[Conversation | None]):
    BINDINGS: ClassVar = [
        Binding("escape", "close", "Back"),
        Binding("enter", "open", "Open"),
        Binding("down", "focus_results", "Results", show=False),
        Binding("d", "delete", "Delete"),
        Binding("e", "export", "Export"),
    ]

    def __init__(
        self, store: ConversationRepository, agent_name: str, query: str = ""
    ) -> None:
        super().__init__()
        self._store = store
        self._agent_name = agent_name
        self._initial_query = query
        self._conversations: dict[str, Conversation] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="screen-panel"):
            yield Label(f"{self._agent_name} / Conversations", classes="screen-title")
            yield Input(
                value=self._initial_query,
                placeholder="Search titles and transcripts",
                id="history-search",
            )
            yield ListView(id="history-list")
            with Horizontal(classes="screen-actions"):
                yield Button("Open", id="history-open", variant="primary")
                yield Button("Export", id="history-export")
                yield Button("Delete", id="history-delete", variant="error")
                yield Button("Back", id="history-back")
            yield Label(
                "Type to search · Enter open · ↓ results, then d delete · e export",
                classes="hint",
            )
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
            await view.append(ListItem(Label(label, markup=False), id=item_id))
        if self._conversations:
            view.index = 0

    @on(Input.Submitted, "#history-search")
    def search_submitted(self) -> None:
        self.action_open()

    def action_focus_results(self) -> None:
        self.query_one("#history-list", ListView).focus()

    def _highlighted(self) -> Conversation | None:
        return self._conversation_for(
            self.query_one("#history-list", ListView).highlighted_child
        )

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
            ConfirmScreen(f'Delete "{conversation.title}"?'), confirmed
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


class IdentityScreen(Screen[AgentProfile | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, profile: AgentProfile) -> None:
        super().__init__()
        self._profile = profile

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="form-panel"):
            yield Label("Conversation style", classes="screen-title")
            yield Label("Agent name")
            yield Input(value=self._profile.name, id="identity-name", max_length=40)
            yield Label("Persona instructions")
            yield TextArea(self._profile.persona, id="identity-persona")
            yield Label("Response style")
            yield Select(
                (("Compact", "compact"), ("Detailed", "detailed")),
                value=self._profile.response_style,
                allow_blank=False,
                id="identity-style",
            )
            with Horizontal(classes="screen-actions"):
                yield Button("Cancel", id="identity-cancel")
                yield Button("Save", id="identity-save", variant="primary")
        yield Footer()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "identity-cancel":
            self.dismiss(None)
            return
        if event.button.id == "identity-save":
            self.dismiss(
                fresh_profile(
                    self._profile,
                    name=self.query_one("#identity-name", Input).value,
                    persona=self.query_one("#identity-persona", TextArea).text,
                    response_style=str(self.query_one("#identity-style", Select).value),
                )
            )

    def action_cancel(self) -> None:
        self.dismiss(None)


class AgentFormScreen(Screen[AgentProfile | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        service: AgentService,
        defaults: OllamaDefaults,
        profile: AgentProfile,
    ) -> None:
        super().__init__()
        self._service = service
        self._defaults = defaults
        self._profile = profile
        self._provider = service.registry.provider(profile.kind)

    def compose(self) -> ComposeResult:
        definition = self._provider.definition
        yield Header()
        with Vertical(classes="form-panel", id="agent-form"):
            yield Label(
                f"{'Edit' if self._exists else 'Add'} {definition.name}",
                classes="screen-title",
            )
            yield Static(
                self._subtitle(definition.description), classes="screen-subtitle"
            )
            yield Label("Display name")
            yield Input(value=self._profile.name, id="agent-name", max_length=40)
            yield Label("Persona instructions (optional)")
            yield TextArea(self._profile.persona, id="agent-persona")
            yield Label(f"Ollama URL (blank uses {self._defaults.url})")
            yield Input(value=self._profile.ollama_url, id="agent-ollama-url")
            yield Label(f"Model (blank uses {self._defaults.model})")
            yield Input(value=self._profile.model, id="agent-model")
            yield Label("Response style")
            yield Select(
                (("Compact", "compact"), ("Detailed", "detailed")),
                value=self._profile.response_style,
                allow_blank=False,
                id="agent-style",
            )
            for field in definition.fields:
                suffix = (
                    " (leave blank to keep current)"
                    if field.secret and self._exists
                    else ""
                )
                yield Label(f"{field.label}{suffix}")
                yield Input(
                    value=""
                    if field.secret
                    else self._profile.settings.get(field.key, ""),
                    placeholder=field.placeholder,
                    password=field.secret,
                    id=f"config-{field.key}",
                )
            yield Static("", id="agent-form-status")
            with Horizontal(classes="screen-actions"):
                yield Button("Cancel", id="agent-cancel")
                yield Button("Test connection", id="agent-test")
                yield Button("Save agent", id="agent-save", variant="primary")
        yield Footer()

    def _subtitle(self, description: str) -> str:
        return f"{description} · ID {self._profile.id}" if self._exists else description

    @property
    def _exists(self) -> bool:
        return self._service.store.agent(self._profile.id) is not None

    def _values(self) -> tuple[AgentProfile, dict[str, str]]:
        settings = dict(self._profile.settings)
        secrets: dict[str, str] = {}
        for field in self._provider.definition.fields:
            value = self.query_one(f"#config-{field.key}", Input).value.strip()
            if field.secret:
                secrets[field.key] = value
            else:
                settings[field.key] = value
        profile = fresh_profile(
            self._profile,
            name=self.query_one("#agent-name", Input).value,
            persona=self.query_one("#agent-persona", TextArea).text,
            response_style=str(self.query_one("#agent-style", Select).value),
            ollama_url=self.query_one("#agent-ollama-url", Input).value,
            model=self.query_one("#agent-model", Input).value,
            settings=settings,
        )
        return profile, secrets

    @on(Button.Pressed, "#agent-cancel")
    def cancel_pressed(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#agent-test")
    def test_pressed(self) -> None:
        """Probe in a worker, so Cancel and Esc stay responsive meanwhile.

        The worker belongs to this screen and is cancelled when it closes.
        """
        profile, secrets = self._values()
        self._status("Testing Nineveh and Ollama…")
        self.run_worker(self._test(profile, secrets), exclusive=True, group="probe")

    @on(Button.Pressed, "#agent-save")
    def save_pressed(self) -> None:
        profile, secrets = self._values()
        try:
            saved = self._service.save(profile, secrets)
        except OlympusError as error:
            self._status(str(error), "red")
        else:
            self.dismiss(saved)

    async def _test(self, profile: AgentProfile, secrets: dict[str, str]) -> None:
        try:
            await self._service.probe(profile, secrets, self._defaults)
        except OlympusError as error:
            self._status(str(error), "red")
        else:
            self._status("Connections look good.", "green")

    def _status(self, message: str, style: str = "") -> None:
        self.query_one("#agent-form-status", Static).update(Text(message, style=style))

    def action_cancel(self) -> None:
        self.dismiss(None)


class DefaultsScreen(Screen[OllamaDefaults | None]):
    """Edit the saved defaults, never the environment's session overrides."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, store: OlympusStore, settings: Settings) -> None:
        super().__init__()
        self._store = store
        self._settings = settings

    def compose(self) -> ComposeResult:
        saved = self._store.ollama_defaults()
        yield Header()
        with Vertical(classes="form-panel"):
            yield Label("Ollama defaults", classes="screen-title")
            yield Static("Agents can override either value individually.")
            yield Label("Server URL")
            yield Input(value=saved.url, id="defaults-url")
            yield Label("Model")
            yield Input(value=saved.model, id="defaults-model")
            yield Static(self._override_note(), id="defaults-note", classes="hint")
            yield Static("", id="defaults-status")
            with Horizontal(classes="screen-actions"):
                yield Button("Cancel", id="defaults-cancel")
                yield Button("Save", id="defaults-save", variant="primary")
        yield Footer()

    def _override_note(self) -> str:
        overrides = [
            name
            for name, value in (
                ("OLLAMA_URL", self._settings.ollama_url_override),
                ("OLYMPUS_MODEL", self._settings.model_override),
            )
            if value
        ]
        if not overrides:
            return ""
        return (
            f"{' and '.join(overrides)} set in the environment "
            "override the saved values for this session."
        )

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "defaults-cancel":
            self.dismiss(None)
            return
        try:
            saved = self._store.save_ollama_defaults(
                self.query_one("#defaults-url", Input).value,
                self.query_one("#defaults-model", Input).value,
            )
        except StoreError as error:
            status = self.query_one("#defaults-status", Static)
            status.update(Text(str(error), style="red"))
        else:
            self.dismiss(saved)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AgentTypeScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, service: AgentService) -> None:
        super().__init__()
        self._service = service
        self._kinds: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="type-dialog"):
            yield Label("Choose an agent type", classes="dialog-title")
            yield ListView(id="type-list")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="type-cancel")

    async def on_mount(self) -> None:
        view = self.query_one("#type-list", ListView)
        for provider in self._service.registry.providers():
            definition = provider.definition
            item_id = f"type-{definition.kind}"
            self._kinds[item_id] = definition.kind
            await view.append(
                ListItem(
                    Label(
                        f"{definition.glyph}  {definition.name}\n   {definition.description}"
                    ),
                    id=item_id,
                )
            )
        view.index = 0
        view.focus()

    @on(ListView.Selected, "#type-list")
    def selected(self, event: ListView.Selected) -> None:
        if event.item and event.item.id in self._kinds:
            self.dismiss(self._kinds[event.item.id])

    @on(Button.Pressed, "#type-cancel")
    def cancel_pressed(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True, slots=True)
class AgentManagerResult:
    selected: AgentProfile | None = None
    changed: bool = False


class AgentManagerScreen(Screen[AgentManagerResult]):
    BINDINGS: ClassVar = [
        Binding("escape", "close", "Back"),
        Binding("enter", "switch", "Open"),
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "remove", "Remove"),
        Binding("r", "restore", "Restore"),
        Binding("o", "defaults", "Ollama defaults"),
    ]

    def __init__(
        self,
        service: AgentService,
        defaults: OllamaDefaults,
        settings: Settings,
        selected_id: str = "",
    ) -> None:
        super().__init__()
        self._service = service
        self._defaults = defaults
        self._settings = settings
        self._selected_id = selected_id
        self._profiles: dict[str, AgentProfile] = {}
        self._changed = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="screen-panel", id="agents-panel"):
            yield Label("Your agents", classes="screen-title")
            yield Static(
                "Create configured instances of the agent types installed with Olympus.",
                classes="screen-subtitle",
            )
            yield ListView(id="manager-agent-list")
            with Grid(id="manager-actions"):
                yield Button("Open", id="manager-open", variant="primary")
                yield Button("Add", id="manager-add")
                yield Button("Edit", id="manager-edit")
                yield Button("Remove", id="manager-remove", variant="error")
                yield Button("Restore", id="manager-restore")
                yield Button("Defaults", id="manager-defaults")
                yield Button("Back", id="manager-back")
        yield Footer()

    async def on_mount(self) -> None:
        await self._populate()
        self.query_one("#manager-agent-list", ListView).focus()

    async def _populate(self) -> None:
        view = self.query_one("#manager-agent-list", ListView)
        await view.clear()
        self._profiles.clear()
        selected_index = 0
        profiles = self._service.store.agents(include_archived=True)
        for index, profile in enumerate(profiles):
            item_id = f"managed-{profile.id}"
            self._profiles[item_id] = profile
            await view.append(
                ListItem(Label(self._row(profile), markup=False), id=item_id)
            )
            if profile.id == self._selected_id:
                selected_index = index
        if self._profiles:
            view.index = selected_index
        else:
            await view.append(ListItem(Label("No agents yet — choose Add to begin.")))

    def _row(self, profile: AgentProfile) -> str:
        registry = self._service.registry
        if not registry.installed(profile.kind):
            return f"?  {profile.name}\n   Agent type {profile.kind!r} is not installed"
        definition = registry.provider(profile.kind).definition
        details = [definition.description]
        if not profile.model and not profile.ollama_url:
            details.append("defaults")
        location = self._service.secret_location(profile)
        if location:
            details.append(f"token in {location}")
        if profile.archived:
            details.append("archived")
        return f"{definition.glyph}  {profile.name}\n   {' · '.join(details)}"

    def _highlighted(self) -> AgentProfile | None:
        item = self.query_one("#manager-agent-list", ListView).highlighted_child
        return self._profiles.get(item.id or "") if item else None

    @on(ListView.Selected, "#manager-agent-list")
    def selected(self) -> None:
        self.action_switch()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        actions: dict[str, Callable[[], None]] = {
            "manager-open": self.action_switch,
            "manager-add": self.action_add,
            "manager-edit": self.action_edit,
            "manager-restore": self.action_restore,
            "manager-remove": self.action_remove,
            "manager-defaults": self.action_defaults,
            "manager-back": self.action_close,
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    def _usable(self, profile: AgentProfile | None, verb: str) -> bool:
        """Only active agents of installed types can be opened or edited."""
        if profile is None:
            return False
        if profile.archived:
            self.notify(f"Restore this agent before {verb} it.", severity="warning")
            return False
        if not self._service.registry.installed(profile.kind):
            self.notify(
                f"Agent type {profile.kind!r} is not installed.", severity="warning"
            )
            return False
        return True

    def action_switch(self) -> None:
        profile = self._highlighted()
        if self._usable(profile, "opening"):
            self.dismiss(AgentManagerResult(profile, self._changed))

    def action_add(self) -> None:
        self.app.push_screen(AgentTypeScreen(self._service), self._type_chosen)

    def _type_chosen(self, kind: str | None) -> None:
        if kind:
            self.app.push_screen(
                AgentFormScreen(
                    self._service, self._defaults, self._service.draft(kind)
                ),
                self._profile_saved,
            )

    def action_edit(self) -> None:
        profile = self._highlighted()
        if self._usable(profile, "editing"):
            self.app.push_screen(
                AgentFormScreen(self._service, self._defaults, profile),
                self._profile_saved,
            )

    def action_restore(self) -> None:
        profile = self._highlighted()
        if profile is None or not profile.archived:
            return
        try:
            self._service.restore(profile)
        except OlympusError as error:
            self.notify(str(error), severity="error")
            return
        self._refresh(profile.id)

    def _profile_saved(self, profile: AgentProfile | None) -> None:
        if profile:
            self._refresh(profile.id)

    def action_remove(self) -> None:
        profile = self._highlighted()
        if profile is None:
            return
        self.app.push_screen(
            RemoveAgentScreen(profile, self._service.conversation_count(profile)),
            lambda choice: self._remove_chosen(profile, choice),
        )

    def _remove_chosen(self, profile: AgentProfile, choice: str | None) -> None:
        if choice is None:
            return
        try:
            self._service.remove(profile, delete_history=choice == "delete")
        except OlympusError as error:
            self.notify(f"{profile.name} was removed, but: {error}", severity="warning")
        self._refresh("")

    def action_defaults(self) -> None:
        self.app.push_screen(
            DefaultsScreen(self._service.store, self._settings),
            self._defaults_saved,
        )

    def _defaults_saved(self, defaults: OllamaDefaults | None) -> None:
        if defaults:
            self._defaults = self._service.store.ollama_defaults(self._settings)
            self._changed = True

    def _refresh(self, selected_id: str) -> None:
        self._changed = True
        self._selected_id = selected_id
        self.run_worker(self._populate())

    def action_close(self) -> None:
        self.dismiss(AgentManagerResult(changed=self._changed))
