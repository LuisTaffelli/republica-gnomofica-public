from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence

from .message import Message, utc_now
from .repository import ConversationRepository


class TextGenerator(Protocol):
    def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    session_id: str
    content: str
    through_message_id: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SummaryPolicy:
    trigger_messages: int = 40
    keep_recent_messages: int = 16

    def __post_init__(self) -> None:
        if self.trigger_messages < 2:
            raise ValueError(
                "trigger_messages debe ser al menos 2."
            )

        if self.keep_recent_messages < 1:
            raise ValueError(
                "keep_recent_messages debe ser al menos 1."
            )

        if self.keep_recent_messages >= self.trigger_messages:
            raise ValueError(
                "keep_recent_messages debe ser menor "
                "que trigger_messages."
            )


class HistorySummarizer:
    def __init__(
        self,
        repository: ConversationRepository,
        generator: TextGenerator,
        policy: SummaryPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.generator = generator
        self.policy = policy or SummaryPolicy()

    def should_summarize(self, session_id: str) -> bool:
        session = self.repository.load_session(session_id)

        if session.mode != "persistent":
            return False

        latest = self.repository.load_latest_summary(
            session_id
        )
        messages = self.repository.load_messages_after(
            session_id,
            latest.through_message_id if latest else None,
        )

        return (
            len(messages)
            >= self.policy.trigger_messages
        )

    def summarize_if_needed(
        self,
        session_id: str,
    ) -> ConversationSummary | None:
        session = self.repository.load_session(session_id)

        if session.mode != "persistent":
            return None

        latest = self.repository.load_latest_summary(
            session_id
        )
        pending = self.repository.load_messages_after(
            session_id,
            latest.through_message_id if latest else None,
        )

        if len(pending) < self.policy.trigger_messages:
            return None

        messages_to_summarize = pending[
            : -self.policy.keep_recent_messages
        ]
        if not messages_to_summarize:
            return None

        prompt = self._build_prompt(
            previous_summary=(
                latest.content if latest else ""
            ),
            messages=messages_to_summarize,
        )

        content = self.generator.ask(prompt).strip()
        if not content:
            raise RuntimeError(
                "El proveedor devolvió un resumen vacío."
            )

        summary = ConversationSummary(
            session_id=session_id,
            content=content,
            through_message_id=(
                messages_to_summarize[-1].id
            ),
        )
        self.repository.save_summary(summary)
        return summary

    @staticmethod
    def _build_prompt(
        previous_summary: str,
        messages: Sequence[Message],
    ) -> str:
        transcript = "\n\n".join(
            f"[{message.role}] {message.content.strip()}"
            for message in messages
        )

        previous = (
            previous_summary.strip()
            or "(No existe un resumen anterior.)"
        )

        return f"""
Actualizá el resumen operativo de una conversación.

El resumen debe preservar únicamente información útil para conversaciones futuras:
- hechos expresados por el usuario;
- preferencias estables;
- proyectos, nombres y conceptos propios;
- decisiones tomadas y sus motivos;
- correcciones hechas por el usuario;
- preguntas abiertas y tareas pendientes;
- incertidumbres, distinguiéndolas claramente de los hechos.

No inventes información.
No conviertas inferencias del asistente en hechos del usuario.
No redactes una narración literaria.
Usá secciones breves y concretas.

RESUMEN ANTERIOR:
{previous}

NUEVOS MENSAJES A INCORPORAR:
{transcript}

RESUMEN ACTUALIZADO:
""".strip()
