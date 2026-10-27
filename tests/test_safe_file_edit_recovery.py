from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import core.actions.file_edit as file_edit_module
from cli import miku_cli
from core.actions import FileEditScope, FileEditValidationError
from core.audit import SQLiteAuditRepository
from core.conversation import SessionService, SQLiteConversationRepository
from core.database import (
    SQLiteDatabase,
    initialize_audit_database,
    initialize_conversation_database,
)
from tests.test_safe_file_edit import RecordingAuditRepository, setup_edit


def _backup_directory(root: Path, reference: str) -> Path:
    prefix = "file-edit-backup:"
    assert reference.startswith(prefix)
    return root / reference.removeprefix(prefix)


def test_prepare_does_not_create_recovery_or_modify_target(tmp_path):
    target, _, _, _, _ = setup_edit(tmp_path)
    assert target.read_bytes() == b"hola mundo\n"
    assert not (tmp_path / "recoveries").exists()


def test_success_preserves_exact_original_bound_to_intent_hash_and_fingerprint(tmp_path):
    target, _, repository, coordinator, plan = setup_edit(tmp_path)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)

    evidence = outcome.effect_result.evidence[0]
    backup = _backup_directory(
        tmp_path / "recoveries", evidence["recovery_reference"]
    )
    original = (backup / "original.utf8").read_bytes()
    metadata = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))

    assert original == b"hola mundo\n"
    assert evidence["recovery_sha256"] == hashlib.sha256(original).hexdigest()
    assert metadata == {
        "version": 1,
        "intent_id": plan.intent.id,
        "fingerprint": plan.intent.fingerprint,
        "target": str(target.resolve()),
        "original_sha256": plan.intent.parameters["expected_sha256"],
        "original_bytes": len(original),
    }
    assert plan.intent.id in backup.name
    assert plan.intent.fingerprint in backup.name
    assert outcome.effect_result.reversible is False
    assert outcome.effect_result.reversal_reference is None
    assert "hola mundo" not in json.dumps(repository.events[-1].payload)


def test_pre_effect_audit_failure_creates_no_recovery(tmp_path):
    from core.audit import AuditEventType

    repository = RecordingAuditRepository(fail_on=AuditEventType.EXECUTION_STARTED)
    target, _, _, coordinator, plan = setup_edit(tmp_path, repository=repository)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)

    assert outcome.state == "audit_failed"
    assert target.read_bytes() == b"hola mundo\n"
    assert not (tmp_path / "recoveries").exists()


def test_target_replace_failure_keeps_original_and_identified_recovery(
    tmp_path, monkeypatch
):
    target, _, _, coordinator, plan = setup_edit(tmp_path)
    real_replace = file_edit_module.os.replace

    def fail_target_only(source, destination):
        if Path(destination) == target:
            raise OSError("target replace failed")
        return real_replace(source, destination)

    monkeypatch.setattr(file_edit_module.os, "replace", fail_target_only)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)

    assert outcome.state == "failed"
    assert target.read_bytes() == b"hola mundo\n"
    evidence = outcome.effect_result.evidence[0]
    backup = _backup_directory(
        tmp_path / "recoveries", evidence["recovery_reference"]
    )
    assert (backup / "original.utf8").read_bytes() == target.read_bytes()
    assert evidence["write_performed"] is False


def test_file_size_limit_is_checked_before_opening_content(tmp_path, monkeypatch):
    target = tmp_path / "oversized.txt"
    with target.open("wb") as stream:
        stream.truncate(file_edit_module.MAX_FILE_BYTES + 1)
    real_open = Path.open

    def reject_content_open(path, *args, **kwargs):
        if path == target:
            raise AssertionError("el contenido no debía abrirse")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_content_open)
    with pytest.raises(FileEditValidationError, match="supera el límite"):
        file_edit_module._read_bounded(target)


def test_symlink_in_target_ancestor_is_rejected_when_supported(tmp_path):
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    target = real_directory / "target.txt"
    target.write_text("hola", encoding="utf-8")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real_directory, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("El entorno no permite crear symlinks de directorio.")

    with pytest.raises(FileEditValidationError, match="ancestros"):
        FileEditScope(alias / "target.txt")


@pytest.mark.skipif(os.name != "nt", reason="La sintaxis ADS pertenece a Windows.")
def test_windows_ads_is_rejected_before_resolution(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("hola", encoding="utf-8")
    with pytest.raises(FileEditValidationError, match="stream alternativo"):
        FileEditScope(Path(f"{target}:secret"))


def test_truncated_cli_proposal_blocks_approval_until_full_diff_is_shown(
    tmp_path, monkeypatch, capsys
):
    expected = "x" * 10_000
    replacement = "y" * 10_000
    target = tmp_path / "long.txt"
    target.write_text(expected, encoding="utf-8")
    conversation_database = SQLiteDatabase(tmp_path / "conversations.db")
    audit_database = SQLiteDatabase(tmp_path / "audit.db")
    initialize_conversation_database(conversation_database)
    initialize_audit_database(audit_database)
    conversation_repository = SQLiteConversationRepository(conversation_database)
    audit_repository = SQLiteAuditRepository(audit_database)
    session = SessionService(conversation_repository).create(
        profile_name="miku", mode="action", memory_policy="disabled"
    )
    answers = iter([expected, replacement, "sí", "ver propuesta completa", "sí"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(miku_cli, "SQLiteAuditRepository", lambda: audit_repository)

    miku_cli.run_file_edit(
        session=session,
        repository=conversation_repository,
        target=str(target.resolve()),
        recovery_root=tmp_path / "recoveries",
    )

    output = capsys.readouterr().out
    assert "aprobación queda bloqueada" in output
    assert "No puedo aceptar la aprobación todavía" in output
    assert "La propuesta completa quedó presentada" in output
    assert target.read_text(encoding="utf-8") == replacement
    with audit_database.connect() as connection:
        approved = connection.execute(
            "SELECT COUNT(*) FROM action_audit_events "
            "WHERE event_type = 'ACTION_APPROVED'"
        ).fetchone()[0]
    assert approved == 1
