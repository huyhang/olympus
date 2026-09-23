from __future__ import annotations

import asyncio
from datetime import datetime

from textual.widgets import Input, ListView, Select, TextArea

from cleo.app import CleoApp, ConfirmScreen, HistoryScreen, SettingsScreen
from cleo.commands import Command
from cleo.domain import AgentEvent, Candidate, Evidence, Message
from cleo.store import ConversationStore


class FakeAgent:
    """Streams a grounded answer, and records how it was called."""

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.grounded: list[bool] = []

    async def respond(self, history, question, identity, prior_evidence=False):
        self.questions.append(question)
        self.grounded.append(prior_evidence)
        yield AgentEvent("status", "Searching Nineveh…")
        yield AgentEvent("evidence", evidence=Evidence("search_series", {"items": []}))
        yield AgentEvent("token", "A safe ")
        yield AgentEvent("token", "response.")
        yield AgentEvent("done", "A safe response.")


def hanging_agent(started: asyncio.Event, release: asyncio.Event):
    class HangingAgent:
        async def respond(self, history, question, identity, prior_evidence=False):
            yield AgentEvent("evidence", evidence=Evidence("search_series", {}))
            yield AgentEvent("token", "partial")
            started.set()
            await release.wait()
            yield AgentEvent("done", "the finished answer")

    return HangingAgent()


