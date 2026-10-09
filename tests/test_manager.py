"""Agent management: roster, add/edit form, removal, restore, and defaults."""

from __future__ import annotations

import asyncio
import sqlite3

from shell_fakes import configured, finish_replies, notifications, type_and_send
from textual.widgets import Button, Input, Label, ListView, Static, Switch

from olympus.config import Settings
from olympus.domain import (
    TOGGLE_OFF,
    TOGGLE_ON,
    AgentDefinition,
    AgentProfile,
    ConfigurationField,
)
from olympus.ports import StoreError
from olympus.ui.screens import (
    AgentFormScreen,
    AgentManagerScreen,
    AgentTypeScreen,
    DefaultsScreen,
    RemoveAgentScreen,
)


async def open_manager(app, pilot):
    app.action_agents()
    await pilot.pause()
    assert isinstance(app.screen, AgentManagerScreen)
    return app.screen


def rows(manager) -> list[str]:
    view = manager.query_one("#manager-agent-list", ListView)
    return [str(item.query_one(Label).render()) for item in view.children]


def test_add_test_save_archive_and_restore(tmp_path):
    app, service, _provider, profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            manager = await open_manager(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AgentTypeScreen)
            await pilot.press("enter")
            await pilot.pause()
            form = app.screen
            assert isinstance(form, AgentFormScreen)
            form.query_one("#agent-name", Input).value = "Second"
            form.query_one("#agent-ollama-url", Input).value = "http://localhost:1143x"
            form.query_one("#config-token", Input).value = "secret"
            form.save_pressed()
            await pilot.pause()
            assert "not a valid URL" in str(
                form.query_one("#agent-form-status", Static).render()
            )
            form.query_one("#agent-ollama-url", Input).value = ""
            form.test_pressed()
            await pilot.pause()
            await pilot.pause()
            assert "Connections look good" in str(
                form.query_one("#agent-form-status", Static).render()
            )
            form.save_pressed()
            await pilot.pause()
            await pilot.pause()
            assert [agent.name for agent in service.store.agents()] == [
                "Oracle 1",
                "Second",
            ]
            assert any("token in memory" in row for row in rows(manager))

            manager.query_one("#manager-agent-list", ListView).index = 0
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, RemoveAgentScreen)
            await pilot.click("#remove-archive")
            await pilot.pause()
            await pilot.pause()
            assert service.store.agent(profiles[0].id) is None
            assert "archived" in rows(manager)[0]

            manager.query_one("#manager-agent-list", ListView).index = 0
            await pilot.press("r")
            await pilot.pause()
            await pilot.pause()
            assert service.store.agent(profiles[0].id) is not None
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert app._profile.id == profiles[0].id

    asyncio.run(exercise())


def test_an_archived_agent_cannot_be_opened_or_edited(tmp_path):
    app, service, _, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            service.remove(profiles[1])
            manager = await open_manager(app, pilot)
            manager.query_one("#manager-agent-list", ListView).index = 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is manager
            await pilot.press("e")
            await pilot.pause()
            assert app.screen is manager
            assert any(
                "Restore this agent" in message for message in notifications(app)
            )
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert app._profile.id == profiles[0].id

    asyncio.run(exercise())


def test_deleting_everything_erases_chats_and_credentials(tmp_path):
    app, service, _, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            await type_and_send(pilot, "private history")
            await finish_replies(app, pilot)
            manager = await open_manager(app, pilot)
            manager.query_one("#manager-agent-list", ListView).index = 0
            await pilot.press("d")
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, RemoveAgentScreen)
            assert "1 conversation" in str(dialog.query_one(".dialog-copy").render())
            await pilot.click("#remove-delete")
            await pilot.pause()
            await pilot.pause()
            assert service.store.agent(profiles[0].id) is None
            assert service.secrets.get(profiles[0].id, "token") is None
            database = sqlite3.connect(tmp_path / "state" / "olympus.sqlite3")
            try:
                leftovers = database.execute(
                    """SELECT COUNT(*) FROM messages
                       WHERE conversation_id NOT IN (SELECT id FROM conversations)
                       OR conversation_id IN (
                           SELECT id FROM conversations WHERE agent_id = ?
                       )""",
                    (profiles[0].id,),
                ).fetchone()[0]
            finally:
                database.close()
            assert leftovers == 0
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert app._profile.id == profiles[1].id

    asyncio.run(exercise())


def test_a_credential_that_cannot_be_deleted_is_reported(tmp_path):
    app, service, _, _profiles = configured(tmp_path)

    def refuse(agent_id, keys=()):
        raise StoreError("macOS Keychain still holds token for this agent.")

    service.secrets.delete_agent = refuse

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            manager = await open_manager(app, pilot)
            manager.action_remove()
            await pilot.pause()
            await pilot.click("#remove-delete")
            await pilot.pause()
            await pilot.pause()
            assert service.store.agents(include_archived=True) == []
            assert any("still holds" in message for message in notifications(app))

    asyncio.run(exercise())


