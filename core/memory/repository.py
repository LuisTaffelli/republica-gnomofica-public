from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.database import SQLiteDatabase, get_memory_database
from .models import MemoryRecord, MemoryStatus, utc_now


class MemoryRepository(Protocol):
    def save(self, memory: MemoryRecord) -> None: ...
    def get(self, memory_id: str) -> MemoryRecord | None: ...
    def list(self, profile_name: str, status: MemoryStatus | None = None) -> list[MemoryRecord]: ...
    def retire(self, memory_id: str, profile_name: str) -> bool: ...
    def save_and_retire(
        self, memory: MemoryRecord, retired_memory_id: str, profile_name: str
    ) -> bool: ...


class SQLiteMemoryRepository:
    def __init__(self, database: SQLiteDatabase | None = None) -> None:
        self.database = database or get_memory_database()

    def save(self, memory: MemoryRecord) -> None:
        with self.database.connect() as connection:
            self._insert(connection, memory)

    def save_and_retire(
        self, memory: MemoryRecord, retired_memory_id: str, profile_name: str
    ) -> bool:
        """Persiste una corrección y retira su antecedente en una transacción."""
        with self.database.connect() as connection:
            self._insert(connection, memory)
            cursor = connection.execute(
                "UPDATE personal_memories SET status = 'retired', updated_at = ? "
                "WHERE id = ? AND profile_name = ? AND status = 'active'",
                (utc_now().isoformat(), retired_memory_id, profile_name),
            )
            if cursor.rowcount != 1:
                raise ValueError("El recuerdo relacionado ya no está activo.")
        return True

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM personal_memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self, profile_name: str, status: MemoryStatus | None = None) -> list[MemoryRecord]:
        query = "SELECT * FROM personal_memories WHERE profile_name = ?"
        values: list[str] = [profile_name]
        if status is not None:
            query += " AND status = ?"
            values.append(status)
        query += " ORDER BY created_at DESC, id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._from_row(row) for row in rows]

    def retire(self, memory_id: str, profile_name: str) -> bool:
        now = utc_now().isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE personal_memories SET status = 'retired', updated_at = ? "
                "WHERE id = ? AND profile_name = ? AND status = 'active'",
                (now, memory_id, profile_name),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _insert(connection, memory: MemoryRecord) -> None:
        connection.execute(
            """
            INSERT INTO personal_memories (
                id, profile_name, category, content, status, source_type,
                source_session_id, source_message_id, created_at, updated_at,
                related_memory_id, relation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory.id, memory.profile_name, memory.category, memory.content,
             memory.status, memory.source_type, memory.source_session_id,
             memory.source_message_id, memory.created_at.isoformat(),
             memory.updated_at.isoformat(), memory.related_memory_id,
             memory.relation),
        )

    @staticmethod
    def _from_row(row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], profile_name=row["profile_name"], category=row["category"],
            content=row["content"], status=row["status"], source_type=row["source_type"],
            source_session_id=row["source_session_id"],
            source_message_id=row["source_message_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            related_memory_id=row["related_memory_id"], relation=row["relation"],
        )