def test_the_full_screen_app_mounts_and_local_help_never_calls_the_agent(tmp_path):
    calls = 0

    class CountingAgent(FakeAgent):
        async def respond(self, history, question, identity, prior_evidence=False):
            nonlocal calls
            calls += 1
            yield AgentEvent("done", "unexpected")

    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(CountingAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            assert "read-only Nineveh" in str(app.query_one("#identity").render())
            await app._handle_command(Command("help"))
            await pilot.pause()
            assert calls == 0

    asyncio.run(exercise())


def test_the_identity_bar_names_the_configured_model(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store, model="ornith-1.5:35b")
        async with app.run_test(size=(100, 50)):
            assert "ornith-1.5:35b" in str(app.query_one("#identity").render())

    asyncio.run(exercise())


def test_chat_streams_persists_evidence_and_supports_local_preferences(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "What is in Nineveh?"
            composer.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app._reply_task is not None
            await app._reply_task
            await pilot.pause()

            messages = store.messages(app._conversation.id)
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[-1].content == "A safe response."
            assert store.message_evidence(app._conversation.id)[-1][0].tool == (
                "search_series"
            )

            composer.text = "line one"
            composer.focus()
            await pilot.press("shift+enter")
            assert composer.text.replace("\n", "") == "line one"
            assert "\n" in composer.text
            assert len(store.messages(app._conversation.id)) == 2

            await app._handle_command(Command("name", "Hypatia"))
            await app._handle_command(Command("persona", "Warm but concise"))
            await app._handle_command(Command("style", "detailed"))
            assert store.identity().name == "Hypatia"
            assert store.identity().persona == "Warm but concise"
            assert store.identity().response_style == "detailed"

    asyncio.run(exercise())


def test_an_over_long_message_is_refused_before_the_agent_sees_it(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = FakeAgent()
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "x" * 4_001
            await app._submit()
            await pilot.pause()
            assert agent.questions == []
            assert store.messages(app._conversation.id) == []

    asyncio.run(exercise())


def test_a_second_question_waits_for_the_first_reply(tmp_path):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(hanging_agent(started, release), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "first"
            await app._submit()
            await started.wait()
            app.query_one("#composer", TextArea).text = "second"
            await app._submit()
            await pilot.pause()
            assert [m.content for m in store.messages(app._conversation.id)] == [
                "first"
            ]
            release.set()
            assert app._reply_task is not None
            await app._reply_task

    asyncio.run(exercise())


def test_assistant_timestamp_is_created_only_when_the_response_finishes(tmp_path):
    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        class DelayedAgent:
            async def respond(self, history, question, identity, prior_evidence=False):
                yield AgentEvent("token", "Still working")
                started.set()
                await release.wait()
                yield AgentEvent("done", "Finished")

        store = ConversationStore(tmp_path / "state")
        app = CleoApp(DelayedAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "A question"
            await app._submit()
            await started.wait()

            assert len(store.messages(app._conversation.id)) == 1
            assert " · " not in app._assistant_draft_markdown("Still working")
            completion_started = datetime.now().astimezone().replace(microsecond=0)

            release.set()
            assert app._reply_task is not None
            await app._reply_task
            assistant = store.messages(app._conversation.id)[-1]
            assert datetime.fromisoformat(assistant.created_at) >= completion_started
            assert " · " in app._message_markdown(assistant)
            await pilot.pause()

    asyncio.run(exercise())


def test_history_settings_and_confirmed_clear_are_full_screen_flows(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        conversation = store.create_conversation()
        store.add_message(conversation.id, Message("user", "Pluto"))
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.action_history()
            await pilot.pause()
            assert isinstance(app.screen, HistoryScreen)
            search = app.screen.query_one("#history-search", Input)
            search.value = "Pluto"
            await pilot.pause()
            app.screen.query_one("#history-list", ListView).index = 0
            app.screen.action_open()
            await pilot.pause()
            assert app._conversation.id == conversation.id

            app.action_settings()
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            app.screen.query_one("#settings-name", Input).value = "Athena"
            app.screen.query_one("#settings-persona", TextArea).text = "Cheerful"
            app.screen.query_one("#settings-style", Select).value = "detailed"
            await pilot.click("#settings-save")
            await pilot.pause()
            assert store.identity().name == "Athena"
            assert store.identity().response_style == "detailed"

            current_id = app._conversation.id
            await app._handle_command(Command("clear"))
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            assert store.conversation(current_id) is None

    asyncio.run(exercise())


# --- destructive paths: every one of these must refuse to delete ---------------


def seeded(tmp_path):
    store = ConversationStore(tmp_path / "state")
    first = store.create_conversation()
    store.add_message(first.id, Message("user", "keep me"))
    second = store.create_conversation()
    store.add_message(second.id, Message("user", "keep me too"))
    return store, first, second


def test_cancelling_the_dialog_keeps_the_conversation(tmp_path):
    async def exercise():
        store, first, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("clear", first.id[:8]))
            await pilot.pause()
            await pilot.click("#cancel")
            await pilot.pause()
            assert store.conversation(first.id) is not None
            assert len(store.conversations()) == 2

    asyncio.run(exercise())


def test_escaping_the_dialog_keeps_the_conversation(tmp_path):
    async def exercise():
        store, first, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("clear", first.id[:8]))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert len(store.conversations()) == 2

    asyncio.run(exercise())


def test_cancelling_clear_all_keeps_every_conversation(tmp_path):
    async def exercise():
        store, _, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("clear", "all"))
            await pilot.pause()
            await pilot.click("#cancel")
            await pilot.pause()
            assert len(store.conversations()) == 2

    asyncio.run(exercise())


def test_confirming_clear_all_removes_every_conversation(tmp_path):
    async def exercise():
        store, _, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("clear", "all"))
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            assert store.conversations() == []

    asyncio.run(exercise())


def test_clearing_an_unknown_id_deletes_nothing(tmp_path):
    async def exercise():
        store, _, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("clear", "no-such-conversation"))
            await pilot.pause()
            await pilot.pause()
            # No dialog at all: an unknown id must not quietly retarget
            # whatever conversation happens to be open.
            assert [
                type(s) for s in app.screen_stack if isinstance(s, ConfirmScreen)
            ] == []
            assert len(store.conversations()) == 2

    asyncio.run(exercise())


def test_exporting_an_unknown_id_reports_it_rather_than_the_open_one(tmp_path):
    async def exercise():
        state = tmp_path / "state"
        store, first, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("open", first.id[:8]))
            await app._handle_command(Command("export", "no-such-conversation"))
            await pilot.pause()
            # Nothing written at all: an unknown id must be reported, not
            # quietly retargeted at whatever conversation happens to be open.
            assert list((state / "exports").glob("*.md")) == []

    asyncio.run(exercise())


