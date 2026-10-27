from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Protocol, Sequence

from core.database import SQLiteDatabase, get_conversation_database, require_conversation_schema

from .chat_session import ChatSession, MemoryPolicy, SessionMode
from .message import Message, utc_now


class ConversationRepository(Protocol):
    def create_session(self, session: ChatSession) -> None: ...
    def save_message(self, session_id: str, message: Message) -> None: ...
    def load_session(self, session_id: str) -> ChatSession: ...
    def list_sessions(self, limit: int = 20) -> list[ChatSession]: ...

    def load_recent_messages(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[Message]: ...

    def load_messages_after(
        self,
        session_id: str,
        message_id: str | None,
        limit: int | None = None,
    ) -> list[Message]: ...

    def load_messages_until(
        self,
        session_id: str,
        message_id: str,
    ) -> list[Message]: ...

    def load_context_messages(
        self,
        session_id: str,
    ) -> list[Message]: ...

    def load_context_messages_until(
        self,
        session_id: str,
        message_id: str,
    ) -> list[Message]: ...

    def create_branch(
        self,
        parent_session_id: str,
        parent_message_id: str,
        *,
        profile_name: str | None = None,
        title: str | None = None,
        mode: SessionMode = "persistent",
        memory_policy: MemoryPolicy | None = None,
    ) -> ChatSession: ...

    def close_session(self, session_id: str) -> None: ...
    def save_summary(self, summary: "ConversationSummary") -> None: ...

    def load_latest_summary(
        self,
        session_id: str,
    ) -> "ConversationSummary | None": ...


class SessionNotFoundError(LookupError):
    pass


class MessageNotFoundError(LookupError):
    pass


class InvalidBranchError(ValueError):
    pass


class SQLiteConversationRepository:
    def __init__(
        self,
        database: SQLiteDatabase | None = None,
    ) -> None:
        self.database = database or get_conversation_database()
        require_conversation_schema(self.database)

    def create_session(self, session: ChatSession) -> None:
        if session.mode == "temporary":
            raise ValueError(
                "Las sesiones temporales no se guardan en SQLite."
            )

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions (
                    id,
                    profile_name,
                    title,
                    mode,
                    memory_policy,
                    parent_session_id,
                    parent_message_id,
                    created_at,
                    updated_at,
                    closed_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.profile_name,
                    session.title,
                    session.mode,
                    session.memory_policy,
                    session.parent_session_id,
                    session.parent_message_id,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    (
                        session.closed_at.isoformat()
                        if session.closed_at
                        else None
                    ),
                    json.dumps(
                        session.metadata,
                        ensure_ascii=False,
                    ),
                ),
            )

            for message in session.messages:
                self._insert_message(
                    connection,
                    session.id,
                    message,
                )

    def save_message(
        self,
        session_id: str,
        message: Message,
    ) -> None:
        with self.database.connect() as connection:
            exists = connection.execute(
                """
                SELECT 1
                FROM chat_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()

            if exists is None:
                raise SessionNotFoundError(
                    f"No existe la sesión: {session_id}"
                )

            self._insert_message(
                connection,
                session_id,
                message,
            )

            connection.execute(
                """
                UPDATE chat_sessions
                SET updated_at = ?
                WHERE id = ?
                """,
                (
                    message.timestamp.isoformat(),
                    session_id,
                ),
            )

    @staticmethod
    def _insert_message(
        connection: sqlite3.Connection,
        session_id: str,
        message: Message,
    ) -> None:
        connection.execute(
            """
            INSERT INTO messages (
                id,
                session_id,
                role,
                content,
                timestamp,
                metadata_json,
                ordinal
            )
            SELECT ?, ?, ?, ?, ?, ?, COALESCE(MAX(ordinal), 0) + 1
            FROM messages WHERE session_id = ?
            """,
            (
                message.id,
                session_id,
                message.role,
                message.content,
                message.timestamp.isoformat(),
                json.dumps(
                    message.metadata,
                    ensure_ascii=False,
                ),
                session_id,
            ),
        )

    def load_session(self, session_id: str) -> ChatSession:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM chat_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()

            if row is None:
                raise SessionNotFoundError(
                    f"No existe la sesión: {session_id}"
                )

            message_rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC, ordinal ASC
                """,
                (session_id,),
            ).fetchall()

        session = self._session_from_row(row)
        session.messages = [
            self._message_from_row(item)
            for item in message_rows
        ]
        return session

    def list_sessions(self, limit: int = 20) -> list[ChatSession]:
        if limit < 1:
            return []

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM chat_sessions
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            self._session_from_row(row)
            for row in rows
        ]

    def load_recent_messages(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[Message]:
        if limit < 1:
            return []

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM (
                    SELECT *
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp DESC, ordinal DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, ordinal ASC
                """,
                (session_id, limit),
            ).fetchall()

        return [
            self._message_from_row(row)
            for row in rows
        ]

    def load_messages_after(
        self,
        session_id: str,
        message_id: str | None,
        limit: int | None = None,
    ) -> list[Message]:
        with self.database.connect() as connection:
            if message_id is None:
                sql = """
                    SELECT *
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp ASC, ordinal ASC
                """
                params: Sequence[object] = (session_id,)
            else:
                anchor = connection.execute(
                    """
                    SELECT timestamp, ordinal
                    FROM messages
                    WHERE id = ?
                      AND session_id = ?
                    """,
                    (message_id, session_id),
                ).fetchone()

                if anchor is None:
                    raise MessageNotFoundError(
                        f"No existe el mensaje: {message_id}"
                    )

                sql = """
                    SELECT *
                    FROM messages
                    WHERE session_id = ?
                      AND (
                        timestamp > ?
                        OR (
                            timestamp = ?
                            AND ordinal > ?
                        )
                      )
                    ORDER BY timestamp ASC, ordinal ASC
                """
                params = (
                    session_id,
                    anchor["timestamp"],
                    anchor["timestamp"],
                    anchor["ordinal"],
                )

            if limit is not None:
                if limit < 1:
                    return []
                sql += " LIMIT ?"
                params = (*params, limit)

            rows = connection.execute(sql, params).fetchall()

        return [
            self._message_from_row(row)
            for row in rows
        ]

    def load_messages_until(
        self,
        session_id: str,
        message_id: str,
    ) -> list[Message]:
        with self.database.connect() as connection:
            anchor = connection.execute(
                """
                SELECT timestamp, ordinal
                FROM messages
                WHERE id = ?
                  AND session_id = ?
                """,
                (message_id, session_id),
            ).fetchone()

            if anchor is None:
                raise MessageNotFoundError(
                    f"El mensaje {message_id} no pertenece "
                    f"a la sesión {session_id}."
                )

            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE session_id = ?
                  AND (
                    timestamp < ?
                    OR (
                        timestamp = ?
                        AND ordinal <= ?
                    )
                  )
                ORDER BY timestamp ASC, ordinal ASC
                """,
                (
                    session_id,
                    anchor["timestamp"],
                    anchor["timestamp"],
                    anchor["ordinal"],
                ),
            ).fetchall()

        return [
            self._message_from_row(row)
            for row in rows
        ]

    def load_context_messages(
        self,
        session_id: str,
    ) -> list[Message]:
        session = self.load_session(session_id)

        if not session.parent_session_id:
            return list(session.messages)

        if not session.parent_message_id:
            raise InvalidBranchError(
                "La rama no tiene punto de bifurcación."
            )

        inherited = self.load_context_messages_until(
            session.parent_session_id,
            session.parent_message_id,
        )
        return [*inherited, *session.messages]

    def load_context_messages_until(
        self,
        session_id: str,
        message_id: str,
    ) -> list[Message]:
        session = self.load_session(session_id)
        own_until_anchor = self.load_messages_until(
            session_id,
            message_id,
        )

        if not session.parent_session_id:
            return own_until_anchor

        if not session.parent_message_id:
            raise InvalidBranchError(
                "La rama padre está incompleta."
            )

        inherited = self.load_context_messages_until(
            session.parent_session_id,
            session.parent_message_id,
        )
        return [*inherited, *own_until_anchor]

    def create_branch(
        self,
        parent_session_id: str,
        parent_message_id: str,
        *,
        profile_name: str | None = None,
        title: str | None = None,
        mode: SessionMode = "persistent",
        memory_policy: MemoryPolicy | None = None,
    ) -> ChatSession:
        parent = self.load_session(parent_session_id)
        self._assert_message_belongs_to_session(
            parent_session_id,
            parent_message_id,
        )

        effective_policy: MemoryPolicy
        if mode in {"temporary", "action"}:
            effective_policy = "disabled"
        elif memory_policy is not None:
            effective_policy = memory_policy
        else:
            effective_policy = parent.memory_policy

        branch = ChatSession(
            profile_name=profile_name or parent.profile_name,
            title=title,
            mode=mode,
            memory_policy=effective_policy,
            parent_session_id=parent_session_id,
            parent_message_id=parent_message_id,
            metadata={"branch": True},
        )

        if branch.is_persistent:
            self.create_session(branch)

        return branch

    def close_session(self, session_id: str) -> None:
        now = utc_now()

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE chat_sessions
                SET closed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now.isoformat(),
                    now.isoformat(),
                    session_id,
                ),
            )

            if cursor.rowcount == 0:
                raise SessionNotFoundError(
                    f"No existe la sesión: {session_id}"
                )

    def save_summary(
        self,
        summary: "ConversationSummary",
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_summaries (
                    session_id,
                    content,
                    through_message_id,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    summary.session_id,
                    summary.content,
                    summary.through_message_id,
                    summary.created_at.isoformat(),
                ),
            )

    def load_latest_summary(
        self,
        session_id: str,
    ) -> "ConversationSummary | None":
        from .history_summarizer import ConversationSummary

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM conversation_summaries
                WHERE session_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return ConversationSummary(
            session_id=row["session_id"],
            content=row["content"],
            through_message_id=row["through_message_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _assert_message_belongs_to_session(
        self,
        session_id: str,
        message_id: str,
    ) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM messages
                WHERE id = ?
                  AND session_id = ?
                """,
                (message_id, session_id),
            ).fetchone()

        if row is None:
            raise InvalidBranchError(
                f"El mensaje {message_id} no pertenece "
                f"a la sesión {session_id}."
            )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> ChatSession:
        return ChatSession(
            id=row["id"],
            profile_name=row["profile_name"],
            title=row["title"],
            mode=row["mode"],
            memory_policy=row["memory_policy"],
            parent_session_id=row["parent_session_id"],
            parent_message_id=row["parent_message_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            closed_at=(
                datetime.fromisoformat(row["closed_at"])
                if row["closed_at"]
                else None
            ),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            role=row["role"],
            content=row["content"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            metadata=json.loads(row["metadata_json"]),
        )
