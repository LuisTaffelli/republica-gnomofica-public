from __future__ import annotations

from contextlib import contextmanager
from threading import Event, Thread
import sqlite3

import pytest

from cli import miku_cli
from core.conversation import Message, SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from miku.voice import FakeSpeechInput, FakeSpeechOutput


@contextmanager
def handled_startup():
    try:
        yield
    except (Exception, KeyboardInterrupt) as error:
        pytest.fail(f"El fallo de arranque escapó del adaptador: {type(error).__name__}: {error}")


@pytest.fixture
def startup(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    sessions = SessionService(repository)
    closes = []
    close = repository.close_session

    def recording_close(session_id):
        closes.append(session_id)
        close(session_id)

    monkeypatch.setattr(repository, "close_session", recording_close)
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr(miku_cli, "create_default_voice_adapters", lambda **_: (FakeSpeechInput([]), FakeSpeechOutput()))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("No iniciar interacción después del fallo"))
    monkeypatch.setattr(miku_cli, "print_goodbye", lambda: pytest.fail("No presentar despedida de un arranque fallido"))
    return repository, sessions, closes


@pytest.mark.parametrize("interface", ["chat", "pet", "voice", "voice_pet"])
@pytest.mark.parametrize("stage", ["provider", "memory", "turn_service"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_failed_composition_closes_confirmed_session_once(startup, monkeypatch, capsys, interface, stage, error_type):
    repository, _, closes = startup

    def fail(*args, **kwargs):
        raise error_type("synthetic-startup")

    target = {"provider": "create_provider", "memory": "SQLiteMemoryRepository", "turn_service": "build_miku_turn_service"}[stage]
    monkeypatch.setattr(miku_cli, target, fail)
    if interface == "pet":
        monkeypatch.setattr("miku.pet.view.run_pet_window", lambda *_a, **_k: None)
    command = ["voice", "--pet"] if interface == "voice_pet" else [interface]
    with handled_startup():
        miku_cli.handle_miku_command(command)
    saved = repository.list_sessions()
    assert len(saved) == 1 and saved[0].closed_at is not None
    assert closes == [saved[0].id]
    assert repository.load_session(saved[0].id).messages == []
    assert "arranque" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("mode", ["persistent", "temporary"])
@pytest.mark.parametrize("interface", ["chat", "voice"])
def test_direct_run_failure_preserves_history_and_closes_ram_after_storage(startup, monkeypatch, mode, interface):
    repository, sessions, closes = startup
    session = sessions.create(mode=mode)
    if mode == "persistent":
        repository.save_message(session.id, Message(role="user", content="historia sintética"))
        session = sessions.resume(session.id)
    before = list(session.messages)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: (_ for _ in ()).throw(ValueError("config inválida")))
    with handled_startup():
        getattr(miku_cli, "run_" + interface)(session=session, repository=repository)
    assert session.closed_at is not None
    assert session.messages == before
    assert closes == ([session.id] if mode == "persistent" else [])
    if mode == "persistent":
        assert repository.load_session(session.id).messages == before


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, KeyboardInterrupt])
def test_startup_cleanup_failure_is_not_retried_or_claimed_success(startup, monkeypatch, capsys, committed, error_type):
    repository, sessions, _ = startup
    session = sessions.create()
    attempts = []
    actual_close = repository.close_session
    before = session.updated_at

    def fail_close(session_id):
        attempts.append(session_id)
        if committed:
            actual_close(session_id)
        raise error_type("synthetic-close")

    monkeypatch.setattr(repository, "close_session", fail_close)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: (_ for _ in ()).throw(RuntimeError("synthetic-provider")))
    with handled_startup():
        miku_cli.run_chat(session=session, repository=repository)
    assert attempts == [session.id]
    assert session.closed_at is None and session.updated_at == before
    assert (repository.load_session(session.id).closed_at is not None) is committed
    output = capsys.readouterr().out
    assert "No pude confirmar" in output and "No se reintent" in output
    assert "cerrada sin iniciar" not in output


