from __future__ import annotations

from gnomos.archive.archive import (
    backup_database,
    create_note,
    delete_note,
    edit_note,
    export_notes,
    get_note,
    get_related_notes,
    get_stats,
    get_timeline,
    health_check,
    import_notes,
    link_notes,
    list_archived_notes,
    list_by_tag,
    list_notes,
    recall_note,
    restore_note,
    search_notes,
)


def print_note(note) -> None:
    note_id, title, content, tags, created_at = note

    print(f"\n# [{note_id}] {title}\n")
    print(content)
    print(f"\nTags: {tags or 'sin tags'}")
    print(f"Creada: {created_at}")


def print_archive_usage() -> None:
    print("Uso:")
    print("  archive health")
    print("  archive note")
    print("  archive search <texto>")
    print("  archive list")
    print("  archive show <id>")
    print("  archive edit <id>")
    print("  archive delete <id>")
    print("  archive recall")
    print("  archive export")
    print("  archive import")
    print("  archive tag <tag>")
    print("  archive archived")
    print("  archive restore <id>")
    print("  archive stats")
    print("  archive backup")
    print("  archive link <id1> <id2> [motivo]")
    print("  archive related <id>")
    print("  archive timeline [--full]")


def handle_archive_command(args: list[str]) -> None:
    if not args:
        print_archive_usage()
        return

    command = args[0]
    command_args = args[1:]

    if command == "note":
        title = input("Título: ").strip()
        content = input("Contenido: ").strip()
        tags = input("Tags: ").strip()

        if not title or not content:
            print("🧙 GNOMO-ARCHIVE: Título y contenido son obligatorios.")
            return

        create_note(title, content, tags)
        print("🧙 GNOMO-ARCHIVE: Nota almacenada.")

    elif command == "search":
        if not command_args:
            print("Uso: archive search <texto>")
            return

        query = " ".join(command_args)
        results = search_notes(query)

        print("Buscando:", query)

        if not results:
            print("No se encontró nada en la biblioteca de la República.")
            return

        for note_id, title, content, tags in results:
            preview = content[:120]
            if len(content) > 120:
                preview += "..."

            print(f"\n[{note_id}] {title}")
            print(f"Tags: {tags or 'sin tags'}")
            print(preview)

    elif command == "list":
        results = list_notes()

        if not results:
            print("🧙 GNOMO-ARCHIVE: No hay notas almacenadas.")
            return

        for note_id, title, tags, created_at in results:
            print(
                f"[{note_id}] {title} | "
                f"{tags or 'sin tags'} | {created_at}"
            )

    elif command == "show":
        if not command_args:
            print("Uso: archive show <id>")
            return

        note = get_note(command_args[0])

        if not note:
            print("🧙 GNOMO-ARCHIVE: No encontré esa nota.")
            return

        print_note(note)

    elif command == "edit":
        if not command_args:
            print("Uso: archive edit <id>")
            return

        note_id = command_args[0]
        note = get_note(note_id)

        if not note:
            print("🧙 GNOMO-ARCHIVE: No encontré esa nota.")
            return

        print("Nota actual:")
        print_note(note)
        print("\nDejá vacío cualquier campo que no quieras cambiar.")

        title = input("Nuevo título: ")
        content = input("Nuevo contenido: ")
        tags = input("Nuevos tags: ")

        if edit_note(note_id, title, content, tags):
            print("🧙 GNOMO-ARCHIVE: Nota editada.")
        else:
            print("🧙 GNOMO-ARCHIVE: No pude editar la nota.")

    elif command == "delete":
        if not command_args:
            print("Uso: archive delete <id>")
            return

        note_id = command_args[0]
        note = get_note(note_id)

        if not note:
            print("🧙 GNOMO-ARCHIVE: No encontré esa nota.")
            return

        print("Vas a archivar esta nota:")
        print_note(note)

        confirm = input("\nConfirmar soft delete? (s/n): ").strip().lower()

        if confirm != "s":
            print("Operación cancelada.")
            return

        if delete_note(note_id):
            print("🧙 GNOMO-ARCHIVE: Nota archivada. No fue destruida.")
        else:
            print("🧙 GNOMO-ARCHIVE: No pude archivar la nota.")

    elif command == "recall":
        note = recall_note()

        if not note:
            print("🧙 GNOMO-ARCHIVE: No hay recuerdos todavía.")
            return

        print("🧙 GNOMO-ARCHIVE recuerda:")
        print_note(note)

    elif command == "export":
        path = export_notes()
        print(f"🧙 GNOMO-ARCHIVE: Export listo en {path}")

    elif command == "import":
        count = import_notes()
        print(f"🧙 GNOMO-ARCHIVE: Importadas {count} notas.")

    elif command == "tag":
        if not command_args:
            print("Uso: archive tag <tag>")
            return

        tag = " ".join(command_args)
        results = list_by_tag(tag)

        if not results:
            print("No encontré notas con ese tag.")
            return

        for note_id, title, tags, created_at in results:
            print(
                f"[{note_id}] {title} | "
                f"{tags or 'sin tags'} | {created_at}"
            )

    elif command == "archived":
        results = list_archived_notes()

        if not results:
            print("🧙 GNOMO-ARCHIVE: No hay notas archivadas.")
            return

        for note_id, title, tags, created_at, deleted_at in results:
            print(
                f"[{note_id}] {title} | "
                f"{tags or 'sin tags'} | "
                f"creada: {created_at} | "
                f"archivada: {deleted_at}"
            )

    elif command == "restore":
        if not command_args:
            print("Uso: archive restore <id>")
            return

        if restore_note(command_args[0]):
            print("🧙 GNOMO-ARCHIVE: Nota restaurada.")
        else:
            print("🧙 GNOMO-ARCHIVE: No encontré esa nota archivada.")

    elif command == "stats":
        stats = get_stats()

        print("\n🧙 GNOMO-ARCHIVE — Estado de la biblioteca\n")
        print(f"Notas activas: {stats['active_count']}")
        print(f"Notas archivadas: {stats['archived_count']}")
        print(f"Total histórico: {stats['total_count']}")

        print(
            "Tags detectados: "
            + (
                ", ".join(stats["tags"])
                if stats["tags"]
                else "ninguno"
            )
        )

        if stats["last_note"]:
            title, created_at = stats["last_note"]
            print(f"Última nota: {title} | {created_at}")
        else:
            print("Última nota: ninguna")

    elif command == "backup":
        path = backup_database()

        if not path:
            print("🧙 GNOMO-ARCHIVE: No encontré la base de datos.")
            return

        print(f"🧙 GNOMO-ARCHIVE: Backup creado en {path}")

    elif command == "health":
        health = health_check()

        print("\n🛡️ GNOMO-ARCHIVE HEALTH REPORT\n")
        print(
            "Base de datos:",
            "✅ OK" if health["database_exists"] else "❌ FALLO",
        )
        print(
            "Tabla notes:",
            "✅ OK" if health["notes_table_exists"] else "❌ FALLO",
        )
        print(
            "Soft delete:",
            "✅ OK" if health["deleted_at_exists"] else "❌ FALLO",
        )
        print(f"Notas totales: {health['total_notes']}")
        print(
            "Último backup:",
            health["last_backup"] or "ninguno",
        )

    elif command == "link":
        if len(command_args) < 2:
            print("Uso: archive link <id1> <id2> [motivo]")
            return

        from_note_id = command_args[0]
        to_note_id = command_args[1]
        reason = " ".join(command_args[2:])

        ok, message = link_notes(
            from_note_id,
            to_note_id,
            reason,
        )

        if ok:
            print("🧙 GNOMO-ARCHIVE: Notas vinculadas.")
        else:
            print(f"🧙 GNOMO-ARCHIVE: {message}")

    elif command == "related":
        if not command_args:
            print("Uso: archive related <id>")
            return

        note_id = command_args[0]
        note = get_note(note_id)

        if not note:
            print("🧙 GNOMO-ARCHIVE: No encontré esa nota.")
            return

        related = get_related_notes(note_id)

        if not related:
            print("🧙 GNOMO-ARCHIVE: Esta nota no tiene vínculos todavía.")
            return

        print("🧙 GNOMO-ARCHIVE: Relaciones encontradas:\n")

        for related_id, title, tags, reason, created_at in related:
            print(f"→ [{related_id}] {title}")
            print(f"  Tags: {tags or 'sin tags'}")

            if reason:
                print(f"  Motivo: {reason}")

            print(f"  Vinculada: {created_at}\n")

    elif command == "timeline":
        unknown_options = [
            value
            for value in command_args
            if value != "--full"
        ]

        if unknown_options:
            print("Uso: archive timeline [--full]")
            return

        full = "--full" in command_args
        timeline = get_timeline(full=full)

        if not timeline:
            print("🧙 GNOMO-ARCHIVE: No hay eventos registrados.")
            return

        current_day = None
        print("\n📜 CRÓNICA DE LA REPÚBLICA GNOMÓFICA\n")

        for row in timeline:
            if full:
                note_id, title, content, tags, created_at = row
            else:
                note_id, title, tags, created_at = row

            day = created_at[:10]

            if day != current_day:
                current_day = day
                print(f"\n=== {day} ===\n")

            print(f"[{note_id}] {title} | {tags or 'sin tags'}")

            if full:
                print(content.strip())
                print()

    else:
        print(f"Comando archive desconocido: {command}")
        print_archive_usage()
