from core.database import get_connection
from core.paths import DATA_DIR
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import random
import json
import shutil

from gnomos.archive.reader import SQLiteArchiveReader

# LOCAL_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

def now_local():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Meter import después por si cambio de pais o alguna cosa rara
# def now_local():
#     return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")

def create_note(title, content, tags=""):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO notes(title, content, tags, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (title, content, tags, now_local())
    )

    conn.commit()
    conn.close()


def search_notes(query):
    rows = SQLiteArchiveReader.from_connection_factory(
        get_connection, source=DATA_DIR / "swarm.db"
    ).search_notes(query)
    return [(row.note_id, row.title, row.content, row.tags) for row in rows]


def list_notes():
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT id, title, tags, created_at
        FROM notes
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC
        """
    )

    results = cursor.fetchall()
    conn.close()

    return results


def get_note(note_id):
    row = SQLiteArchiveReader.from_connection_factory(
        get_connection, source=DATA_DIR / "swarm.db"
    ).read_note(note_id)
    if row is None:
        return None
    return row.note_id, row.title, row.content, row.tags, row.created_at


def edit_note(note_id, title=None, content=None, tags=None):
    note = get_note(note_id)

    if not note:
        return False

    current_id, current_title, current_content, current_tags, created_at = note

    new_title = title if title else current_title
    new_content = content if content else current_content
    new_tags = tags if tags else current_tags

    conn = get_connection()

    conn.execute(
        """
        UPDATE notes
        SET title = ?, content = ?, tags = ?
        WHERE id = ?
        AND deleted_at IS NULL
        """,
        (new_title, new_content, new_tags, note_id)
    )

    conn.commit()
    conn.close()

    return True


def delete_note(note_id):
    conn = get_connection()

    cursor = conn.execute(
        """
        UPDATE notes
        SET deleted_at = ?
        WHERE id = ?
        AND deleted_at IS NULL
        """,
        (now_local(), note_id)
    )

    conn.commit()
    changed = cursor.rowcount
    conn.close()

    return changed > 0


def recall_note():
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT id, title, content, tags, created_at
        FROM notes
        WHERE deleted_at IS NULL
        """
    )

    results = cursor.fetchall()
    conn.close()

    if not results:
        return None

    return random.choice(results)

def restore_note(note_id):
    conn = get_connection()

    cursor = conn.execute(
        """
        UPDATE notes
        SET deleted_at = NULL
        WHERE id = ?
        AND deleted_at IS NOT NULL
        """,
        (note_id,)
    )

    conn.commit()
    changed = cursor.rowcount
    conn.close()

    return changed > 0

def export_notes(path=None):
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT id, title, content, tags, created_at
        FROM notes
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC
        """
    )

    rows = cursor.fetchall()
    conn.close()

    notes = []

    for note_id, title, content, tags, created_at in rows:
        notes.append({
            "id": note_id,
            "title": title,
            "content": content,
            "tags": tags,
            "created_at": created_at,
        })

    export_path = (
        Path(path)
        if path is not None
        else DATA_DIR / "export_notes.json"
    )
    export_path.parent.mkdir(parents=True, exist_ok=True)

    with open(export_path, "w", encoding="utf-8") as file:
        json.dump(notes, file, ensure_ascii=False, indent=2)

    return export_path


def import_notes(path=None):
    import_path = (
        Path(path)
        if path is not None
        else DATA_DIR / "export_notes.json"
    )

    if not import_path.exists():
        return 0

    with open(import_path, "r", encoding="utf-8") as file:
        notes = json.load(file)

    conn = get_connection()

    imported = 0

    for note in notes:
        title = note.get("title")
        content = note.get("content")
        tags = note.get("tags", "")

        if not title or not content:
            continue

        conn.execute(
            """
            INSERT INTO notes(title, content, tags)
            VALUES (?, ?, ?)
            """,
            (title, content, tags)
        )

        imported += 1

    conn.commit()
    conn.close()

    return imported


def list_by_tag(tag):
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT id, title, tags, created_at
        FROM notes
        WHERE deleted_at IS NULL
        AND tags LIKE ?
        ORDER BY created_at DESC
        """,
        (f"%{tag}%",)
    )

    results = cursor.fetchall()
    conn.close()

    return results

