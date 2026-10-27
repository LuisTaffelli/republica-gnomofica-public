import builtins
import sqlite3

import pytest

from cli import miku_cli
from core.database import SQLiteDatabase, initialize_conversation_database
from core.conversation import SessionService, SQLiteConversationRepository


def test_minimum_persistent_message_flow(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    service = SessionService(repository)
    session = service.create(profile_name="miku")

    user_message = session.add_user("Hola")
    repository.save_message(session.id, user_message)
    assistant_message = session.add_assistant("Hola, Visitante")
    repository.save_message(session.id, assistant_message)

    restored = repository.load_session(session.id)

    assert [message.role for message in restored.messages] == [
        "user",
        "assistant",
    ]
    assert [message.content for message in restored.messages] == [
        "Hola",
        "Hola, Visitante",
    ]
    assert restored.memory_policy == "enabled"


def test_chat_flow_persists_user_and_assistant_messages(
    tmp_path,
    monkeypatch,
) -> None:
    database = SQLiteDatabase(tmp_path / "chat-flow.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")

    class FakeProvider:
        def ask(self, prompt) -> str:
            assert "Hola" in prompt
            assert "--- INVARIANTES ---" in prompt
            return "Hola desde el provider falso"

    inputs = iter(["Hola", "salir"])
    monkeypatch.setattr(miku_cli, "create_provider", FakeProvider)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    miku_cli.run_chat(session=session, repository=repository)

    restored = repository.load_session(session.id)
    assert [message.role for message in restored.messages] == [
        "user",
        "assistant",
    ]
    assert restored.messages[-1].content == (
        "Hola desde el provider falso"
    )
    assert restored.closed_at is not None


def test_resume_no_memory_is_ephemeral_and_preserves_persisted_policy(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "resume-no-memory.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    service = SessionService(repository)
    original = service.create(profile_name="miku", memory_policy="enabled")

    resumed = miku_cli.prepare_chat_session(
        options=miku_cli.ChatOptions(session_id=original.id, no_memory=True),
        service=service,
    )

    assert resumed.memory_policy == "enabled"
    assert repository.load_session(original.id).memory_policy == "enabled"


def test_cli_shows_discreet_memory_diagnostic_without_losing_response(
    tmp_path, monkeypatch, capsys,
) -> None:
    database = SQLiteDatabase(tmp_path / "memory-diagnostic.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")

    class ProviderWithBrokenExtraction:
        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return "json inválido"
            return "respuesta conservada"

    inputs = iter(["Prefiero usar Helix siempre", "salir"])
    monkeypatch.setattr(miku_cli, "create_provider", ProviderWithBrokenExtraction)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    miku_cli.run_chat(session=session, repository=repository)

    output = capsys.readouterr().out
    assert "Miku > respuesta conservada" in output
    assert "Aviso de memoria: no pude completar la operación local" in output


@pytest.mark.parametrize("failed_role", ["user", "assistant", "tool"])
def test_cli_reports_failed_turn_write_without_retry_or_closing(
    tmp_path, monkeypatch, capsys, failed_role,
):
    database = SQLiteDatabase(tmp_path / "failed-turn.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")
    attempts = []
    inputs = []
    save_message = repository.save_message

    class Provider:
        def ask(self, prompt):
            if failed_role == "tool":
                raise RuntimeError("provider falló")
            return "respuesta no guardada"

    def fail_save(session_id, message):
        attempts.append(message.role)
        if message.role == failed_role:
            raise sqlite3.OperationalError("escritura bloqueada")
        save_message(session_id, message)

    def next_input(prompt):
        inputs.append(prompt)
        assert len(inputs) == 1, "No debe continuar después del fallo."
        return "hola"

    def forbidden_close(session_id):
        pytest.fail("No debe intentar otra escritura para cerrar tras fallar el turno.")

    monkeypatch.setattr(repository, "save_message", fail_save)
    monkeypatch.setattr(repository, "close_session", forbidden_close)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    monkeypatch.setattr("builtins.input", next_input)

    miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)

    output = capsys.readouterr().out
    assert "persistencia del turno" in output
    assert "escritura bloqueada" in output
    assert "sin reintentar" in output
    assert "Hasta luego" not in output
    assert "Miku > respuesta no guardada" not in output
    assert session.closed_at is None
    assert session.messages == repository.load_session(session.id).messages
    assert attempts == (["user"] if failed_role == "user" else ["user", failed_role])


@pytest.mark.parametrize("exit_kind", ["salir", "interrupt_input", "eof"])
@pytest.mark.parametrize("close_error", ["sqlite", "interrupt_close"])
def test_cli_reports_unconfirmed_close_once(
    tmp_path, monkeypatch, capsys, exit_kind, close_error,
):
    database = SQLiteDatabase(tmp_path / "failed-close.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")
    close_attempts = []

    def close_once(session_id):
        close_attempts.append(session_id)
        assert len(close_attempts) == 1, "Un cierre interrumpido no se reintenta."
        if close_error == "interrupt_close":
            raise KeyboardInterrupt
        raise sqlite3.OperationalError("cierre bloqueado")

    def read_exit(prompt):
        if exit_kind == "interrupt_input":
            raise KeyboardInterrupt
        if exit_kind == "eof":
            raise EOFError
        return "salir"

    monkeypatch.setattr(repository, "close_session", close_once)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr("builtins.input", read_exit)

    try:
        miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)
    except KeyboardInterrupt:
        pytest.fail("La interrupción simulada del cierre escapó de la CLI.")

    output = capsys.readouterr().out
    assert "cierre de la sesión" in output
    assert "No se reintentó" in output
    assert "Hasta luego" not in output
    assert close_attempts == [session.id]
    assert session.closed_at is None
    assert repository.load_session(session.id).closed_at is None


