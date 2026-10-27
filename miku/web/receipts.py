"""Durable HTTP delivery receipts; no queue, retries or conversational schema."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


class StorageInUseError(RuntimeError):
    pass


class ProcessFileLock:
    """OS-owned lock, released on process death. Never unlink the lock file."""

    def __init__(self, path: Path) -> None:
        self._file = None
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(descriptor, "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise StorageInUseError("El almacenamiento ya está abierto por otra web.") from error
        self._file = stream

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


class ReceiptStore:
    """The caller holds both the conversation and receipt process locks."""

    def __init__(self, path: Path, conversation_path: Path) -> None:
        self.path = path
        binding = os.path.normcase(str(conversation_path.resolve()))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version == 0 and not tables:
                connection.execute("CREATE TABLE storage (conversation_path TEXT NOT NULL)")
                connection.execute("INSERT INTO storage VALUES (?)", (binding,))
                connection.execute("""
                    CREATE TABLE receipts (
                        request_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (state IN
                            ('running', 'completed', 'failed', 'interrupted')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        user_message_id TEXT,
                        response_message_id TEXT,
                        result_status TEXT,
                        error_type TEXT
                    )
                """)
                connection.execute("PRAGMA user_version = 1")
            elif version != 1:
                raise ValueError("El esquema de recibos web no es compatible.")
            stored = connection.execute("SELECT conversation_path FROM storage").fetchall()
            if len(stored) != 1 or stored[0][0] != binding:
                raise ValueError("Los recibos pertenecen a otro almacenamiento conversacional.")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, request_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM receipts WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def begin(self, request_id: str, session_id: str, payload_hash: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO receipts
                   (request_id, session_id, payload_hash, state, created_at, updated_at)
                   VALUES (?, ?, ?, 'running', ?, ?)""",
                (request_id, session_id, payload_hash, now, now),
            )

    def finish(
        self, request_id: str, state: str, *, user_message_id: str | None = None,
        response_message_id: str | None = None, result_status: str | None = None,
        error_type: str | None = None,
    ) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE receipts SET state = ?, updated_at = ?, user_message_id = ?,
                   response_message_id = ?, result_status = ?, error_type = ?
                   WHERE request_id = ? AND state = 'running'""",
                (state, datetime.now(timezone.utc).isoformat(), user_message_id,
                 response_message_id, result_status, error_type, request_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("El recibo ya no está en ejecución.")

    def interrupt_running(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE receipts SET state = 'interrupted', updated_at = ?,
                   error_type = 'server_interrupted', result_status = 'error'
                   WHERE state = 'running'""",
                (datetime.now(timezone.utc).isoformat(),),
            )
