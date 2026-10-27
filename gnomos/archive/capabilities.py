from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any, Callable

from .models import (
    ArchiveEvidence,
    ArchiveLimits,
    ArchiveOperationRequest,
    ArchiveOperationResult,
)
from .reader import ArchiveReader


class ArchiveReadOnlyCapabilities:
    """Frontera cerrada: sólo búsqueda, lectura y vínculos de Archive."""

    def __init__(self, reader: ArchiveReader, limits: ArchiveLimits | None = None) -> None:
        self.reader = reader
        self.limits = limits or ArchiveLimits()
        self._registry: dict[str, Callable[[dict[str, Any]], ArchiveOperationResult]] = {
            "search_notes": self._search_notes,
            "read_note": self._read_note,
            "list_links": self._list_links,
        }
        self._contracts = {
            "search_notes": ({"query"}, set()),
            "read_note": ({"note_id"}, set()),
            "list_links": ({"note_id"}, set()),
        }

    @property
    def operation_names(self) -> tuple[str, ...]:
        return tuple(self._registry)

    def execute(self, request: ArchiveOperationRequest) -> ArchiveOperationResult:
        if not isinstance(request.operation, str):
            return self._error(str(request.operation), "operation debe ser un string.")
        handler = self._registry.get(request.operation)
        if handler is None:
            return self._error(
                request.operation,
                f"Operación desconocida o no autorizada: {request.operation}",
            )
        if not isinstance(request.arguments, dict):
            return self._error(request.operation, "Los argumentos deben ser un objeto.")
        try:
            self._validate(request.operation, request.arguments)
            return handler(request.arguments)
        except (ValueError, sqlite3.Error, OSError) as error:
            return self._error(request.operation, str(error))

    def _validate(self, operation: str, arguments: dict[str, Any]) -> None:
        required, optional = self._contracts[operation]
        missing = required - arguments.keys()
        if missing:
            raise ValueError("Faltan argumentos requeridos: " + ", ".join(sorted(missing)))
        unknown = arguments.keys() - required - optional
        if unknown:
            raise ValueError("Argumentos desconocidos: " + ", ".join(sorted(unknown)))
        if operation == "search_notes":
            query = arguments["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query debe ser un string no vacío.")
            if len(query) > self.limits.max_query_chars:
                raise ValueError(
                    f"query supera el límite de {self.limits.max_query_chars} caracteres."
                )
        else:
            note_id = arguments["note_id"]
            if not isinstance(note_id, int) or isinstance(note_id, bool) or note_id < 1:
                raise ValueError("note_id debe ser un entero positivo.")

    def _search_notes(self, arguments: dict[str, Any]) -> ArchiveOperationResult:
        query = arguments["query"].strip()
        rows = self.reader.search_notes(query, self.limits.max_search_results + 1)
        truncated = len(rows) > self.limits.max_search_results
        rows = rows[: self.limits.max_search_results]
        notes = []
        evidence = []
        for record in rows:
            excerpt, excerpt_truncated = self._excerpt(record.content, query)
            note = {
                "note_id": record.note_id, "title": record.title, "excerpt": excerpt,
                "tags": record.tags, "created_at": record.created_at,
                "excerpt_truncated": excerpt_truncated,
            }
            notes.append(note)
            evidence.append(self._evidence(
                note["note_id"], note["title"], excerpt, "search-match"
            ))
        return ArchiveOperationResult(
            "search_notes", True, {"query": query, "notes": notes}, tuple(evidence),
            truncated=truncated,
        )

    def _read_note(self, arguments: dict[str, Any]) -> ArchiveOperationResult:
        note_id = arguments["note_id"]
        row = self.reader.read_note(note_id)
        if row is None:
            return self._error("read_note", f"No existe una nota activa con ID {note_id}.")
        content = row.content
        truncated = len(content) > self.limits.max_note_content_chars
        content = content[: self.limits.max_note_content_chars]
        data = {
            "note_id": row.note_id, "title": row.title, "content": content,
            "tags": row.tags, "created_at": row.created_at,
        }
        return ArchiveOperationResult(
            "read_note", True, data,
            (self._evidence(row.note_id, row.title, content, "note-content"),),
            truncated=truncated,
        )

    def _list_links(self, arguments: dict[str, Any]) -> ArchiveOperationResult:
        note_id = arguments["note_id"]
        rows = self.reader.list_links(note_id, self.limits.max_links + 1)
        if rows is None:
            return self._error("list_links", f"No existe una nota activa con ID {note_id}.")
        truncated = len(rows) > self.limits.max_links
        rows = rows[: self.limits.max_links]
        links = [asdict(row) for row in rows]
        evidence = tuple(
            ArchiveEvidence(
                note_id=note_id,
                related_note_id=row.linked_note_id,
                title=row.title,
                relation=f"{row.direction}:{row.reason or ''}",
                locator=self._locator(note_id, f"link:{row.direction}:{row.linked_note_id}"),
                kind="note-link",
            )
            for row in rows
        )
        return ArchiveOperationResult(
            "list_links", True, {"note_id": note_id, "links": links}, evidence,
            truncated=truncated,
        )

    def _excerpt(self, content: str, query: str) -> tuple[str, bool]:
        limit = self.limits.max_search_excerpt_chars
        folded = content.casefold()
        index = folded.find(query.casefold())
        if index < 0:
            start = 0
        else:
            start = max(0, index - limit // 3)
        excerpt = content[start : start + limit]
        return excerpt, start > 0 or start + len(excerpt) < len(content)

    def _locator(self, note_id: int, suffix: str = "note") -> str:
        return f"archive:{self.reader.source}#note/{note_id}/{suffix}"

    def _evidence(self, note_id: int, title: str, excerpt: str, kind: str) -> ArchiveEvidence:
        return ArchiveEvidence(
            note_id=note_id, title=title, excerpt=excerpt,
            locator=self._locator(note_id), kind=kind,
        )

    @staticmethod
    def _error(operation: str, message: str) -> ArchiveOperationResult:
        return ArchiveOperationResult(operation, False, errors=(message,))