def test_cli_does_not_close_again_when_turn_is_interrupted(tmp_path, monkeypatch, capsys):
    database = SQLiteDatabase(tmp_path / "interrupted-turn.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")

    class InterruptedProvider:
        def ask(self, prompt):
            raise KeyboardInterrupt

    def forbidden_close(session_id):
        pytest.fail("No debe escribir un cierre después de un turno interrumpido.")

    monkeypatch.setattr(repository, "close_session", forbidden_close)
    monkeypatch.setattr(miku_cli, "create_provider", InterruptedProvider)
    monkeypatch.setattr("builtins.input", lambda _: "hola")

    miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)

    output = capsys.readouterr().out
    assert "turno se interrumpió" in output
    assert "sin reintentar" in output
    assert "Hasta luego" not in output
    assert session.closed_at is None
    assert [item.content for item in repository.load_session(session.id).messages] == ["hola"]


@pytest.mark.parametrize("exit_kind", ["salir", "interrupt_input", "eof"])
@pytest.mark.parametrize("mode", ["persistent", "action", "temporary"])
def test_cli_normal_exit_closes_once_and_respects_temporary_mode(
    tmp_path, monkeypatch, capsys, exit_kind, mode,
):
    database = SQLiteDatabase(tmp_path / "normal-close.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku", mode=mode)
    close_attempts = []
    close_session = repository.close_session

    def record_close(session_id):
        close_attempts.append(session_id)
        close_session(session_id)

    def read_exit(prompt):
        if exit_kind == "interrupt_input":
            raise KeyboardInterrupt
        if exit_kind == "eof":
            raise EOFError
        return "salir"

    monkeypatch.setattr(repository, "close_session", record_close)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr("builtins.input", read_exit)

    miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)

    output = capsys.readouterr().out
    assert "Hasta luego" in output
    assert "No pude confirmar" not in output
    assert session.closed_at is not None
    assert session.updated_at == session.closed_at
    assert session.messages == []
    if mode == "temporary":
        assert close_attempts == []
        assert repository.list_sessions() == []
    else:
        assert close_attempts == [session.id]
        restored = repository.load_session(session.id)
        assert restored.closed_at is not None
        assert restored.updated_at == restored.closed_at
        assert restored.messages == []


@pytest.mark.parametrize("mode,phase", [
    ("persistent", "response"),
    ("persistent", "memory_diagnostic"),
    ("temporary", "response"),
    ("action", "response"),
])
def test_cli_interrupted_presentation_closes_confirmed_turn_once(
    tmp_path, monkeypatch, capsys, mode, phase,
):
    database = SQLiteDatabase(tmp_path / "interrupted-output.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku", mode=mode)
    close_attempts = []
    interruptions = []
    inputs = []
    close_session = repository.close_session
    original_print = builtins.print
    trigger = "Miku > respuesta confirmada" if phase == "response" else "Aviso de memoria:"

    class Provider:
        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return "json inválido"
            return "respuesta confirmada"

    def record_close(session_id):
        close_attempts.append(session_id)
        close_session(session_id)

    def interrupt_output(*args, **kwargs):
        if args and trigger in str(args[0]):
            interruptions.append(phase)
            assert session.messages[-1].content == "respuesta confirmada"
            if session.is_persistent:
                assert repository.load_session(session.id).messages == session.messages
            raise KeyboardInterrupt
        original_print(*args, **kwargs)

    def read_once(prompt):
        inputs.append(prompt)
        assert len(inputs) == 1, "Interrumpir la presentación debe terminar el chat."
        return "Prefiero usar Helix siempre"

    monkeypatch.setattr(repository, "close_session", record_close)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    monkeypatch.setattr("builtins.input", read_once)
    monkeypatch.setattr("builtins.print", interrupt_output)

    try:
        miku_cli.run_chat(
            session=session, repository=repository, memory_enabled=phase == "memory_diagnostic",
        )
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C durante la presentación escapó sin cerrar la sesión.")

    output = capsys.readouterr().out
    assert interruptions == [phase]
    assert "Hasta luego" in output
    assert session.closed_at is not None
    assert [message.role for message in session.messages] == ["user", "assistant"]
    if session.is_persistent:
        assert close_attempts == [session.id]
        restored = repository.load_session(session.id)
        assert restored.closed_at is not None
        assert restored.messages == session.messages
    else:
        assert close_attempts == []
        assert repository.list_sessions() == []