def test_cancelling_a_history_screen_delete_keeps_it(tmp_path):
    async def exercise():
        store, _, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.action_history()
            await pilot.pause()
            app.screen.query_one("#history-list", ListView).index = 0
            await pilot.click("#history-delete")
            await pilot.pause()
            await pilot.click("#cancel")
            await pilot.pause()
            assert len(store.conversations()) == 2

    asyncio.run(exercise())


def test_the_history_screen_deletes_and_exports_on_confirmation(tmp_path):
    async def exercise():
        state = tmp_path / "state"
        store = ConversationStore(state)
        conversation = store.create_conversation()
        store.add_message(conversation.id, Message("user", "delete me"))
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.action_history()
            await pilot.pause()
            app.screen.query_one("#history-list", ListView).index = 0
            await pilot.click("#history-export")
            await pilot.pause()
            assert len(list((state / "exports").glob("*.md"))) == 1
            app.screen.action_export()  # a second export refuses rather than overwrites
            await pilot.pause()
            assert len(list((state / "exports").glob("*.md"))) == 1

            await pilot.click("#history-delete")
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            assert store.conversations() == []
            await pilot.click("#history-back")
            await pilot.pause()

    asyncio.run(exercise())


# --- a reply outliving its screen or its conversation --------------------------


def test_quitting_while_a_reply_streams_shuts_down_cleanly(tmp_path):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(hanging_agent(started, release), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "hello"
            await app._submit()
            await asyncio.wait_for(started.wait(), 5)
            conversation_id = app._conversation.id
            # Deliberately not released: the reply is mid-stream at teardown.
            await pilot.press("ctrl+q")
            await pilot.pause()
        return conversation_id

    conversation_id = asyncio.run(exercise())
    reopened = ConversationStore(tmp_path / "state")
    assert "cancelled" in reopened.messages(conversation_id)[-1].content.lower()
    reopened.close()


def test_cancelling_a_reply_files_a_cancellation_note(tmp_path):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(hanging_agent(started, release), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "hello"
            await app._submit()
            await asyncio.wait_for(started.wait(), 5)
            app.action_cancel_reply()
            assert app._reply_task is not None
            await asyncio.wait_for(app._reply_task, 5)
            await pilot.pause()
            messages = store.messages(app._conversation.id)
            assert "cancelled" in messages[-1].content.lower()

    asyncio.run(exercise())


def test_deleting_the_open_conversation_mid_reply_does_not_crash(tmp_path):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(hanging_agent(started, release), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "delete me while I answer"
            await app._submit()
            live = app._conversation.id
            await asyncio.wait_for(started.wait(), 5)
            app.action_history()
            await pilot.pause()
            app.screen.query_one("#history-list", ListView).index = 0
            app.screen.action_delete()
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            release.set()
            assert app._reply_task is not None
            await asyncio.wait_for(app._reply_task, 5)
            await pilot.pause()
            assert store.conversation(live) is None
            assert store.messages(live) == []

    asyncio.run(exercise())


def test_starting_a_new_conversation_mid_reply_keeps_the_answer_with_its_question(
    tmp_path,
):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(hanging_agent(started, release), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "where does my answer go?"
            await app._submit()
            original = app._conversation.id
            await asyncio.wait_for(started.wait(), 5)
            await pilot.press("ctrl+n")
            await pilot.pause()
            release.set()
            assert app._reply_task is not None
            await asyncio.wait_for(app._reply_task, 5)
            await pilot.pause()

            filed = [(m.role, m.content) for m in store.messages(original)]
            assert filed == [
                ("user", "where does my answer go?"),
                ("assistant", "the finished answer"),
            ]
            assert store.messages(app._conversation.id) == []

    asyncio.run(exercise())


# --- grounding, disambiguation and untrusted text ------------------------------


def test_a_follow_up_is_told_the_conversation_is_already_grounded(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = FakeAgent()
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "first question"
            await app._submit()
            await app._reply_task
            app.query_one("#composer", TextArea).text = "follow up"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            assert agent.grounded == [False, True]

    asyncio.run(exercise())


def choosing_agent(choices: tuple[Candidate, ...]):
    class ChoosingAgent:
        def __init__(self) -> None:
            self.questions: list[str] = []

        async def respond(self, history, question, identity, prior_evidence=False):
            self.questions.append(question)
            if len(self.questions) == 1:
                yield AgentEvent("evidence", evidence=Evidence("search_series", {}))
                yield AgentEvent("choice", text="Which one?", candidates=choices)
            else:
                yield AgentEvent("done", "Twelve volumes.")

    return ChoosingAgent()


def test_an_ambiguous_search_asks_and_a_number_picks_the_series(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = choosing_agent(
            (Candidate("s1", "Saga"), Candidate("s2", "Vinland Saga"))
        )
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "latest Saga?"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            asked = store.messages(app._conversation.id)[-1].content
            assert "1. Saga" in asked and "2. Vinland Saga" in asked
            assert len(app._pending_choice) == 2

            app.query_one("#composer", TextArea).text = "9"
            await app._submit()
            await pilot.pause()
            assert len(agent.questions) == 1  # out of range, nothing was asked
            assert len(app._pending_choice) == 2

            app.query_one("#composer", TextArea).text = "2"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            assert 'Use the series "Vinland Saga" (id s2).' in agent.questions
            assert app._pending_choice == ()

    asyncio.run(exercise())


def test_plain_text_after_a_pick_list_is_treated_as_a_new_question(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = choosing_agent((Candidate("s1", "Saga"), Candidate("s2", "Other")))
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "latest Saga?"
            await app._submit()
            await app._reply_task
            app.query_one("#composer", TextArea).text = "never mind, what libraries?"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            assert agent.questions[-1] == "never mind, what libraries?"
            assert app._pending_choice == ()

    asyncio.run(exercise())


def test_terminal_escapes_in_catalog_text_are_never_rendered(tmp_path):
    evil = "\x1b[2J\x1b[31mHIJACKED"

    class EvilAgent:
        async def respond(self, history, question, identity, prior_evidence=False):
            yield AgentEvent(
                "evidence", evidence=Evidence("search_series", {"title": evil})
            )
            yield AgentEvent("done", f"You hold {evil}.")

    async def exercise():
        state = tmp_path / "state"
        store = ConversationStore(state)
        app = CleoApp(EvilAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "what do I have?"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            frame = "\n".join(
                segment.text
                for strip in app.screen._compositor.render_strips()
                for segment in strip
            )
            assert "\x1b" not in frame
            assert "HIJACKED" in frame
            stored = store.messages(app._conversation.id)[-1].content
            assert "\x1b" not in stored
            assert "\x1b" not in store.export_markdown(app._conversation.id).read_text()

    asyncio.run(exercise())


# --- the remaining local commands ----------------------------------------------


def test_open_export_and_new_are_addressable_from_the_composer(tmp_path):
    async def exercise():
        state = tmp_path / "state"
        store, first, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("open", first.id[:8]))
            assert app._conversation.id == first.id
            await app._handle_command(Command("open", "nope"))
            assert app._conversation.id == first.id

            await app._handle_command(Command("export", ""))
            assert len(list((state / "exports").glob("*.md"))) == 1
            await app._handle_command(Command("export", ""))  # refuses to overwrite
            assert len(list((state / "exports").glob("*.md"))) == 1
            await app._handle_command(Command("export", "nope"))

            await app._handle_command(Command("new", "unexpected argument"))
            assert app._conversation.id == first.id
            await app._handle_command(Command("new", ""))
            assert app._conversation.id != first.id

            await app._handle_command(Command("name", ""))
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            await pilot.click("#settings-cancel")
            await pilot.pause()

            await app._handle_command(Command("name", "x" * 200))
            assert store.identity().name == "Cleo"
            await app._handle_command(Command("style", "sideways"))
            assert store.identity().response_style == "compact"
            await app._handle_command(Command("frobnicate", ""))
            await app._handle_command(Command("help", "extra"))
            await app._handle_command(Command("history", "keep"))
            await pilot.pause()
            assert isinstance(app.screen, HistoryScreen)
            app.screen.action_close()
            await pilot.pause()

    asyncio.run(exercise())


def test_a_slash_command_typed_into_the_composer_never_reaches_the_agent(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = FakeAgent()
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "/help"
            composer.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert agent.questions == []
            assert store.messages(app._conversation.id) == []

    asyncio.run(exercise())


def test_a_question_asked_after_the_conversation_vanished_opens_a_fresh_one(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            gone = app._conversation.id
            store.delete_conversation(gone)
            app.query_one("#composer", TextArea).text = "still listening?"
            await app._submit()
            await app._reply_task
            await pilot.pause()
            assert app._conversation.id != gone
            assert len(store.messages(app._conversation.id)) == 2

    asyncio.run(exercise())


def test_closing_history_after_deleting_the_open_conversation_opens_a_fresh_one(
    tmp_path,
):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "delete me later"
            await app._submit()
            await app._reply_task
            live = app._conversation.id

            app.action_history()
            await pilot.pause()
            app.screen.query_one("#history-list", ListView).index = 0
            app.screen.action_delete()
            await pilot.pause()
            await pilot.click("#confirm")
            await pilot.pause()
            app.screen.action_close()
            await pilot.pause()
            assert app._conversation.id != live

    asyncio.run(exercise())


def test_the_history_screen_tolerates_an_empty_list_and_opens_by_click(tmp_path):
    async def exercise():
        store, first, _ = seeded(tmp_path)
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.action_history()
            await pilot.pause()
            screen = app.screen
            screen.query_one("#history-search", Input).value = "nothing matches this"
            await pilot.pause()
            screen.action_open()  # nothing highlighted
            screen.action_delete()
            screen.action_export()
            await pilot.pause()
            assert isinstance(app.screen, HistoryScreen)

            screen.query_one("#history-search", Input).value = ""
            await pilot.pause()
            await pilot.click(f"#conversation-{first.id}")
            await pilot.pause()
            assert app._conversation.id == first.id

    asyncio.run(exercise())


def test_the_settings_screen_reports_a_rejected_name_and_can_be_escaped(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.action_settings()
            await pilot.pause()
            app.screen.query_one("#settings-name", Input).value = "   "
            await pilot.click("#settings-save")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            assert store.identity().name == "Cleo"
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)

    asyncio.run(exercise())


def test_persona_editing_opens_settings_and_reports_an_over_long_one(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            await app._handle_command(Command("persona", ""))
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            app.screen.action_cancel()
            await pilot.pause()

            await app._handle_command(Command("persona", "x" * 2_001))
            assert store.identity().persona == ""

    asyncio.run(exercise())


def test_the_adapters_are_closed_when_the_app_shuts_down(tmp_path):
    closed = []

    async def close_one():
        closed.append("one")

    async def close_two():
        closed.append("two")

    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store, (close_one, close_two))
        async with app.run_test(size=(100, 50)):
            pass

    asyncio.run(exercise())
    assert closed == ["one", "two"]


def test_the_composer_recalls_earlier_input(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        app = CleoApp(FakeAgent(), store)
        async with app.run_test(size=(100, 50)) as pilot:
            composer = app.query_one("#composer", TextArea)
            app.action_previous_input()
            app.action_next_input()
            assert composer.text == ""

            composer.text = "first"
            await app._submit()
            await app._reply_task
            composer.text = "second"
            await app._submit()
            await app._reply_task
            await pilot.pause()

            app.action_previous_input()
            assert composer.text == "second"
            app.action_previous_input()
            assert composer.text == "first"
            app.action_next_input()
            assert composer.text == "second"
            app.action_next_input()
            assert composer.text == ""

    asyncio.run(exercise())


def test_an_empty_composer_sends_nothing(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = FakeAgent()
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "   "
            app.query_one("#composer", TextArea).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert agent.questions == []
            assert app._reply_task is None

    asyncio.run(exercise())


def test_the_send_button_asks_the_question(tmp_path):
    async def exercise():
        store = ConversationStore(tmp_path / "state")
        agent = FakeAgent()
        app = CleoApp(agent, store)
        async with app.run_test(size=(100, 50)) as pilot:
            app.query_one("#composer", TextArea).text = "a real question"
            await pilot.click("#send")
            await pilot.pause()
            assert app._reply_task is not None
            await app._reply_task
            assert agent.questions == ["a real question"]

    asyncio.run(exercise())
