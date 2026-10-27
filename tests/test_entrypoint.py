import sqlite3
import sys

import pytest

import main
from cli import miku_cli
from core.conversation import SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from core.memory import MemoryRecord


def test_entrypoint_usage_without_touching_databases(
    monkeypatch,
    capsys,
) -> None:
    initialized = False

    def fake_initialize() -> None:
        nonlocal initialized
        initialized = True

    monkeypatch.setattr(main, "initialize_archive_database", fake_initialize)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    main.main()

    assert initialized is False
    assert "python main.py miku <comando>" in capsys.readouterr().out


def test_laboral_dispatch_does_not_initialize_databases(monkeypatch, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("dato", encoding="utf-8")
    initialized = False

    def fake_initialize() -> None:
        nonlocal initialized
        initialized = True

    monkeypatch.setattr(main, "initialize_archive_database", fake_initialize)
    monkeypatch.setattr(sys, "argv", ["main.py", "laboral", "read", str(target)])
    main.main()
    assert initialized is False


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ["chat"]),
        (["--temporary", "--no-memory"], ["chat", "--temporary", "--no-memory"]),
        (["--session", "session-id"], ["chat", "--session", "session-id"]),
        (["chat", "--inspect", "target"], ["chat", "--inspect", "target"]),
        (["voice", "--no-tts"], ["voice", "--no-tts"]),
        (["pet", "--no-topmost"], ["pet", "--no-topmost"]),
        (["edit", "target"], ["edit", "target"]),
        (["sessions"], ["sessions"]),
        (["messages", "session-id"], ["messages", "session-id"]),
        (["branch", "session-id", "message-id"], ["branch", "session-id", "message-id"]),
        (["unknown-command"], ["unknown-command"]),
    ],
)
def test_direct_miku_dispatch_preserves_arguments(monkeypatch, arguments, expected):
    calls = []
    monkeypatch.setattr(main, "handle_miku_command", calls.append)
    monkeypatch.setattr(sys, "argv", ["miku", *arguments])

    main.miku_main()

    assert calls == [expected]
    assert sys.argv == ["miku", *arguments]


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_direct_miku_help_does_not_start_a_session(monkeypatch, capsys, help_flag):
    def forbidden_handler(args):
        pytest.fail("La ayuda no debe iniciar el handler de sesiones.")

    monkeypatch.setattr(main, "handle_miku_command", forbidden_handler)
    monkeypatch.setattr(sys, "argv", ["miku", help_flag])

    main.miku_main()

    assert "miku chat" in capsys.readouterr().out


