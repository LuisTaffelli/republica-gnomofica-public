from __future__ import annotations

from dataclasses import dataclass, replace
import re
import unicodedata
from typing import Sequence

from .extractor import MemoryExtractor
from .models import MemoryCandidate, MemoryRecord
from .normalization import (
    is_ephemeral_conversation_reference,
    is_question_shaped,
    normalize_memory_content,
)
from .policy import MemoryDecision, PersonalMemoryPolicy
from .repository import MemoryRepository


@dataclass(frozen=True, slots=True)
class MemoryTurnResult:
    saved: tuple[MemoryRecord, ...] = ()
    retired_ids: tuple[str, ...] = ()
    pending: tuple[MemoryCandidate, ...] = ()
    already_known: tuple[MemoryRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _PendingBatch:
    candidates: tuple[MemoryCandidate, ...]
    allowed_related_ids: frozenset[str]


class MemoryService:
    """Orquesta extracción y política después de la respuesta principal."""

    _acknowledgements = {"ok", "okay", "dale", "gracias", "sí", "si", "no", "bien", "joya"}
    _affirmative_phrases = {
        "si", "si guardalo", "si guardala", "si dale", "si acordate",
        "sip guardalo", "dale", "dale guardalo", "recordalo", "guarda eso",
    }
    _negative_phrases = {
        "no", "no lo guardes", "no la guardes", "no guardes eso",
        "descartalo", "descartala", "mejor no", "no hace falta",
        "nah descartalo", "nop",
    }

    def __init__(
        self, repository: MemoryRepository, extractor: MemoryExtractor,
        policy: PersonalMemoryPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.policy = policy or PersonalMemoryPolicy()
        self._pending: dict[str, _PendingBatch] = {}

    def should_extract(self, message: str, *, source_type: str = "conversation") -> bool:
        words = re.findall(r"\w+", message.casefold())
        if source_type != "conversation" or not words:
            return False
        if len(words) <= 2 and " ".join(words) in self._acknowledgements:
            return False
        if is_ephemeral_conversation_reference(message):
            return False
        return True

    def confirm_from_message(self, session_id: str, message: str, profile_name: str) -> tuple[MemoryRecord, ...]:
        batch = self._pending.get(session_id)
        if batch is None:
            return ()
        intent = self._confirmation_intent(message)
        if intent == "confirm":
            proposed = batch.candidates[0]
            current = self.repository.list(profile_name, "active")
            candidate, related_is_valid = self._validated_candidate(
                proposed, current, batch.allowed_related_ids
            )
            saved: tuple[MemoryRecord, ...] = ()
            if candidate.operation == "retire":
                target = (
                    self._retirement_target(candidate, current)
                    if related_is_valid else None
                )
                if target is not None:
                    self.repository.retire(target.id, profile_name)
            else:
                known = self._equivalent_active(candidate, current)
                if known is None:
                    saved = (self._save_candidate(candidate, profile_name),)
            self._advance_pending(session_id, batch)
            return saved
        if intent == "reject":
            self._advance_pending(session_id, batch)
        return ()

    def has_pending(self, session_id: str) -> bool:
        return session_id in self._pending

    def process_turn(
        self, *, profile_name: str, message: str, conversation_window: Sequence[str],
        source_session_id: str | None, source_message_id: str | None,
        source_type: str = "conversation", review_only: bool = False,
    ) -> MemoryTurnResult:
        if not self.should_extract(message, source_type=source_type):
            return MemoryTurnResult()
        existing = self.repository.list(profile_name, "active")
        available = existing[:30]
        allowed_related_ids = frozenset(memory.id for memory in available)
        candidates = self.extractor.extract(
            message, conversation_window, available,
            source_session_id=source_session_id, source_message_id=source_message_id,
            source_type=source_type,
        )
        saved: list[MemoryRecord] = []
        retired: list[str] = []
        pending: list[MemoryCandidate] = []
        already_known: list[MemoryRecord] = []
        for proposed in candidates:
            candidate, related_is_valid = self._validated_candidate(
                proposed, existing, allowed_related_ids
            )
            if (proposed.operation == "retire"
                    and proposed.related_memory_id is not None
                    and not related_is_valid):
                continue
            if (
                is_ephemeral_conversation_reference(candidate.content)
                or is_question_shaped(candidate.content)
            ):
                continue
            known = self._equivalent_active(candidate, existing)
            if known is not None:
                already_known.append(known)
                continue
            decision = self.policy.decide(candidate, review_only=review_only)
            if decision is MemoryDecision.DISCARD:
                continue
            if decision is MemoryDecision.CONFIRM:
                pending.append(candidate)
                continue
            if candidate.operation == "retire":
                target = (
                    self._retirement_target(candidate, existing)
                    if related_is_valid else None
                )
                if target is not None and self.repository.retire(target.id, profile_name):
                    retired.append(target.id)
                    existing = [item for item in existing if item.id != target.id]
            else:
                stored = self._save_candidate(candidate, profile_name)
                saved.append(stored)
                if (related_is_valid and candidate.relation in {"corrects", "replaces"}
                        and candidate.related_memory_id):
                    retired.append(candidate.related_memory_id)
                    existing = [
                        item for item in existing
                        if item.id != candidate.related_memory_id
                    ]
                existing.append(stored)
        if pending and source_session_id:
            self._pending[source_session_id] = _PendingBatch(
                tuple(pending), allowed_related_ids
            )
        return MemoryTurnResult(
            tuple(saved), tuple(retired), tuple(pending), tuple(already_known)
        )

    @staticmethod
    def _equivalent_active(
        candidate: MemoryCandidate,
        active_memories: Sequence[MemoryRecord],
    ) -> MemoryRecord | None:
        # Una corrección/reemplazo explícito debe atravesar el flujo normal aun
        # si coincide con otro texto: su relación forma parte de la intención.
        if candidate.operation != "remember" or candidate.relation in {
            "corrects", "replaces"
        }:
            return None
        normalized = normalize_memory_content(candidate.content)
        return next(
            (
                memory for memory in active_memories
                if normalize_memory_content(memory.content) == normalized
            ),
            None,
        )

    def _save_candidate(
        self, candidate: MemoryCandidate, profile_name: str,
    ) -> MemoryRecord:
        record = MemoryRecord(
            profile_name=profile_name, category=candidate.category, content=candidate.content,
            source_type=candidate.source_type, source_session_id=candidate.source_session_id,
            source_message_id=candidate.source_message_id,
            related_memory_id=candidate.related_memory_id, relation=candidate.relation,
        )
        if candidate.relation in {"corrects", "replaces"} and candidate.related_memory_id:
            self.repository.save_and_retire(
                record, candidate.related_memory_id, profile_name
            )
        else:
            self.repository.save(record)
        return record

    def _advance_pending(self, session_id: str, batch: _PendingBatch) -> None:
        remaining = batch.candidates[1:]
        if remaining:
            self._pending[session_id] = _PendingBatch(
                remaining, batch.allowed_related_ids
            )
        else:
            del self._pending[session_id]

    @staticmethod
    def _validated_candidate(
        candidate: MemoryCandidate,
        active_memories: Sequence[MemoryRecord],
        allowed_related_ids: frozenset[str],
    ) -> tuple[MemoryCandidate, bool]:
        if candidate.related_memory_id is None:
            return candidate, True
        active_ids = {memory.id for memory in active_memories}
        if (candidate.related_memory_id in active_ids
                and candidate.related_memory_id in allowed_related_ids):
            return candidate, True
        return replace(candidate, related_memory_id=None, relation=None), False

    @classmethod
    def _confirmation_intent(cls, message: str) -> str | None:
        words = re.findall(r"[\wáéíóúñü]+", message.casefold())
        if not words:
            return None
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", " ".join(words))
            if not unicodedata.combining(character)
        )
        if normalized in cls._negative_phrases:
            return "reject"
        if normalized in cls._affirmative_phrases:
            return "confirm"
        return None

    @staticmethod
    def _retirement_target(candidate: MemoryCandidate, existing: Sequence[MemoryRecord]) -> MemoryRecord | None:
        if candidate.related_memory_id:
            return next((memory for memory in existing if memory.id == candidate.related_memory_id), None)
        query = {word for word in re.findall(r"\w+", candidate.content.casefold()) if len(word) >= 4}
        matches = [memory for memory in existing if query & set(re.findall(r"\w+", memory.content.casefold()))]
        return matches[0] if len(matches) == 1 else None
