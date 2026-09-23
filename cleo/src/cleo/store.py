"""Private local conversation and preference persistence."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from cleo.domain import Conversation, Evidence, Identity, Message, utc_now
from cleo.ports import StoreError
from cleo.presentation import format_timestamp, plain_text

DEFAULT_NAME = "Cleo"
MAX_NAME_LENGTH = 40
MAX_PERSONA_LENGTH = 2_000


def _encode(evidence: tuple[Evidence, ...]) -> str:
    """The Nineveh reads behind one answer, as stored beside it."""
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


class ConversationStore:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._database = state_dir / "history.sqlite3"
        self._exports = state_dir / "exports"
        self._prepare_directory()
        if self._database.exists():
            self._database.chmod(0o600)
        self._connection = sqlite3.connect(self._database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self._database.chmod(0o600)

    def _prepare_directory(self) -> None:
        if self._state_dir.is_symlink():
            raise StoreError("Cleo's state directory cannot be a symbolic link.")
        self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._state_dir.chmod(0o700)
        if self._database.is_symlink():
            raise StoreError("Cleo's history database cannot be a symbolic link.")

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id)
                    ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def create_conversation(self, title: str = "New conversation") -> Conversation:
        conversation_id = str(uuid4())
        now = utc_now()
        self._connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            (conversation_id, title, now, now),
        )
        self._connection.commit()
        return Conversation(conversation_id, title, now, now)

    def draft_or_create(self) -> Conversation:
        draft = self._connection.execute(
            """SELECT c.* FROM conversations AS c
               WHERE NOT EXISTS (
                   SELECT 1 FROM messages AS m WHERE m.conversation_id = c.id
               )
               ORDER BY c.created_at DESC LIMIT 1"""
        ).fetchone()
        if draft is not None:
            return Conversation(**dict(draft))
        return self.create_conversation()

    def add_message(
        self,
        conversation_id: str,
        message: Message,
        evidence: tuple[Evidence, ...] = (),
    ) -> bool:
        """Append a message. False if the conversation was deleted meanwhile.

        A reply can still be in flight when its conversation is cleared, and
        losing that answer is the correct outcome -- crashing the reply is not.
        """
        content = plain_text(message.content)
        try:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, created_at, evidence_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        conversation_id,
                        message.role,
                        content,
                        message.created_at,
                        _encode(evidence),
                    ),
                )
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
        """Whether any answer here was already backed by a Nineveh read."""
        row = self._connection.execute(
            """SELECT 1 FROM messages
               WHERE conversation_id = ? AND evidence_json != '[]' LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        return row is not None

    def messages(self, conversation_id: str) -> list[Message]:
        rows = self._connection.execute(
            """SELECT role, content, created_at FROM messages
               WHERE conversation_id = ? ORDER BY id""",
            (conversation_id,),
        ).fetchall()
        return [Message(row["role"], row["content"], row["created_at"]) for row in rows]

    def message_evidence(self, conversation_id: str) -> list[tuple[Evidence, ...]]:
        rows = self._connection.execute(
            """SELECT evidence_json FROM messages
               WHERE conversation_id = ? ORDER BY id""",
            (conversation_id,),
        ).fetchall()
        return [
            tuple(Evidence(**item) for item in json.loads(row["evidence_json"]))
            for row in rows
        ]

    def conversations(self, query: str = "") -> list[Conversation]:
        if query:
            pattern = f"%{self._escape_like(query)}%"
            rows = self._connection.execute(
                """SELECT DISTINCT c.* FROM conversations AS c
                   LEFT JOIN messages AS m ON m.conversation_id = c.id
                   WHERE c.title LIKE ? ESCAPE '\\'
                      OR m.content LIKE ? ESCAPE '\\'
                   ORDER BY c.updated_at DESC""",
                (pattern, pattern),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT c.* FROM conversations AS c
                   WHERE EXISTS (
                       SELECT 1 FROM messages AS m WHERE m.conversation_id = c.id
                   )
                   ORDER BY c.updated_at DESC"""
            ).fetchall()
        return [Conversation(**dict(row)) for row in rows]

    def conversation(self, prefix: str) -> Conversation | None:
        rows = self._connection.execute(
            """SELECT * FROM conversations
               WHERE substr(id, 1, length(?)) = ? ORDER BY updated_at DESC""",
            (prefix, prefix),
        ).fetchall()
        return Conversation(**dict(rows[0])) if len(rows) == 1 else None

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete one known database record; this is never model-callable."""
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
        return cursor.rowcount == 1

    def clear_conversations(self) -> int:
        """Clear Cleo-owned history; this is never model-callable."""
        with self._connection:
            cursor = self._connection.execute("DELETE FROM conversations")
        return cursor.rowcount

    def identity(self) -> Identity:
        rows = dict(self._connection.execute("SELECT key, value FROM settings"))
        return Identity(
            rows.get("name", DEFAULT_NAME),
            rows.get("persona", ""),
            rows.get("response_style", "compact"),
        )

    def save_identity(
        self, name: str, persona: str, response_style: str = "compact"
    ) -> Identity:
        clean_name = name.strip()
        clean_persona = persona.strip()
        if (
            not clean_name
            or len(clean_name) > MAX_NAME_LENGTH
            or not clean_name.isprintable()
        ):
            raise StoreError(f"Name must be 1-{MAX_NAME_LENGTH} printable characters.")
        if len(clean_persona) > MAX_PERSONA_LENGTH:
            raise StoreError(
                f"Persona instructions must be at most {MAX_PERSONA_LENGTH} characters."
            )
        if response_style not in {"compact", "detailed"}:
            raise StoreError("Response style must be compact or detailed.")
        with self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (
                    ("name", clean_name),
                    ("persona", clean_persona),
                    ("response_style", response_style),
                ),
            )
        return Identity(clean_name, clean_persona, response_style)

    def export_markdown(self, conversation_id: str) -> Path:
        conversation = self.conversation(conversation_id)
        if conversation is None:
            raise StoreError("Conversation not found.")
        if self._exports.is_symlink():
            raise StoreError("Cleo's export directory cannot be a symbolic link.")
        self._exports.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._exports.chmod(0o700)
        stem = re.sub(r"[^a-z0-9]+", "-", conversation.title.lower()).strip("-")
        target = (
            self._exports
            / f"{conversation.created_at[:10]}-{stem or 'chat'}-{conversation.id[:8]}.md"
        )
        lines = [
            f"# {conversation.title}",
            "",
            f"Started: {format_timestamp(conversation.created_at)}",
            "",
        ]
        librarian = self.identity().name
        for message in self.messages(conversation.id):
            label = "You" if message.role == "user" else librarian
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

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _title(content: str) -> str:
        title = " ".join(content.split())
        return title[:60] + ("…" if len(title) > 60 else "")

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
