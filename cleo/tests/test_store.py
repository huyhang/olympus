from __future__ import annotations

import re
import stat

import pytest

from cleo.domain import Evidence, Message
from cleo.store import ConversationStore, StoreError


def test_conversations_can_be_reviewed_searched_resumed_and_cleared(tmp_path):
    store = ConversationStore(tmp_path / "state")
    first = store.create_conversation()
    second = store.create_conversation()
    store.add_message(first.id, Message("user", "Tell me about Pluto"))
    store.add_message(
        first.id,
        Message("assistant", "Nineveh says there are eight volumes."),
        (
            Evidence(
                "get_series",
                {"title": "Pluto", "volumes": 8},
                arguments={"series_id": "pluto"},
            ),
        ),
    )
    store.add_message(second.id, Message("user", "Find Vinland Saga"))

    assert [item.role for item in store.messages(first.id)] == ["user", "assistant"]
    assert store.conversations("eight")[0].id == first.id
    assert store.conversation(first.id[:8]).id == first.id
    assert store.message_evidence(first.id)[1][0].tool == "get_series"
    assert store.message_evidence(first.id)[1][0].arguments == {"series_id": "pluto"}
    assert store.delete_conversation(first.id)
    assert store.messages(first.id) == []
    assert store.clear_conversations() == 1
    assert store.conversations() == []
    store.close()


def test_identity_is_validated_and_persisted(tmp_path):
    state = tmp_path / "state"
    store = ConversationStore(state)
    assert store.identity().name == "Cleo"
    identity = store.save_identity("Hypatia", "Warm and brief", "detailed")
    assert identity.persona == "Warm and brief"
    assert identity.response_style == "detailed"
    store.close()

    reopened = ConversationStore(state)
    assert reopened.identity().name == "Hypatia"
    assert reopened.identity().response_style == "detailed"
    with pytest.raises(StoreError):
        reopened.save_identity("", "")
    with pytest.raises(StoreError):
        reopened.save_identity("Cleo", "x" * 2_001)
    with pytest.raises(StoreError, match="compact or detailed"):
        reopened.save_identity("Cleo", "", "verbose")
    reopened.close()


def test_state_and_exports_are_owner_only_and_exports_never_overwrite(tmp_path):
    state = tmp_path / "state"
    store = ConversationStore(state)
    conversation = store.create_conversation()
    store.add_message(conversation.id, Message("user", "What is here?"))
    exported = store.export_markdown(conversation.id)

    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "history.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE(exported.stat().st_mode) == 0o600
    assert "## You" in exported.read_text(encoding="utf-8")
    with pytest.raises(StoreError, match="already exists"):
        store.export_markdown(conversation.id)
    store.close()


def test_an_answer_whose_conversation_was_cleared_is_dropped_not_raised(tmp_path):
    store = ConversationStore(tmp_path / "state")
    conversation = store.create_conversation()
    assert store.add_message(conversation.id, Message("user", "Pluto")) is True
    store.delete_conversation(conversation.id)
    assert store.add_message(conversation.id, Message("assistant", "Eight.")) is False
    store.close()


def test_a_conversation_knows_whether_nineveh_ever_answered_in_it(tmp_path):
    store = ConversationStore(tmp_path / "state")
    conversation = store.create_conversation()
    store.add_message(conversation.id, Message("user", "Pluto"))
    assert store.has_evidence(conversation.id) is False
    store.add_message(
        conversation.id,
        Message("assistant", "Eight."),
        (Evidence("get_series", {"volumes": 8}),),
    )
    assert store.has_evidence(conversation.id) is True
    store.close()


def test_terminal_escapes_are_not_written_to_the_transcript(tmp_path):
    store = ConversationStore(tmp_path / "state")
    conversation = store.create_conversation()
    store.add_message(conversation.id, Message("user", "\x1b[2Jwhat is \x1b[31mhere?"))
    stored = store.messages(conversation.id)[0].content
    assert "\x1b" not in stored
    assert "what is" in stored
    assert "\x1b" not in store.conversation(conversation.id).title
    store.close()


def test_the_export_reads_in_local_time_not_iso(tmp_path):
    store = ConversationStore(tmp_path / "state")
    conversation = store.create_conversation()
    store.add_message(conversation.id, Message("user", "What is here?"))
    text = store.export_markdown(conversation.id).read_text(encoding="utf-8")
    started = text.split("Started: ")[1].split("\n")[0]
    speaker = text.split("## You — ")[1].split("\n")[0]
    for stamp in (started, speaker):
        assert not re.match(r"\d{4}-\d{2}-\d{2}T", stamp), stamp
        assert " at " in stamp
    store.close()


def test_exporting_is_refused_without_a_conversation_or_a_real_directory(tmp_path):
    state = tmp_path / "state"
    store = ConversationStore(state)
    with pytest.raises(StoreError, match="not found"):
        store.export_markdown("no-such-conversation")

    conversation = store.create_conversation()
    store.add_message(conversation.id, Message("user", "What is here?"))
    (state / "exports").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(StoreError, match="symbolic link"):
        store.export_markdown(conversation.id)
    store.close()


def test_symbolic_link_database_is_refused(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("not a database")
    (state / "history.sqlite3").symlink_to(target)
    with pytest.raises(StoreError, match="symbolic link"):
        ConversationStore(state)
