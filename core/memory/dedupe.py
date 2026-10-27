from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import MemoryRecord
from .normalization import normalize_memory_content
from .repository import MemoryRepository


@dataclass(frozen=True, slots=True)
class DuplicateMemory:
    canonical: MemoryRecord
    duplicate: MemoryRecord


def find_active_duplicates(
    memories: Sequence[MemoryRecord],
) -> tuple[DuplicateMemory, ...]:
    """Conserva como canonical el active más antiguo de cada texto equivalente."""
    canonical_by_content: dict[str, MemoryRecord] = {}
    duplicates: list[DuplicateMemory] = []
    ordered = sorted(memories, key=lambda item: (item.created_at, item.id))
    for memory in ordered:
        if memory.status != "active":
            continue
        key = normalize_memory_content(memory.content)
        canonical = canonical_by_content.setdefault(key, memory)
        if canonical.id != memory.id:
            duplicates.append(DuplicateMemory(canonical, memory))
    return tuple(duplicates)


def deduplicate_active_memories(
    repository: MemoryRepository,
    profile_name: str,
    *,
    apply: bool = False,
) -> tuple[DuplicateMemory, ...]:
    duplicates = find_active_duplicates(repository.list(profile_name, "active"))
    if apply:
        for item in duplicates:
            repository.retire(item.duplicate.id, profile_name)
    return duplicates
