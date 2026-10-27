from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal

from core.activity import ActivitySink, NULL_ACTIVITY
from core.context.context_manager import ContextManager
from core.context.prompt_composer import PromptComposer
from core.context.projection import ContextProjection
from core.conversation import ChatSession, ConversationRepository, Message, MessageRole
from core.memory import (
    JsonMemoryExtractor,
    MemoryService,
    PersonalMemoryPolicy,
    SimpleMemoryRetriever,
    SQLiteMemoryRepository,
)
from gnomos.archive import ArchiveQueryRequest, ArchiveReader
from gnomos.laboral import InspectionRequest, InspectionScope
from providers import TextProvider

from .miku import Miku
from .read_contract import ReadTurnOutcome


TurnStatus = Literal["completed", "partial", "error", "scope_updated"]


@dataclass(frozen=True, slots=True)
class TurnRequest:
    text: str
    source: str = "cli"
    scope_extension: str | Path | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("El texto del turno no puede estar vacío.")
        if not self.source.strip():
            raise ValueError("La fuente del turno no puede estar vacía.")


@dataclass(frozen=True, slots=True)
class TurnResult:
    response_text: str
    status: TurnStatus
    session_id: str
    user_message_id: str | None = None
    response_message_id: str | None = None
    response_role: str = "assistant"
    secondary_diagnostic: str | None = None
    error_type: str | None = None
    pending_memory: bool = False
    pending_action: bool = False
    observed_source: str | None = None
    read_outcome: ReadTurnOutcome | None = None


