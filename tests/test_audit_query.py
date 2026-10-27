from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cli.audit_cli import handle_audit_command
from core.actions import (
    ActionCoordinator,
    ActionIntent,
    EffectKind,
    EffectResult,
    FileEditScope,
    build_file_edit_coordinator,
    prepare_text_patch,
)
from core.audit import (
    AuditEventType,
    AuditQueryError,
    AuditQueryService,
    AuditService,
    SQLiteAuditReader,
    SQLiteAuditRepository,
)
from core.database import SQLiteDatabase, initialize_audit_database


class CountingCapability:
    def __init__(self, *, success=True):
        self.executions = 0
        self.success = success

    def execute(self, intent):
        self.executions += 1
        return EffectResult(
            self.success,
            evidence=({"kind": "test", "hash": "abc"},),
            error=None if self.success else "fallo esperado",
        )


def _intent(intent_id: str, *, parameters=None) -> ActionIntent:
    return ActionIntent(
        id=intent_id,
        capability="test_effect",
        operation="test",
        parameters=parameters or {"value": 1},
        effect_kind=EffectKind.EFFECTFUL,
        target="test://safe-target",
        requested_by="user",
        profile_name="miku",
        session_id=intent_id,
    )


def _environment(tmp_path, intent_id="intent-1", *, success=True, parameters=None):
    database = SQLiteDatabase(tmp_path / "audit.db")
    initialize_audit_database(database)
    repository = SQLiteAuditRepository(database)
    capability = CountingCapability(success=success)
    coordinator = ActionCoordinator(
        audit=AuditService(repository), capabilities={"test_effect": capability}
    )
    intent = _intent(intent_id, parameters=parameters)
    query = AuditQueryService(SQLiteAuditReader(database.path))
    return database, repository, capability, coordinator, intent, query


def test_derived_proposed_approved_denied_and_cancelled_states(tmp_path):
    _, _, _, coordinator, proposed, query = _environment(tmp_path, "proposed")
    coordinator.propose(proposed)
    assert query.get(proposed.id).summary.state == "proposed"

    approved = _intent("approved")
    coordinator.propose(approved)
    coordinator.audit.append(
        intent_id=approved.id,
        event_type=AuditEventType.ACTION_APPROVED,
        actor="user",
        fingerprint=approved.fingerprint,
    )
    assert query.get(approved.id).summary.state == "approved"

    denied = _intent("denied")
    coordinator.propose(denied)
    coordinator.deny(denied)
    assert query.get(denied.id).summary.state == "denied"

    cancelled = _intent("cancelled")
    coordinator.propose(cancelled)
    coordinator.audit.append(
        intent_id=cancelled.id,
        event_type=AuditEventType.ACTION_CANCELLED,
        actor="republic",
        fingerprint=cancelled.fingerprint,
    )
    assert query.get(cancelled.id).summary.state == "cancelled"


def test_started_without_terminal_is_execution_unknown_not_success(tmp_path):
    _, _, capability, coordinator, intent, query = _environment(tmp_path)
    coordinator.propose(intent)
    coordinator.audit.append(
        intent_id=intent.id,
        event_type=AuditEventType.ACTION_APPROVED,
        actor="user",
        fingerprint=intent.fingerprint,
    )
    coordinator.audit.append(
        intent_id=intent.id,
        event_type=AuditEventType.EXECUTION_STARTED,
        actor="republic",
        fingerprint=intent.fingerprint,
    )

    detail = query.get(intent.id)
    assert detail.summary.state == "execution_unknown"
    assert detail.summary.phase == "executing"
    assert detail.summary.interrupted
    assert "no existe evidencia durable" in detail.explanation
    assert capability.executions == 0


@pytest.mark.parametrize(
    "success, expected_state, expected_error",
    [(True, "succeeded", None), (False, "failed", "fallo esperado")],
)
def test_terminal_success_and_failure_are_derived_with_evidence(
    tmp_path, success, expected_state, expected_error
):
    _, _, capability, coordinator, intent, query = _environment(
        tmp_path, success=success
    )
    coordinator.propose(intent)
    coordinator.approve(intent)
    detail = query.get(intent.id)
    assert detail.summary.state == expected_state
    assert detail.evidence == ({"kind": "test", "hash": "abc"},)
    assert detail.error == expected_error
    assert capability.executions == 1


