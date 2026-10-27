"""Contrato puro de S2.1, utilizado por la integración acotada S2.2.

No llama al provider, no ejecuta capabilities y no concede scope. El
caller debe consumir los presupuestos y validar cada propuesta con Laboral.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from gnomos.laboral.models import CapabilityResult, Evidence
from gnomos.laboral.protocol import ModelDecision, parse_model_decision


ReadStopReason = Literal[
    "decision_limit", "read_limit", "provider_error", "invalid_decision", "tool_error",
]
_STOP_REASONS = frozenset({
    "decision_limit", "read_limit", "provider_error", "invalid_decision", "tool_error",
})


class ReadBudgetExceeded(RuntimeError):
    def __init__(self, reason: Literal["decision_limit", "read_limit"]) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReadTurnBudget:
    """Contadores por turno, sin reloj, ejecución ni persistencia.

    Una decisión se reserva ANTES de pedirla al provider; una lectura, ANTES de
    entregar la propuesta a Laboral (incluso si luego rechaza args o scope).
    """

    max_decisions: int = 8
    max_reads: int = 4
    decisions: int = 0
    reads: int = 0
    _read_in_decision: bool = False

    def __post_init__(self) -> None:
        for name in ("max_decisions", "max_reads"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} debe ser un entero positivo.")
        for name, maximum in (("decisions", self.max_decisions), ("reads", self.max_reads)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{name} debe estar entre 0 y {maximum}.")
        if self.reads > self.decisions or (self._read_in_decision and not self.reads):
            raise ValueError("Las lecturas requieren decisiones previas.")

    def reserve_decision(self) -> ReadTurnBudget:
        if self.decisions == self.max_decisions:
            raise ReadBudgetExceeded("decision_limit")
        return replace(self, decisions=self.decisions + 1, _read_in_decision=False)

    def reserve_read(self) -> ReadTurnBudget:
        if not self.decisions or self._read_in_decision:
            raise ValueError("Se admite una lectura como máximo por decisión reservada.")
        if self.reads == self.max_reads:
            raise ReadBudgetExceeded("read_limit")
        return replace(self, reads=self.reads + 1, _read_in_decision=True)


def parse_read_decision(raw: str) -> ModelDecision:
    """Valida el sobre; los argumentos y el scope aún deben pasar por Laboral.

    Reutiliza el protocolo existente. El resultado sigue siendo una propuesta
    del modelo: un path, source o texto no otorgan autorización.
    """
    decision = parse_model_decision(raw)
    if decision.tool_request is not None and decision.tool_request.capability != "read_file":
        raise ValueError("S2.1 sólo admite la capability read_file.")
    return decision


@dataclass(frozen=True, slots=True)
class ReadTurnOutcome:
    """Resultado efímero del contrato; TurnResult puede transportarlo sin persistirlo.

    operations recibe resultados de la frontera de ejecución confiable, nunca
    registros deserializados de la respuesta del modelo. Su contenido es dato
    observado no confiable, sin promoción a memoria, instrucciones o permisos.
    """

    status: Literal["completed", "partial", "error"]
    answer: str | None
    operations: tuple[CapabilityResult, ...]
    stop_reason: ReadStopReason | Literal["truncated"] | None
    diagnostic: str | None

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(item for operation in self.operations for item in operation.evidence)


def _read_operations(operations: Sequence[CapabilityResult]) -> tuple[CapabilityResult, ...]:
    records = tuple(operations)
    if any(not isinstance(item, CapabilityResult) or item.operation != "read_file" for item in records):
        raise ValueError("Se requieren resultados read_file de la frontera de ejecución.")
    return records


def complete_read_turn(
    answer: str, operations: Sequence[CapabilityResult] = (),
) -> ReadTurnOutcome:
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("La respuesta final debe ser texto no vacío.")
    records = _read_operations(operations)
    if any(not item.success or item.errors for item in records):
        raise ValueError("Un fallo de herramienta requiere stop_read_turn, no una final exitosa.")
    if any(item.truncated for item in records):
        return ReadTurnOutcome(
            "partial", answer.strip(), records, "truncated",
            "Una lectura devolvió contenido truncado; la respuesta conserva esa limitación.",
        )
    return ReadTurnOutcome("completed", answer.strip(), records, None, None)


def stop_read_turn(
    reason: ReadStopReason,
    diagnostic: str,
    operations: Sequence[CapabilityResult] = (),
) -> ReadTurnOutcome:
    if reason not in _STOP_REASONS:
        raise ValueError("Motivo de terminación desconocido.")
    if not isinstance(diagnostic, str) or not diagnostic.strip():
        raise ValueError("La terminación requiere explicar su limitación.")
    records = _read_operations(operations)
    has_observation = any(item.success or item.evidence for item in records)
    return ReadTurnOutcome(
        "partial" if has_observation else "error",
        None, records, reason, diagnostic.strip(),
    )
