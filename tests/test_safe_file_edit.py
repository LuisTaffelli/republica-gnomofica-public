from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import core.actions.file_edit as file_edit_module
from core.actions import (
    ActionIntent,
    EffectKind,
    FileEditScope,
    FileEditValidationError,
    build_file_edit_coordinator,
    prepare_text_patch,
)
from core.audit import AuditEvent, AuditEventType, AuditService
from core.audit import SQLiteAuditRepository
from core.conversation import SessionService, SQLiteConversationRepository
from core.database import (
    SQLiteDatabase,
    initialize_audit_database,
    initialize_conversation_database,
)


class RecordingAuditRepository:
    def __init__(self, fail_on: AuditEventType | None = None):
        self.events: list[AuditEvent] = []
        self.fail_on = fail_on

    def append_event(self, event: AuditEvent) -> AuditEvent:
        if event.event_type is self.fail_on:
            raise OSError(f"audit failure: {event.event_type.value}")
        stored = replace(event, sequence=len(self.events) + 1)
        self.events.append(stored)
        return stored

    def list_events(self, intent_id: str) -> list[AuditEvent]:
        return [event for event in self.events if event.intent_id == intent_id]


def setup_edit(tmp_path, *, content="hola mundo\n", repository=None):
    target = tmp_path / "target.txt"
    target.write_bytes(content.encode("utf-8"))
    scope = FileEditScope(target.resolve())
    repository = repository or RecordingAuditRepository()
    coordinator = build_file_edit_coordinator(
        scope=scope,
        audit=AuditService(repository),
        recovery_root=tmp_path / "recoveries",
    )
    plan = prepare_text_patch(
        scope=scope,
        target=target.resolve(),
        expected_text="mundo",
        replacement_text="República",
        requested_by="user",
        profile_name="miku",
        session_id="session-1",
        message_id="message-1",
    )
    return target, scope, repository, coordinator, plan


def event_types(repository, intent):
    return [event.event_type for event in repository.list_events(intent.id)]


def test_without_durable_proposal_there_is_no_write(tmp_path):
    repository = RecordingAuditRepository(fail_on=AuditEventType.ACTION_PROPOSED)
    target, _, _, coordinator, plan = setup_edit(tmp_path, repository=repository)
    with pytest.raises(OSError):
        coordinator.propose(plan.intent)
    assert target.read_text(encoding="utf-8") == "hola mundo\n"


def test_without_approval_there_is_no_write(tmp_path):
    target, _, repository, coordinator, plan = setup_edit(tmp_path)
    coordinator.propose(plan.intent)
    assert target.read_text(encoding="utf-8") == "hola mundo\n"
    assert event_types(repository, plan.intent) == [AuditEventType.ACTION_PROPOSED]


def test_human_approval_writes_once_with_hash_evidence_and_replay_is_blocked(
    tmp_path, monkeypatch
):
    target, _, repository, coordinator, plan = setup_edit(tmp_path)
    replacements = 0
    original_replace = file_edit_module.os.replace

    def tracked_replace(source, destination):
        nonlocal replacements
        if Path(destination) == target:
            replacements += 1
        return original_replace(source, destination)

    monkeypatch.setattr(file_edit_module.os, "replace", tracked_replace)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "succeeded"
    assert target.read_text(encoding="utf-8") == "hola República\n"
    assert replacements == 1
    assert coordinator.approve(plan.intent).state == "not_pending"
    assert replacements == 1
    evidence = outcome.effect_result.evidence[0]
    assert evidence["sha256_before"] == hashlib.sha256(b"hola mundo\n").hexdigest()
    assert evidence["sha256_after"] == hashlib.sha256(
        "hola República\n".encode("utf-8")
    ).hexdigest()
    assert evidence["target"] == str(target.resolve())
    assert evidence["write_strategy"] == "same_directory_temp_then_os_replace"
    assert outcome.effect_result.reversible is False
    assert outcome.effect_result.reversal_reference is None
    assert event_types(repository, plan.intent)[-1] is AuditEventType.EXECUTION_SUCCEEDED