def test_events_are_ordered_and_lookup_accepts_exact_fingerprint(tmp_path):
    _, _, _, coordinator, intent, query = _environment(tmp_path)
    coordinator.propose(intent)
    coordinator.approve(intent)
    detail = query.get(intent.fingerprint)
    assert detail.summary.intent_id == intent.id
    sequences = [event.sequence for event in detail.events]
    assert sequences == sorted(sequences)


def test_stored_redaction_is_preserved_by_query(tmp_path):
    _, _, _, coordinator, intent, query = _environment(
        tmp_path, parameters={"token": "secret-value", "visible": "safe"}
    )
    coordinator.propose(intent)
    detail = query.get(intent.id)
    parameters = detail.events[0].payload["parameters"]
    assert parameters == {"token": "[REDACTED]", "visible": "safe"}
    assert "secret-value" not in json.dumps(parameters)


def test_query_is_read_only_does_not_authorize_rebuild_pending_or_retry(tmp_path):
    database, repository, capability, coordinator, intent, query = _environment(tmp_path)
    coordinator.propose(intent)
    before = hashlib.sha256(database.path.read_bytes()).hexdigest()
    restarted = ActionCoordinator(
        audit=AuditService(repository), capabilities={"test_effect": capability}
    )

    assert query.list_recent()[0].state == "proposed"
    assert query.get(intent.id) is not None
    assert hashlib.sha256(database.path.read_bytes()).hexdigest() == before
    assert restarted.pending_intent(intent.session_id) is None
    assert restarted.approve(intent).state == "not_pending"
    assert capability.executions == 0
    assert not any(hasattr(query, name) for name in ("approve", "retry", "resume"))


def test_safe_edit_evidence_exposes_recovery_reference_without_content(tmp_path):
    database = SQLiteDatabase(tmp_path / "audit.db")
    initialize_audit_database(database)
    repository = SQLiteAuditRepository(database)
    target = tmp_path / "target.txt"
    target.write_text("contenido anterior único", encoding="utf-8")
    scope = FileEditScope(target)
    coordinator = build_file_edit_coordinator(
        scope=scope,
        audit=AuditService(repository),
        recovery_root=tmp_path / "recoveries",
    )
    plan = prepare_text_patch(
        scope=scope,
        target=target,
        expected_text="anterior",
        replacement_text="nuevo",
        requested_by="user",
        profile_name="miku",
        session_id="safe-edit",
    )
    coordinator.propose(plan.intent)
    coordinator.approve(plan.intent)

    detail = AuditQueryService(SQLiteAuditReader(database.path)).get(plan.intent.id)
    rendered = json.dumps(detail.evidence, ensure_ascii=False)
    assert "file-edit-backup:" in rendered
    assert plan.intent.parameters["expected_sha256"] in rendered
    assert "contenido anterior único" not in rendered


def test_invalid_sequence_is_reported_inconsistent_without_rewriting(tmp_path):
    database, _, _, coordinator, intent, query = _environment(tmp_path)
    coordinator.propose(intent)
    before_count = 1
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            INSERT INTO action_audit_events (
                id, intent_id, event_type, actor, fingerprint, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "invalid-start",
                intent.id,
                "EXECUTION_STARTED",
                "republic",
                intent.fingerprint,
                "{}",
                "2026-09-14T00:00:00+00:00",
            ),
        )
    detail = query.get(intent.id)
    assert detail.summary.state == "inconsistent"
    assert "Secuencia inválida" in detail.explanation
    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM action_audit_events"
        ).fetchone()[0] == before_count + 1


def test_cli_list_and_show_are_human_readable_and_do_not_offer_retry(tmp_path, capsys, monkeypatch):
    from functools import partial
    from main import COMMANDS

    _, _, _, coordinator, intent, query = _environment(tmp_path)
    coordinator.propose(intent)
    coordinator.approve(intent)
    monkeypatch.setattr("cli.audit_cli.handle_audit_command", partial(handle_audit_command, service=query))

    COMMANDS["audit"](["list"])
    list_output = capsys.readouterr().out
    assert intent.id in list_output
    assert "succeeded" in list_output

    COMMANDS["audit"](["show", intent.id])
    show_output = capsys.readouterr().out
    assert "Estado derivado: succeeded" in show_output
    assert "EXECUTION_SUCCEEDED" in show_output
    assert "retry" not in show_output.casefold()


def test_missing_database_is_not_created_by_reader(tmp_path):
    path = tmp_path / "missing" / "audit.db"
    query = AuditQueryService(SQLiteAuditReader(path))
    with pytest.raises(AuditQueryError, match="No existe"):
        query.list_recent()
    assert not path.exists()