def test_an_uninstalled_agent_type_can_still_be_seen_and_removed(tmp_path):
    app, service, _, _ = configured(tmp_path, 0)
    ghost = service.store.save_agent(AgentProfile("ghost-1", "ghost", "Ghost"))

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            assert app._profile is None
            manager = await open_manager(app, pilot)
            assert "not installed" in rows(manager)[0]
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is manager
            await pilot.press("d")
            await pilot.pause()
            await pilot.click("#remove-delete")
            await pilot.pause()
            await pilot.pause()
            assert service.store.agents(include_archived=True) == []
            assert ghost.id not in [agent.id for agent in service.store.agents()]

    asyncio.run(exercise())


def test_every_manager_action_stays_reachable_on_small_terminals(tmp_path):
    for width, height in ((70, 24), (88, 30), (120, 40)):
        app, _, _, _ = configured(tmp_path / str(width))

        async def exercise(app=app, width=width, height=height):
            async with app.run_test(size=(width, height)) as pilot:
                manager = await open_manager(app, pilot)
                for button in manager.query(Button):
                    region = button.region
                    assert region.width >= 6, (width, button.id)
                    assert region.right <= width and region.bottom <= height, (
                        width,
                        button.id,
                    )

        asyncio.run(exercise())


def test_defaults_are_saved_without_absorbing_environment_overrides(tmp_path):
    settings = Settings(tmp_path / "state", "http://env-ollama:9999", "")
    app, service, _, _ = configured(tmp_path, settings=settings)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            await open_manager(app, pilot)
            await pilot.press("o")
            await pilot.pause()
            defaults = app.screen
            assert isinstance(defaults, DefaultsScreen)
            assert defaults.query_one("#defaults-url", Input).value == (
                "http://localhost:11434"
            )
            assert "OLLAMA_URL" in str(defaults.query_one("#defaults-note").render())
            defaults.query_one("#defaults-url", Input).value = "invalid"
            await pilot.click("#defaults-save")
            await pilot.pause()
            assert app.screen is defaults
            defaults.query_one("#defaults-model", Input).value = "llama-new"
            defaults.query_one("#defaults-url", Input).value = "http://saved:11434"
            await pilot.pause(0.3)  # a button ignores clicks during its press effect
            await pilot.click("#defaults-save")
            await pilot.pause()
            saved = service.store.ollama_defaults()
            assert (saved.url, saved.model) == ("http://saved:11434", "llama-new")
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert app._defaults.url == "http://env-ollama:9999"
            assert app._defaults.model == "llama-new"

    asyncio.run(exercise())


def test_testing_a_connection_never_freezes_the_form(tmp_path):
    app, _, provider, _ = configured(tmp_path)
    provider.probe_gate = asyncio.Event()

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            manager = await open_manager(app, pilot)
            await pilot.press("e")
            await pilot.pause()
            form = app.screen
            form.query_one("#config-token", Input).value = "token"
            await pilot.click("#agent-test")
            await pilot.pause()
            assert "Testing" in str(form.query_one("#agent-form-status").render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is manager

    asyncio.run(exercise())


def test_editing_updates_the_running_agent(tmp_path):
    app, service, provider, profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            manager = await open_manager(app, pilot)
            await pilot.press("e")
            await pilot.pause()
            form = app.screen
            form.query_one("#agent-model", Input).value = "special-model"
            form.save_pressed()
            await pilot.pause()
            await pilot.pause()
            assert service.store.agent(profiles[0].id).model == "special-model"
            assert app.screen is manager
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert provider.opened[-1] == (profiles[0].id, "special-model")
            assert (profiles[0].id, "model-1") in provider.closed

    asyncio.run(exercise())


def test_a_toggle_setting_is_a_switch_that_saves_on_or_off(tmp_path):
    app, service, provider, profiles = configured(tmp_path)
    provider.definition = AgentDefinition(
        "fake",
        "Oracle",
        "A deterministic test agent",
        "O",
        (
            ConfigurationField("token", "Token", secret=True),
            ConfigurationField(
                "filing",
                "Allow filing",
                placeholder="Needs more permissions.",
                default=TOGGLE_OFF,
                required=False,
                kind="toggle",
            ),
        ),
    )

    async def exercise():
        async with app.run_test(size=(110, 50)) as pilot:
            form = AgentFormScreen(service, app._defaults, profiles[0])
            app.push_screen(form)
            await pilot.pause()
            switch = form.query_one("#config-filing", Switch)
            assert switch.value is False
            assert "Needs more permissions." in [
                str(item.render()) for item in form.query(".hint")
            ]
            switch.value = True
            form.save_pressed()
            await pilot.pause()
            saved = service.store.agent(profiles[0].id)
            assert saved.settings["filing"] == TOGGLE_ON

    asyncio.run(exercise())
