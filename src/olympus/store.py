"""Versioned private persistence for agents and their conversations."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from olympus.config import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, Settings
from olympus.domain import (
    AgentProfile,
    Conversation,
    Evidence,
    Message,
    OllamaDefaults,
    utc_now,
)
from olympus.ports import StoreError
from olympus.presentation import format_timestamp, plain_text
from olympus.validation import http_url_problem

SCHEMA_VERSION = 1
MAX_NAME_LENGTH = 40
MAX_PERSONA_LENGTH = 2_000

# One tuple of statements per schema version. Each runs in its own
# transaction together with the `user_version` bump that records it.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            persona TEXT NOT NULL DEFAULT '',
            response_style TEXT NOT NULL DEFAULT 'compact'
                CHECK (response_style IN ('compact', 'detailed')),
            ollama_url TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            settings_json TEXT NOT NULL DEFAULT '{}',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE UNIQUE INDEX active_agent_names
            ON agents(lower(name)) WHERE archived = 0""",
        """CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE INDEX conversations_by_agent
            ON conversations(agent_id, updated_at DESC)""",
        """CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id)
                ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '[]'
        )""",
        """CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    )
}


def _encode_evidence(evidence: tuple[Evidence, ...]) -> str:
    return json.dumps(
        [
            {
                "tool": item.tool,
                "payload": item.payload,
                "retrieved_at": item.retrieved_at,
                "arguments": item.arguments,
            }
            for item in evidence
        ],
        ensure_ascii=False,
    )


def _create_private(path: Path) -> None:
    """Create `path` owner-only before SQLite opens it.

    SQLite gives its `-wal` and `-shm` files the database's mode, so the
    database must never exist, even briefly, with a wider one.
    """
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    path.chmod(0o600)


def _restrict_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.exists():
            sidecar.chmod(0o600)


class OlympusStore:
    """The application store; views scope conversation operations to one agent."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.database = state_dir / "olympus.sqlite3"
        self.exports = state_dir / "exports"
        self.backups = state_dir / "backups"
        try:
            self._open()
        except (OSError, sqlite3.Error) as error:
            raise StoreError(
                f"Olympus could not open its data in {state_dir}: {error}"
            ) from error

    def _open(self) -> None:
        self._prepare_directory()
        existed = self.database.exists() and self.database.stat().st_size > 0
        _create_private(self.database)
        self._connection = sqlite3.connect(self.database)
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._migrate(existed)
            _restrict_sidecars(self.database)
        except BaseException:
            self._connection.close()
            raise

    def _prepare_directory(self) -> None:
        if self.state_dir.is_symlink():
            raise StoreError("Olympus's state directory cannot be a symbolic link.")
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        if self.database.is_symlink():
            raise StoreError("Olympus's database cannot be a symbolic link.")

    def _migrate(self, existed: bool) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise StoreError(
                "This Olympus data was created by a newer application version."
            )
        if existed and version < SCHEMA_VERSION:
            self._backup(version)
        for target in range(version + 1, SCHEMA_VERSION + 1):
            self._apply_migration(target)

    def _apply_migration(self, target: int) -> None:
        """Run one schema step atomically.

        Python's sqlite3 does not open a transaction for DDL on its own, so
        the step is bracketed explicitly; SQLite then rolls the half-created
        tables back together with everything else if any statement fails.
        """
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in MIGRATIONS[target]:
                self._connection.execute(statement)
            self._connection.execute(f"PRAGMA user_version = {target}")
        except BaseException:
            self._connection.rollback()
            raise
        self._connection.commit()

    def _backup(self, version: int) -> None:
        self.backups.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backups.chmod(0o700)
        target = (
            self.backups / f"olympus-v{version}-{utc_now().replace(':', '-')}.sqlite3"
        )
        destination = sqlite3.connect(target)
        try:
            self._connection.backup(destination)
        finally:
            destination.close()
        target.chmod(0o600)

    def agents(self, include_archived: bool = False) -> list[AgentProfile]:
        where = "" if include_archived else "WHERE archived = 0"
        rows = self._connection.execute(
            f"SELECT * FROM agents {where} ORDER BY created_at, rowid"
        ).fetchall()
        return [self._profile(row) for row in rows]

    def agent(self, identifier: str) -> AgentProfile | None:
        exact = self._connection.execute(
            """SELECT * FROM agents WHERE archived = 0
               AND (id = ? OR lower(name) = lower(?))""",
            (identifier, identifier),
        ).fetchall()
        if len(exact) == 1:
            return self._profile(exact[0])
        rows = self._connection.execute(
            """SELECT * FROM agents
               WHERE archived = 0 AND substr(id, 1, length(?)) = ?
               ORDER BY updated_at DESC""",
            (identifier, identifier),
        ).fetchall()
        return self._profile(rows[0]) if len(rows) == 1 else None

    def save_agent(self, profile: AgentProfile) -> AgentProfile:
        self._validate_identity(profile.name, profile.persona, profile.response_style)
        now = utc_now()
        saved = AgentProfile(
            id=profile.id,
            kind=profile.kind,
            name=profile.name.strip(),
            persona=profile.persona.strip(),
            response_style=profile.response_style,
            ollama_url=profile.ollama_url.rstrip("/"),
            model=profile.model.strip(),
            settings=dict(profile.settings),
            archived=profile.archived,
            created_at=profile.created_at,
            updated_at=now,
        )
        try:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO agents
                       (id, kind, name, persona, response_style, ollama_url, model,
                        settings_json, archived, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         kind=excluded.kind, name=excluded.name,
                         persona=excluded.persona,
                         response_style=excluded.response_style,
                         ollama_url=excluded.ollama_url, model=excluded.model,
                         settings_json=excluded.settings_json,
                         archived=excluded.archived, updated_at=excluded.updated_at""",
                    (
                        saved.id,
                        saved.kind,
                        saved.name,
                        saved.persona,
                        saved.response_style,
                        saved.ollama_url,
                        saved.model,
                        json.dumps(saved.settings, ensure_ascii=False),
                        int(saved.archived),
                        saved.created_at,
                        saved.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreError("Every active agent needs a unique name.") from error
        return saved

    def archive_agent(self, agent_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE agents SET archived = 1, updated_at = ? WHERE id = ?",
                (utc_now(), agent_id),
            )
        return cursor.rowcount == 1

    def restore_agent(self, agent_id: str) -> bool:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    "UPDATE agents SET archived = 0, updated_at = ? WHERE id = ?",
                    (utc_now(), agent_id),
                )
        except sqlite3.IntegrityError as error:
            raise StoreError(
                "Rename the active agent with this name before restoring it."
            ) from error
        return cursor.rowcount == 1

    def delete_agent(self, agent_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM agents WHERE id = ?", (agent_id,)
            )
        return cursor.rowcount == 1

    def for_agent(self, agent_id: str) -> AgentConversationStore:
        return AgentConversationStore(self, agent_id)

    def ollama_defaults(self, settings: Settings | None = None) -> OllamaDefaults:
        rows = dict(self._connection.execute("SELECT key, value FROM settings"))
        configured_url = rows.get("ollama_url", DEFAULT_OLLAMA_URL)
        configured_model = rows.get("ollama_model", DEFAULT_MODEL)
        return OllamaDefaults(
            url=(settings.ollama_url_override if settings else "") or configured_url,
            model=(settings.model_override if settings else "") or configured_model,
        )

    def save_ollama_defaults(self, url: str, model: str) -> OllamaDefaults:
        clean_url = url.strip().rstrip("/")
        clean_model = model.strip()
        problem = http_url_problem(clean_url, "Ollama URL")
        if problem:
            raise StoreError(problem)
        if not clean_model:
            raise StoreError("Choose a default Ollama model.")
        with self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (("ollama_url", clean_url), ("ollama_model", clean_model)),
            )
        return OllamaDefaults(clean_url, clean_model)

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _profile(row: sqlite3.Row) -> AgentProfile:
        return AgentProfile(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            persona=row["persona"],
            response_style=row["response_style"],
            ollama_url=row["ollama_url"],
            model=row["model"],
            settings=json.loads(row["settings_json"]),
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _validate_identity(name: str, persona: str, response_style: str) -> None:
        clean_name = name.strip()
        if (
            not clean_name
            or len(clean_name) > MAX_NAME_LENGTH
            or not clean_name.isprintable()
        ):
            raise StoreError(f"Name must be 1-{MAX_NAME_LENGTH} printable characters.")
        if len(persona.strip()) > MAX_PERSONA_LENGTH:
            raise StoreError(
                f"Persona instructions must be at most {MAX_PERSONA_LENGTH} characters."
            )
        if response_style not in {"compact", "detailed"}:
            raise StoreError("Response style must be compact or detailed.")


class AgentConversationStore:
    """Conversation repository scoped so one agent can never see another's rows."""

    def __init__(self, store: OlympusStore, agent_id: str) -> None:
        self._store = store
        self._agent_id = agent_id

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._store._connection

    def create_conversation(self, title: str = "New conversation") -> Conversation:
        conversation_id = str(uuid4())
        now = utc_now()
        with self._connection:
            self._connection.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                (conversation_id, self._agent_id, title, now, now),
            )
        return Conversation(conversation_id, title, now, now)

    def draft_or_create(self) -> Conversation:
        row = self._connection.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at
               FROM conversations AS c
               WHERE c.agent_id = ? AND NOT EXISTS (
                   SELECT 1 FROM messages AS m WHERE m.conversation_id = c.id
               ) ORDER BY c.created_at DESC LIMIT 1""",
            (self._agent_id,),
        ).fetchone()
        return Conversation(**dict(row)) if row else self.create_conversation()

    def add_message(
        self,
        conversation_id: str,
        message: Message,
        evidence: tuple[Evidence, ...] = (),
    ) -> bool:
        content = plain_text(message.content)
        try:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, created_at, evidence_json)
                       SELECT id, ?, ?, ?, ? FROM conversations
                       WHERE id = ? AND agent_id = ?""",
                    (
                        message.role,
                        content,
                        message.created_at,
                        _encode_evidence(evidence),
                        conversation_id,
                        self._agent_id,
                    ),
                )
                changed = self._connection.execute("SELECT changes()").fetchone()[0]
                if not changed:
                    return False
                self._connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (message.created_at, conversation_id),
                )
                if message.role == "user":
                    self._connection.execute(
                        """UPDATE conversations SET title = ?
                           WHERE id = ? AND title = 'New conversation'""",
                        (self._title(content), conversation_id),
                    )
        except sqlite3.IntegrityError:
            return False
        return True

    def has_evidence(self, conversation_id: str) -> bool:
        row = self._connection.execute(
            """SELECT 1 FROM messages AS m
               JOIN conversations AS c ON c.id = m.conversation_id
               WHERE c.agent_id = ? AND c.id = ? AND m.evidence_json != '[]' LIMIT 1""",
            (self._agent_id, conversation_id),
        ).fetchone()
        return row is not None

    def messages(self, conversation_id: str) -> list[Message]:
        rows = self._connection.execute(
            """SELECT m.role, m.content, m.created_at FROM messages AS m
               JOIN conversations AS c ON c.id = m.conversation_id
               WHERE c.agent_id = ? AND c.id = ? ORDER BY m.id""",
            (self._agent_id, conversation_id),
        ).fetchall()
        return [Message(row["role"], row["content"], row["created_at"]) for row in rows]

    def message_evidence(self, conversation_id: str) -> list[tuple[Evidence, ...]]:
        rows = self._connection.execute(
            """SELECT m.evidence_json FROM messages AS m
               JOIN conversations AS c ON c.id = m.conversation_id
               WHERE c.agent_id = ? AND c.id = ? ORDER BY m.id""",
            (self._agent_id, conversation_id),
        ).fetchall()
        return [
            tuple(Evidence(**item) for item in json.loads(row["evidence_json"]))
            for row in rows
        ]

    def conversations(self, query: str = "") -> list[Conversation]:
        if query:
            pattern = f"%{self._escape_like(query)}%"
            rows = self._connection.execute(
                """SELECT DISTINCT c.id, c.title, c.created_at, c.updated_at
                   FROM conversations AS c LEFT JOIN messages AS m
                   ON m.conversation_id = c.id
                   WHERE c.agent_id = ? AND
                     (c.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\')
                   ORDER BY c.updated_at DESC""",
                (self._agent_id, pattern, pattern),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT c.id, c.title, c.created_at, c.updated_at
                   FROM conversations AS c WHERE c.agent_id = ? AND EXISTS (
                       SELECT 1 FROM messages AS m WHERE m.conversation_id = c.id
                   ) ORDER BY c.updated_at DESC""",
                (self._agent_id,),
            ).fetchall()
        return [Conversation(**dict(row)) for row in rows]

    def conversation(self, prefix: str) -> Conversation | None:
        rows = self._connection.execute(
            """SELECT id, title, created_at, updated_at FROM conversations
               WHERE agent_id = ? AND substr(id, 1, length(?)) = ?
               ORDER BY updated_at DESC""",
            (self._agent_id, prefix, prefix),
        ).fetchall()
        return Conversation(**dict(rows[0])) if len(rows) == 1 else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE id = ? AND agent_id = ?",
                (conversation_id, self._agent_id),
            )
        return cursor.rowcount == 1

    def clear_conversations(self) -> int:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE agent_id = ?", (self._agent_id,)
            )
        return cursor.rowcount

    def _agent_name(self) -> str:
        row = self._connection.execute(
            "SELECT name FROM agents WHERE id = ?", (self._agent_id,)
        ).fetchone()
        if row is None:
            raise StoreError("This agent is no longer available.")
        return row["name"]

    def export_markdown(self, conversation_id: str) -> Path:
        conversation = self.conversation(conversation_id)
        if conversation is None:
            raise StoreError("Conversation not found.")
        if self._store.exports.is_symlink():
            raise StoreError("Olympus's export directory cannot be a symbolic link.")
        directory = self._store.exports / self._agent_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        stem = re.sub(r"[^a-z0-9]+", "-", conversation.title.lower()).strip("-")
        target = directory / (
            f"{conversation.created_at[:10]}-{stem or 'chat'}-{conversation.id[:8]}.md"
        )
        agent_name = self._agent_name()
        lines = [
            f"# {conversation.title}",
            "",
            f"Agent: {agent_name}",
            f"Started: {format_timestamp(conversation.created_at)}",
            "",
        ]
        for message in self.messages(conversation.id):
            label = "You" if message.role == "user" else agent_name
            lines.extend(
                (
                    f"## {label} — {format_timestamp(message.created_at)}",
                    "",
                    message.content,
                    "",
                )
            )
        try:
            with target.open("x", encoding="utf-8") as output:
                output.write("\n".join(lines))
        except FileExistsError as error:
            raise StoreError(f"Export already exists: {target}") from error
        target.chmod(0o600)
        return target

    @staticmethod
    def _title(content: str) -> str:
        title = " ".join(content.split())
        return title[:60] + ("…" if len(title) > 60 else "")

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
