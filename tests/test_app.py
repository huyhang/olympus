from __future__ import annotations

import asyncio

import httpx
from shell_fakes import (
    ChoosingSession,
    FakeSession,
    HangingSession,
    configured,
    evidence_titles,
    finish_replies,
    identity_line,
    notifications,
    started,
    status_line,
    transcript_text,
    type_and_send,
)
from textual.widgets import ListView, Static, TextArea

from olympus.commands import Command
from olympus.domain import AgentEvent
from olympus.services import fresh_profile
from olympus.ui.screens import AgentManagerResult, HistoryScreen, IdentityScreen
from olympus.ui.widgets import AgentListItem


def messages_of(service, profile, conversation_id):
    store = service.store.for_agent(profile.id)
    return [item.content for item in store.messages(conversation_id)]


def test_empty_first_run_leads_to_agent_management(tmp_path):
    app, _, _, _ = configured(tmp_path, 0)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            assert app._profile is None
            assert app.query_one("#composer", TextArea).disabled
            assert "Ctrl+A" in transcript_text(app)
            await pilot.click("#quick-add")
            await pilot.pause()
            assert type(app.screen).__name__ == "AgentManagerScreen"

    asyncio.run(exercise())


def test_a_streamed_answer_shows_with_evidence_and_is_filed(tmp_path):
    app, service, provider, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            sidebar = app.query_one("#agent-list", ListView).children[0]
            assert isinstance(sidebar, AgentListItem)
            label = str(sidebar.query_one(Static).render())
            assert label.startswith("1 ○ Oracle 1") and "model-1" in label
            assert "model-1" in identity_line(app)

            await type_and_send(pilot, "question one")
            await finish_replies(app, pilot)
            conversation = app._conversation.id
            assert "question one" in transcript_text(app)
            assert "Oracle 1 complete" in transcript_text(app)
            assert any("memory" in title for title in evidence_titles(app))
            assert status_line(app) == "Ready"
            assert messages_of(service, profiles[0], conversation) == [
                "question one",
                "Oracle 1 complete",
            ]
            assert profiles[1].id not in provider.sessions

    asyncio.run(exercise())


def test_switching_back_reopens_the_conversation_you_left(tmp_path):
    app, _, _, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "remember me")
            await finish_replies(app, pilot)
            left = app._conversation.id

            await app._activate(profiles[1])
            assert "remember me" not in transcript_text(app)
            await app._activate(profiles[0])
            assert app._conversation.id == left
            assert "remember me" in transcript_text(app)
            assert "Oracle 1 complete" in transcript_text(app)

            await app._activate(profiles[0])
            assert app._conversation.id == left

    asyncio.run(exercise())


def test_unsent_text_stays_with_the_agent_it_was_typed_for(tmp_path):
    app, _, provider, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "half a thought for Oracle 1"
            await app._activate(profiles[1])
            assert composer.text == ""
            composer.text = "draft for Oracle 2"
            await app._activate(profiles[0])
            assert composer.text == "half a thought for Oracle 1"
            await pilot.press("enter")
            await finish_replies(app, pilot)
            await app._activate(profiles[1])
            assert composer.text == "draft for Oracle 2"
            assert provider.sessions[profiles[0].id].questions == [
                "half a thought for Oracle 1"
            ]
            assert provider.sessions[profiles[1].id].questions == []

    asyncio.run(exercise())


def test_a_reply_streams_on_after_switching_away_and_back(tmp_path):
    app, service, provider, profiles = configured(tmp_path, 2)
    slow = provider.sessions[profiles[0].id] = HangingSession()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "take your time")
            await started(slow)
            await pilot.pause()
            conversation = app._conversation.id
            assert status_line(app) == "Oracle 1 is reading…"

            await app._activate(profiles[1])
            assert "slow partial" not in transcript_text(app)
            assert status_line(app) == "Ready"
            assert evidence_titles(app) == []

            await app._activate(profiles[0])
            assert "slow partial" in transcript_text(app)
            assert status_line(app) == "Oracle 1 is reading…"

            slow.release.set()
            await finish_replies(app, pilot)
            assert "slow finished" in transcript_text(app)
            assert "slow partial" not in transcript_text(app)
            assert any("slow_lookup" in title for title in evidence_titles(app))
            assert status_line(app) == "Ready"
            assert messages_of(service, profiles[0], conversation)[-1] == (
                "slow finished"
            )

    asyncio.run(exercise())