class MikuTurnService:
    """Frontera serial de un turno; no es runtime, scheduler ni fuente de autoridad."""

    def __init__(
        self,
        *,
        session: ChatSession,
        repository: ConversationRepository,
        miku: Miku,
        inspection_scope: InspectionScope | None = None,
        archive_reader: ArchiveReader | None = None,
    ) -> None:
        if inspection_scope is not None and archive_reader is not None:
            raise ValueError("Un servicio de turno no puede mezclar Laboral y Archive.")
        self.session = session
        self.repository = repository
        self.miku = miku
        self.inspection_scope = inspection_scope
        self.archive_reader = archive_reader
        self._handling = False
        self._lifecycle_lock = Lock()
        self._close_attempted = False
        self._closed = False

    def handle(self, request: TurnRequest) -> TurnResult:
        """Procesa contenido/procedencia sin conceder autoridad humana."""
        return self._handle(request, trusted_human_interaction=False)

    def handle_human(self, request: TurnRequest) -> TurnResult:
        """Entrada exclusiva para una frontera que observó interacción humana directa."""
        return self._handle(request, trusted_human_interaction=True)

    def _handle(
        self, request: TurnRequest, *, trusted_human_interaction: bool
    ) -> TurnResult:
        with self._lifecycle_lock:
            if self._close_attempted:
                raise RuntimeError("Esta ejecución ya inició su cierre; retomá la sesión explícitamente.")
            if self._handling:
                raise RuntimeError("MikuTurnService admite un turno serial por vez.")
            self._handling = True
        try:
            self.miku.last_read_outcome = None
            self.miku.last_response_observed_source = None
            if request.scope_extension is not None:
                if not trusted_human_interaction:
                    return TurnResult(
                        "Ampliar el scope requiere interacción humana confiable.",
                        "error", self.session.id, error_type="human_scope_required",
                    )
                return self._extend_scope(request)
            text = request.text.strip()
            user_message = self._record_message(
                "user", text, interface_source=request.source
            )
            try:
                listen = (
                    self.miku.listen_to_human
                    if trusted_human_interaction
                    else self.miku.listen
                )
                response = listen(
                    text,
                    self.session,
                    inspection_request=(
                        InspectionRequest(
                            target=self.inspection_scope.root,
                            scope=self.inspection_scope,
                            intent=text,
                        )
                        if self.inspection_scope is not None else None
                    ),
                    archive_request=(
                        ArchiveQueryRequest(text)
                        if self.archive_reader is not None else None
                    ),
                )
            except Exception as error:
                error_message = self._record_message(
                    "tool",
                    f"No fue posible generar una respuesta: {error}",
                    kind="provider_error",
                    error_type=type(error).__name__,
                )
                return TurnResult(
                    response_text=error_message.content,
                    status="error",
                    session_id=self.session.id,
                    user_message_id=user_message.id,
                    response_message_id=error_message.id,
                    response_role="tool",
                    error_type=type(error).__name__,
                    pending_memory=self._has_pending_memory(),
                    pending_action=self._has_pending_action(),
                )

            read_outcome = self.miku.last_read_outcome
            observed_source = self.miku.last_response_observed_source or (
                "archive" if self.archive_reader is not None else None
            )
            response_role = "tool" if read_outcome is not None and read_outcome.answer is None else "assistant"
            recorded = self._record_message(
                response_role, response,
                **({"observed_source": observed_source} if observed_source else {}),
                **({"kind": "read_turn", "status": read_outcome.status,
                    "stop_reason": read_outcome.stop_reason} if read_outcome else {}),
            )
            memory_error = self.miku.last_memory_error
            pending_memory = self._has_pending_memory()
            pending_action = self._has_pending_action()
            return TurnResult(
                response_text=response,
                status=read_outcome.status if read_outcome is not None else "completed",
                session_id=self.session.id,
                user_message_id=user_message.id,
                response_message_id=recorded.id,
                response_role=response_role,
                secondary_diagnostic=(str(memory_error) if memory_error else None),
                pending_memory=pending_memory,
                pending_action=pending_action,
                observed_source=observed_source,
                read_outcome=read_outcome,
                error_type=read_outcome.stop_reason if read_outcome is not None else None,
            )
        finally:
            with self._lifecycle_lock:
                self._handling = False

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._handling:
                raise RuntimeError("No se puede cerrar mientras hay un turno en curso.")
            if self._close_attempted:
                raise RuntimeError("El cierre anterior no quedó confirmado; no se reintenta.")
            self._close_attempted = True
            if self.session.is_persistent:
                self.repository.close_session(self.session.id)
            self.session.close()
            self._closed = True

    def _extend_scope(self, request: TurnRequest) -> TurnResult:
        if self.inspection_scope is None:
            return TurnResult(
                "/allow requiere iniciar el chat con --inspect.",
                "error",
                self.session.id,
                error_type="scope_not_configured",
            )
        target = str(request.scope_extension).strip()
        if not target:
            return TurnResult(
                "/allow requiere un path.", "error", self.session.id,
                error_type="invalid_scope_extension",
            )
        try:
            self.inspection_scope = InspectionScope(
                root=self.inspection_scope.root,
                additional_allowed_targets=(
                    *self.inspection_scope.additional_allowed_targets,
                    Path(target).expanduser().resolve(),
                ),
            )
        except (OSError, ValueError) as error:
            return TurnResult(
                f"No pude ampliar el scope: {error}",
                "error",
                self.session.id,
                error_type=type(error).__name__,
            )
        return TurnResult(
            "Scope ampliado con "
            f"{self.inspection_scope.additional_allowed_targets[-1]}.",
            "scope_updated",
            self.session.id,
        )

    def _record_message(
        self, role: MessageRole, content: str, **metadata: Any,
    ) -> Message:
        message = Message(role=role, content=content, metadata=metadata)
        if self.session.is_persistent:
            self.repository.save_message(self.session.id, message)
        # La sesión sólo avanza después de confirmar la escritura. Si falla,
        # la excepción conserva su contrato y el historial previo queda intacto.
        self.session.extend([message])
        return message

    def _has_pending_memory(self) -> bool:
        return bool(
            self.miku.memory_service is not None
            and self.miku.memory_service.has_pending(self.session.id)
        )

    def _has_pending_action(self) -> bool:
        return bool(
            self.miku.action_coordinator is not None
            and self.miku.action_coordinator.pending_intent(self.session.id) is not None
        )


def build_miku_turn_service(
    *,
    session: ChatSession,
    repository: ConversationRepository,
    provider: TextProvider,
    inspection_scope: InspectionScope | None = None,
    archive_reader: ArchiveReader | None = None,
    activity: ActivitySink = NULL_ACTIVITY,
    memory_enabled: bool = True,
    memory_repository: SQLiteMemoryRepository | None = None,
    context_manager_factory: Callable[..., ContextManager] = ContextManager,
    context_observer: Callable[[ContextProjection], None] | None = None,
) -> MikuTurnService:
    """Compone la semántica reutilizable sin acoplarla a una interfaz."""
    memories = memory_repository or SQLiteMemoryRepository()
    memory_service = MemoryService(
        memories,
        JsonMemoryExtractor(provider),
        PersonalMemoryPolicy(),
    )
    miku = Miku(
        provider=provider,
        context_builder=context_manager_factory(
            repository=repository,
            memory_retriever=SimpleMemoryRetriever(memories),
            include_memory=memory_enabled,
        ),
        serializer=PromptComposer(),
        memory_service=memory_service,
        archive_reader=archive_reader,
        activity=activity,
        memory_enabled=memory_enabled,
        context_observer=context_observer,
    )
    return MikuTurnService(
        session=session,
        repository=repository,
        miku=miku,
        inspection_scope=inspection_scope,
        archive_reader=archive_reader,
    )
