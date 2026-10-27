from __future__ import annotations

from enum import Enum
import re

from .models import MemoryCandidate


class MemoryDecision(str, Enum):
    AUTO_SAVE = "auto_save"
    CONFIRM = "confirm"
    DISCARD = "discard"


class PersonalMemoryPolicy:
    """Autoridad determinista de persistencia; no interpreta conversación."""

    _secret_patterns = (
        re.compile(r"\b(?:password|contrase(?:ña|na)|api[_ -]?key|token de sesi[oó]n)\b", re.I),
        re.compile(r"\b(?:private key|clave privada|recovery code|c[oó]digo 2fa)\b", re.I),
        re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    )
    # Defensa acotada ante una clasificación errónea del extractor. No intenta
    # diagnosticar: sólo reconoce declaraciones de salud inequívocas que nunca
    # deben pasar por autosave.
    _obvious_health_patterns = (
        re.compile(r"\b(?:soy|es)\s+al[eé]rgic[oa]s?\b", re.I),
        re.compile(r"\balergias?\b", re.I),
        re.compile(r"\b(?:tengo|tiene|padezco|padece)\s+diabetes\b", re.I),
    )

    def decide(self, candidate: MemoryCandidate, *, review_only: bool = False) -> MemoryDecision:
        if candidate.source_type != "conversation":
            return MemoryDecision.DISCARD
        if candidate.sensitivity == "secret" or self._looks_like_secret(candidate.content):
            return MemoryDecision.DISCARD
        if (
            review_only
            or candidate.sensitivity == "sensitive"
            or candidate.clarity == "ambiguous"
            or self._looks_like_obvious_health(candidate.content)
        ):
            return MemoryDecision.CONFIRM
        return MemoryDecision.AUTO_SAVE

    def _looks_like_secret(self, content: str) -> bool:
        return any(pattern.search(content) for pattern in self._secret_patterns)

    def _looks_like_obvious_health(self, content: str) -> bool:
        return any(pattern.search(content) for pattern in self._obvious_health_patterns)
