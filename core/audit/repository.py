from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Protocol

from core.database import SQLiteDatabase, get_audit_database

from .models import AuditEvent, AuditEventType


class AuditConflictError(RuntimeError):
    """El evento duplicaría un paso que ya fue registrado."""


class AuditRepository(Protocol):
    def append_event(self, event: AuditEvent) -> AuditEvent: ...
    def list_events(self, intent_id: str) -> list[AuditEvent]: ...


class SQLiteAuditRepository:
    def __init__(self, database: SQLiteDatabase | None = None) -> None:
        self.database = database or get_audit_database()

    def append_event(self, event: AuditEvent) -> AuditEvent:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO action_audit_events (
                        id, intent_id, event_type, actor, fingerprint,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.intent_id,
                        event.event_type.value,
                        event.actor,
                        event.fingerprint,
                        json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                        event.created_at.isoformat(),
                    ),
                )
                sequence = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise AuditConflictError(str(error)) from error
        return AuditEvent(
            id=event.id,
            intent_id=event.intent_id,
            event_type=event.event_type,
            actor=event.actor,
            fingerprint=event.fingerprint,
            payload=event.payload,
            created_at=event.created_at,
            sequence=sequence,
        )

    def list_events(self, intent_id: str) -> list[AuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, id, intent_id, event_type, actor, fingerprint,
                       payload_json, created_at
                FROM action_audit_events
                WHERE intent_id = ?
                ORDER BY sequence ASC
                """,
                (intent_id,),
            ).fetchall()
        return [
            AuditEvent(
                sequence=int(row["sequence"]),
                id=row["id"],
                intent_id=row["intent_id"],
                event_type=AuditEventType(row["event_type"]),
                actor=row["actor"],
                fingerprint=row["fingerprint"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
