from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4


MemoryCategory = Literal["preference", "personal_fact", "shared_context"]
MemoryStatus = Literal["active", "retired"]
MemoryClarity = Literal["clear", "ambiguous"]
MemorySensitivity = Literal["low", "sensitive", "secret"]
MemoryRelation = Literal["corrects", "replaces", "contextualizes"]
MemoryOperation = Literal["remember", "retire"]

VALID_CATEGORIES = {"preference", "personal_fact", "shared_context"}
VALID_STATUSES = {"active", "retired"}
VALID_CLARITIES = {"clear", "ambiguous"}
VALID_SENSITIVITIES = {"low", "sensitive", "secret"}
VALID_RELATIONS = {"corrects", "replaces", "contextualizes"}
VALID_OPERATIONS = {"remember", "retire"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    category: MemoryCategory
    content: str
    clarity: MemoryClarity
    sensitivity: MemorySensitivity
    source_type: str = "conversation"
    source_session_id: str | None = None
    source_message_id: str | None = None
    related_memory_id: str | None = None
    relation: MemoryRelation | None = None
    operation: MemoryOperation = "remember"

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"Categoría de memoria inválida: {self.category}")
        if not self.content.strip():
            raise ValueError("El candidato de memoria no puede estar vacío.")
        if self.clarity not in VALID_CLARITIES:
            raise ValueError(f"Claridad inválida: {self.clarity}")
        if self.sensitivity not in VALID_SENSITIVITIES:
            raise ValueError(f"Sensibilidad inválida: {self.sensitivity}")
        if self.relation is not None and self.relation not in VALID_RELATIONS:
            raise ValueError(f"Relación de memoria inválida: {self.relation}")
        if self.operation not in VALID_OPERATIONS:
            raise ValueError(f"Operación de memoria inválida: {self.operation}")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    profile_name: str
    category: MemoryCategory
    content: str
    source_type: str
    id: str = field(default_factory=lambda: str(uuid4()))
    status: MemoryStatus = "active"
    source_session_id: str | None = None
    source_message_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    related_memory_id: str | None = None
    relation: MemoryRelation | None = None

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES or self.status not in VALID_STATUSES:
            raise ValueError("Registro de memoria inválido.")
        if not self.profile_name.strip() or not self.content.strip() or not self.source_type.strip():
            raise ValueError("Perfil, contenido y procedencia son obligatorios.")
