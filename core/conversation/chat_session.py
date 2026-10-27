from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal
from uuid import uuid4

from .message import Message, MessageRole, utc_now


SessionMode = Literal["persistent", "temporary", "action"]
MemoryPolicy = Literal["enabled", "review_only", "disabled"]

VALID_SESSION_MODES: set[str] = {
    "persistent",
    "temporary",
    "action",
}

VALID_MEMORY_POLICIES: set[str] = {
    "enabled",
    "review_only",
    "disabled",
}


@dataclass(slots=True)
class ChatSession:
    profile_name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str | None = None

    mode: SessionMode = "persistent"
    memory_policy: MemoryPolicy = "enabled"

    parent_session_id: str | None = None
    parent_message_id: str | None = None

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    closed_at: datetime | None = None

    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_name.strip():
            raise ValueError("profile_name no puede estar vacío.")

        if self.mode not in VALID_SESSION_MODES:
            raise ValueError(f"Modo de sesión inválido: {self.mode}")

        if self.memory_policy not in VALID_MEMORY_POLICIES:
            raise ValueError(
                f"Política de memoria inválida: {self.memory_policy}"
            )

        if (
            self.parent_message_id is not None
            and self.parent_session_id is None
        ):
            raise ValueError(
                "parent_message_id requiere parent_session_id."
            )

        if self.mode in {"temporary", "action"}:
            self.memory_policy = "disabled"

    @property
    def is_persistent(self) -> bool:
        return self.mode != "temporary"

    @property
    def allows_memory(self) -> bool:
        return (
            self.mode == "persistent"
            and self.memory_policy != "disabled"
        )

    @property
    def is_branch(self) -> bool:
        return self.parent_session_id is not None

    def add(
        self,
        role: MessageRole,
        content: str,
        **metadata: Any,
    ) -> Message:
        message = Message(
            role=role,
            content=content,
            metadata=metadata,
        )
        self.messages.append(message)
        self.updated_at = message.timestamp
        return message

    def add_user(self, content: str, **metadata: Any) -> Message:
        return self.add("user", content, **metadata)

    def add_assistant(self, content: str, **metadata: Any) -> Message:
        return self.add("assistant", content, **metadata)

    def add_system(self, content: str, **metadata: Any) -> Message:
        return self.add("system", content, **metadata)

    def add_tool(self, content: str, **metadata: Any) -> Message:
        return self.add("tool", content, **metadata)

    def extend(self, messages: Iterable[Message]) -> None:
        new_messages = list(messages)
        if not new_messages:
            return

        self.messages.extend(new_messages)
        self.updated_at = max(
            message.timestamp for message in new_messages
        )

    def recent(self, limit: int = 10) -> list[Message]:
        if limit < 1:
            return []
        return self.messages[-limit:]

    def close(self) -> None:
        self.closed_at = utc_now()
        self.updated_at = self.closed_at