def test_failed_resume_does_not_rewrite_previous_closed_marker(startup, monkeypatch):
    repository, sessions, closes = startup
    session = sessions.create()
    repository.close_session(session.id)
    before = repository.load_session(session.id)
    closes.clear()
    monkeypatch.setattr(miku_cli, "create_provider", lambda: (_ for _ in ()).throw(RuntimeError("provider")))
    with handled_startup():
        miku_cli.handle_miku_command(["chat", "--session", session.id])
    after = repository.load_session(session.id)
    assert (after.closed_at, after.updated_at, after.messages) == (before.closed_at, before.updated_at, before.messages)
    assert closes == []


@pytest.mark.parametrize("phase", ["scope", "archive", "voice_adapter"])
def test_preflight_failure_creates_no_session(startup, monkeypatch, phase):
    repository, _, closes = startup

    def fail(*args, **kwargs):
        raise OSError("preflight sintético")

    if phase == "scope":
        monkeypatch.setattr(miku_cli, "InspectionScope", fail)
        args = ["chat", "--inspect", "."]
    elif phase == "archive":
        monkeypatch.setattr(miku_cli, "prepare_archive_reader", fail)
        args = ["chat", "--archive"]
    else:
        monkeypatch.setattr(miku_cli, "create_default_voice_adapters", fail)
        args = ["voice"]
    with handled_startup():
        miku_cli.handle_miku_command(args)
    assert repository.list_sessions() == [] and closes == []


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, KeyboardInterrupt])
def test_failed_session_creation_has_no_cleanup_write_or_retry(startup, monkeypatch, capsys, committed, error_type):
    repository, _, closes = startup
    create = repository.create_session
    attempts = []

    def uncertain(session):
        attempts.append(session.id)
        if committed:
            create(session)
        else:
            with repository.database.connect() as connection:
                connection.execute("INSERT INTO sessions (id, profile_name, mode, memory_policy, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)", (session.id, "miku", "persistent", "enabled", session.created_at.isoformat(), session.updated_at.isoformat(), "{}"))
                raise error_type("rollback confirmado por SQLite")
        raise error_type("commit sin confirmación al caller")

    monkeypatch.setattr(repository, "create_session", uncertain)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: pytest.fail("No preparar un turno de sesión incierta"))
    with handled_startup():
        miku_cli.handle_miku_command(["chat"])
    assert len(attempts) == 1 and closes == []
    assert len(repository.list_sessions()) == int(committed)
    assert "confirmar" in capsys.readouterr().out


@pytest.mark.parametrize("interface", ["pet", "voice_pet"])
@pytest.mark.parametrize("after_start", [False, True])
def test_failed_pet_start_never_runs_callback_and_closes_once(startup, monkeypatch, interface, after_start):
    from miku.pet import runtime, view

    repository, _, closes = startup
    start = Thread.start
    workers = []

    def uncertain_start(thread):
        workers.append(thread)
        if after_start:
            start(thread)
        raise RuntimeError("synthetic thread start")

    monkeypatch.setattr(runtime.Thread, "start", uncertain_start)
    monkeypatch.setattr(view, "run_pet_window", lambda *_a, **_k: pytest.fail("No abrir ventana"))
    monkeypatch.setattr(miku_cli, "run_chat", lambda **_: pytest.fail("No ejecutar callback cancelado"))
    monkeypatch.setattr(miku_cli, "run_prepared_voice", lambda **_: pytest.fail("No ejecutar voz cancelada"))
    args = ["voice", "--pet"] if interface == "voice_pet" else ["pet"]
    try:
        with handled_startup():
            miku_cli.handle_miku_command(args)
    finally:
        for worker in workers:
            if worker.ident is not None:
                worker.join(timeout=3)
    saved = repository.list_sessions()
    assert len(saved) == 1 and saved[0].closed_at is not None
    assert closes == [saved[0].id]
    assert repository.load_session(saved[0].id).messages == []