@pytest.mark.parametrize(
    "failure_event",
    [AuditEventType.ACTION_APPROVED, AuditEventType.EXECUTION_STARTED],
)
def test_audit_failure_before_effect_prevents_real_write(tmp_path, failure_event):
    repository = RecordingAuditRepository(fail_on=failure_event)
    target, _, _, coordinator, plan = setup_edit(tmp_path, repository=repository)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "audit_failed"
    assert target.read_text(encoding="utf-8") == "hola mundo\n"


def test_audit_failure_after_effect_does_not_retry_the_write(tmp_path, monkeypatch):
    repository = RecordingAuditRepository(fail_on=AuditEventType.EXECUTION_SUCCEEDED)
    target, _, _, coordinator, plan = setup_edit(tmp_path, repository=repository)
    replacements = 0
    original_replace = file_edit_module.os.replace

    def tracked_replace(source, destination):
        nonlocal replacements
        if Path(destination) == target:
            replacements += 1
        return original_replace(source, destination)

    monkeypatch.setattr(file_edit_module.os, "replace", tracked_replace)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "audit_failed_after_effect"
    assert outcome.effect_may_have_occurred
    assert target.read_text(encoding="utf-8") == "hola República\n"
    assert replacements == 1
    assert coordinator.approve(plan.intent).state == "not_pending"
    assert replacements == 1


def test_file_changed_after_proposal_blocks_execution_with_failure_evidence(tmp_path):
    target, _, repository, coordinator, plan = setup_edit(tmp_path)
    coordinator.propose(plan.intent)
    target.write_text("cambio concurrente\n", encoding="utf-8")
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "failed"
    assert target.read_text(encoding="utf-8") == "cambio concurrente\n"
    evidence = outcome.effect_result.evidence[0]
    assert evidence["write_performed"] is False
    assert evidence["sha256_expected_before"] != evidence["sha256_actual_before"]
    assert event_types(repository, plan.intent)[-1] is AuditEventType.EXECUTION_FAILED


def test_second_precondition_check_blocks_change_after_temp_is_prepared(
    tmp_path, monkeypatch
):
    target, _, _, coordinator, plan = setup_edit(tmp_path)
    original_write_temp = file_edit_module._ApplyTextPatchCapability._write_temp

    def change_after_temp(target_path, content):
        temp_path = original_write_temp(target_path, content)
        target_path.write_bytes(b"late concurrent change\n")
        return temp_path

    monkeypatch.setattr(
        file_edit_module._ApplyTextPatchCapability,
        "_write_temp",
        staticmethod(change_after_temp),
    )
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "failed"
    assert target.read_bytes() == b"late concurrent change\n"
    assert outcome.effect_result.evidence[0]["write_performed"] is False


def test_os_replace_failure_preserves_original_and_reports_no_write(tmp_path, monkeypatch):
    target, _, _, coordinator, plan = setup_edit(tmp_path)
    original_replace = file_edit_module.os.replace

    def fail_replace(source, destination):
        if Path(destination) == target:
            raise OSError("replace unavailable")
        return original_replace(source, destination)

    monkeypatch.setattr(file_edit_module.os, "replace", fail_replace)
    coordinator.propose(plan.intent)
    outcome = coordinator.approve(plan.intent)
    assert outcome.state == "failed"
    assert target.read_text(encoding="utf-8") == "hola mundo\n"
    assert outcome.effect_result.evidence[0]["write_performed"] is False
    assert "replace unavailable" in outcome.effect_result.error


def test_target_outside_exact_file_scope_does_not_write(tmp_path):
    target, scope, _, coordinator, _ = setup_edit(tmp_path)
    sibling = tmp_path / "sibling.txt"
    sibling.write_text("hola mundo\n", encoding="utf-8")
    with pytest.raises(FileEditValidationError):
        prepare_text_patch(
            scope=scope,
            target=sibling.resolve(),
            expected_text="mundo",
            replacement_text="otro",
            requested_by="user",
            profile_name="miku",
        )
    assert sibling.read_text(encoding="utf-8") == "hola mundo\n"
    assert coordinator.pending_intent("session-1") is None
    assert target.read_text(encoding="utf-8") == "hola mundo\n"


