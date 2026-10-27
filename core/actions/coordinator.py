from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from core.activity import (
    ActivitySink, ActivityState, NULL_ACTIVITY, best_effort_activity,
)
from core.audit import AuditEventType, AuditService

from .models import (
    ActionIntent,
    ActionOutcome,
    EffectCapability,
    EffectKind,
    EffectResult,
)
from .policy import ActionDecision, ActionPolicy


@dataclass(frozen=True, slots=True)
class _PendingAction:
    intent: ActionIntent
    capability: EffectCapability


class ActionCoordinator:
    """Única vía normal para invocar una capability con efectos."""

    def __init__(
        self,
        *,
        audit: AuditService,
        policy: ActionPolicy | None = None,
        capabilities: Mapping[str, EffectCapability] | None = None,
        activity: ActivitySink = NULL_ACTIVITY,
    ) -> None:
        self.audit = audit
        self.policy = policy or ActionPolicy()
        self._capabilities = dict(capabilities or {})
        self._pending: dict[str, _PendingAction] = {}
        self._lock = threading.RLock()
        self.activity = best_effort_activity(activity)

    @staticmethod
    def _session_key(intent: ActionIntent) -> str:
        return intent.session_id or f"intent:{intent.id}"

    def pending_intent(self, session_id: str) -> ActionIntent | None:
        pending = self._pending.get(session_id)
        return pending.intent if pending else None

    def propose(self, intent: ActionIntent) -> ActionOutcome:
        if not intent.has_valid_fingerprint():
            raise ValueError("El fingerprint del intent no coincide con su contenido.")
        if intent.effect_kind is EffectKind.READ_ONLY:
            raise ValueError("Las operaciones read-only conservan su política existente.")
        capability = self._capabilities.get(intent.capability)
        if capability is None:
            raise ValueError(f"Capability effectful no registrada: {intent.capability}")
        validator = getattr(capability, "validate", None)
        if validator is not None:
            validator(intent)

        key = self._session_key(intent)
        with self._lock:
            previous = self._pending.get(key)
            if previous is not None:
                self.audit.append(
                    intent_id=previous.intent.id,
                    event_type=AuditEventType.ACTION_CANCELLED,
                    actor="republic",
                    fingerprint=previous.intent.fingerprint,
                    payload={"reason": "replaced_by_new_session_intent"},
                )
                del self._pending[key]

            # Invariante crítica: nada puede avanzar si este append falla.
            self.audit.append(
                intent_id=intent.id,
                event_type=AuditEventType.ACTION_PROPOSED,
                actor=intent.requested_by,
                fingerprint=intent.fingerprint,
                payload=intent.audit_snapshot(),
            )
            try:
                decision = self.policy.decide(intent)
            except Exception as error:
                self.audit.append(
                    intent_id=intent.id,
                    event_type=AuditEventType.ACTION_DENIED,
                    actor="republic",
                    fingerprint=intent.fingerprint,
                    payload={"reason": "policy_failure", "error": str(error)},
                )
                self.activity.set_state(ActivityState.ERROR)
                return ActionOutcome("denied", intent, reason="policy_failure")

            if decision is ActionDecision.DENIED:
                self.audit.append(
                    intent_id=intent.id,
                    event_type=AuditEventType.ACTION_DENIED,
                    actor="republic",
                    fingerprint=intent.fingerprint,
                    payload={"reason": "policy_denied"},
                )
                self.activity.set_state(ActivityState.IDLE)
                return ActionOutcome("denied", intent, reason="policy_denied")
            if decision is not ActionDecision.APPROVAL_REQUIRED:
                raise ValueError("El coordinator effectful exige aprobación humana.")

            self._pending[key] = _PendingAction(intent, capability)
            self.activity.set_state(ActivityState.WAITING_APPROVAL)
            return ActionOutcome("proposed", intent)

    def deny(self, intent: ActionIntent, *, denied_by: str = "user") -> ActionOutcome:
        if denied_by != "user":
            return ActionOutcome("invalid_authority", intent, reason="human_authority_required")
        key = self._session_key(intent)
        with self._lock:
            pending = self._matching_pending(key, intent)
            if pending is None:
                return ActionOutcome("not_pending", intent, reason="intent_not_pending")
            current_state = self.audit.state(intent.id)
            event_type = (
                AuditEventType.ACTION_DENIED
                if current_state == "proposed"
                else AuditEventType.ACTION_CANCELLED
            )
            self.audit.append(
                intent_id=intent.id,
                event_type=event_type,
                actor=denied_by,
                fingerprint=intent.fingerprint,
                payload={
                    "reason": (
                        "human_denied"
                        if event_type is AuditEventType.ACTION_DENIED
                        else "human_cancelled_before_execution"
                    )
                },
            )
            del self._pending[key]
            self.activity.set_state(ActivityState.IDLE)
            return ActionOutcome(
                "denied" if event_type is AuditEventType.ACTION_DENIED else "cancelled",
                intent,
                reason=(
                    "human_denied"
                    if event_type is AuditEventType.ACTION_DENIED
                    else "human_cancelled_before_execution"
                ),
            )

    def approve(self, intent: ActionIntent, *, approved_by: str = "user") -> ActionOutcome:
        if approved_by != "user":
            return ActionOutcome("invalid_authority", intent, reason="human_authority_required")
        key = self._session_key(intent)
        with self._lock:
            pending = self._matching_pending(key, intent)
            if pending is None:
                return ActionOutcome("not_pending", intent, reason="intent_not_pending_or_changed")

            try:
                current_state = self.audit.state(intent.id)
                if current_state == "proposed":
                    self.audit.append(
                        intent_id=intent.id,
                        event_type=AuditEventType.ACTION_APPROVED,
                        actor=approved_by,
                        fingerprint=intent.fingerprint,
                        payload={"approved_by": approved_by},
                    )
                self.audit.append(
                    intent_id=intent.id,
                    event_type=AuditEventType.EXECUTION_STARTED,
                    actor="republic",
                    fingerprint=intent.fingerprint,
                )
            except Exception as error:
                self.activity.set_state(ActivityState.ERROR)
                return ActionOutcome(
                    "audit_failed", intent, reason=str(error), critical_audit_failure=True
                )

            # La aprobación queda consumida sólo cuando el inicio durable impide replay.
            del self._pending[key]
            self.activity.set_state(ActivityState.ACTING)
            try:
                result = pending.capability.execute(intent)
                if not isinstance(result, EffectResult):
                    raise TypeError("La capability no devolvió EffectResult.")
            except Exception as error:
                result = EffectResult(success=False, error=str(error))

            event_type = (
                AuditEventType.EXECUTION_SUCCEEDED
                if result.success
                else AuditEventType.EXECUTION_FAILED
            )
            payload = {
                "success": result.success,
                "evidence": [dict(item) for item in result.evidence],
                "error": result.error,
                "reversible": result.reversible,
                "reversal_reference": result.reversal_reference,
            }
            try:
                self.audit.append(
                    intent_id=intent.id,
                    event_type=event_type,
                    actor="republic",
                    fingerprint=intent.fingerprint,
                    payload=payload,
                )
            except Exception as error:
                self.activity.set_state(ActivityState.ERROR)
                return ActionOutcome(
                    "audit_failed_after_effect",
                    intent,
                    effect_result=result,
                    reason=str(error),
                    critical_audit_failure=True,
                    effect_may_have_occurred=True,
                )
            self.activity.set_state(
                ActivityState.IDLE if result.success else ActivityState.ERROR
            )
            return ActionOutcome("succeeded" if result.success else "failed", intent, result)

    def handle_human_response(self, session_id: str, response: str) -> ActionOutcome | None:
        pending = self._pending.get(session_id)
        if pending is None:
            return None
        decision = _confirmation_decision(response)
        if decision == "approve":
            return self.approve(pending.intent)
        if decision == "deny":
            return self.deny(pending.intent)
        self.activity.set_state(ActivityState.WAITING_APPROVAL)
        return ActionOutcome("ambiguous", pending.intent, reason="explicit_yes_or_no_required")

    def _matching_pending(self, key: str, intent: ActionIntent) -> _PendingAction | None:
        pending = self._pending.get(key)
        if pending is None or not intent.has_valid_fingerprint():
            return None
        if pending.intent.id != intent.id or pending.intent.fingerprint != intent.fingerprint:
            return None
        if self.audit.state(intent.id) not in {"proposed", "approved"}:
            return None
        return pending


def _confirmation_decision(response: str) -> str | None:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", response.casefold())
        if not unicodedata.combining(character)
    ).strip(" .!¡?¿")
    if normalized in {"si", "yes", "aprobar", "apruebo", "confirmo"}:
        return "approve"
    if normalized in {"no", "rechazar", "rechazo", "cancelar", "cancelo"}:
        return "deny"
    return None
