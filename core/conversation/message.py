from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


MessageRole = Literal["user", "assistant", "system", "tool"]

VALID_MESSAGE_ROLES: set[str] = {
    "user",
    "assistant",
    "system",
    "tool",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Message:
    role: MessageRole
    content: str
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in VALID_MESSAGE_ROLES:
            raise ValueError(f"Rol de mensaje inválido: {self.role}")

        if not self.content.strip():
            raise ValueError("El contenido del mensaje no puede estar vacío.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            id=str(data["id"]),
            role=data["role"],
            content=str(data["content"]),
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            metadata=dict(data.get("metadata") or {}),
        )
