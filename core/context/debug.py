from __future__ import annotations

import re

from core.context.projection import ContextProjection


_SECRET_VALUE = re.compile(
    r"(?i)\b(password|contraseña|api[_ -]?key|token|secret|credential|"
    r"clave privada)\b\s*[:=]\s*[^\s,;]+"
)
_SECRET_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.I)


def _safe_text(value: str, *, limit: int = 180) -> str:
    compact = " ".join(value.split())
    if "redacted" in compact.casefold():
        return "<redacted content omitted>"
    compact = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=<redacted>", compact)
    compact = _SECRET_TOKEN.sub("<redacted>", compact)
    if len(compact) > limit:
        return compact[: limit - 1].rstrip() + "…"
    return compact


def render_context_debug(projection: ContextProjection) -> str:
    """Renderiza una vista acotada de la proyección, nunca el prompt interno."""
    lines = ["[context-debug]"]
    lines.append(
        "identity: "
        f"source={projection.identity.source} | chars={len(projection.identity.content)}"
    )
    lines.append("memory:")
    if projection.memory:
        for item in projection.memory:
            lines.append(f"  - source={item.source} | {_safe_text(item.content)}")
    else:
        lines.append("  - <none>")

    lines.append("conversation:")
    conversation = projection.conversation
    for message in conversation.messages[-6:]:
        summary = (
            "<observed content omitted>"
            if message.metadata.get("observed_source") else _safe_text(message.content)
        )
        lines.append(
            f"  - {message.role} | source=conversation:{message.id} | "
            f"{summary}"
        )
    lines.append(
        "  - user | "
        f"source={conversation.current_message.source} | "
        f"{_safe_text(conversation.current_message.content)}"
    )

    lines.append("task:")
    if projection.task:
        for item in projection.task:
            if item.source.startswith(("inspection:", "archive:")):
                summary = "<observed content omitted>"
            else:
                summary = _safe_text(item.content)
            lines.append(f"  - source={item.source} | {summary}")
    else:
        lines.append("  - <none>")
    return "\n".join(lines)
