from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gnomos.laboral.scope import InspectionScope


@dataclass(frozen=True, slots=True)
class Evidence:
    path: str
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None
    excerpt: str | None = None
    kind: str = "observation"


@dataclass(slots=True)
class CapabilityRequest:
    """Propuesta validable; sus argumentos son deliberadamente mutables."""

    capability: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    operation: str
    target: str | None
    success: bool
    data: Any = None
    evidence: tuple[Evidence, ...] = ()
    errors: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class InspectionRequest:
    target: str | Path
    scope: "InspectionScope"
    intent: str = ""


@dataclass(slots=True)
class InspectionResult:
    requested_target: str
    effective_scope: tuple[str, ...]
    inspected_items: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)
    blocked_external_references: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    operations: list[CapabilityResult] = field(default_factory=list)
    partial: bool = False
