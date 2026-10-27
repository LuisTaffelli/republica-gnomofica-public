from __future__ import annotations

from typing import Any, Mapping

from .models import AuditEvent, AuditEventType
from .repository import AuditRepository


class InvalidAuditTransition(RuntimeError):
    pass


_STATES = {
    AuditEventType.ACTION_PROPOSED: "proposed",
    AuditEventType.ACTION_APPROVED: "approved",
    AuditEventType.ACTION_DENIED: "denied",
    AuditEventType.EXECUTION_STARTED: "executing",
    AuditEventType.EXECUTION_SUCCEEDED: "succeeded",
    AuditEventType.EXECUTION_FAILED: "failed",
    AuditEventType.ACTION_CANCELLED: "cancelled",
}

_ALLOWED = {
    None: {AuditEventType.ACTION_PROPOSED},
    "proposed": {
        AuditEventType.ACTION_APPROVED,
        AuditEventType.ACTION_DENIED,
        AuditEventType.ACTION_CANCELLED,
    },
    "approved": {
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.ACTION_CANCELLED,
    },
    "executing": {
        AuditEventType.EXECUTION_SUCCEEDED,
        AuditEventType.EXECUTION_FAILED,
    },
    "denied": set(),
    "cancelled": set(),
    "succeeded": set(),
    "failed": set(),
}


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def events(self, intent_id: str) -> list[AuditEvent]:
        return self.repository.list_events(intent_id)

    def state(self, intent_id: str) -> str | None:
        events = self.events(intent_id)
        return _STATES[events[-1].event_type] if events else None

    def append(
        self,
        *,
        intent_id: str,
        event_type: AuditEventType,
        actor: str,
        fingerprint: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        events = self.events(intent_id)
        state = _STATES[events[-1].event_type] if events else None
        if events and events[0].fingerprint != fingerprint:
            raise InvalidAuditTransition(
                f"El fingerprint no coincide con el intent propuesto {intent_id}."
            )
        if event_type not in _ALLOWED[state]:
            raise InvalidAuditTransition(
                f"Transición inválida para {intent_id}: {state} -> {event_type.value}."
            )
        return self.repository.append_event(
            AuditEvent(
                intent_id=intent_id,
                event_type=event_type,
                actor=actor,
                fingerprint=fingerprint,
                payload=dict(payload or {}),
            )
        )
