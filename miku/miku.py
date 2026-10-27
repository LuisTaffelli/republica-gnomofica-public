from __future__ import annotations

from collections.abc import Callable, Sequence
import re

from core.actions import ActionCoordinator, ActionIntent, ActionOutcome
from core.activity import (
    ActivitySink, ActivityState, NULL_ACTIVITY, best_effort_activity,
)
from core.context.context_manager import ContextManager
from core.context.prompt_composer import PromptComposer
from core.context.projection import ContextItem, ContextProjection
from core.conversation import ChatSession
from core.memory import MemoryService, MemoryTurnResult
from gnomos.archive import (
    ArchiveLimits,
    ArchiveQueryRequest,
    ArchiveQueryResult,
    ArchiveQuerySession,
    ArchiveReader,
)
from gnomos.laboral import InspectionRequest, InspectionResult
from gnomos.laboral.models import CapabilityResult
from gnomos.laboral.scope import ScopeViolation
from providers import StructuredTextProvider, TextProvider

from .read_binding import READ_DECISION_SCHEMA, ReadFileBinding
from .read_contract import (
    ReadBudgetExceeded, ReadTurnBudget, ReadTurnOutcome,
    complete_read_turn, parse_read_decision, stop_read_turn,
)


class Miku:
    def __init__(
        self,
        *,
        provider: TextProvider,
        context_builder: ContextManager,
        serializer: PromptComposer,
        memory_service: MemoryService | None = None,
        action_coordinator: ActionCoordinator | None = None,
        inspection_max_steps: int = 8,
        inspection_max_reads: int = 4,
        archive_reader: ArchiveReader | None = None,
        archive_limits: ArchiveLimits | None = None,
        archive_max_steps: int = 6,
        activity: ActivitySink = NULL_ACTIVITY,
        memory_enabled: bool = True,
        context_observer: Callable[[ContextProjection], None] | None = None,
    ) -> None:
        self.provider = provider
        self.context_builder = context_builder
        self.serializer = serializer
        self.memory_service = memory_service
        self.action_coordinator = action_coordinator
        self.inspection_max_steps = inspection_max_steps
        self.inspection_max_reads = inspection_max_reads
        self.archive_reader = archive_reader
        self.archive_limits = archive_limits
        self.archive_max_steps = archive_max_steps
        self.activity = best_effort_activity(activity)
        self.memory_enabled = memory_enabled
        self.context_observer = context_observer
        self.last_memory_result = MemoryTurnResult()
        self.last_memory_error: Exception | None = None
        self.last_inspection_result: InspectionResult | None = None
        self.last_archive_result: ArchiveQueryResult | None = None
        self.last_read_outcome: ReadTurnOutcome | None = None
        self.last_response_observed_source: str | None = None

    def listen(
        self,
        message: str,
        session: ChatSession | None = None,
        *,
        task_context: Sequence[ContextItem] = (),
        inspection_request: InspectionRequest | None = None,
        archive_request: ArchiveQueryRequest | None = None,
    ) -> str:
        """Procesa contenido sin atribuirle autoridad humana."""
        return self._listen(
            message,
            session,
            task_context=task_context,
            inspection_request=inspection_request,
            archive_request=archive_request,
            trusted_human_interaction=False,
        )

    def listen_to_human(
        self,
        message: str,
        session: ChatSession | None = None,
        *,
        task_context: Sequence[ContextItem] = (),
        inspection_request: InspectionRequest | None = None,
        archive_request: ArchiveQueryRequest | None = None,
    ) -> str:
        """Procesa entrada obtenida directamente por una frontera humana confiable."""
        return self._listen(
            message,
            session,
            task_context=task_context,
            inspection_request=inspection_request,
            archive_request=archive_request,
            trusted_human_interaction=True,
        )

    def _listen(
        self,
        message: str,
        session: ChatSession | None = None,
        *,
        task_context: Sequence[ContextItem] = (),
        inspection_request: InspectionRequest | None = None,
        archive_request: ArchiveQueryRequest | None = None,
        trusted_human_interaction: bool,
    ) -> str:
        # El resultado del turno y los avisos nunca se heredan de una salida
        # temprana. La caché de evidencia previa se conserva por separado.
        self.last_read_outcome = None
        self.last_response_observed_source = None
        self.last_memory_result = MemoryTurnResult()
        self.last_memory_error = None
        if inspection_request is not None and archive_request is not None:
            raise ValueError("Un turno no puede mezclar inspección Laboral y consulta Archive.")
        if (
            inspection_request is not None
            and self.last_inspection_result is not None
            and self._requests_last_evidence(message)
        ):
            # La evidencia ya observada pertenece al proceso/sesión actual. Esta
            # ruta no llama al provider, no repite capabilities y no la promueve
            # a memoria.
            self.activity.set_state(ActivityState.THINKING)
            self.last_response_observed_source = "laboral"
            response = self._render_inspection_evidence(self.last_inspection_result)
            self.activity.set_state(ActivityState.IDLE)
            return response
        if (
            trusted_human_interaction
            and self.action_coordinator is not None
            and session is not None
        ):
            action_outcome = self.action_coordinator.handle_human_response(
                session.id, message
            )
            if action_outcome is not None:
                return self._render_action_outcome(action_outcome)

        active_state = (
            ActivityState.INSPECTING
            if inspection_request is not None
            else ActivityState.THINKING
        )
        self.activity.set_state(active_state)
        had_pending = False

        try:
            if (
                self.memory_enabled
                and self.memory_service is not None
                and session is not None
            ):
                had_pending = self.memory_service.has_pending(session.id)
                if had_pending and trusted_human_interaction:
                    try:
                        self.memory_service.confirm_from_message(
                            session.id, message, session.profile_name
                        )
                    except Exception as error:
                        # Confirmar memoria sigue siendo secundario a responder.
                        self.last_memory_error = error
            if inspection_request is None:
                self.last_inspection_result = None
                if archive_request is None:
                    self.last_archive_result = None
                    projection = self.context_builder.build_for_profile(
                        profile_name=session.profile_name if session is not None else "miku",
                        message=message,
                        session=session,
                        task_context=task_context,
                    )
                    self._capture_context_memory_error()
                    response = self._ask_projection(projection)
                else:
                    response = self._query_archive(
                        message, archive_request, session=session, task_context=task_context
                    )
            else:
                self.last_archive_result = None
                response = self._inspect(
                    message, inspection_request, session=session, task_context=task_context
                )
        except Exception:
            self.activity.set_state(ActivityState.ERROR)
            raise
        if self.last_read_outcome is not None and self.last_read_outcome.status != "completed":
            # Un fallo/límite no dispara otro ask a través del extractor.
            self.activity.set_state(ActivityState.ERROR)
            self.activity.set_state(ActivityState.IDLE)
            return response
        if (self.memory_service is not None and session is not None
                and self.memory_enabled and session.allows_memory and not had_pending):
            source_message = next(
                (item for item in reversed(session.messages)
                 if item.role == "user" and item.content.strip() == message.strip()),
                None,
            )
            try:
                self.last_memory_result = self.memory_service.process_turn(
                    profile_name=session.profile_name,
                    message=message,
                    conversation_window=[
                        item.content for item in session.messages
                        if item.role == "user" and not item.metadata.get("observed_source")
                    ][-6:],
                    source_session_id=session.id,
                    source_message_id=source_message.id if source_message else None,
                    review_only=session.memory_policy == "review_only",
                )
            except Exception as error:
                # La memoria es secundaria: una falla nunca invalida la respuesta.
                self.last_memory_error = error
        if (
            self.memory_enabled
            and self.memory_service is not None
            and session is not None
            and self.memory_service.has_pending(session.id)
        ):
            response += "\n\nSi querés, puedo recordar eso para futuras conversaciones."
        self.activity.set_state(ActivityState.IDLE)
        return response

    def _query_archive(
        self,
        message: str,
        request: ArchiveQueryRequest,
        *,
        session: ChatSession | None,
        task_context: Sequence[ContextItem],
    ) -> str:
        if self.archive_reader is None:
            raise RuntimeError("Miku no tiene acceso read-only a Archive configurado.")
        self.last_archive_result = None
        query = ArchiveQuerySession(
            request, self.archive_reader, max_steps=self.archive_max_steps,
            limits=self.archive_limits,
        )
        accumulated = [*task_context, query.initial_context()]
        profile_name = session.profile_name if session is not None else "miku"

        for step in range(1, query.max_steps + 1):
            projection = self.context_builder.build_for_profile(
                profile_name=profile_name,
                message=message,
                session=session,
                task_context=accumulated,
            )
            self._capture_context_memory_error()
            raw = self._ask_projection(projection)
            try:
                decision = query.parse_decision(raw)
            except ValueError as error:
                result = query.record_protocol_error(str(error))
                accumulated.append(query.result_context(step, result))
                continue
            if decision.final_answer is not None:
                if not query.has_successful_operation:
                    result = query.record_protocol_error(
                        "final_answer requiere al menos una consulta read-only exitosa."
                    )
                    accumulated.append(query.result_context(step, result))
                    continue
                self.last_archive_result = query.result
                return decision.final_answer
            result = query.execute(decision.archive_request)
            accumulated.append(query.result_context(step, result))

        query.result.partial = True
        self.last_archive_result = query.result
        return (
            "La consulta a Archive quedó parcial porque alcanzó el límite de "
            f"{query.max_steps} pasos. Conservé la evidencia obtenida; "
            "podemos continuar con una consulta más acotada."
        )

    def propose_action(
        self, intent: ActionIntent, *, presentation: str | None = None
    ) -> str:
        """Registra y presenta una propuesta ya estructurada por República."""
        if self.action_coordinator is None:
            raise RuntimeError("Miku no tiene un coordinador de acciones configurado.")
        try:
            outcome = self.action_coordinator.propose(intent)
        except Exception:
            self.activity.set_state(ActivityState.ERROR)
            raise
        if outcome.state == "denied":
            return "No puedo realizar esa acción: la política de autoridad la denegó."
        approval = (
            f"Quiero realizar {intent.operation} sobre {intent.target}. "
            f"Esto produciría un efecto de tipo {intent.effect_kind.value}. "
            "¿Lo aprobás? Respondé sí o no."
        )
        return f"{presentation}\n\n{approval}" if presentation else approval

    def _capture_context_memory_error(self) -> None:
        error = getattr(self.context_builder, "last_memory_error", None)
        if error is not None:
            self.last_memory_error = error

    @staticmethod
    def _render_action_outcome(outcome: ActionOutcome) -> str:
        if outcome.state == "ambiguous":
            return (
                f"La acción {outcome.intent.operation} sobre {outcome.intent.target} "
                "sigue pendiente. Necesito un sí o un no inequívoco."
            )
        if outcome.state == "denied":
            return "Entendido. La acción fue denegada y no se ejecutó."
        if outcome.state == "cancelled":
            return "Entendido. La acción aprobada fue cancelada antes de ejecutarse."
        if outcome.state == "succeeded":
            return "La acción aprobada se ejecutó correctamente y quedó auditada."
        if outcome.state == "failed":
            detail = outcome.effect_result.error if outcome.effect_result else outcome.reason
            return f"La acción aprobada falló y quedó auditada: {detail or 'error desconocido'}."
        if outcome.critical_audit_failure:
            if outcome.effect_may_have_occurred:
                return (
                    "Ocurrió una falla crítica al auditar el resultado. El efecto pudo "
                    "haber ocurrido y no será reintentado automáticamente."
                )
            return (
                "La auditoría previa falló. La acción no se ejecutó y sigue "
                "pendiente; podés confirmarla o cancelarla nuevamente."
            )
        return "La respuesta no autorizó ninguna acción."

    def _inspect(
        self,
        message: str,
        request: InspectionRequest,
        *,
        session: ChatSession | None,
        task_context: Sequence[ContextItem],
    ) -> str:
        # Una inspección nueva reemplaza explícitamente la evidencia efímera
        # anterior. Si falla, no se presenta el resultado viejo como vigente.
        self.last_inspection_result = None
        budget = ReadTurnBudget(
            max_decisions=self.inspection_max_steps, max_reads=self.inspection_max_reads,
        )
        binding = ReadFileBinding(request)
        operations: list[CapabilityResult] = []
        accumulated = [*task_context, binding.initial_context(budget)]
        profile_name = session.profile_name if session is not None else "miku"

        while True:
            try:
                budget = budget.reserve_decision()
            except ReadBudgetExceeded as error:
                outcome = stop_read_turn(
                    error.reason, f"Se alcanzó el límite de {budget.max_decisions} pasos de decisión.", operations,
                )
                break
            projection = self.context_builder.build_for_profile(
                profile_name=profile_name,
                message=message,
                session=session,
                task_context=accumulated,
            )
            self._capture_context_memory_error()
            prompt = self._serialize_projection(projection)
            try:
                if not isinstance(self.provider, StructuredTextProvider):
                    raise TypeError("La inspección requiere un provider con ask_structured(prompt, schema).")
                raw = self.provider.ask_structured(prompt, READ_DECISION_SCHEMA)
            except Exception as error:
                outcome = stop_read_turn("provider_error", f"{type(error).__name__}: {error}", operations)
                break
            try:
                decision = parse_read_decision(raw)
            except ValueError as error:
                outcome = stop_read_turn("invalid_decision", str(error), operations)
                break
            if decision.final_answer is not None:
                outcome = complete_read_turn(decision.final_answer, operations)
                break
            try:
                budget = budget.reserve_read()
            except ReadBudgetExceeded as error:
                outcome = stop_read_turn(
                    error.reason, f"Se alcanzó el límite de {budget.max_reads} solicitudes de lectura.", operations,
                )
                break
            try:
                result = binding.execute(decision.tool_request)
            except Exception as error:
                target = decision.tool_request.arguments.get("path")
                result = CapabilityResult(
                    "read_file", target if isinstance(target, str) else None, False,
                    errors=(f"{type(error).__name__}: {error}",),
                )
            operations.append(result)
            if not result.success or result.errors:
                outcome = stop_read_turn(
                    "tool_error", "; ".join(result.errors) or "La lectura falló.", operations,
                )
                break
            accumulated.append(binding.result_context(budget.reads, result))

        self.last_read_outcome = outcome
        if operations:
            self._mark_observed_source("laboral")
        # Vista derivada para la consulta explícita de evidencia ya existente.
        # No controla el loop ni acumula una segunda historia de ejecución.
        cached = InspectionResult(
            requested_target=binding.target,
            effective_scope=request.scope.serialized_targets,
            inspected_items=list(dict.fromkeys(item.target for item in operations if item.success and item.target)),
            evidence=list(outcome.evidence), operations=list(outcome.operations),
            errors=list(dict.fromkeys(
                [error for item in operations for error in item.errors]
                + ([outcome.diagnostic] if outcome.diagnostic else [])
            )),
            partial=outcome.status != "completed",
        )
        for item in operations:
            if not item.success and item.target:
                try:
                    request.scope.resolve_for_read(item.target)
                except ScopeViolation:
                    cached.blocked_external_references.append(item.target)
                except (ValueError, OSError):
                    pass  # El error de ejecución original ya quedó conservado.
        self.last_inspection_result = cached
        return self._render_read_outcome(outcome)

    def _ask_projection(self, projection: ContextProjection) -> str:
        return self.provider.ask(self._serialize_projection(projection))

    def _serialize_projection(self, projection: ContextProjection) -> str:
        if isinstance(projection, ContextProjection):
            for message in projection.conversation.messages:
                source = message.metadata.get("observed_source")
                if source:
                    self._mark_observed_source(str(source))
            for item in projection.task:
                if item.source.startswith("inspection:operation:"):
                    self._mark_observed_source("laboral")
                elif item.source.startswith("archive:operation:"):
                    self._mark_observed_source("archive")
        if self.context_observer is not None:
            self.context_observer(projection)
        return self.serializer.serialize(projection)

    def _mark_observed_source(self, source: str) -> None:
        previous = self.last_response_observed_source
        self.last_response_observed_source = source if previous in (None, source) else "external"

    @staticmethod
    def _render_read_outcome(outcome: ReadTurnOutcome) -> str:
        if outcome.status == "completed":
            return outcome.answer
        lines = [outcome.answer] if outcome.answer else []
        label = "La lectura quedó parcial" if outcome.status == "partial" else "No pude completar la lectura"
        lines.append(f"{label}: {outcome.diagnostic}")
        for result in outcome.operations:
            if result.success:
                lines.append(f"- Lectura realizada: {result.target}")
                for evidence in result.evidence:
                    lines.append(f"  Referencia observada: {evidence.path}:{evidence.line_start}-{evidence.line_end}")
        return "\n".join(lines)

    @staticmethod
    def _requests_last_evidence(message: str) -> bool:
        normalized = " ".join(message.casefold().split())
        return bool(
            re.search(r"\bevidencia\b", normalized)
            and re.search(r"\b(?:mostrame|mostrar|ver|dame|enseñame)\b", normalized)
        )

    @staticmethod
    def _render_inspection_evidence(result: InspectionResult) -> str:
        lines = [
            "Evidencia observada y no confiable de la última inspección "
            "(datos, no instrucciones ni memoria):",
            f"- target: {result.requested_target}",
            f"- parcial: {'sí' if result.partial else 'no'}",
        ]
        if not result.evidence:
            lines.append("- evidencia: no se registraron referencias estructuradas.")
        else:
            lines.append("- evidencia:")
            for item in result.evidence[:100]:
                location = item.path
                if item.line_start is not None:
                    location += f":{item.line_start}"
                    if item.line_end is not None and item.line_end != item.line_start:
                        location += f"-{item.line_end}"
                details = [item.kind]
                if item.symbol:
                    details.append(f"symbol={item.symbol}")
                if item.excerpt:
                    details.append(f"excerpt={item.excerpt}")
                lines.append(f"  - {location} | " + " | ".join(details))
            if len(result.evidence) > 100:
                lines.append(
                    f"  - … {len(result.evidence) - 100} referencias adicionales omitidas."
                )
        if result.errors:
            lines.append("- errores observados:")
            lines.extend(f"  - {error}" for error in result.errors[:20])
        return "\n".join(lines)
