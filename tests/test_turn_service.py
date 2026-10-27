from __future__ import annotations

import inspect
import json
import sqlite3

import pytest

from cli import miku_cli
from core.activity import ActivityState
from core.actions import ActionCoordinator, ActionIntent, EffectKind, EffectResult
from core.audit import AuditEvent, AuditEventType, AuditService
from core.context.context_manager import ContextManager
from core.context.prompt_composer import PromptComposer
from core.conversation import ChatSession, SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database, initialize_memory_database
from core.memory import MemoryRecord, SQLiteMemoryRepository
from gnomos.archive import SQLiteArchiveReader
from gnomos.laboral import InspectionScope
from miku.miku import Miku
from miku.turn import MikuTurnService, TurnRequest, TurnResult, build_miku_turn_service


def _conversation(tmp_path):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    return repository, SessionService(repository)


def _memory(tmp_path):
    database = SQLiteDatabase(tmp_path / "memory.db")
    initialize_memory_database(database)
    return SQLiteMemoryRepository(database)


class EchoProvider:
    def ask(self, prompt):
        return "respuesta estructurada"


def test_normal_persistent_turn_is_structured_and_persisted_without_io(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")
    service = build_miku_turn_service(
        session=session, repository=repository, provider=EchoProvider(),
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )
    result = service.handle(TurnRequest("hola", source="test-interface"))

    assert result.status == "completed"
    assert result.response_text == "respuesta estructurada"
    assert result.session_id == session.id
    assert result.user_message_id and result.response_message_id
    restored = repository.load_session(session.id)
    assert [item.role for item in restored.messages] == ["user", "assistant"]
    assert restored.messages[0].metadata["interface_source"] == "test-interface"
    assert session.messages == restored.messages
    assert session.updated_at == restored.updated_at
    assert result.user_message_id == restored.messages[0].id
    assert result.response_message_id == restored.messages[1].id


def test_temporary_and_no_memory_turns_do_not_persist_or_acquire(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode="temporary")
    memories = _memory(tmp_path)
    service = build_miku_turn_service(
        session=session, repository=repository, provider=EchoProvider(),
        memory_enabled=False, memory_repository=memories,
    )
    result = service.handle(TurnRequest("prefiero algo"))

    assert result.status == "completed" and not result.pending_memory
    assert memories.list("miku") == []
    assert repository.list_sessions() == []
    assert [item.role for item in session.messages] == ["user", "assistant"]
    assert session.updated_at == session.messages[-1].timestamp


def test_provider_error_is_a_persisted_structured_result(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")

    class BrokenProvider:
        def ask(self, prompt):
            raise RuntimeError("provider caído")

    service = build_miku_turn_service(
        session=session, repository=repository, provider=BrokenProvider(),
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )
    result = service.handle(TurnRequest("hola"))

    assert result.status == "error"
    assert result.error_type == "RuntimeError"
    assert "provider caído" in result.response_text
    restored = repository.load_session(session.id)
    assert restored.messages[-1].role == "tool"
    assert restored.messages[-1].metadata["kind"] == "provider_error"


@pytest.mark.parametrize("failed_role", ["user", "assistant", "tool"])
@pytest.mark.parametrize("mode", ["persistent", "action"])
def test_failed_message_write_keeps_only_confirmed_history(
    tmp_path, monkeypatch, failed_role, mode,
):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode=mode)

    class Provider:
        prompts = []
        broken = False
        response = "respuesta anterior guardada"

        def ask(self, prompt):
            self.prompts.append(prompt)
            if self.broken:
                raise RuntimeError("provider caído durante el turno")
            return self.response

    provider = Provider()
    service = build_miku_turn_service(
        session=session, repository=repository, provider=provider,
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )
    handle = service.handle_human if mode == "action" else service.handle
    handle(TurnRequest("turno anterior guardado"))
    previous_messages = list(session.messages)
    provider.prompts.clear()
    provider.response = "respuesta no persistida"
    provider.broken = failed_role == "tool"
    failure = sqlite3.OperationalError("fallo simulado de escritura")
    attempted_roles = []
    insert_message = repository._insert_message

    def fail_after_insert(connection, session_id, message):
        attempted_roles.append(message.role)
        insert_message(connection, session_id, message)
        if message.role == failed_role:
            # Fallar dentro de la transacción prueba también el rollback real.
            raise failure

    monkeypatch.setattr(repository, "_insert_message", fail_after_insert)

    with pytest.raises(sqlite3.OperationalError) as caught:
        handle(TurnRequest("entrada del turno fallido", source="test"))

    assert caught.value is failure
    restored = repository.load_session(session.id)
    assert session.messages == restored.messages
    assert session.updated_at == restored.updated_at
    assert session.messages[:len(previous_messages)] == previous_messages
    assert [message.role for message in restored.messages] == (
        ["user", "assistant"] if failed_role == "user"
        else ["user", "assistant", "user"]
    )
    assert attempted_roles == (
        ["user"] if failed_role == "user" else ["user", failed_role]
    )
    assert len(provider.prompts) == (0 if failed_role == "user" else 1)

    monkeypatch.setattr(repository, "_insert_message", insert_message)
    provider.broken = False
    provider.response = "respuesta siguiente"
    result = handle(TurnRequest("seguimos con otra entrada"))

    assert result.status == "completed"
    assert session.messages == repository.load_session(session.id).messages
    assert "respuesta no persistida" not in provider.prompts[-1]
    assert "provider caído durante el turno" not in provider.prompts[-1]
    if failed_role == "user":
        assert "entrada del turno fallido" not in provider.prompts[-1]


def test_failed_branch_message_does_not_change_inherited_history(tmp_path, monkeypatch):
    repository, sessions = _conversation(tmp_path)
    parent = sessions.create(profile_name="miku")
    anchor = parent.add_user("mensaje heredado")
    repository.save_message(parent.id, anchor)
    outside = parent.add_assistant("mensaje posterior al punto de rama")
    repository.save_message(parent.id, outside)
    branch = sessions.branch(parent_session_id=parent.id, parent_message_id=anchor.id)
    original_updated_at = branch.updated_at
    original_metadata = dict(branch.metadata)
    service = build_miku_turn_service(
        session=branch, repository=repository, provider=EchoProvider(),
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )

    def fail_before_write(session_id, message):
        raise sqlite3.OperationalError("base no disponible")

    monkeypatch.setattr(repository, "save_message", fail_before_write)

    with pytest.raises(sqlite3.OperationalError, match="base no disponible"):
        service.handle(TurnRequest("mensaje de rama no guardado"))

    assert branch.messages == sessions.resume(branch.id).messages == [anchor]
    assert branch.updated_at == original_updated_at
    assert branch.metadata == original_metadata
    assert repository.load_session(branch.id).messages == []
    assert repository.load_session(parent.id).messages == [anchor, outside]


@pytest.mark.parametrize("entry", ["turn_service", "legacy_cli"])
@pytest.mark.parametrize("previously_closed", [False, True])
def test_failed_close_keeps_previous_session_state(tmp_path, entry, previously_closed):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")
    if previously_closed:
        repository.close_session(session.id)
        session = sessions.resume(session.id)
    previous_closed_at = session.closed_at
    previous_updated_at = session.updated_at
    service = build_miku_turn_service(
        session=session, repository=repository, provider=EchoProvider(),
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )
    with repository.database.connect() as connection:
        connection.executescript(
            "CREATE TRIGGER reject_close AFTER UPDATE OF closed_at ON chat_sessions "
            "BEGIN SELECT RAISE(ABORT, 'fallo simulado al cerrar'); END;"
        )

    with pytest.raises(sqlite3.IntegrityError, match="fallo simulado al cerrar"):
        if entry == "turn_service":
            service.close()
        else:
            miku_cli.close_chat(session, repository)

    restored = repository.load_session(session.id)
    assert session.closed_at == restored.closed_at == previous_closed_at
    assert session.updated_at == restored.updated_at == previous_updated_at
    assert session.messages == restored.messages == []


def test_memory_warning_and_pending_are_exposed_without_losing_answer(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")

    class Provider:
        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return "json inválido"
            return "respuesta principal"

    service = build_miku_turn_service(
        session=session, repository=repository, provider=Provider(),
        memory_repository=_memory(tmp_path),
    )
    result = service.handle(TurnRequest("Prefiero usar Helix siempre"))
    assert result.status == "completed"
    assert result.response_text == "respuesta principal"
    assert result.secondary_diagnostic is not None


@pytest.mark.parametrize("untrusted_source", ["model-output", "voice", "human", "invented"])
def test_pending_memory_round_trip_is_preserved_by_boundary(
    tmp_path, untrusted_source,
):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")

    class Provider:
        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return json.dumps([{
                    "category": "personal_fact",
                    "content": "Visitante tiene información sensible",
                    "clarity": "clear",
                    "sensitivity": "sensitive",
                    "operation": "remember",
                }])
            return "respuesta principal"

    memories = _memory(tmp_path)
    service = build_miku_turn_service(
        session=session, repository=repository, provider=Provider(),
        memory_repository=memories,
    )
    proposed = service.handle(TurnRequest("Tengo información sensible"))
    untrusted_confirmation = service.handle(
        TurnRequest("sí, guardalo", source=untrusted_source)
    )
    assert untrusted_confirmation.pending_memory
    assert memories.list("miku", "active") == []
    confirmed = service.handle_human(TurnRequest("sí, guardalo"))
    assert proposed.pending_memory
    assert "puedo recordar" in proposed.response_text
    assert not confirmed.pending_memory
    assert len(memories.list("miku", "active")) == 1


def test_first_person_memory_reaches_provider_with_explicit_user_subject(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")
    memories = _memory(tmp_path)
    stored = MemoryRecord(
        profile_name="miku",
        category="personal_fact",
        content="soy alérgico a la avellana",
        source_type="conversation",
    )
    memories.save(stored)

    class SubjectAwareProvider:
        prompts = []

        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return "[]"
            self.prompts.append(prompt)
            if (
                "subject=user | display_name=Visitante" in prompt
                and "fact=soy alérgico a la avellana" in prompt
            ):
                return "Visitante es alérgico a la avellana."
            return "Miku es alérgica a la avellana."

    provider = SubjectAwareProvider()
    service = build_miku_turn_service(
        session=session,
        repository=repository,
        provider=provider,
        memory_repository=memories,
    )

    result = service.handle(TurnRequest("te acordás a qué soy alérgico"))

    assert result.response_text == "Visitante es alérgico a la avellana."
    assert f"memory:{stored.id};subject=user" in provider.prompts[0]
    assert "fact=soy alérgico a la avellana" in provider.prompts[0]


def test_known_sensitive_memory_answers_without_new_pending_or_invitation(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")
    memories = _memory(tmp_path)
    stored = MemoryRecord(
        profile_name="miku", category="personal_fact",
        content="Soy alérgico a la avellana", source_type="conversation",
    )
    memories.save(stored)

    class DogfoodProvider:
        prompts = []

        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return json.dumps([{
                    "category": "personal_fact",
                    "content": "Soy alérgico a la avellana",
                    "clarity": "clear",
                    "sensitivity": "sensitive",
                    "operation": "remember",
                }])
            self.prompts.append(prompt)
            return "Sos alérgico a la avellana."

    provider = DogfoodProvider()
    service = build_miku_turn_service(
        session=session, repository=repository, provider=provider,
        memory_repository=memories,
    )

    result = service.handle_human(TurnRequest("A qué soy alérgico"))

    assert result.response_text == "Sos alérgico a la avellana."
    assert not result.pending_memory
    assert "puedo recordar" not in result.response_text
    assert f"memory:{stored.id};subject=user" in provider.prompts[0]
    assert memories.list("miku", "active") == [stored]


def test_turn_request_is_not_modified_by_provider_wrapper(tmp_path):
    repository, sessions = _conversation(tmp_path)
    request = TurnRequest("texto exacto", source="voice")
    service = build_miku_turn_service(
        session=sessions.create(profile_name="miku"), repository=repository,
        provider=EchoProvider(), memory_enabled=False,
        memory_repository=_memory(tmp_path),
    )
    service.handle(request)
    assert request == TurnRequest("texto exacto", source="voice")


def test_ephemeral_question_uses_history_without_becoming_personal_memory(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku")
    previous_user = session.add_user("¿Cuál es mi animal favorito?")
    previous_assistant = session.add_assistant("Todavía no me lo dijiste.")
    repository.save_message(session.id, previous_user)
    repository.save_message(session.id, previous_assistant)

    class HistoryProvider:
        prompts = []

        def ask(self, prompt):
            self.prompts.append(prompt)
            return "Me acabás de preguntar cuál es tu animal favorito."

    provider = HistoryProvider()
    memories = _memory(tmp_path)
    service = build_miku_turn_service(
        session=session, repository=repository, provider=provider,
        memory_repository=memories,
    )

    result = service.handle_human(TurnRequest("qué te acabo de preguntar"))

    assert result.response_text.startswith("Me acabás de preguntar")
    assert len(provider.prompts) == 1  # no segunda llamada al extractor
    assert "¿Cuál es mi animal favorito?" in provider.prompts[0]
    assert memories.list("miku") == []


def test_archive_and_laboral_keep_observed_provenance_through_boundary(tmp_path):
    repository, sessions = _conversation(tmp_path)
    archive_path = tmp_path / "archive.db"
    with sqlite3.connect(archive_path) as connection:
        connection.executescript(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT, content TEXT, "
            "tags TEXT, created_at TEXT, deleted_at TEXT);"
            "CREATE TABLE note_links (id INTEGER PRIMARY KEY, from_note_id INTEGER, "
            "to_note_id INTEGER, reason TEXT, created_at TEXT);"
            "INSERT INTO notes VALUES (1, 'Bosque', 'aguja junto al roble', '', 'hoy', NULL);"
        )
    archive_responses = iter([
        {"archive_request": {"operation": "search_notes", "arguments": {"query": "aguja"}}},
        {"final_answer": "Está junto al roble."},
    ])

    class ArchiveProvider:
        def ask(self, prompt): return json.dumps(next(archive_responses))

    archive_session = sessions.create(profile_name="miku")
    archive_service = build_miku_turn_service(
        session=archive_session, repository=repository, provider=ArchiveProvider(),
        archive_reader=SQLiteArchiveReader(archive_path), memory_enabled=False,
        memory_repository=_memory(tmp_path),
    )
    archive_result = archive_service.handle(TurnRequest("buscá aguja"))
    assert archive_result.observed_source == "archive"
    assert repository.load_session(archive_session.id).messages[-1].metadata[
        "observed_source"
    ] == "archive"

    target = tmp_path / "observed.txt"
    target.write_text("dato observado", encoding="utf-8")
    laboral_responses = iter([
        {"tool_request": {"capability": "read_file", "arguments": {"path": str(target)}}},
        {"final_answer": "Leí el dato observado."},
    ])

    class LaboralProvider:
        def ask_structured(self, prompt, schema): return self.ask(prompt)
        def ask(self, prompt): return json.dumps(next(laboral_responses))

    laboral_session = sessions.create(profile_name="miku")
    laboral_service = build_miku_turn_service(
        session=laboral_session, repository=repository, provider=LaboralProvider(),
        inspection_scope=InspectionScope(target), memory_enabled=False,
        memory_repository=_memory(tmp_path),
    )
    laboral_result = laboral_service.handle(TurnRequest("leé el archivo"))
    assert laboral_result.observed_source == "laboral"
    assert repository.load_session(laboral_session.id).messages[-1].metadata[
        "observed_source"
    ] == "laboral"


def test_scope_extension_and_best_effort_activity_are_interface_neutral(tmp_path):
    repository, sessions = _conversation(tmp_path)
    root = tmp_path / "root.txt"
    extra = tmp_path / "extra.txt"
    root.write_text("root", encoding="utf-8")
    extra.write_text("extra", encoding="utf-8")

    class BrokenActivity:
        def set_state(self, state: ActivityState):
            raise RuntimeError("vista rota")

    service = build_miku_turn_service(
        session=sessions.create(profile_name="miku", mode="temporary"),
        repository=repository, provider=EchoProvider(),
        inspection_scope=InspectionScope(root), activity=BrokenActivity(),
        memory_enabled=False, memory_repository=_memory(tmp_path),
    )
    result = service.handle_human(TurnRequest("/allow", scope_extension=extra))
    assert result.status == "scope_updated"
    assert service.inspection_scope.additional_allowed_targets == (extra.resolve(),)


def test_request_cannot_forge_approval_and_service_rejects_reentrant_turn(tmp_path):
    with pytest.raises(TypeError):
        TurnRequest(text="sí", approved=True)  # type: ignore[call-arg]
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode="temporary")
    miku = Miku(
        provider=EchoProvider(), context_builder=ContextManager(repository=repository),
        serializer=PromptComposer(), memory_enabled=False,
    )
    service = MikuTurnService(session=session, repository=repository, miku=miku)
    service._handling = True
    with pytest.raises(RuntimeError, match="serial"):
        service.handle(TurnRequest("hola"))


@pytest.mark.parametrize(
    "source",
    ("model-output", "archive", "laboral", "voice", "stt", "invented", "human"),
)
def test_arbitrary_turn_request_source_never_grants_action_authority(tmp_path, source):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode="action")

    class AuditRepository:
        def __init__(self): self.events = []
        def append_event(self, event):
            stored = AuditEvent(
                id=event.id, intent_id=event.intent_id, event_type=event.event_type,
                actor=event.actor, fingerprint=event.fingerprint, payload=event.payload,
                created_at=event.created_at, sequence=len(self.events) + 1,
            )
            self.events.append(stored)
            return stored
        def list_events(self, intent_id):
            return [event for event in self.events if event.intent_id == intent_id]

    class Capability:
        def __init__(self): self.executions = 0
        def execute(self, intent):
            self.executions += 1
            return EffectResult(True)

    capability = Capability()
    coordinator = ActionCoordinator(
        audit=AuditService(AuditRepository()), capabilities={"fake": capability}
    )
    intent = ActionIntent(
        capability="fake", operation="effect", parameters={},
        effect_kind=EffectKind.EFFECTFUL, target="test://target",
        requested_by="model", profile_name="miku", session_id=session.id,
    )
    assert coordinator.propose(intent).state == "proposed"
    miku = Miku(
        provider=EchoProvider(), context_builder=ContextManager(repository=repository),
        serializer=PromptComposer(), action_coordinator=coordinator,
        memory_enabled=False,
    )
    service = MikuTurnService(session=session, repository=repository, miku=miku)
    result = service.handle(TurnRequest("sí", source=source))

    assert result.response_text == "respuesta estructurada"
    assert result.status == "completed"
    assert capability.executions == 0
    assert coordinator.pending_intent(session.id) == intent
    assert all(event.event_type is not AuditEventType.ACTION_APPROVED
               for event in coordinator.audit.repository.events)


def test_trusted_human_operation_approves_once_and_does_not_depend_on_source(tmp_path):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode="action")

    class AuditRepository:
        def __init__(self): self.events = []
        def append_event(self, event):
            stored = AuditEvent(
                id=event.id, intent_id=event.intent_id, event_type=event.event_type,
                actor=event.actor, fingerprint=event.fingerprint, payload=event.payload,
                created_at=event.created_at, sequence=len(self.events) + 1,
            )
            self.events.append(stored)
            return stored
        def list_events(self, intent_id):
            return [event for event in self.events if event.intent_id == intent_id]

    class Capability:
        def __init__(self): self.executions = 0
        def execute(self, intent):
            self.executions += 1
            return EffectResult(True)

    capability = Capability()
    coordinator = ActionCoordinator(
        audit=AuditService(AuditRepository()), capabilities={"fake": capability}
    )
    intent = ActionIntent(
        capability="fake", operation="effect", parameters={},
        effect_kind=EffectKind.EFFECTFUL, target="test://target",
        requested_by="model", profile_name="miku", session_id=session.id,
    )
    coordinator.propose(intent)
    service = MikuTurnService(
        session=session, repository=repository,
        miku=Miku(
            provider=EchoProvider(), context_builder=ContextManager(repository=repository),
            serializer=PromptComposer(), action_coordinator=coordinator,
            memory_enabled=False,
        ),
    )

    ambiguous = service.handle_human(TurnRequest("tal vez", source="anything"))
    approved = service.handle_human(TurnRequest("sí", source="anything"))
    replay = service.handle_human(TurnRequest("sí", source="anything"))

    assert "sigue pendiente" in ambiguous.response_text
    assert approved.response_text.startswith("La acción aprobada")
    assert replay.response_text == "respuesta estructurada"
    assert capability.executions == 1


def test_cli_consumes_turn_result_and_boundary_has_no_ui_or_audio_dependency(
    tmp_path, monkeypatch, capsys,
):
    repository, sessions = _conversation(tmp_path)
    session = sessions.create(profile_name="miku", mode="temporary")
    calls = []

    class FakeTurnService:
        def handle(self, request):
            calls.append(request)
            return TurnResult("desde frontera", "completed", session.id)
        def handle_human(self, request):
            calls.append(request)
            return TurnResult("desde frontera", "completed", session.id)
        def close(self): pass

    monkeypatch.setattr(miku_cli, "build_miku_turn_service", lambda **kwargs: FakeTurnService())
    monkeypatch.setattr(miku_cli, "create_provider", EchoProvider)
    answers = iter(["hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    miku_cli.run_chat(session=session, repository=repository)

    assert calls == [TurnRequest("hola")]
    assert "Miku > desde frontera" in capsys.readouterr().out
    source = inspect.getsource(__import__("miku.turn", fromlist=["*"]))
    assert "input(" not in source and "print(" not in source
    assert "tkinter" not in source.casefold() and "audio" not in source.casefold()
