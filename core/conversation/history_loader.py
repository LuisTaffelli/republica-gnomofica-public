from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .message import Message
from .repository import ConversationRepository


DEFAULT_ROLE_LABELS: dict[str, str] = {
    "user": "Usuario",
    "assistant": "Asistente",
    "system": "Sistema",
    "tool": "Herramienta",
}


@dataclass(slots=True)
class HistoryContext:
    summary: str
    messages: list[Message]


class HistoryFormatter:
    def __init__(
        self,
        role_labels: Mapping[str, str] | None = None,
    ) -> None:
        self.role_labels = dict(
            role_labels or DEFAULT_ROLE_LABELS
        )

    def format_messages(
        self,
        messages: Sequence[Message],
    ) -> str:
        lines: list[str] = []

        for message in messages:
            speaker = self.role_labels.get(
                message.role,
                message.role.upper(),
            )
            lines.append(
                f"{speaker}: {message.content.strip()}"
            )

        return "\n\n".join(lines)

    def format_context(self, context: HistoryContext) -> str:
        parts: list[str] = []

        if context.summary.strip():
            parts.append(
                "RESUMEN ACUMULADO:\n"
                + context.summary.strip()
            )

        if context.messages:
            parts.append(
                "MENSAJES RECIENTES:\n"
                + self.format_messages(context.messages)
            )

        return "\n\n".join(parts)


class HistoryLoader:
    def __init__(
        self,
        repository: ConversationRepository,
        formatter: HistoryFormatter | None = None,
    ) -> None:
        self.repository = repository
        self.formatter = formatter or HistoryFormatter()

    def load(
        self,
        session_id: str,
        recent_limit: int = 20,
    ) -> HistoryContext:
        summary = self.repository.load_latest_summary(
            session_id
        )

        all_context_messages = (
            self.repository.load_context_messages(
                session_id
            )
        )

        messages = (
            all_context_messages[-recent_limit:]
            if recent_limit > 0
            else []
        )

        return HistoryContext(
            summary=summary.content if summary else "",
            messages=messages,
        )

    def build(
        self,
        session_id: str,
        recent_limit: int = 20,
    ) -> str:
        return self.formatter.format_context(
            self.load(
                session_id=session_id,
                recent_limit=recent_limit,
            )
        )