def test_a_background_reply_is_filed_with_its_question_and_announced(tmp_path):
    app, service, provider, profiles = configured(tmp_path, 2)
    slow = provider.sessions[profiles[0].id] = HangingSession()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "background please")
            await started(slow)
            asked_in = app._conversation.id
            busy = app.query_one("#agent-list", ListView).children[0]
            assert "●" in str(busy.query_one(Static).render())

            await app._activate(profiles[1])
            slow.release.set()
            await finish_replies(app, pilot)
            assert "Oracle 1 finished replying." in notifications(app)
            assert "slow finished" not in transcript_text(app)
            assert evidence_titles(app) == []
            assert messages_of(service, profiles[0], asked_in) == [
                "background please",
                "slow finished",
            ]
            idle = app.query_one("#agent-list", ListView).children[0]
            assert "○" in str(idle.query_one(Static).render())

            await app._activate(profiles[0])
            assert "slow finished" in transcript_text(app)

    asyncio.run(exercise())


def test_escape_cancels_only_the_reply_on_screen(tmp_path):
    app, service, provider, profiles = configured(tmp_path, 2)
    first = provider.sessions[profiles[0].id] = HangingSession("first")
    second = provider.sessions[profiles[1].id] = HangingSession("second")

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "one")
            await started(first)
            first_conversation = app._conversation.id
            await app._activate(profiles[1])
            await type_and_send(pilot, "two")
            await started(second)
            second_conversation = app._conversation.id

            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert messages_of(service, profiles[1], second_conversation)[-1] == (
                "Response cancelled. Nothing was changed."
            )
            assert app._replies[first_conversation].running

            first.release.set()
            await finish_replies(app, pilot)
            assert messages_of(service, profiles[0], first_conversation)[-1] == (
                "first finished"
            )

    asyncio.run(exercise())


def test_one_busy_agent_never_blocks_asking_another(tmp_path):
    app, _, provider, profiles = configured(tmp_path, 2)
    slow = provider.sessions[profiles[0].id] = HangingSession()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "slow one")
            await started(slow)
            await type_and_send(pilot, "too soon")
            assert "Cancel or wait for this response." in notifications(app)
            assert slow.questions == ["slow one"]

            await app._activate(profiles[1])
            await type_and_send(pilot, "meanwhile")
            await asyncio.sleep(0.05)
            assert provider.sessions[profiles[1].id].questions == ["meanwhile"]
            slow.release.set()
            await finish_replies(app, pilot)

    asyncio.run(exercise())


def test_a_pending_pick_list_survives_switching_agents(tmp_path):
    app, _, provider, profiles = configured(tmp_path, 2)
    chooser = provider.sessions[profiles[0].id] = ChoosingSession()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "choose")
            await finish_replies(app, pilot)
            assert "Reply with a number." in transcript_text(app)
            await type_and_send(pilot, "9")
            assert "Pick a number between 1 and 2." in transcript_text(app)
            assert chooser.questions == ["choose"]

            await app._activate(profiles[1])
            await app._activate(profiles[0])
            await type_and_send(pilot, "2")
            await finish_replies(app, pilot)
            assert chooser.questions[-1] == 'The user selected "Two" (id two).'

    asyncio.run(exercise())


def test_new_and_history_commands_do_different_things(tmp_path):
    app, _service, _, _profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "first chat")
            await finish_replies(app, pilot)
            first = app._conversation.id

            await type_and_send(pilot, "/new")
            assert app._conversation.id != first
            assert "first chat" not in transcript_text(app)
            assert "A deterministic test agent" in transcript_text(app)

            await type_and_send(pilot, "/history")
            assert isinstance(app.screen, HistoryScreen)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert app._conversation.id == first
            assert "first chat" in transcript_text(app)

    asyncio.run(exercise())


def test_commands_and_preferences_stay_local(tmp_path):
    app, _, provider, profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "/help")
            assert "Local commands" in transcript_text(app)
            await app._handle_command(Command("name", "Athena"))
            assert app._profile.name == "Athena"
            assert "Athena" in identity_line(app)
            await app._handle_command(Command("style", "detailed"))
            assert app._profile.response_style == "detailed"
            await app._handle_command(Command("persona", "Formal"))
            assert app._profile.persona == "Formal"
            await app._handle_command(Command("frobnicate"))
            assert "Unknown command" in transcript_text(app)
            assert provider.sessions[profiles[0].id].questions == []

    asyncio.run(exercise())


