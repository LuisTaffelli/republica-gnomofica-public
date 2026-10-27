from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from cli import backup_cli
from core.backup import BackupError, BackupService
from core.audit import AuditQueryService, SQLiteAuditReader
from core.conversation import SQLiteConversationRepository
from core.database import (
    SQLiteDatabase,
    initialize_audit_database,
    initialize_conversation_database,
    initialize_memory_database,
)
from core.memory import SQLiteMemoryRepository
from gnomos.archive import SQLiteArchiveReader


def _archive(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                content TEXT NOT NULL, tags TEXT, created_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE note_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_note_id INTEGER NOT NULL, to_note_id INTEGER NOT NULL,
                reason TEXT, created_at TEXT,
                UNIQUE(from_note_id, to_note_id)
            );
            INSERT INTO notes(title, content, tags, created_at)
            VALUES ('Una nota', 'contenido archive', 'test', '2026-01-01');
            """
        )


def _sources(tmp_path: Path) -> dict[str, Path]:
    result = {
        "conversations": tmp_path / "active" / "conversations.db",
        "memory": tmp_path / "active" / "memory.db",
        "audit": tmp_path / "active" / "audit.db",
        "archive": tmp_path / "active" / "swarm.db",
    }
    result["conversations"].parent.mkdir()
    initialize_conversation_database(SQLiteDatabase(result["conversations"]))
    initialize_memory_database(SQLiteDatabase(result["memory"]))
    initialize_audit_database(SQLiteDatabase(result["audit"]))
    _archive(result["archive"])
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_contains_four_safe_sqlite_snapshots_and_complete_manifest(tmp_path):
    sources = _sources(tmp_path)
    # Mantener un writer WAL abierto prueba que no se realiza una copia ingenua del .db.
    active = sqlite3.connect(sources["conversations"])
    active.execute("PRAGMA journal_mode = WAL")
    active.execute(
        "INSERT INTO chat_sessions (id, profile_name, mode, memory_policy, "
        "created_at, updated_at, metadata_json) VALUES "
        "('active-session', 'miku', 'persistent', 'enabled', "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '{}')"
    )
    active.commit()
    try:
        result = BackupService(sources).create(tmp_path / "backup")
    finally:
        active.close()

    assert result.manifest.status == "complete"
    assert [item.domain for item in result.manifest.domains] == [
        "conversations", "memory", "audit", "archive"
    ]
    assert "no distributed atomic" in result.manifest.consistency
    assert "excluded" in result.manifest.action_backups
    for item in result.manifest.domains:
        path = Path(result.location) / item.backup_file
        assert item.status == "complete"
        assert item.size_bytes == path.stat().st_size
        assert item.sha256 == _sha256(path)
    restored_reader = SQLiteConversationRepository(
        SQLiteDatabase(Path(result.location) / "domains" / "conversations.db")
    )
    assert restored_reader.load_session("active-session").id == "active-session"
    assert BackupService(sources).verify(result.location).valid


def test_missing_domain_is_preserved_as_incomplete_and_restore_is_rejected(tmp_path):
    sources = _sources(tmp_path)
    sources["audit"].unlink()
    service = BackupService(sources)
    result = service.create(tmp_path / "incomplete")

    assert result.manifest.status == "incomplete"
    audit = next(item for item in result.manifest.domains if item.domain == "audit")
    assert audit.status == "failed"
    assert Path(result.location, "manifest.json").is_file()
    verification = service.verify(result.location)
    assert not verification.valid
    with pytest.raises(BackupError, match="incompleto"):
        service.restore(result.location, tmp_path / "restore")


def test_corrupted_domain_and_corrupted_manifest_are_detected(tmp_path):
    sources = _sources(tmp_path)
    service = BackupService(sources)
    first = service.create(tmp_path / "backup-corrupt-db")
    archive = Path(first.location) / "domains" / "swarm.db"
    archive.write_bytes(archive.read_bytes() + b"corruption")
    verification = service.verify(first.location)
    assert not verification.valid
    assert any("archive: tamaño" in error for error in verification.errors)

    second = service.create(tmp_path / "backup-corrupt-manifest")
    manifest_path = Path(second.location) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["domains"][0]["sha256"] = "not-a-hash"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verification = service.verify(second.location)
    assert not verification.valid
    assert verification.manifest is None
    assert "Manifiesto inválido" in verification.errors[0]


def test_restore_requires_new_destination_and_all_domains_remain_readable(tmp_path):
    sources = _sources(tmp_path)
    service = BackupService(sources)
    backup = service.create(tmp_path / "backup")
    destination = tmp_path / "restored"
    result = service.restore(backup.location, destination)

    assert {item.domain for item in result.domains} == set(sources)
    for item in result.domains:
        restored = destination / Path(item.logical_source).name
        assert _sha256(restored) == item.sha256

    assert SQLiteConversationRepository(
        SQLiteDatabase(destination / "conversations.db")
    ).list_sessions() == []
    memory = SQLiteMemoryRepository(SQLiteDatabase(destination / "memory.db"))
    assert memory.list("miku") == []  # leer no dispara extracción ni adquisición
    assert AuditQueryService(
        SQLiteAuditReader(destination / "audit.db")
    ).list_recent() == []  # consultar evidencia no reconstruye pending
    archive = SQLiteArchiveReader(destination / "swarm.db")
    assert archive.search_notes("archive")[0].title == "Una nota"

    with pytest.raises(BackupError, match="ya existe"):
        service.restore(backup.location, destination)
    with pytest.raises(BackupError, match="ya existe"):
        service.create(tmp_path / "backup")


def test_backup_service_does_not_touch_active_files(tmp_path):
    sources = _sources(tmp_path)
    before = {name: _sha256(path) for name, path in sources.items()}
    BackupService(sources).create(tmp_path / "backup")
    after = {name: _sha256(path) for name, path in sources.items()}
    assert after == before


def test_human_cli_exposes_create_verify_and_restore(tmp_path, monkeypatch, capsys):
    service = BackupService(_sources(tmp_path))
    monkeypatch.setattr(backup_cli, "BackupService", lambda: service)
    backup = tmp_path / "cli-backup"
    restored = tmp_path / "cli-restored"

    backup_cli.handle_backup_command(["create", str(backup)])
    backup_cli.handle_backup_command(["verify", str(backup)])
    backup_cli.handle_backup_command(["restore", str(backup), str(restored)])

    output = capsys.readouterr().out
    assert "Backup complete" in output
    assert "Backup válido" in output
    assert "Restauración verificada" in output
    assert (restored / "conversations.db").is_file()
