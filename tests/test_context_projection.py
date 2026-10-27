from dataclasses import fields, replace
import sys

import pytest

import main
from cli import miku_cli

from core.context.context_manager import ContextManager
from core.context.debug import render_context_debug
from core.context.profile_loader import ProfileLoader
from core.context.projection import ContextItem, ContextProjection
from core.context.prompt_composer import PromptComposer
from core.context_builder import ContextBuilder
from core.conversation import (
    ChatSession,
    ConversationSummary,
    SessionService,
    SQLiteConversationRepository,
)
from core.database import SQLiteDatabase, initialize_conversation_database
from core.paths import PROJECT_ROOT
from miku.miku import Miku


@pytest.fixture
def repository(tmp_path):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    return SQLiteConversationRepository(database)


@pytest.fixture
def sources(tmp_path):
    profile_dir = tmp_path / "profiles" / "miku"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text(
        'name = "miku"\nidentity = "identity.md"\nprompt = "prompt.md"\n'
        'documents = ["available.md", "not-loaded.md"]\n', encoding="utf-8"
    )
    for name, content in [
        ("identity.md", "IDENTIDAD ÚNICA"),
        ("prompt.md", "INSTRUCCIONES ÚNICAS"),
        ("available.md", "DOCUMENTO DISPONIBLE NO INYECTADO"),
    ]:
        (profile_dir / name).write_text(content, encoding="utf-8")
    invariants = tmp_path / "INVARIANTS.md"
    invariants.write_text("INVARIANTES ÚNICOS", encoding="utf-8")
    return profile_dir, invariants


@pytest.fixture
def builder(repository, sources):
    profile_dir, invariants = sources
    return ContextManager(repository, ProfileLoader(profile_dir.parent), invariants)


def test_layers_sources_and_documents_remain_available(builder, sources):
    # Distingue las capas seleccionadas de las fuentes meramente disponibles.
    projection = builder.build_for_profile("miku", "Hola")
    assert [field.name for field in fields(ContextProjection)] == [
        "invariants", "identity", "instructions", "memory", "conversation", "task"
    ]
    assert projection.invariants.content == "INVARIANTES ÚNICOS"
    assert projection.invariants.source == sources[1].as_posix()
    assert projection.identity.content == "IDENTIDAD ÚNICA"
    assert projection.identity.source.endswith("/identity.md")
    assert projection.instructions.content == "INSTRUCCIONES ÚNICAS"
    assert projection.instructions.source.endswith("/prompt.md")
    assert projection.memory == ()
    assert projection.conversation.messages == ()
    assert projection.conversation.summary is None
    assert projection.task == ()
    available = builder.profile_loader.load("miku")
    assert available["documents"] == ["available.md", "not-loaded.md"]
    assert (available["profile_dir"] / available["documents"][0]).read_text(
        encoding="utf-8"
    ) == "DOCUMENTO DISPONIBLE NO INYECTADO"
    assert "DOCUMENTO DISPONIBLE" not in PromptComposer().serialize(projection)
    assert ContextBuilder is ContextManager


def test_context_debug_is_bounded_subject_explicit_and_redacts_secrets(builder):
    projection = builder.build_for_profile("miku", "password=hunter2")
    projection = replace(
        projection,
        memory=(ContextItem(
            "subject=user | display_name=Visitante\nfact=soy alérgico a la avellana",
            "memory:one;subject=user;status=active;category=personal_fact",
        ),),
        task=(
            ContextItem("password=archivo-secreto", "inspection:operation:1:read_file"),
            ContextItem("token=abc123 dato", "task:synthetic"),
        ),
    )

    debug = render_context_debug(projection)

    assert debug.startswith("[context-debug]\nidentity: source=")
    assert "IDENTIDAD ÚNICA" not in debug
    assert "subject=user | display_name=Visitante" in debug
    assert "observed content omitted" in debug
    assert "hunter2" not in debug
    assert "archivo-secreto" not in debug
    assert "abc123" not in debug