def test_path_traversal_is_rejected_even_when_it_resolves_to_authorized_file(tmp_path):
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    target = project / "target.txt"
    target.write_text("hola mundo", encoding="utf-8")
    scope = FileEditScope(target.resolve())
    traversal = nested / ".." / "target.txt"
    with pytest.raises(FileEditValidationError, match="traversal"):
        scope.resolve_for_write(traversal)


def test_symlink_escape_is_rejected_when_supported(tmp_path):
    target = tmp_path / "target.txt"
    outside = tmp_path / "outside.txt"
    alias = tmp_path / "alias.txt"
    target.write_text("hola mundo", encoding="utf-8")
    outside.write_text("hola mundo", encoding="utf-8")
    try:
        alias.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("El entorno no permite crear symlinks.")
    with pytest.raises(FileEditValidationError, match="symlink|reparse"):
        FileEditScope(alias.absolute())
    scope = FileEditScope(target.resolve())
    with pytest.raises(FileEditValidationError, match="symlink|reparse"):
        scope.resolve_for_write(alias.absolute())
    assert outside.read_text(encoding="utf-8") == "hola mundo"


def test_missing_file_is_rejected_before_an_intent_exists(tmp_path):
    with pytest.raises(FileEditValidationError, match="no existe"):
        FileEditScope((tmp_path / "missing.txt").resolve())


def test_non_utf8_file_is_rejected_explicitly(tmp_path):
    target = tmp_path / "binary.txt"
    target.write_bytes(b"\xff\xfe\x00")
    scope = FileEditScope(target.resolve())
    with pytest.raises(FileEditValidationError, match="UTF-8"):
        prepare_text_patch(
            scope=scope,
            target=target.resolve(),
            expected_text="x",
            replacement_text="y",
            requested_by="user",
            profile_name="miku",
        )


def test_patch_fingerprint_changes_with_replacement_and_approval_a_cannot_apply_b(tmp_path):
    target, scope, _, coordinator, plan_a = setup_edit(tmp_path)
    plan_b = prepare_text_patch(
        scope=scope,
        target=target.resolve(),
        expected_text="mundo",
        replacement_text="otro cambio",
        requested_by="user",
        profile_name="miku",
        session_id="session-1",
    )
    assert plan_a.intent.fingerprint != plan_b.intent.fingerprint
    coordinator.propose(plan_a.intent)
    coordinator.propose(plan_b.intent)
    assert coordinator.approve(plan_a.intent).state == "not_pending"
    assert target.read_text(encoding="utf-8") == "hola mundo\n"
    assert coordinator.approve(plan_b.intent).state == "succeeded"
    assert target.read_text(encoding="utf-8") == "hola otro cambio\n"


def test_real_capability_revalidates_closed_contract_before_audit(tmp_path):
    target, _, repository, coordinator, plan = setup_edit(tmp_path)
    invalid = ActionIntent(
        capability=plan.intent.capability,
        operation=plan.intent.operation,
        parameters={**plan.intent.parameters, "unexpected": "model supplied"},
        effect_kind=EffectKind.EFFECTFUL,
        target=plan.intent.target,
        requested_by="model",
        profile_name="miku",
        session_id="session-1",
    )
    with pytest.raises(FileEditValidationError, match="Parámetros inválidos"):
        coordinator.propose(invalid)
    assert repository.events == []
    assert target.read_text(encoding="utf-8") == "hola mundo\n"


def test_patch_contents_are_redacted_from_audit_but_fingerprinted(tmp_path):
    _, _, repository, coordinator, plan = setup_edit(tmp_path)
    coordinator.propose(plan.intent)
    payload = repository.events[0].payload["parameters"]
    assert payload["expected_text"] == "[REDACTED]"
    assert payload["replacement_text"] == "[REDACTED]"
    assert payload["expected_sha256"] == plan.intent.parameters["expected_sha256"]


def test_human_presentation_contains_target_effect_and_readable_diff(tmp_path):
    target, _, _, _, plan = setup_edit(tmp_path)
    assert str(target.resolve()) in plan.presentation
    assert "acción con efecto" in plan.presentation
    assert "-hola mundo" in plan.presentation
    assert "+hola República" in plan.presentation


