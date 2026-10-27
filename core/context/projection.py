from dataclasses import dataclass

from core.conversation import ConversationSummary, Message


@dataclass(frozen=True, slots=True)
class ContextItem:
    content: str
    source: str

    def __post_init__(self) -> None:
        if not self.content.strip() or not self.source.strip():
            raise ValueError("El contexto requiere contenido y procedencia no vacíos.")


@dataclass(frozen=True, slots=True)
class ConversationProjection:
    current_message: ContextItem
    messages: tuple[Message, ...] = ()
    summary: ConversationSummary | None = None


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """Selección efímera de un turno; no es almacenamiento ni memoria."""

    invariants: ContextItem
    identity: ContextItem
    instructions: ContextItem
    memory: tuple[ContextItem, ...]
    conversation: ConversationProjection
    task: tuple[ContextItem, ...] = ()