def test_context_debug_cli_flag_is_opt_in(builder, repository, monkeypatch, capsys):
    class Provider:
        def ask(self, _prompt):
            return "respuesta"

    monkeypatch.setattr(miku_cli, "ContextManager", lambda **_kwargs: builder)
    monkeypatch.setattr(miku_cli, "create_provider", Provider)
    sessions = SessionService(repository)

    answers = iter(["hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    miku_cli.run_chat(
        session=sessions.create(mode="temporary"),
        repository=repository,
        memory_enabled=False,
    )
    assert "[context-debug]" not in capsys.readouterr().out

    answers = iter(["hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    miku_cli.run_chat(
        session=sessions.create(mode="temporary"),
        repository=repository,
        memory_enabled=False,
        context_debug=True,
    )
    assert "[context-debug]" in capsys.readouterr().out
    assert miku_cli.parse_chat_options(["--context-debug"]).context_debug


def test_real_sources_not_full_canon(repository, monkeypatch, tmp_path):
    # Omite documentos completos incluso al construir desde otro directorio.
    monkeypatch.chdir(tmp_path)
    projection = ContextManager(repository).build_for_profile("miku", "Hola")
    assert projection.invariants.source == "docs/INVARIANTS.md"
    assert projection.identity.source == "profiles/miku/identity.md"
    assert projection.instructions.source == "profiles/miku/prompt.md"
    assert projection.invariants.content == (PROJECT_ROOT / "docs/INVARIANTS.md").read_text(
        encoding="utf-8"
    )
    prompt = PromptComposer().serialize(projection)
    for path in ProfileLoader().load("miku")["documents"]:
        document = PROJECT_ROOT / "profiles/miku" / path
        assert document.read_text(encoding="utf-8").strip() not in prompt


def test_recent_twenty_excluding_current_preserves_objects_and_order(builder):
    # Selecciona veinte mensajes previos sin duplicar la entrada actual.
    session = ChatSession(profile_name="miku", mode="temporary")
    for index in range(25):
        session.add_assistant(f"anterior {index}", marker=index)
    current = session.add_user("actual")
    before = list(session.messages)
    projection = builder.build_for_profile("miku", " actual ", session)
    assert projection.conversation.messages == tuple(before[-21:-1])
    assert projection.conversation.messages[0] is before[5]
    assert projection.conversation.messages[0].metadata == {"marker": 5}
    assert projection.conversation.current_message.source == f"conversation:{current.id}"
    assert session.messages == before


def test_nonmatching_last_user_is_not_removed(builder):
    # Una entrada distinta no debe eliminar el último mensaje del usuario.
    session = ChatSession(profile_name="miku", mode="temporary")
    previous = session.add_user("otra pregunta")
    projection = builder.build_for_profile("miku", "actual", session)
    assert projection.conversation.messages == (previous,)


def test_branches_and_resumed_nested_path_exclude_siblings(repository, builder):
    # Mantiene el camino de la rama sin incorporar mensajes de sus hermanas.
    service = SessionService(repository)
    parent = service.create()
    anchor = parent.add_user("punto común")
    repository.save_message(parent.id, anchor)
    after = parent.add_assistant("padre después del ancla")
    repository.save_message(parent.id, after)
    branch = service.branch(parent_session_id=parent.id, parent_message_id=anchor.id)
    own = branch.add_assistant("camino elegido")
    repository.save_message(branch.id, own)
    sibling = service.branch(parent_session_id=parent.id, parent_message_id=anchor.id)
    sibling_message = sibling.add_assistant("hermana excluida")
    repository.save_message(sibling.id, sibling_message)
    nested = service.branch(parent_session_id=branch.id, parent_message_id=own.id)
    for session in (branch, nested, service.resume(nested.id)):
        projection = builder.build_for_profile("miku", "siguiente", session)
        assert [message.id for message in projection.conversation.messages] == [
            anchor.id, own.id
        ]


def test_existing_summary_is_read_not_generated(repository, builder):
    # Aprovecha el resumen existente sin crear registros adicionales.
    session = SessionService(repository).create()
    anchor = session.add_user("ancla")
    repository.save_message(session.id, anchor)
    summary = ConversationSummary(session.id, "resumen preexistente", anchor.id)
    repository.save_summary(summary)
    projection = builder.build_for_profile("miku", "siguiente", session)
    assert projection.conversation.summary == summary
    assert "RESUMEN ACUMULADO" in PromptComposer().serialize(projection)
    assert summary.through_message_id in PromptComposer().serialize(projection)
    with repository.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0] == 1


def test_task_is_ordered_ephemeral_and_provider_gets_exact_serialization(builder, repository):
    session = SessionService(repository).create()
    task = [ContextItem("dato A", "task:a"), ContextItem("dato B", "task:b")]
    projection = builder.build_for_profile("miku", "Hola", session, task)
    serializer = PromptComposer()
    expected = serializer.serialize(projection)
    assert serializer.serialize(projection) == expected
    assert projection.task == tuple(task)
    task.clear()
    assert len(projection.task) == 2
    markers = ["--- INVARIANTES ---", "--- IDENTIDAD ---", "--- INSTRUCCIONES ---",
               "--- MEMORIA PERSONAL PERTINENTE ---",
               "--- CONTEXTO CONVERSACIONAL ---", "--- TAREA ---", "task:a", "task:b"]
    positions = [expected.index(marker) for marker in markers]
    assert positions == sorted(positions)

    class FakeProvider:
        def ask(self, prompt):
            # Expectativa independiente del serializer: el contenido real de
            # cada fuente debe llegar al provider, no sólo títulos/procedencia.
            content_in_order = [
                "INVARIANTES ÚNICOS", "IDENTIDAD ÚNICA", "INSTRUCCIONES ÚNICAS",
                "Hola", "dato A", "dato B",
            ]
            for content in content_in_order:
                assert content in prompt, f"Falta contenido serializado: {content}"
            content_positions = [prompt.index(content) for content in content_in_order]
            assert content_positions == sorted(content_positions)
            assert prompt == expected
            return "respuesta exacta"

    miku = Miku(provider=FakeProvider(), context_builder=builder, serializer=serializer)
    assert miku.listen("Hola", session, task_context=projection.task) == "respuesta exacta"
    next_projection = builder.build_for_profile("miku", "siguiente", session)
    assert next_projection.task == ()
    assert "dato A" not in serializer.serialize(next_projection)
    assert repository.load_session(session.id).messages == []
    assert repository.load_latest_summary(session.id) is None
    assert session.messages == []


@pytest.mark.parametrize("layer,filename", [
    ("invariants", "INVARIANTS.md"), ("identity", "identity.md"),
    ("instructions", "prompt.md"),
])
@pytest.mark.parametrize("empty", [False, True])
def test_required_sources_fail_before_provider(builder, sources, layer, filename, empty):
    path = sources[1] if layer == "invariants" else sources[0] / filename
    if empty:
        path.write_text(" \n", encoding="utf-8")
    else:
        path.unlink()

    class NeverCalled:
        def ask(self, prompt):
            pytest.fail("No debe consultarse el provider sin fuentes fundamentales")

    miku = Miku(provider=NeverCalled(), context_builder=builder, serializer=PromptComposer())
    with pytest.raises(ValueError if empty else FileNotFoundError, match=layer):
        miku.listen("Hola")


@pytest.mark.parametrize("mode", ["temporary", "persistent"])
@pytest.mark.parametrize("layer,filename", [
    ("invariants", "INVARIANTS.md"), ("identity", "identity.md"),
    ("instructions", "prompt.md"),
])
@pytest.mark.parametrize("empty", [False, True])
def test_cli_shows_context_source_failure(
    builder, sources, repository, monkeypatch, capsys, mode, layer, filename, empty
):
    path = sources[1] if layer == "invariants" else sources[0] / filename
    if empty:
        path.write_text(" \n", encoding="utf-8")
    else:
        path.unlink()
    calls = []

    class NeverCalled:
        def ask(self, prompt):
            calls.append(prompt)
            raise AssertionError("No debe consultarse el provider")

    session = SessionService(repository).create(mode=mode)
    monkeypatch.setattr(miku_cli, "ContextManager", lambda **kwargs: builder)
    monkeypatch.setattr(miku_cli, "create_provider", NeverCalled)
    inputs = iter(["Hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    miku_cli.run_chat(session=session, repository=repository)

    output = capsys.readouterr().out
    assert "No fue posible generar una respuesta" in output
    assert "fuente fundamental" in output
    assert layer in output
    assert str(path.resolve()) in output
    assert ("está vacía" if empty else "Falta") in output
    assert "consultar el modelo" not in output
    assert calls == []
    assert [message.role for message in session.messages] == ["user", "tool"]
    if mode == "temporary":
        assert repository.list_sessions() == []
    else:
        assert repository.load_session(session.id).messages == session.messages


@pytest.mark.parametrize("flag,mode", [
    (None, "persistent"), ("--temporary", "temporary"), ("--action", "action"),
])
def test_entrypoint_chat_modes_with_real_context_and_fake_provider(
    repository, monkeypatch, capsys, flag, mode
):
    prompts = []

    class FakeProvider:
        def ask(self, prompt):
            prompts.append(prompt)
            if "Sos un extractor de candidatos de memoria personal" in prompt:
                return "[]"
            return "Respuesta de integración"

    monkeypatch.setattr(
        main,
        "initialize_archive_database",
        lambda: pytest.fail("El entrypoint de Miku no debe inicializar Archive."),
    )
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(miku_cli, "create_provider", FakeProvider)
    monkeypatch.setattr(sys, "argv", ["republica", "miku", "chat"] + ([flag] if flag else []))
    inputs = iter(["Hola de integración", "salir"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    main.main()
    response_prompts = [prompt for prompt in prompts if "--- INVARIANTES ---" in prompt]
    assert len(response_prompts) == 1
    assert response_prompts[0].count("Hola de integración") == 1
    assert len(prompts) == (2 if mode == "persistent" else 1)
    assert "Respuesta de integración" in capsys.readouterr().out
    sessions = repository.list_sessions()
    if mode == "temporary":
        assert sessions == []
    else:
        assert len(sessions) == 1
        restored = repository.load_session(sessions[0].id)
        assert restored.mode == mode
        assert restored.memory_policy == ("disabled" if mode == "action" else "enabled")
        assert [message.content for message in restored.messages] == [
            "Hola de integración", "Respuesta de integración"
        ]