def list_archived_notes():
    conn = get_connection()

    cursor = conn.execute(
        """
        SELECT id, title, tags, created_at, deleted_at
        FROM notes
        WHERE deleted_at IS NOT NULL
        ORDER BY deleted_at DESC
        """
    )

    results = cursor.fetchall()
    conn.close()

    return results

def get_stats():
    conn = get_connection()

    active_count = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL"
    ).fetchone()[0]

    archived_count = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]

    total_count = conn.execute(
        "SELECT COUNT(*) FROM notes"
    ).fetchone()[0]

    last_note = conn.execute(
        """
        SELECT title, created_at
        FROM notes
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()

    tags_rows = conn.execute(
        """
        SELECT tags
        FROM notes
        WHERE deleted_at IS NULL
        AND tags IS NOT NULL
        AND tags != ''
        """
    ).fetchall()

    conn.close()

    tags = set()

    for row in tags_rows:
        raw_tags = row[0]
        for tag in raw_tags.split(","):
            clean_tag = tag.strip()
            if clean_tag:
                tags.add(clean_tag)

    return {
        "active_count": active_count,
        "archived_count": archived_count,
        "total_count": total_count,
        "last_note": last_note,
        "tags": sorted(tags),
    }

def backup_database():
    source = DATA_DIR / "swarm.db"

    if not source.exists():
        return None

    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = backup_dir / f"swarm_backup_{timestamp}.db"

    shutil.copy2(source, destination)

    return destination

def health_check():
    report = {
        "database_exists": False,
        "notes_table_exists": False,
        "deleted_at_exists": False,
        "total_notes": 0,
        "last_backup": None,
    }

    db_path = DATA_DIR / "swarm.db"

    if not db_path.exists():
        return report

    report["database_exists"] = True

    conn = get_connection()

    # Verificar tabla notes
    cursor = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name='notes'
    """)

    if cursor.fetchone():
        report["notes_table_exists"] = True

    if not report["notes_table_exists"]:
        conn.close()
        return report

    # Verificar columnas
    cursor = conn.execute("PRAGMA table_info(notes)")
    columns = cursor.fetchall()

    column_names = [column[1] for column in columns]

    if "deleted_at" in column_names:
        report["deleted_at_exists"] = True

    # Contar notas
    cursor = conn.execute(
        """
        SELECT COUNT(*)
        FROM notes
        """
    )

    report["total_notes"] = cursor.fetchone()[0]

    conn.close()

    # Buscar último backup
    backup_dir = DATA_DIR / "backups"

    if backup_dir.exists():
        backups = list(backup_dir.glob("*.db"))

        if backups:
            latest = max(backups, key=lambda f: f.stat().st_mtime)

            report["last_backup"] = latest.name

    return report

def link_notes(from_note_id, to_note_id, reason=""):
    if from_note_id == to_note_id:
        return False, "No podés vincular una nota consigo misma."

    from_note = get_note(from_note_id)
    to_note = get_note(to_note_id)

    if not from_note or not to_note:
        return False, "Una de las notas no existe o está archivada."

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO note_links(from_note_id, to_note_id, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (from_note_id, to_note_id, reason, now_local())
        )

        conn.commit()
        conn.close()
        return True, "Notas vinculadas."

    except Exception:
        conn.close()
        return False, "Ese vínculo ya existe."


def get_related_notes(note_id):
    rows = SQLiteArchiveReader.from_connection_factory(
        get_connection, source=DATA_DIR / "swarm.db"
    ).list_links(note_id)
    if rows is None:
        return None
    return [
        (row.linked_note_id, row.title, row.tags, row.reason, row.linked_at)
        for row in rows
    ]

def get_timeline(full=False):
    conn = get_connection()

    if full:
        cursor = conn.execute(
            """
            SELECT id, title, content, tags, created_at
            FROM notes
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            """
        )
    else:
        cursor = conn.execute(
            """
            SELECT id, title, tags, created_at
            FROM notes
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            """
        )

    results = cursor.fetchall()
    conn.close()

    return results