def test_late_native_pet_thread_after_interruption_cannot_start_chat(startup, monkeypatch):
    import threading
    from miku.pet.controller import PetController

    if not hasattr(threading, "_start_new_thread"):
        pytest.skip("Reproducción de la ventana de arranque de CPython 3.11/3.12")
    repository, sessions, closes = startup
    session = sessions.create()
    controller = PetController()
    native_start = threading._start_new_thread
    bootstrap_allowed, native_done = Event(), Event()
    callbacks = []

    def uncertain_start(function, args):
        def delayed():
            try:
                assert bootstrap_allowed.wait(5)
                function(*args)
            finally:
                native_done.set()
        native_start(delayed, ())
        raise KeyboardInterrupt

    monkeypatch.setattr("miku.pet.view.run_pet_window", lambda *_a, **_k: pytest.fail("No abrir ventana"))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading, "_start_new_thread", uncertain_start)
            with handled_startup():
                miku_cli._run_conversation_with_pet(
                    lambda _: callbacks.append("chat"), session=session,
                    repository=repository, controller=controller,
                )
        assert session.closed_at is not None and closes == [session.id]
        assert controller.close_requested and not controller.visual_active
        before = repository.load_session(session.id)
    finally:
        bootstrap_allowed.set()
        assert native_done.wait(5)
    assert callbacks == [] and closes == [session.id]
    after = repository.load_session(session.id)
    assert (after.closed_at, after.updated_at, after.messages) == (before.closed_at, before.updated_at, [])


@pytest.mark.parametrize("stage", ["import", "thread_constructor", "controller"])
def test_pet_pre_worker_failures_close_session_once(startup, monkeypatch, stage):
    import builtins
    from miku.pet import runtime

    repository, _, closes = startup

    def fail(*_a, **_k):
        raise RuntimeError("fallo sintético antes del worker")

    if stage == "import":
        actual_import = builtins.__import__

        def failed_import(name, *args, **kwargs):
            if name == "miku.pet.runtime":
                raise ImportError("pet sintético no disponible")
            return actual_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failed_import)
    else:
        monkeypatch.setattr(runtime, "Thread" if stage == "thread_constructor" else "PetController", fail)
    with handled_startup():
        miku_cli.handle_miku_command(["pet"])
    saved = repository.list_sessions()
    assert len(saved) == 1 and saved[0].closed_at is not None
    assert closes == [saved[0].id]
    assert repository.load_session(saved[0].id).messages == []


@pytest.mark.parametrize("interface", ["voice", "voice_pet"])
def test_voice_loop_constructor_failure_after_turn_composition_closes_once(startup, monkeypatch, interface):
    repository, _, closes = startup

    def fail(**kwargs):
        assert kwargs["turn_service"].session.id
        raise ValueError("fallo sintético al construir VoiceLoop")

    monkeypatch.setattr(miku_cli, "VoiceLoop", fail)
    with handled_startup():
        miku_cli.handle_miku_command(["voice", "--pet"] if interface == "voice_pet" else ["voice"])
    saved = repository.list_sessions()
    assert len(saved) == 1 and saved[0].closed_at is not None
    assert closes == [saved[0].id]
    assert repository.load_session(saved[0].id).messages == []


@pytest.mark.parametrize("notice_error", [KeyboardInterrupt, EOFError, OSError])
def test_failed_startup_notice_cannot_prevent_or_repeat_cleanup(startup, monkeypatch, notice_error):
    repository, sessions, closes = startup
    session = sessions.create()
    monkeypatch.setattr(miku_cli, "create_provider", lambda: (_ for _ in ()).throw(RuntimeError("provider")))
    monkeypatch.setattr(miku_cli, "print", lambda *_a, **_k: (_ for _ in ()).throw(notice_error()), raising=False)
    with handled_startup():
        miku_cli.run_chat(session=session, repository=repository)
    assert closes == [session.id]
    assert session.closed_at is not None and repository.load_session(session.id).closed_at is not None


@pytest.mark.parametrize("committed", [False, True])
def test_failed_pet_start_with_prebuilt_voice_does_not_retry_uncertain_close(startup, monkeypatch, capsys, committed):
    from miku.pet import runtime

    repository, _, closes = startup
    close = repository.close_session
    attempted = []

    def uncertain_close(session_id):
        attempted.append(session_id)
        if committed:
            close(session_id)
        raise sqlite3.OperationalError("cierre incierto")

    monkeypatch.setattr(repository, "close_session", uncertain_close)
    monkeypatch.setattr(runtime.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("worker")))
    with handled_startup():
        miku_cli.handle_miku_command(["voice", "--pet"])
    saved = repository.list_sessions()
    assert len(saved) == 1 and attempted == [saved[0].id]
    assert closes == ([saved[0].id] if committed else [])
    assert (saved[0].closed_at is not None) is committed
    assert repository.load_session(saved[0].id).messages == []
    output = capsys.readouterr().out
    assert "No pude confirmar" in output and "No se reintent" in output
    assert "cerrada sin iniciar" not in output