@pytest.mark.parametrize("close_error", ["sqlite", "interrupt"])
def test_cli_interrupted_presentation_does_not_retry_failed_close(
    tmp_path, monkeypatch, capsys, close_error,
):
    database = SQLiteDatabase(tmp_path / "output-then-failed-close.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")
    close_attempts = []
    original_print = builtins.print

    class Provider:
        def ask(self, prompt):
            return "respuesta confirmada"

    def interrupt_output(*args, **kwargs):
        if args and "Miku > respuesta confirmada" in str(args[0]):
            raise KeyboardInterrupt
        original_print(*args, **kwargs)

    def fail_close(session_id):
        close_attempts.append(session_id)
        assert len(close_attempts) == 1
        if close_error == "interrupt":
            raise KeyboardInterrupt
        raise sqlite3.OperationalError("cierre bloqueado")

    monkeypatch.setattr(repository, "close_session", fail_close)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    monkeypatch.setattr("builtins.input", lambda _: "hola")
    monkeypatch.setattr("builtins.print", interrupt_output)

    try:
        miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)
    except KeyboardInterrupt:
        pytest.fail("La interrupción debe conducir a un único intento de cierre.")

    output = capsys.readouterr().out
    assert "cierre de la sesión" in output
    assert "No se reintentó" in output
    assert "Hasta luego" not in output
    assert close_attempts == [session.id]
    assert session.closed_at is None
    restored = repository.load_session(session.id)
    assert restored.closed_at is None
    assert restored.messages == session.messages
    assert restored.messages[-1].content == "respuesta confirmada"


def test_cli_interrupted_goodbye_does_not_close_twice(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / "interrupted-goodbye.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")
    close_attempts = []
    goodbye_attempts = []
    close_session = repository.close_session
    original_print = builtins.print

    def record_close(session_id):
        close_attempts.append(session_id)
        close_session(session_id)

    def interrupt_goodbye(*args, **kwargs):
        if args and "Hasta luego" in str(args[0]):
            goodbye_attempts.append(True)
            assert repository.load_session(session.id).closed_at is not None
            raise KeyboardInterrupt
        original_print(*args, **kwargs)

    monkeypatch.setattr(repository, "close_session", record_close)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr("builtins.input", lambda _: "salir")
    monkeypatch.setattr("builtins.print", interrupt_goodbye)

    try:
        miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)
    except KeyboardInterrupt:
        pytest.fail("Interrumpir la despedida no debe escapar ni repetir el cierre.")

    assert close_attempts == [session.id]
    assert goodbye_attempts == [True]
    assert session.closed_at is not None


@pytest.mark.parametrize("origin,notice", [
    ("write_failure", "No pude confirmar la persistencia del turno"),
    ("turn_interrupt", "El turno se interrumpió"),
    ("close_failure", "No pude confirmar el cierre de la sesión"),
    ("close_interrupt", "Se interrumpió el cierre de la sesión"),
])
def test_cli_interrupted_failure_notice_does_not_write_or_show_goodbye(
    tmp_path, monkeypatch, capsys, origin, notice,
):
    database = SQLiteDatabase(tmp_path / "interrupted-notice.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku")
    close_attempts = []
    notice_attempts = []
    save_attempts = []
    original_print = builtins.print

    class Provider:
        def ask(self, prompt):
            raise KeyboardInterrupt

    def fail_save(session_id, message):
        save_attempts.append(message.role)
        raise sqlite3.OperationalError("escritura bloqueada")

    def fail_close(session_id):
        close_attempts.append(session_id)
        assert origin.startswith("close"), "Un turno incierto no debe intentar cerrar."
        assert len(close_attempts) == 1
        if origin == "close_interrupt":
            raise KeyboardInterrupt
        raise sqlite3.OperationalError("cierre bloqueado")

    def interrupt_notice(*args, **kwargs):
        if args and str(args[0]).startswith(notice):
            notice_attempts.append(True)
            raise KeyboardInterrupt
        original_print(*args, **kwargs)

    if origin == "write_failure":
        monkeypatch.setattr(repository, "save_message", fail_save)
    monkeypatch.setattr(repository, "close_session", fail_close)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    monkeypatch.setattr("builtins.input", lambda _: "salir" if origin.startswith("close") else "hola")
    monkeypatch.setattr("builtins.print", interrupt_notice)

    try:
        miku_cli.run_chat(session=session, repository=repository, memory_enabled=False)
    except KeyboardInterrupt:
        pytest.fail("Interrumpir el aviso debe terminar sin nuevas escrituras.")

    assert notice_attempts == [True]
    assert close_attempts == ([session.id] if origin.startswith("close") else [])
    assert save_attempts == (["user"] if origin == "write_failure" else [])
    assert "Hasta luego" not in capsys.readouterr().out
    assert session.closed_at is None
    restored = repository.load_session(session.id)
    assert restored.closed_at is None
    assert restored.messages == session.messages
    assert len(restored.messages) == (1 if origin == "turn_interrupt" else 0)
