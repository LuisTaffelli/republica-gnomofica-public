from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

from core.database import AUDIT_DB_PATH

from .models import AuditEventType


class AuditQueryError(RuntimeError):
    pass


class AuditIdentifierAmbiguous(AuditQueryError):
    pass


@dataclass(frozen=True, slots=True)
class AuditEventView:
    sequence: int
    id: str
    intent_id: str
    event_type: str
    actor: str
    fingerprint: str
    payload: Mapping[str, Any]
    created_at: datetime
    payload_valid: bool = True


@dataclass(frozen=True, slots=True)
class AuditIntentSummary:
    intent_id: str
    fingerprint: str
    capability: str | None
    target: str | None
    created_at: datetime
    updated_at: datetime
    state: str
    phase: str | None
    interrupted: bool


@dataclass(frozen=True, slots=True)
class AuditIntentDetail:
    summary: AuditIntentSummary
    events: tuple[AuditEventView, ...]
    evidence: tuple[Mapping[str, Any], ...]
    error: str | None
    explanation: str | None


class AuditReadRepository(Protocol):
    def list_recent_intent_ids(self, limit: int) -> list[str]: ...
    def resolve_intent_id(self, identifier: str) -> str | None: ...
    def list_event_views(self, intent_id: str) -> list[AuditEventView]: ...


class SQLiteAuditReader:
    """Reader SQLite estricto: no crea, migra ni escribe la base de Audit."""

    def __init__(self, path: str | Path = AUDIT_DB_PATH) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.is_file():
            raise AuditQueryError(f"No existe la base de auditoría: {self.path}")
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as error:
            raise AuditQueryError(f"No se pudo abrir Audit en modo read-only: {error}") from error
        try:
            yield connection
        except sqlite3.Error as error:
            raise AuditQueryError(f"Falló la consulta de Audit: {error}") from error
        finally:
            connection.close()

    def list_recent_intent_ids(self, limit: int) -> list[str]:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Audit admite entre 1 y 100 intents recientes.")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT intent_id, MAX(sequence) AS last_sequence
                FROM action_audit_events
                GROUP BY intent_id
                ORDER BY last_sequence DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["intent_id"]) for row in rows]

    def resolve_intent_id(self, identifier: str) -> str | None:
        value = identifier.strip()
        if not value:
            raise ValueError("El identificador de Audit no puede estar vacío.")
        with self._connect() as connection:
            exact = connection.execute(
                "SELECT 1 FROM action_audit_events WHERE intent_id = ? LIMIT 1",
                (value,),
            ).fetchone()
            if exact is not None:
                return value
            rows = connection.execute(
                """
                SELECT intent_id, MIN(sequence) AS first_sequence
                FROM action_audit_events
                WHERE fingerprint = ?
                GROUP BY intent_id
                ORDER BY first_sequence DESC
                LIMIT 2
                """,
                (value,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise AuditIdentifierAmbiguous(
                "El fingerprint corresponde a más de un intent; usá el intent_id exacto."
            )
        return str(rows[0]["intent_id"])

    def list_event_views(self, intent_id: str) -> list[AuditEventView]:
        with self._connect() as connection:
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
        events: list[AuditEventView] = []
        for row in rows:
            payload_valid = True
            try:
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict):
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
                payload_valid = False
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError):
                created_at = datetime.min
                payload_valid = False
            events.append(
                AuditEventView(
                    sequence=int(row["sequence"]),
                    id=str(row["id"]),
                    intent_id=str(row["intent_id"]),
                    event_type=str(row["event_type"]),
                    actor=str(row["actor"]),
                    fingerprint=str(row["fingerprint"]),
                    payload=payload,
                    created_at=created_at,
                    payload_valid=payload_valid,
                )
            )
        return events