def test_the_palette_switches_agents_and_opens_recent_conversations(tmp_path):
    app, _, _, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            titles = [command.title for command in app.get_system_commands(app.screen)]
            assert "Switch to Oracle 2" in titles
            assert "Manage agents" in titles

            await type_and_send(pilot, "palette memory")
            await finish_replies(app, pilot)
            remembered = app._conversation.id
            await app._handle_command(Command("new"))
            commands = {
                command.title: command
                for command in app.get_system_commands(app.screen)
            }
            commands["Open: palette memory"].callback()
            await pilot.pause()
            await pilot.pause()
            assert app._conversation.id == remembered

            commands["Switch to Oracle 2"].callback()
            await pilot.pause()
            await pilot.pause()
            assert app._profile.id == profiles[1].id

    asyncio.run(exercise())


def test_history_export_delete_and_clear_flows(tmp_path):
    app, service, _, profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "remember this")
            await finish_replies(app, pilot)
            conversation_id = app._conversation.id

            app.action_history()
            await pilot.pause()
            history = app.screen
            assert isinstance(history, HistoryScreen)
            await pilot.press("down")
            await pilot.press("e")
            await pilot.pause()
            exports = list((tmp_path / "state" / "exports").rglob("*.md"))
            assert len(exports) == 1
            history.action_export()
            history.action_delete()
            await pilot.pause()
            await pilot.click("#cancel")
            await pilot.pause()
            assert service.store.for_agent(profiles[0].id).conversation(conversation_id)

            history.action_delete()
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            assert not service.store.for_agent(profiles[0].id).conversation(
                conversation_id
            )
            history.action_close()
            await pilot.pause()
            await pilot.pause()
            assert app._conversation.id != conversation_id

            await type_and_send(pilot, "another")
            await finish_replies(app, pilot)
            await app._handle_command(Command("clear", "all"))
            await pilot.pause()
            await pilot.click("#cancel")
            await pilot.pause()
            assert service.store.for_agent(profiles[0].id).conversations()
            await app._handle_command(Command("clear", "all"))
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            assert service.store.for_agent(profiles[0].id).conversations() == []

    asyncio.run(exercise())


def test_export_open_and_clear_commands_by_id(tmp_path):
    app, service, _, profiles = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "keep me")
            await finish_replies(app, pilot)
            kept = app._conversation
            await app._handle_command(Command("new"))
            await app._handle_command(Command("export", kept.id[:8]))
            assert "Exported to" in transcript_text(app)
            await app._handle_command(Command("export", kept.id[:8]))
            assert "already exists" in transcript_text(app)
            await app._handle_command(Command("export", "missing"))
            assert "missing or ambiguous" in transcript_text(app)
            await app._handle_command(Command("open", kept.id[:8]))
            assert app._conversation.id == kept.id
            await app._handle_command(Command("clear", "missing"))
            assert "missing or ambiguous" in transcript_text(app)
            await app._handle_command(Command("clear"))
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            await pilot.pause()
            assert not service.store.for_agent(profiles[0].id).conversation(kept.id)
            assert app._conversation.id != kept.id

    asyncio.run(exercise())


def test_identity_screen_saves_presentation_preferences(tmp_path):
    app, _, _, _ = configured(tmp_path)

    async def exercise():
        async with app.run_test(size=(120, 55)) as pilot:
            app.action_identity()
            await pilot.pause()
            assert isinstance(app.screen, IdentityScreen)
            app.screen.query_one("#identity-name").value = "Minerva"
            await pilot.click("#identity-save")
            await pilot.pause()
            await pilot.pause()
            assert app._profile.name == "Minerva"
            assert "Minerva" in identity_line(app)

    asyncio.run(exercise())


