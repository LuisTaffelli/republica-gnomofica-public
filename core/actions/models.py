from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EffectKind(str, Enum):
    READ_ONLY = "read_only"
    EFFECTFUL = "effectful"
    FORBIDDEN = "forbidden"


_COMMON_SENSITIVE_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "token",
    }
)


def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("Los parámetros de una acción deben ser JSON serializables.") from error
    if not isinstance(decoded, dict):
        raise ValueError("Los parámetros de una acción deben formar un objeto JSON.")
    return decoded


def sanitize_parameters(
    parameters: Mapping[str, Any], *, redacted_fields: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Crea la vista auditable sin alterar el payload de ejecución."""
    normalized = _json_object(parameters)
    sensitive = _COMMON_SENSITIVE_FIELDS | {item.casefold() for item in redacted_fields}

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if key.casefold() in sensitive else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return redact(normalized)


def compute_intent_fingerprint(
    *,
    capability: str,
    operation: str,
    parameters: Mapping[str, Any],
    effect_kind: EffectKind,
    target: str,
) -> str:
    material = {
        "capability": capability,
        "effect_kind": effect_kind.value,
        "operation": operation,
        "parameters": _json_object(parameters),
        "target": target,
    }
    canonical = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ActionIntent:
    capability: str
    operation: str
    parameters: Mapping[str, Any]
    effect_kind: EffectKind
    target: str
    requested_by: str
    profile_name: str
    session_id: str | None = None
    message_id: str | None = None
    redacted_fields: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utc_now)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "capability", "operation", "target", "requested_by", "profile_name", "id"
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} no puede estar vacío.")
        normalized = _json_object(self.parameters)
        if not all(isinstance(item, str) and item.strip() for item in self.redacted_fields):
            raise ValueError("redacted_fields sólo admite nombres de campo no vacíos.")
        object.__setattr__(self, "parameters", normalized)
        object.__setattr__(self, "redacted_fields", tuple(self.redacted_fields))
        object.__setattr__(
            self,
            "fingerprint",
            compute_intent_fingerprint(
                capability=self.capability,
                operation=self.operation,
                parameters=normalized,
                effect_kind=self.effect_kind,
                target=self.target,
            ),
        )

    def has_valid_fingerprint(self) -> bool:
        return self.fingerprint == compute_intent_fingerprint(
            capability=self.capability,
            operation=self.operation,
            parameters=self.parameters,
            effect_kind=self.effect_kind,
            target=self.target,
        )

    def audit_snapshot(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "operation": self.operation,
            "parameters": sanitize_parameters(
                self.parameters, redacted_fields=self.redacted_fields
            ),
            "effect_kind": self.effect_kind.value,
            "target": self.target,
            "requested_by": self.requested_by,
            "profile_name": self.profile_name,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EffectResult:
    success: bool
    evidence: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None
    reversible: bool = False
    reversal_reference: str | None = None


class EffectCapability(Protocol):
    def execute(self, intent: ActionIntent) -> EffectResult: ...


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    state: str
    intent: ActionIntent
    effect_result: EffectResult | None = None
    reason: str | None = None
    critical_audit_failure: bool = False
    effect_may_have_occurred: bool = False
