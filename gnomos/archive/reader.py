from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .models import ArchiveLink, ArchiveNote


class ArchiveReader(Protocol):
    @property
    def source(self) -> str: ...

    def search_notes(self, query: str, limit: int | None = None) -> list[ArchiveNote]: ...
    def read_note(self, note_id: int) -> ArchiveNote | None: ...
    def list_links(self, note_id: int, limit: int | None = None) -> list[ArchiveLink] | None: ...


class SQLiteArchiveReader:
    """Consultas compartidas de Archive mediante conexiones SQLite read-only."""

    def __init__(
        self,
        path: str | Path,
        *,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
    ) -> None:
        self.path = Path(path).resolve()
        self._connection_factory = connection_factory

    @classmethod
    def from_connection_factory(
        cls,
        factory: Callable[[], sqlite3.Connection],
        *,
        source: str | Path,
    ) -> "SQLiteArchiveReader":
        return cls(source, connection_factory=factory)

    @property
    def source(self) -> str:
        return self.path.as_posix()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = (
            self._connection_factory()
            if self._connection_factory is not None
            else sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def search_notes(self, query: str, limit: int | None = None) -> list[ArchiveNote]:
        sql = """
            SELECT id, title, content, tags, created_at
            FROM notes
            WHERE deleted_at IS NULL
              AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)
            ORDER BY created_at DESC, id DESC
        """
        parameters: list[object] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._note(row) for row in rows]

    def read_note(self, note_id: int) -> ArchiveNote | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, content, tags, created_at
                FROM notes WHERE id = ? AND deleted_at IS NULL
                """,
                (note_id,),
            ).fetchone()
        return self._note(row) if row is not None else None

    def list_links(self, note_id: int, limit: int | None = None) -> list[ArchiveLink] | None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)
            ).fetchone()
            if exists is None:
                return None
            sql = """
                SELECT linked_note_id, title, tags, reason, linked_at, direction
                FROM (
                    SELECT n.id AS linked_note_id, n.title, n.tags, nl.reason,
                           nl.created_at AS linked_at, 'outgoing' AS direction
                    FROM note_links nl JOIN notes n ON n.id = nl.to_note_id
                    WHERE nl.from_note_id = ? AND n.deleted_at IS NULL
                    UNION ALL
                    SELECT n.id AS linked_note_id, n.title, n.tags, nl.reason,
                           nl.created_at AS linked_at, 'incoming' AS direction
                    FROM note_links nl JOIN notes n ON n.id = nl.from_note_id
                    WHERE nl.to_note_id = ? AND n.deleted_at IS NULL
                )
                ORDER BY linked_at DESC, linked_note_id DESC
            """
            parameters: list[object] = [note_id, note_id]
            if limit is not None:
                sql += " LIMIT ?"
                parameters.append(limit)
            rows = connection.execute(sql, parameters).fetchall()
        return [
            ArchiveLink(
                linked_note_id=row["linked_note_id"], title=row["title"],
                tags=row["tags"], reason=row["reason"], linked_at=row["linked_at"],
                direction=row["direction"],
            )
            for row in rows
        ]

    @staticmethod
    def _note(row: sqlite3.Row) -> ArchiveNote:
        return ArchiveNote(
            note_id=row["id"], title=row["title"], content=row["content"],
            tags=row["tags"], created_at=row["created_at"],
        )