def test_local_command_validation_and_input_recall(tmp_path):
    app, _, provider, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            first_conversation = app._conversation
            app.action_previous_input()
            app.action_next_input()
            composer = app.query_one("#composer", TextArea)
            assert composer.text == ""

            await type_and_send(pilot, "first")
            await finish_replies(app, pilot)
            await type_and_send(pilot, "second")
            await finish_replies(app, pilot)
            app.action_previous_input()
            assert composer.text == "second"
            app.action_previous_input()
            assert composer.text == "first"
            app.action_next_input()
            app.action_next_input()
            assert composer.text == ""

            for command in ("new extra", "agents extra", "help extra", "open missing"):
                name, _, argument = command.partition(" ")
                await app._handle_command(Command(name, argument))
            await app._handle_command(Command("style", "verbose"))
            assert "compact or detailed" in transcript_text(app)
            await app._handle_command(Command("agent", "missing"))
            assert "sidebar number" in transcript_text(app)
            assert app._conversation.id == first_conversation.id

            await app._handle_command(Command("agent", "2"))
            assert app._profile.id == profiles[1].id
            await app._handle_command(Command("agent", profiles[0].name))
            assert app._profile.id == profiles[0].id
            app.action_switch_agent(2)
            await pilot.pause()
            assert app._profile.id == profiles[1].id
            app.action_switch_agent(99)

            await app._handle_command(Command("name", ""))
            await pilot.pause()
            assert isinstance(app.screen, IdentityScreen)
            await pilot.press("escape")
            await pilot.pause()
            await app._handle_command(Command("persona", ""))
            await pilot.pause()
            await pilot.click("#identity-cancel")
            await pilot.pause()

            composer.text = "x" * 4_001
            await pilot.press("enter")
            assert provider.sessions[profiles[1].id].questions == []

    asyncio.run(exercise())


def test_narrow_terminals_hide_the_sidebar_until_asked(tmp_path):
    app, _, _, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(70, 40)) as pilot:
            sidebar = app.query_one("#sidebar")
            assert not sidebar.display
            await pilot.press("ctrl+g")
            assert sidebar.display
            assert app.focused is app.query_one("#agent-list")
            await pilot.press("down", "enter")
            await pilot.pause()
            assert app._profile.id == profiles[1].id
            assert not sidebar.display
            app.action_toggle_sidebar()
            assert sidebar.display

    asyncio.run(exercise())


def test_editing_a_busy_agent_takes_effect_once_its_reply_ends(tmp_path):
    app, service, provider, profiles = configured(tmp_path)
    slow = provider.sessions[profiles[0].id] = HangingSession()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "long question")
            await started(slow)
            service.store.save_agent(fresh_profile(profiles[0], model="model-new"))
            await app._apply_agent_changes(AgentManagerResult(changed=True))
            assert "model-new" in identity_line(app)
            assert (profiles[0].id, "model-1") not in provider.closed

            slow.release.set()
            await finish_replies(app, pilot)
            assert (profiles[0].id, "model-1") in provider.closed
            provider.sessions[profiles[0].id] = FakeSession("fresh")
            await type_and_send(pilot, "next question")
            await finish_replies(app, pilot)
            assert provider.opened[-1] == (profiles[0].id, "model-new")

    asyncio.run(exercise())


def test_an_agent_that_raises_shows_the_failure_instead_of_crashing(tmp_path):
    app, _, provider, profiles = configured(tmp_path)

    class Unreachable(FakeSession):
        async def respond(self, *args, **kwargs):
            yield AgentEvent("token", "partial")
            raise httpx.ConnectError("connection refused")

    class Broken(FakeSession):
        async def respond(self, *args, **kwargs):
            yield AgentEvent("status", "working")
            raise TypeError("plugin bug")

    provider.sessions[profiles[0].id] = Unreachable()

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            await type_and_send(pilot, "hello?")
            await finish_replies(app, pilot)
            assert "connection refused" in transcript_text(app)
            assert status_line(app) == "Ready"

            provider.sessions[profiles[0].id] = Broken()
            app._runtimes.retire_all()
            await type_and_send(pilot, "again?")
            await finish_replies(app, pilot)
            assert any("plugin bug" in message for message in notifications(app))
            assert "finished without an answer" in transcript_text(app)

    asyncio.run(exercise())


def test_an_agent_that_cannot_start_leaves_the_workspace_usable(tmp_path):
    app, _, provider, profiles = configured(tmp_path, 2)

    async def exercise():
        async with app.run_test(size=(110, 45)) as pilot:
            provider.refuse_open = True
            await app._apply_agent_changes(AgentManagerResult(changed=True))
            await pilot.pause()
            assert app._profile is None
            assert "Welcome to Olympus" in identity_line(app)
            assert "cannot start" in " ".join(notifications(app))
            provider.refuse_open = False
            await app._apply_agent_changes(AgentManagerResult(changed=True))
            assert app._profile.id == profiles[0].id

    asyncio.run(exercise())
