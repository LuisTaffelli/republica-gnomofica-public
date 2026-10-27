from __future__ import annotations

import re
import unicodedata


def normalize_memory_content(content: str) -> str:
    """Normalización conservadora para equivalencia textual de recuerdos."""
    decomposed = unicodedata.normalize("NFKD", content.casefold())
    without_marks = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"\w+", without_marks))


_EPHEMERAL_CONVERSATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^que te acabo de (?:preguntar|decir)$",
        r"^te acordas (?:de )?lo que dije recien$",
        r"^que dije antes$",
        r"^que me respondiste$",
        r"^repeti (?:lo )?ultimo$",
    )
)

_ORDINARY_QUESTION_PATTERN = re.compile(
    r"^(?:a que|por que|que|quien|cuando|donde|como|cual|cuanto)\b"
)


def is_ephemeral_conversation_reference(content: str) -> bool:
    normalized = normalize_memory_content(content)
    return any(
        pattern.fullmatch(normalized)
        for pattern in _EPHEMERAL_CONVERSATION_PATTERNS
    )


def is_question_shaped(content: str) -> bool:
    """Reconoce preguntas informativas, no cualquier texto que contenga '?'."""
    return bool(_ORDINARY_QUESTION_PATTERN.match(normalize_memory_content(content)))
