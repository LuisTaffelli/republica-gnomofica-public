from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from types import SimpleNamespace
import sqlite3

import pytest

from cli import miku_cli
from core.conversation import ChatSession, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from miku.turn import MikuTurnService, TurnRequest


@contextmanager
def interrupt_handled():
    try:
        yield
    except KeyboardInterrupt:
        pytest.fail("La interrupción debe informarse sin escapar del adaptador")


class SyntheticMiku:
    memory_service = None
    action_coordinator = None
    last_memory_error = None

    def listen(self, *_args, **_kwargs):
        return "respuesta sintética"

    listen_to_human = listen


def make_service(tmp_path, miku=None, session=None):
    database = SQLiteDatabase(tmp_path / "lifecycle.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    if session is None:
        session = ChatSession(profile_name="synthetic", memory_policy="disabled")
        repository.create_session(session)
    return MikuTurnService(session=session, repository=repository, miku=miku or SyntheticMiku())


def count_closes(monkeypatch, service):
    attempts = []
    original = service.repository.close_session

    def close(session_id):
        attempts.append(session_id)
        original(session_id)

    monkeypatch.setattr(service.repository, "close_session", close)
    return attempts


def test_close_is_once_per_service_and_a_resumed_service_can_continue(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    attempts = count_closes(monkeypatch, service)
    service.close()
    first_closed_at = service.session.closed_at
    service.close()
    assert attempts == [service.session.id]
    assert service.session.closed_at == first_closed_at
    with pytest.raises(RuntimeError, match="cierre|cerrad"):
        service.handle(TurnRequest("no iniciar otro turno"))
    assert service.repository.load_session(service.session.id).messages == []

    resumed = service.repository.load_session(service.session.id)
    new_service = MikuTurnService(session=resumed, repository=service.repository, miku=SyntheticMiku())
    new_service.handle(TurnRequest("continuación explícita"))
    new_service.close()
    assert attempts == [service.session.id, service.session.id]
    assert len(service.repository.load_session(service.session.id).messages) == 2


@pytest.mark.parametrize("error", [sqlite3.OperationalError("fallo"), KeyboardInterrupt()])
def test_uncertain_close_is_not_retried_and_does_not_advance_ram(tmp_path, monkeypatch, error):
    service = make_service(tmp_path)
    previous_updated = service.session.updated_at
    attempts = []

    def fail(session_id):
        attempts.append(session_id)
        raise error

    monkeypatch.setattr(service.repository, "close_session", fail)
    with pytest.raises(type(error)):
        service.close()
    with interrupt_handled(), pytest.raises(RuntimeError, match="cierre"):
        service.close()
    with pytest.raises(RuntimeError, match="cierre|cerrad"):
        service.handle(TurnRequest("no reintentar"))
    assert attempts == [service.session.id]
    assert service.session.closed_at is None
    assert service.session.updated_at == previous_updated
    assert service.repository.load_session(service.session.id).closed_at is None


def test_close_cannot_race_a_response_in_flight(tmp_path):
    entered, release = Event(), Event()

    class SlowMiku(SyntheticMiku):
        def listen(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(5)
            return "respuesta terminada antes del cierre"

    service = make_service(tmp_path, SlowMiku())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.handle, TurnRequest("turno en vuelo"))
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeError, match="turno"):
                service.close()
            assert service.session.closed_at is None
            assert service.repository.load_session(service.session.id).closed_at is None
        finally:
            release.set()
        assert future.result(timeout=5).response_text == "respuesta terminada antes del cierre"
    service.close()
    restored = service.repository.load_session(service.session.id)
    assert restored.messages == service.session.messages
    assert len(restored.messages) == 2 and restored.closed_at is not None


def test_concurrent_close_requests_write_once(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    attempts = count_closes(monkeypatch, service)
    barrier = Barrier(2)

    def close():
        barrier.wait(timeout=5)
        service.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(close) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    assert attempts == [service.session.id]


@pytest.mark.parametrize("stop_at", ["before_input", "after_input", "during_turn"])
def test_chat_observes_pet_stop_at_safe_boundaries(tmp_path, monkeypatch, stop_at):
    stop = Event()

    class Miku(SyntheticMiku):
        def listen_to_human(self, *_args, **_kwargs):
            stop.set()
            return "respuesta que se conserva"

    service = make_service(tmp_path, Miku())
    attempts = count_closes(monkeypatch, service)
    inputs = []

    def prompt(_):
        inputs.append(True)
        assert len(inputs) == 1, "No pedir otro turno después de parar"
        if stop_at == "after_input":
            stop.set()
        return "hola"

    if stop_at == "before_input":
        stop.set()
    monkeypatch.setattr("builtins.input", prompt)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr(miku_cli, "build_miku_turn_service", lambda **_: service)
    miku_cli.run_chat(session=service.session, repository=service.repository, stop_requested=stop.is_set)
    restored = service.repository.load_session(service.session.id)
    assert len(inputs) == (0 if stop_at == "before_input" else 1)
    assert len(restored.messages) == (2 if stop_at == "during_turn" else 0)
    assert restored.messages == service.session.messages
    assert restored.closed_at is not None and attempts == [service.session.id]


@pytest.mark.parametrize("interface", ["chat", "voice"])
def test_header_interruption_closes_without_starting_a_turn(tmp_path, monkeypatch, interface):
    service = make_service(tmp_path)
    attempts = count_closes(monkeypatch, service)
    monkeypatch.setattr(miku_cli, "print_session_header", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("No pedir entrada"))
    monkeypatch.setattr(miku_cli, "print_goodbye", lambda: pytest.fail("No imprimir despedida tras interrumpir encabezado"))
    if interface == "chat":
        monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
        monkeypatch.setattr(miku_cli, "build_miku_turn_service", lambda **_: service)
        with interrupt_handled():
            miku_cli.run_chat(session=service.session, repository=service.repository)
    else:
        loop = SimpleNamespace(turn_service=service, run=lambda: pytest.fail("No arrancar voz"))
        with interrupt_handled():
            miku_cli.run_prepared_voice(loop=loop, session=service.session, memory_enabled=False)
    assert attempts == [service.session.id]
    assert service.session.closed_at is not None


def test_voice_does_not_print_goodbye_when_close_is_unconfirmed(monkeypatch):
    monkeypatch.setattr(miku_cli, "print_session_header", lambda *_a, **_k: None)
    monkeypatch.setattr(miku_cli, "print_goodbye", lambda: pytest.fail("Cierre no confirmado"))
    miku_cli.run_prepared_voice(loop=SimpleNamespace(run=lambda: False), session=object(), memory_enabled=False)


def test_voice_goodbye_interruption_does_not_retry_close(monkeypatch):
    monkeypatch.setattr(miku_cli, "print_session_header", lambda *_a, **_k: None)
    monkeypatch.setattr(miku_cli, "print_goodbye", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with interrupt_handled():
        miku_cli.run_prepared_voice(loop=SimpleNamespace(run=lambda: True), session=object(), memory_enabled=False)


@pytest.mark.parametrize("phase", ["input", "presentation"])
def test_console_io_failure_outside_turn_closes_once(tmp_path, monkeypatch, phase):
    service = make_service(tmp_path)
    attempts = count_closes(monkeypatch, service)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr(miku_cli, "build_miku_turn_service", lambda **_: service)

    def prompt(_):
        if phase == "input":
            raise OSError("entrada cerrada")
        return "hola"

    real_print = print

    def present(*args, **kwargs):
        if args and str(args[0]).startswith("\nMiku >"):
            raise BrokenPipeError("salida cerrada")
        return real_print(*args, **kwargs)

    monkeypatch.setattr("builtins.input", prompt)
    monkeypatch.setattr("builtins.print", present)
    miku_cli.run_chat(session=service.session, repository=service.repository)
    assert attempts == [service.session.id]
    restored = service.repository.load_session(service.session.id)
    assert restored.closed_at is not None
    assert restored.messages == service.session.messages
    assert len(restored.messages) == (0 if phase == "input" else 2)


@pytest.mark.parametrize("interface", ["chat", "voice"])
@pytest.mark.parametrize("save_fails", [False, True])
def test_pet_main_interrupt_waits_for_worker_and_confirmed_persistence(
    tmp_path, monkeypatch, interface, save_fails,
):
    from miku.pet.controller import PetController
    from miku.voice import VoiceLoop

    entered, release = Event(), Event()
    writes, controllers, prompts = [], [], []

    class SlowMiku(SyntheticMiku):
        def listen(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(5)
            return "respuesta en vuelo preservada"

        listen_to_human = listen

    service = make_service(tmp_path, SlowMiku())
    repository = service.repository
    original_insert = repository._insert_message
    original_close = repository.close_session

    def insert(connection, session_id, message):
        writes.append(message.role)
        original_insert(connection, session_id, message)
        if save_fails and message.role == "assistant":
            raise sqlite3.OperationalError("fallo sintético de respuesta")

    def close(session_id):
        writes.append("close")
        original_close(session_id)

    def prompt(_):
        prompts.append(True)
        assert len(prompts) == 1, "No iniciar otro turno tras Ctrl+C principal"
        return "hola" if interface == "chat" else ""

    def window(controller, **_kwargs):
        controllers.append(controller)
        try:
            assert entered.wait(5)
            raise KeyboardInterrupt()
        finally:
            if not entered.is_set():
                release.set()

    original_stop = PetController.request_stop

    def request_stop(controller):
        original_stop(controller)
        release.set()

    monkeypatch.setattr(repository, "_insert_message", insert)
    monkeypatch.setattr(repository, "close_session", close)
    monkeypatch.setattr(PetController, "request_stop", request_stop)
    monkeypatch.setattr("miku.pet.view.run_pet_window", window)
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(miku_cli, "prepare_chat_session", lambda **_: service.session)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: object())
    monkeypatch.setattr(miku_cli, "build_miku_turn_service", lambda **_: service)
    monkeypatch.setattr("builtins.input", prompt)
    monkeypatch.setattr(miku_cli, "create_default_voice_adapters", lambda **_: (
        SimpleNamespace(transcribe=lambda: SimpleNamespace(text="hola")),
        SimpleNamespace(speak=lambda _: pytest.fail("No iniciar TTS después de parar")),
    ))
    monkeypatch.setattr(miku_cli, "VoiceLoop", lambda **kwargs: VoiceLoop(**kwargs, prompt=prompt))
    miku_cli.handle_miku_command([interface, "--pet", "--no-memory"])

    assert len(controllers) == 1
    assert controllers[0].stop_requested and controllers[0].close_requested
    assert writes == (["user", "assistant"] if save_fails else ["user", "assistant", "close"])
    restored = repository.load_session(service.session.id)
    assert restored.messages == service.session.messages
    assert len(restored.messages) == (1 if save_fails else 2)
    assert (restored.closed_at is None) == save_fails
