from __future__ import annotations

import re
from typing import Protocol, Sequence

from .models import MemoryRecord
from .repository import MemoryRepository


class MemoryRetriever(Protocol):
    def retrieve(
        self, profile_name: str, message: str, conversation_window: Sequence[str] = ()
    ) -> list[MemoryRecord]: ...


class SimpleMemoryRetriever:
    """Selección textual pequeña y sustituible, sin redefinir la memoria."""

    _stopwords = {
        "para", "como", "esto", "esta", "este", "sobre", "pero", "porque",
        "quiero", "recordas", "acordas", "miku", "algo", "todo", "tengo",
        "what", "about", "remember", "that", "with", "from",
    }
    _history_terms = {"antes", "anterior", "cambio", "cambió", "cambio", "corregí",
                      "corregi", "trayectoria", "historia", "ya no", "era"}
    _broad_terms = ("qué te acordás", "que te acordas", "qué recordás", "que recordas",
                    "qué sabés de mí", "que sabes de mi")

    def __init__(self, repository: MemoryRepository, limit: int = 8) -> None:
        self.repository = repository
        self.limit = limit

    def retrieve(
        self, profile_name: str, message: str, conversation_window: Sequence[str] = ()
    ) -> list[MemoryRecord]:
        normalized = message.casefold()
        active = self.repository.list(profile_name, "active")
        broad = any(term in normalized for term in self._broad_terms)
        terms = self._terms(" ".join([*conversation_window[-3:], message]))
        selected = active[: self.limit] if broad else self._matching(active, terms)

        if any(term in normalized for term in self._history_terms):
            retired = self._matching(self.repository.list(profile_name, "retired"), terms)
            selected.extend(retired)
        return selected[: self.limit]

    def _matching(self, memories: Sequence[MemoryRecord], terms: set[str]) -> list[MemoryRecord]:
        ranked = []
        for memory in memories:
            overlap = len(terms & self._terms(memory.content))
            if overlap:
                ranked.append((overlap, memory.created_at, memory))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in ranked]

    def _terms(self, text: str) -> set[str]:
        return {
            term for term in re.findall(r"[\wáéíóúñü]+", text.casefold())
            if len(term) >= 4 and term not in self._stopwords
        }
