from __future__ import annotations

from core.memory import SQLiteMemoryRepository, deduplicate_active_memories


def print_memory_usage() -> None:
    print("Uso:")
    print("  memory dedupe --dry-run")
    print("  memory dedupe --apply")


def handle_memory_command(args: list[str]) -> None:
    if not args or args[0] != "dedupe":
        print_memory_usage()
        return
    flags = args[1:]
    if len(flags) != 1 or flags[0] not in {"--dry-run", "--apply"}:
        raise ValueError("memory dedupe requiere exactamente --dry-run o --apply.")

    apply = flags[0] == "--apply"
    duplicates = deduplicate_active_memories(
        SQLiteMemoryRepository(), "miku", apply=apply
    )
    if not duplicates:
        print("No se encontraron recuerdos active equivalentes.")
        return

    mode = "aplicado" if apply else "dry-run"
    print(f"Dedupe de memoria ({mode}):")
    for item in duplicates:
        action = "retirado" if apply else "retiraría"
        print(f"- canonical: {item.canonical.id} | {item.canonical.content}")
        print(f"  {action}: {item.duplicate.id} | {item.duplicate.content}")
    if not apply:
        print("Sin cambios. Usá --apply para retirar los duplicados listados.")