_ALLOWED_QUERY_TRANSITIONS: dict[str | None, set[str]] = {
    None: {AuditEventType.ACTION_PROPOSED.value},
    "proposed": {
        AuditEventType.ACTION_APPROVED.value,
        AuditEventType.ACTION_DENIED.value,
        AuditEventType.ACTION_CANCELLED.value,
    },
    "approved": {
        AuditEventType.EXECUTION_STARTED.value,
        AuditEventType.ACTION_CANCELLED.value,
    },
    "executing": {
        AuditEventType.EXECUTION_SUCCEEDED.value,
        AuditEventType.EXECUTION_FAILED.value,
    },
    "denied": set(),
    "cancelled": set(),
    "succeeded": set(),
    "failed": set(),
}

_EVENT_PHASES = {
    AuditEventType.ACTION_PROPOSED.value: "proposed",
    AuditEventType.ACTION_APPROVED.value: "approved",
    AuditEventType.ACTION_DENIED.value: "denied",
    AuditEventType.ACTION_CANCELLED.value: "cancelled",
    AuditEventType.EXECUTION_STARTED.value: "executing",
    AuditEventType.EXECUTION_SUCCEEDED.value: "succeeded",
    AuditEventType.EXECUTION_FAILED.value: "failed",
}


class AuditQueryService:
    """Fachada diagnóstica. No expone authority, pending, retry ni mutaciones."""

    def __init__(self, repository: AuditReadRepository) -> None:
        self.repository = repository

    def list_recent(self, *, limit: int = 20) -> list[AuditIntentSummary]:
        summaries = []
        for intent_id in self.repository.list_recent_intent_ids(limit):
            detail = self._from_events(
                intent_id, self.repository.list_event_views(intent_id)
            )
            summaries.append(detail.summary)
        return summaries

    def get(self, identifier: str) -> AuditIntentDetail | None:
        intent_id = self.repository.resolve_intent_id(identifier)
        if intent_id is None:
            return None
        return self._from_events(
            intent_id, self.repository.list_event_views(intent_id)
        )

    @staticmethod
    def _from_events(
        intent_id: str, events: list[AuditEventView]
    ) -> AuditIntentDetail:
        if not events:
            raise AuditQueryError(f"El intent no tiene eventos: {intent_id}")
        phase: str | None = None
        inconsistent_reason: str | None = None
        fingerprint = events[0].fingerprint
        for event in events:
            if not event.payload_valid:
                inconsistent_reason = "Evento con payload o timestamp inválido."
                break
            if event.fingerprint != fingerprint:
                inconsistent_reason = "Los eventos no conservan un único fingerprint."
                break
            allowed = _ALLOWED_QUERY_TRANSITIONS.get(phase, set())
            if event.event_type not in allowed:
                inconsistent_reason = (
                    f"Secuencia inválida: {phase or 'inicio'} -> {event.event_type}."
                )
                break
            phase = _EVENT_PHASES[event.event_type]

        interrupted = inconsistent_reason is None and phase == "executing"
        if inconsistent_reason is not None:
            state = "inconsistent"
            explanation = inconsistent_reason
        elif interrupted:
            state = "execution_unknown"
            explanation = (
                "La ejecución comenzó, pero no existe evidencia durable de resultado final. "
                "No es seguro asumir éxito ni reintentar."
            )
        else:
            state = phase or "inconsistent"
            explanation = None

        proposal = next(
            (event for event in events if event.event_type == AuditEventType.ACTION_PROPOSED.value),
            None,
        )
        proposal_payload = proposal.payload if proposal is not None else {}
        terminal = events[-1]
        raw_evidence = terminal.payload.get("evidence", [])
        evidence = tuple(
            dict(item) for item in raw_evidence
            if isinstance(item, Mapping)
        ) if isinstance(raw_evidence, list) else ()
        error = terminal.payload.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)
        summary = AuditIntentSummary(
            intent_id=intent_id,
            fingerprint=fingerprint,
            capability=(
                str(proposal_payload["capability"])
                if "capability" in proposal_payload else None
            ),
            target=(
                str(proposal_payload["target"])
                if "target" in proposal_payload else None
            ),
            created_at=events[0].created_at,
            updated_at=events[-1].created_at,
            state=state,
            phase=phase,
            interrupted=interrupted,
        )
        return AuditIntentDetail(summary, tuple(events), evidence, error, explanation)
