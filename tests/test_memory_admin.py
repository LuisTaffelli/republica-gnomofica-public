from __future__ import annotations

import pytest

from cli import memory_cli
from core.database import SQLiteDatabase, initialize_memory_database
from core.memory import (
    MemoryRecord,
    SQLiteMemoryRepository,
    deduplicate_active_memories,
)


def _repository(tmp_path):
    database = SQLiteDatabase(tmp_path / "memory.db")
    initialize_memory_database(database)
    return SQLiteMemoryRepository(database)


def test_dedupe_dry_run_then_apply_retires_only_duplicate(tmp_path):
    repository = _repository(tmp_path)
    canonical = MemoryRecord(
        id="a-canonical", profile_name="miku", category="personal_fact",
        content="Soy alérgico a la avellana", source_type="conversation",
        source_session_id="original-session", source_message_id="original-message",
    )
    duplicate = MemoryRecord(
        id="b-duplicate", profile_name="miku", category="personal_fact",
        content="SOY ALERGICO A LA AVELLANA!!!", source_type="conversation",
        source_session_id="duplicate-session", source_message_id="duplicate-message",
        created_at=canonical.created_at, updated_at=canonical.updated_at,
    )
    distinct = MemoryRecord(
        profile_name="miku", category="preference",
        content="Visitante prefiere café", source_type="conversation",
    )
    for item in (canonical, duplicate, distinct):
        repository.save(item)

    plan = deduplicate_active_memories(repository, "miku")
    assert [(item.canonical.id, item.duplicate.id) for item in plan] == [
        (canonical.id, duplicate.id)
    ]
    assert repository.get(duplicate.id).status == "active"

    applied = deduplicate_active_memories(repository, "miku", apply=True)
    assert applied == plan
    assert repository.get(canonical.id).status == "active"
    assert repository.get(duplicate.id).status == "retired"
    assert repository.get(duplicate.id).source_session_id == "duplicate-session"
    assert repository.get(distinct.id).status == "active"


def test_memory_cli_requires_explicit_mode_and_propagates_apply(monkeypatch, capsys):
    calls = []

    def fake_dedupe(repository, profile_name, *, apply=False):
        calls.append((profile_name, apply))
        return ()

    monkeypatch.setattr(memory_cli, "deduplicate_active_memories", fake_dedupe)
    monkeypatch.setattr(memory_cli, "SQLiteMemoryRepository", object)
    memory_cli.handle_memory_command(["dedupe", "--dry-run"])
    memory_cli.handle_memory_command(["dedupe", "--apply"])
    assert calls == [("miku", False), ("miku", True)]
    assert "No se encontraron" in capsys.readouterr().out
    with pytest.raises(ValueError, match="requiere exactamente"):
        memory_cli.handle_memory_command(["dedupe"])