def test_long_diff_is_truncated_only_in_presentation_not_in_intent(tmp_path):
    target = tmp_path / "long.txt"
    expected = "x" * 10_000
    replacement = "y" * 10_000
    target.write_bytes(expected.encode("utf-8"))
    scope = FileEditScope(target.resolve())
    plan = prepare_text_patch(
        scope=scope,
        target=target.resolve(),
        expected_text=expected,
        replacement_text=replacement,
        requested_by="user",
        profile_name="miku",
    )
    assert "diff truncado" in plan.presentation
    assert plan.intent.parameters["expected_text"] == expected
    assert plan.intent.parameters["replacement_text"] == replacement


def test_no_public_file_edit_api_exposes_execute_or_raw_write():
    public_members = {
        name: value
        for name, value in vars(file_edit_module).items()
        if not name.startswith("_")
    }
    assert "ApplyTextPatchCapability" not in public_members
    assert not any(
        inspect.isclass(value) and "execute" in value.__dict__
        for value in public_members.values()
    )


def test_laboral_remains_read_only_after_real_file_edit_exists():
    from gnomos.laboral import LaboralCapabilities

    assert not hasattr(LaboralCapabilities, "write_file")
    assert not hasattr(LaboralCapabilities, "apply_text_patch")


def test_explicit_miku_cli_flow_persists_conversation_and_audit(tmp_path, monkeypatch):
    from cli import miku_cli

    target = tmp_path / "target.txt"
    target.write_bytes(b"hola mundo\n")
    conversation_database = SQLiteDatabase(tmp_path / "conversations.db")
    audit_database = SQLiteDatabase(tmp_path / "audit.db")
    initialize_conversation_database(conversation_database)
    initialize_audit_database(audit_database)
    conversation_repository = SQLiteConversationRepository(conversation_database)
    audit_repository = SQLiteAuditRepository(audit_database)
    session = SessionService(conversation_repository).create(
        profile_name="miku", mode="action", memory_policy="disabled"
    )

    answers = iter(["mundo", "República", "sí"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        miku_cli, "SQLiteAuditRepository", lambda: audit_repository
    )
    miku_cli.run_file_edit(
        session=session,
        repository=conversation_repository,
        target=str(target.resolve()),
        recovery_root=tmp_path / "recoveries",
    )
    restored = conversation_repository.load_session(session.id)
    assert restored.mode == "action"
    assert restored.memory_policy == "disabled"
    assert restored.closed_at is not None
    assert [message.metadata.get("kind") for message in restored.messages] == [
        "file_edit_request", "action_proposal", "action_authority", "action_result"
    ]
    assert target.read_text(encoding="utf-8") == "hola República\n"
    with audit_database.connect() as connection:
        types = [row[0] for row in connection.execute(
            "SELECT event_type FROM action_audit_events ORDER BY sequence"
        )]
    assert types == [
        "ACTION_PROPOSED", "ACTION_APPROVED", "EXECUTION_STARTED",
        "EXECUTION_SUCCEEDED",
    ]


@pytest.mark.parametrize("denial", ("no", "cancelar"))
def test_explicit_miku_cli_human_denial_keeps_file_unchanged(
    tmp_path, monkeypatch, denial,
):
    from cli import miku_cli

    target = tmp_path / "target.txt"
    target.write_bytes(b"hola mundo\n")
    conversation_database = SQLiteDatabase(tmp_path / "conversations.db")
    audit_database = SQLiteDatabase(tmp_path / "audit.db")
    initialize_conversation_database(conversation_database)
    initialize_audit_database(audit_database)
    conversation_repository = SQLiteConversationRepository(conversation_database)
    audit_repository = SQLiteAuditRepository(audit_database)
    session = SessionService(conversation_repository).create(
        profile_name="miku", mode="action", memory_policy="disabled"
    )
    answers = iter(["mundo", "República", denial])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(miku_cli, "SQLiteAuditRepository", lambda: audit_repository)

    miku_cli.run_file_edit(
        session=session,
        repository=conversation_repository,
        target=str(target.resolve()),
        recovery_root=tmp_path / "recoveries",
    )

    assert target.read_text(encoding="utf-8") == "hola mundo\n"
    with audit_database.connect() as connection:
        types = [row[0] for row in connection.execute(
            "SELECT event_type FROM action_audit_events ORDER BY sequence"
        )]
    assert types == ["ACTION_PROPOSED", "ACTION_DENIED"]
