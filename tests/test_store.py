from __future__ import annotations

import os
import sqlite3
from typing import Any, cast

import pytest

from olympus.config import Settings
from olympus.domain import AgentProfile, Evidence, Message
from olympus.ports import StoreError
from olympus.store import MIGRATIONS, OlympusStore


def profile(identifier: str, name: str) -> AgentProfile:
    return AgentProfile(
        id=identifier,
        kind="test",
        name=name,
        settings={"endpoint": "http://example.test"},
    )


def test_agents_and_conversations_are_persisted_and_isolated(tmp_path):
    store = OlympusStore(tmp_path / "state")
    alpha = store.save_agent(profile("alpha", "Alpha"))
    beta = store.save_agent(profile("beta", "Beta"))
    alpha_store = store.for_agent(alpha.id)
    beta_store = store.for_agent(beta.id)

    alpha_chat = alpha_store.create_conversation()
    beta_chat = beta_store.create_conversation()
    alpha_store.add_message(alpha_chat.id, Message("user", "private alpha"))
    alpha_store.add_message(
        alpha_chat.id,
        Message("assistant", "answer"),
        (Evidence("lookup", {"safe": True}),),
    )
    beta_store.add_message(beta_chat.id, Message("user", "private beta"))

    assert [item.content for item in alpha_store.messages(alpha_chat.id)] == [
        "private alpha",
        "answer",
    ]
    assert alpha_store.messages(beta_chat.id) == []
    assert alpha_store.has_evidence(alpha_chat.id)
    assert not beta_store.has_evidence(beta_chat.id)
    assert [item.title for item in alpha_store.conversations("alpha")] == [
        "private alpha"
    ]

    store.close()
    reopened = OlympusStore(tmp_path / "state")
    assert [item.name for item in reopened.agents()] == ["Alpha", "Beta"]
    assert reopened.for_agent("alpha").messages(alpha_chat.id)[-1].content == "answer"
    reopened.close()


def test_profile_validation_defaults_and_removal_modes(tmp_path):
    store = OlympusStore(tmp_path / "state")
    alpha = store.save_agent(profile("alpha", "Alpha"))
    store.save_agent(profile("beta", "Beta"))
    with pytest.raises(StoreError, match="unique"):
        store.save_agent(profile("gamma", "alpha"))
    with pytest.raises(StoreError, match="1-40"):
        store.save_agent(profile("bad", ""))
    with pytest.raises(StoreError, match="at most"):
        store.save_agent(AgentProfile("bad", "test", "Bad", persona="x" * 2_001))
    with pytest.raises(StoreError, match="compact or detailed"):
        store.save_agent(
            AgentProfile("bad", "test", "Bad", response_style=cast(Any, "verbose"))
        )

    assert store.agent("ALPHA") == alpha
    assert store.agent("a") == alpha
    assert store.save_ollama_defaults("http://ollama.test/", "model-x").url == (
        "http://ollama.test"
    )
    assert store.ollama_defaults().model == "model-x"
    overrides = Settings(tmp_path, "http://override.test", "model-y")
    assert store.ollama_defaults(overrides).model == "model-y"
    with pytest.raises(StoreError, match="http"):
        store.save_ollama_defaults("localhost", "model")
    with pytest.raises(StoreError, match="not a valid URL"):
        store.save_ollama_defaults("http://localhost:1143x", "model")
    with pytest.raises(StoreError, match="model"):
        store.save_ollama_defaults("http://localhost", "")

    store.archive_agent("alpha")
    assert store.agent("alpha") is None
    assert [item.id for item in store.agents(include_archived=True)] == [
        "alpha",
        "beta",
    ]
    assert store.delete_agent("alpha")
    assert not store.delete_agent("missing")
    store.close()


def test_history_operations_export_and_permissions(tmp_path):
    state = tmp_path / "state"
    store = OlympusStore(state)
    store.save_agent(profile("alpha", "Alpha"))
    conversations = store.for_agent("alpha")
    draft = conversations.draft_or_create()
    assert conversations.draft_or_create() == draft
    assert conversations.add_message(draft.id, Message("user", "A / strange title"))
    assert not conversations.add_message("missing", Message("assistant", "lost"))
    assert conversations.conversation(draft.id[:8]) is not None
    target = conversations.export_markdown(draft.id)
    assert target.stat().st_mode & 0o777 == 0o600
    assert "Agent: Alpha" in target.read_text(encoding="utf-8")
    with pytest.raises(StoreError, match="already exists"):
        conversations.export_markdown(draft.id)
    assert conversations.delete_conversation(draft.id)
    assert not conversations.delete_conversation(draft.id)
    with pytest.raises(StoreError, match="not found"):
        conversations.export_markdown("missing")
    conversations.create_conversation()
    assert conversations.clear_conversations() == 1
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / "olympus.sqlite3").stat().st_mode & 0o777 == 0o600
    store.close()


