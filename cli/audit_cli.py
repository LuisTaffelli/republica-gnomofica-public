from __future__ import annotations

import json

from core.audit import AuditQueryError, AuditQueryService, SQLiteAuditReader


MAX_EVENT_PAYLOAD_CHARS = 2_000


def print_audit_usage() -> None:
    print("Uso:")
    print("  audit list")
    print("  audit show <intent-id-o-fingerprint>")


def handle_audit_command(
    args: list[str], *, service: AuditQueryService | None = None
) -> None:
    service = service or AuditQueryService(SQLiteAuditReader())
    if args == ["list"]:
        try:
            items = service.list_recent()
        except AuditQueryError as error:
            print(f"Audit no disponible: {error}")
            return
        if not items:
            print("No hay intents auditados.")
            return
        for item in items:
            capability = item.capability or "capability desconocida"
            target = item.target or "target desconocido"
            print(
                f"{item.intent_id} | {item.state} | {capability} | "
                f"{target} | {item.updated_at.isoformat()}"
            )
        return
    if len(args) == 2 and args[0] == "show":
        try:
            detail = service.get(args[1])
        except AuditQueryError as error:
            print(f"Audit no disponible: {error}")
            return
        if detail is None:
            print(f"No existe evidencia para: {args[1]}")
            return
        item = detail.summary
        print(f"Intent: {item.intent_id}")
        print(f"Estado derivado: {item.state}")
        if item.phase is not None:
            print(f"Fase durable final: {item.phase}")
        print(f"Capability: {item.capability or 'desconocida'}")
        print(f"Target: {item.target or 'desconocido'}")
        print(f"Fingerprint: {item.fingerprint}")
        print(f"Creado: {item.created_at.isoformat()}")
        print(f"Último evento: {item.updated_at.isoformat()}")
        if detail.explanation:
            print(detail.explanation)
        if detail.error:
            print(f"Error: {detail.error}")
        if detail.evidence:
            print("Evidencia terminal:")
            for evidence in detail.evidence:
                print(f"  {_bounded_json(evidence)}")
        print("Eventos:")
        for event in detail.events:
            print(
                f"  {event.sequence}. {event.event_type} | {event.actor} | "
                f"{event.created_at.isoformat()} | {_bounded_json(event.payload)}"
            )
        return
    print_audit_usage()


def _bounded_json(payload) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(rendered) <= MAX_EVENT_PAYLOAD_CHARS:
        return rendered
    return rendered[:MAX_EVENT_PAYLOAD_CHARS] + "... [payload truncado para CLI]"