@pytest.mark.parametrize("entrypoint", ["miku", "republica"])
def test_miku_entries_reach_the_existing_chat_without_ollama(
    monkeypatch, tmp_path, capsys, entrypoint,
):
    database = SQLiteDatabase(tmp_path / "entrypoint-conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    prompts = []

    class FakeProvider:
        def ask(self, prompt):
            prompts.append(prompt)
            return "Respuesta del provider falso"

    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(miku_cli, "create_provider", FakeProvider)
    inputs = iter(["Hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["miku"] if entrypoint == "miku" else ["republica", "miku", "chat"],
    )

    if entrypoint == "miku":
        main.miku_main()
    else:
        main.main()

    sessions = repository.list_sessions()
    assert len(sessions) == 1
    session = repository.load_session(sessions[0].id)
    assert session.mode == "persistent"
    assert session.closed_at is not None
    assert [message.content for message in session.messages] == [
        "Hola", "Respuesta del provider falso",
    ]
    assert any("--- INVARIANTES ---" in prompt for prompt in prompts)
    output = capsys.readouterr().out
    assert f"Sesión: {session.id}" in output
    assert f"Para retomar: miku --session {session.id}" in output
    assert "Chat persistente con memoria personal." in output
    assert "Respuesta del provider falso" in output


@pytest.fixture
def isolated_chat_cli(monkeypatch, tmp_path):
    database = SQLiteDatabase(tmp_path / "sessions.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    prompts = []

    class RecordingProvider:
        def ask(self, prompt):
            if "extractor de candidatos de memoria" in prompt:
                return "[]"
            prompts.append(prompt)
            return "Respuesta para continuar la charla"

    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(miku_cli, "create_provider", RecordingProvider)
    monkeypatch.chdir(tmp_path)
    return repository, prompts


@pytest.mark.parametrize("entrypoint", ["miku", "republica"])
def test_explicit_resume_reuses_visible_session_and_history(
    isolated_chat_cli, monkeypatch, capsys, entrypoint,
):
    repository, prompts = isolated_chat_cli
    service = SessionService(repository)
    other = service.create(profile_name="miku")
    unrelated_message = other.add_user("historia ajena a esta charla")
    repository.save_message(other.id, unrelated_message)
    action = service.create(profile_name="miku", mode="action")
    previous_ids = {other.id, action.id}

    def invoke(arguments, messages):
        inputs = iter([*messages, "salir"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        argv = (
            ["miku", *arguments] if entrypoint == "miku"
            else ["republica", "miku", "chat", *arguments]
        )
        monkeypatch.setattr(sys, "argv", argv)
        (main.miku_main if entrypoint == "miku" else main.main)()
        return capsys.readouterr().out

    first_output = invoke([], ["Hola desde la primera charla"])
    sessions = repository.list_sessions()
    assert len(sessions) == 3
    created = next(session for session in sessions if session.id not in previous_ids)
    first = repository.load_session(created.id)
    assert first.mode == "persistent"
    assert first.memory_policy == "enabled"
    assert first.closed_at is not None
    assert f"Sesión: {first.id}" in first_output
    assert f"Para retomar: miku --session {first.id}" in first_output
    assert "historia ajena a esta charla" not in prompts[0]

    second_output = invoke(["--session", first.id, "--no-memory"], ["Seguimos"])

    assert f"Sesión: {first.id}" in second_output
    assert "Chat persistente sin extracción de memoria." in second_output
    assert {session.id for session in repository.list_sessions()} == previous_ids | {first.id}
    resumed = repository.load_session(first.id)
    assert resumed.messages[:2] == first.messages
    assert [message.content for message in resumed.messages] == [
        "Hola desde la primera charla", "Respuesta para continuar la charla",
        "Seguimos", "Respuesta para continuar la charla",
    ]
    assert resumed.memory_policy == "enabled"  # --no-memory sólo afecta esta invocación.
    assert len(prompts) == 2
    assert "Hola desde la primera charla" in prompts[1]
    assert "Respuesta para continuar la charla" in prompts[1]
    assert "Seguimos" in prompts[1]
    assert "historia ajena a esta charla" not in prompts[1]
    assert repository.load_session(other.id).messages == [unrelated_message]
    assert repository.load_session(action.id) == action


@pytest.mark.parametrize("mode", ["persistent", "action"])
def test_explicit_resume_of_empty_closed_session_keeps_its_id(
    isolated_chat_cli, monkeypatch, capsys, mode,
):
    repository, prompts = isolated_chat_cli
    original = SessionService(repository).create(profile_name="miku", mode=mode)
    repository.close_session(original.id)
    monkeypatch.setattr("builtins.input", lambda _: "salir")
    monkeypatch.setattr(sys, "argv", ["miku", "--session", original.id])

    main.miku_main()

    output = capsys.readouterr().out
    assert f"Sesión: {original.id}" in output
    assert f"Para retomar: miku --session {original.id}" in output
    assert len(repository.list_sessions()) == 1
    restored = repository.load_session(original.id)
    assert restored.mode == mode
    assert restored.messages == []
    assert prompts == []


def test_temporary_chat_has_visible_id_without_resume_hint_or_writes(
    isolated_chat_cli, monkeypatch, capsys,
):
    repository, prompts = isolated_chat_cli
    memory_repository = miku_cli.SQLiteMemoryRepository()
    before_conversation = repository.database.path.read_bytes()
    before_memory = memory_repository.database.path.read_bytes()
    prepared = []
    prepare_session = miku_cli.prepare_chat_session

    def record_session(**kwargs):
        session = prepare_session(**kwargs)
        prepared.append(session)
        return session

    def forbidden_write(*args, **kwargs):
        pytest.fail("La conversación temporal no debe persistir sesión ni mensajes.")

    for operation in ("create_session", "save_message", "close_session"):
        monkeypatch.setattr(repository, operation, forbidden_write)
    monkeypatch.setattr(miku_cli, "prepare_chat_session", record_session)
    inputs = iter(["Hola temporal", "salir"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(sys, "argv", ["miku", "--temporary"])

    main.miku_main()

    output = capsys.readouterr().out
    assert len(prepared) == 1
    assert f"Sesión: {prepared[0].id}" in output
    assert "Chat temporal. No se guardará el historial." in output
    assert "No puede reanudarse." in output
    assert "Para retomar:" not in output
    assert len(prompts) == 1
    assert repository.list_sessions() == []
    assert repository.database.path.read_bytes() == before_conversation
    assert memory_repository.database.path.read_bytes() == before_memory


@pytest.mark.parametrize(
    ("arguments", "diagnostic"),
    [
        (["--session"], "--session requiere un ID"),
        (["--session", ""], "--session requiere un ID"),
        (["--session", "   "], "--session requiere un ID"),
        (["--session", "no-existe"], "No existe la sesión: no-existe"),
        (["--temporary", "--session", "no-existe"], "no pueden combinarse"),
        (["--action", "--session", "no-existe"], "no pueden combinarse"),
        (["--temporary", "--action"], "no pueden combinarse"),
    ],
)
def test_invalid_session_selection_does_not_fall_back_to_new_chat(
    isolated_chat_cli, monkeypatch, capsys, arguments, diagnostic,
):
    repository, _ = isolated_chat_cli
    before = repository.database.path.read_bytes()

    def forbidden_start(*args, **kwargs):
        pytest.fail("Una selección inválida no debe iniciar provider ni conversación.")

    monkeypatch.setattr(miku_cli, "create_provider", forbidden_start)
    monkeypatch.setattr("builtins.input", forbidden_start)
    monkeypatch.setattr(sys, "argv", ["miku", *arguments])

    main.miku_main()

    output = capsys.readouterr().out
    assert diagnostic in output
    assert "Miku está escuchando" not in output
    assert "Hasta luego" not in output
    assert repository.list_sessions() == []
    assert repository.database.path.read_bytes() == before


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, OSError])
def test_conversation_database_open_failure_does_not_start_chat(
    monkeypatch, capsys, error_type,
):
    attempts = []

    def fail_open():
        attempts.append("open")
        raise error_type("base no disponible")

    def forbidden_start(*args, **kwargs):
        pytest.fail("No debe comenzar el chat si falló abrir la base.")

    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", fail_open)
    monkeypatch.setattr(miku_cli, "create_provider", forbidden_start)
    monkeypatch.setattr("builtins.input", forbidden_start)
    monkeypatch.setattr(sys, "argv", ["miku", "--session", "sesion-elegida"])

    main.miku_main()

    output = capsys.readouterr().out
    assert "No pude abrir la base de conversaciones" in output
    assert "base no disponible" in output
    assert "Miku está escuchando" not in output
    assert attempts == ["open"]


@pytest.mark.parametrize("operation", ["create_session", "load_session", "load_context_messages"])
@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, OSError])
def test_session_open_failure_is_reported_without_retry_or_chat(
    isolated_chat_cli, monkeypatch, capsys, operation, error_type,
):
    repository, _ = isolated_chat_cli
    original = SessionService(repository).create(profile_name="miku")
    before = repository.database.path.read_bytes()
    attempts = []

    def fail_open(*args, **kwargs):
        attempts.append(operation)
        raise error_type("almacenamiento no disponible")

    def forbidden_start(*args, **kwargs):
        pytest.fail("No debe comenzar el chat si falló abrir la sesión.")

    monkeypatch.setattr(repository, operation, fail_open)
    monkeypatch.setattr(miku_cli, "create_provider", forbidden_start)
    monkeypatch.setattr("builtins.input", forbidden_start)
    arguments = [] if operation == "create_session" else ["--session", original.id]
    monkeypatch.setattr(sys, "argv", ["miku", *arguments])

    main.miku_main()

    output = capsys.readouterr().out
    assert "No pude confirmar la apertura de la sesión" in output
    assert "No se inició el chat ni se reintentó la apertura o un cierre" in output
    assert "almacenamiento no disponible" in output
    assert "Miku está escuchando" not in output
    assert "Hasta luego" not in output
    assert attempts == [operation]
    assert repository.database.path.read_bytes() == before


def test_conflicting_session_ids_are_rejected_before_selecting_or_starting(
    isolated_chat_cli, monkeypatch, capsys,
):
    repository, _ = isolated_chat_cli
    service = SessionService(repository)
    first = service.create(profile_name="miku")
    second = service.create(profile_name="miku")
    before = repository.database.path.read_bytes()

    def forbidden_start(*args, **kwargs):
        pytest.fail("Dos IDs distintos no permiten elegir una sesión silenciosamente.")

    monkeypatch.setattr(repository, "load_session", forbidden_start)
    monkeypatch.setattr(miku_cli, "create_provider", forbidden_start)
    monkeypatch.setattr("builtins.input", forbidden_start)
    monkeypatch.setattr(sys, "argv", [
        "miku", "--session", first.id, "--session", second.id,
    ])

    main.miku_main()

    output = capsys.readouterr().out
    assert "--session no puede seleccionar dos IDs distintos" in output
    assert "Miku está escuchando" not in output
    assert repository.database.path.read_bytes() == before


@pytest.mark.parametrize("padding", ["", "  "])
def test_repeating_the_same_session_id_remains_unambiguous(
    isolated_chat_cli, monkeypatch, capsys, padding,
):
    repository, prompts = isolated_chat_cli
    original = SessionService(repository).create(profile_name="miku")
    monkeypatch.setattr("builtins.input", lambda _: "salir")
    monkeypatch.setattr(sys, "argv", [
        "miku", "--session", original.id, "--session", f"{padding}{original.id}{padding}",
    ])

    main.miku_main()

    assert f"Sesión: {original.id}" in capsys.readouterr().out
    assert [session.id for session in repository.list_sessions()] == [original.id]
    assert prompts == []


@pytest.mark.parametrize("mode", ["no_memory", "temporary", "temporary_no_memory", "resume_no_memory"])
def test_cli_separates_history_persistence_from_personal_memory(
    isolated_chat_cli, monkeypatch, capsys, mode,
):
    repository, _ = isolated_chat_cli
    memories = miku_cli.SQLiteMemoryRepository()
    memories.save(MemoryRecord(
        profile_name="miku", category="preference",
        content="El editor favorito es EDITOR_PRIVADO_DE_PRUEBA", source_type="conversation",
    ))
    memory_before = memories.database.path.read_bytes()
    original = None
    if mode == "resume_no_memory":
        original = SessionService(repository).create(profile_name="miku", memory_policy="enabled")
        arguments = ["--session", original.id, "--no-memory"]
    elif mode == "no_memory":
        arguments = ["--no-memory"]
    else:
        arguments = ["--temporary"]
        if mode == "temporary_no_memory":
            arguments.append("--no-memory")
    conversation_before = repository.database.path.read_bytes()
    prompts = []

    def forbidden_memory(*args, **kwargs):
        pytest.fail("El modo elegido no debe recuperar ni guardar memoria personal.")

    class Provider:
        def ask(self, prompt):
            assert "extractor de candidatos de memoria" not in prompt
            assert "EDITOR_PRIVADO_DE_PRUEBA" not in prompt
            prompts.append(prompt)
            return "Respuesta sin memoria personal"

    monkeypatch.setattr(memories, "list", forbidden_memory)
    monkeypatch.setattr(memories, "save", forbidden_memory)
    monkeypatch.setattr(miku_cli, "SQLiteMemoryRepository", lambda: memories)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    inputs = iter(["Hablemos del editor favorito", "salir"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(sys, "argv", ["miku", *arguments])

    main.miku_main()

    output = capsys.readouterr().out
    assert "Miku > Respuesta sin memoria personal" in output
    assert len(prompts) == 1
    assert memories.database.path.read_bytes() == memory_before
    sessions = repository.list_sessions()
    if mode.startswith("temporary"):
        assert "Chat temporal." in output
        assert "Para retomar:" not in output
        assert sessions == []
        assert repository.database.path.read_bytes() == conversation_before
    else:
        assert "Chat persistente sin extracción de memoria." in output
        assert len(sessions) == 1
        restored = repository.load_session(sessions[0].id)
        assert [message.content for message in restored.messages] == [
            "Hablemos del editor favorito", "Respuesta sin memoria personal",
        ]
        if original is not None:
            assert restored.id == original.id
            assert restored.memory_policy == "enabled"
        else:
            assert restored.memory_policy == "disabled"
