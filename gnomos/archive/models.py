from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_query_chars: int = 500
    max_search_results: int = 10
    max_search_excerpt_chars: int = 600
    max_note_content_chars: int = 12_000
    max_links: int = 30

    def __post_init__(self) -> None:
        values = {
            "max_query_chars": self.max_query_chars,
            "max_search_results": self.max_search_results,
            "max_search_excerpt_chars": self.max_search_excerpt_chars,
            "max_note_content_chars": self.max_note_content_chars,
            "max_links": self.max_links,
        }
        for name, value in values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} debe ser un entero no negativo.")
        if self.max_query_chars == 0:
            raise ValueError("max_query_chars debe ser mayor que cero.")


@dataclass(frozen=True, slots=True)
class ArchiveNote:
    note_id: int
    title: str
    content: str
    tags: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ArchiveLink:
    linked_note_id: int
    title: str
    tags: str | None
    reason: str | None
    linked_at: str
    direction: str


@dataclass(frozen=True, slots=True)
class ArchiveEvidence:
    note_id: int
    locator: str
    title: str | None = None
    excerpt: str | None = None
    relation: str | None = None
    related_note_id: int | None = None
    kind: str = "archive-observation"


@dataclass(slots=True)
class ArchiveOperationRequest:
    operation: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArchiveOperationResult:
    operation: str
    success: bool
    data: Any = None
    evidence: tuple[ArchiveEvidence, ...] = ()
    errors: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ArchiveQueryRequest:
    intent: str

    def __post_init__(self) -> None:
        if not isinstance(self.intent, str) or not self.intent.strip():
            raise ValueError("La consulta a Archive requiere una intención no vacía.")


@dataclass(slots=True)
class ArchiveQueryResult:
    operations: list[ArchiveOperationResult] = field(default_factory=list)
    evidence: list[ArchiveEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    partial: bool = False