def test_export_refuses_a_symbolic_export_directory(tmp_path):
    state = tmp_path / "state"
    store = OlympusStore(state)
    store.save_agent(profile("alpha", "Alpha"))
    conversations = store.for_agent("alpha")
    chat = conversations.create_conversation()
    conversations.add_message(chat.id, Message("user", "hello"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store.exports.symlink_to(elsewhere)
    with pytest.raises(StoreError, match="symbolic link"):
        conversations.export_markdown(chat.id)
    store.close()


def test_future_schema_and_symbolic_paths_are_refused(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    state = tmp_path / "linked"
    state.symlink_to(target)
    with pytest.raises(StoreError, match="symbolic link"):
        OlympusStore(state)

    future = tmp_path / "future"
    future.mkdir()
    connection = sqlite3.connect(future / "olympus.sqlite3")
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    with pytest.raises(StoreError, match="newer"):
        OlympusStore(future)


def test_an_existing_unversioned_database_is_backed_up_before_migration(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    connection = sqlite3.connect(state / "olympus.sqlite3")
    connection.execute("CREATE TABLE legacy (value TEXT)")
    connection.commit()
    connection.close()

    store = OlympusStore(state)
    backups = list((state / "backups").glob("olympus-v0-*.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute(
            "SELECT name FROM sqlite_master WHERE name = 'legacy'"
        ).fetchone()
    finally:
        backup.close()
    store.close()


def test_history_listings_never_mix_agents(tmp_path):
    store = OlympusStore(tmp_path / "state")
    for identifier in ("alpha", "beta"):
        store.save_agent(profile(identifier, identifier.title()))
        view = store.for_agent(identifier)
        chat = view.create_conversation()
        view.add_message(chat.id, Message("user", f"{identifier} secret"))
    assert [item.title for item in store.for_agent("alpha").conversations()] == [
        "alpha secret"
    ]
    assert store.for_agent("beta").conversations("alpha") == []
    store.close()


def test_deleting_an_agent_deletes_its_conversations_and_messages(tmp_path):
    store = OlympusStore(tmp_path / "state")
    store.save_agent(profile("alpha", "Alpha"))
    view = store.for_agent("alpha")
    chat = view.create_conversation()
    view.add_message(chat.id, Message("user", "goodbye"))
    store.delete_agent("alpha")
    counts = [
        store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("conversations", "messages")
    ]
    assert counts == [0, 0]
    store.close()


def test_agents_created_in_the_same_second_keep_their_order(tmp_path):
    store = OlympusStore(tmp_path / "state")
    for name in ("Zeta", "Alpha", "Mu"):
        store.save_agent(
            AgentProfile(name.lower(), "test", name, created_at="2026-01-01T00:00:00")
        )
    assert [agent.name for agent in store.agents()] == ["Zeta", "Alpha", "Mu"]
    store.close()


def test_a_failed_migration_leaves_nothing_half_built(tmp_path, monkeypatch):
    state = tmp_path / "state"
    broken = (*MIGRATIONS[1][:3], "CREATE TABLE broken (")
    monkeypatch.setitem(MIGRATIONS, 1, broken)
    with pytest.raises(StoreError, match="could not open"):
        OlympusStore(state)
    connection = sqlite3.connect(state / "olympus.sqlite3")
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    assert (tables, version) == ([], 0)

    monkeypatch.undo()
    store = OlympusStore(state)
    assert store.agents() == []
    store.close()


def test_unusable_state_is_reported_rather_than_raised_raw(tmp_path):
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "olympus.sqlite3").write_bytes(b"this is not a database" * 100)
    with pytest.raises(StoreError, match="could not open"):
        OlympusStore(corrupt)

    occupied = tmp_path / "occupied"
    occupied.write_text("a file where the state directory should be")
    with pytest.raises(StoreError, match="could not open"):
        OlympusStore(occupied)

    if os.geteuid() != 0:
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        try:
            with pytest.raises(StoreError, match="could not open"):
                OlympusStore(locked / "state")
        finally:
            locked.chmod(0o700)


def test_database_side_files_are_owner_only(tmp_path):
    state = tmp_path / "state"
    store = OlympusStore(state)
    store.save_agent(profile("alpha", "Alpha"))
    for name in ("olympus.sqlite3", "olympus.sqlite3-wal", "olympus.sqlite3-shm"):
        assert (state / name).stat().st_mode & 0o777 == 0o600, name
    store.close()
