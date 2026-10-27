from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier, Event, Thread

import pytest

from core.context.context_manager import ContextManager
from core.context.profile_loader import ProfileLoader
from core.context.prompt_composer import PromptComposer
from core.conversation import ChatSession, Message, SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from miku.miku import Miku
from miku.turn import MikuTurnService
from miku.web.coordinator import MAX_TEXT_LENGTH, WebCoordinator, WebError
from miku.web.receipts import ProcessFileLock, ReceiptStore, StorageInUseError


class Provider:
    def __init__(self):
        self.prompts = []
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.error = None

    def ask(self, prompt):
        self.prompts.append(prompt)
        self.entered.set()
        assert self.release.wait(10), "Test did not release synthetic provider"
        if self.error:
            raise self.error
        return "Respuesta ficticia <script>alert(1)</script>"


@pytest.fixture
def setup_web(tmp_path):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    profile = tmp_path / "profiles" / "miku"
    profile.mkdir(parents=True)
    (profile / "profile.toml").write_text(
        'name="miku"\nidentity="identity.md"\nprompt="prompt.md"\n', encoding="utf-8"
    )
    (profile / "identity.md").write_text("Identidad ficticia", encoding="utf-8")
    (profile / "prompt.md").write_text("Instrucción ficticia", encoding="utf-8")
    invariants = tmp_path / "invariants.md"
    invariants.write_text("Invariantes ficticios", encoding="utf-8")
    provider = Provider()
    built = []

    def build(session):
        manager = ContextManager(
            repository, ProfileLoader(tmp_path / "profiles"), invariants,
            include_memory=False,
        )
        turn = MikuTurnService(
            session=session, repository=repository,
            miku=Miku(provider=provider, context_builder=manager, serializer=PromptComposer(),
                      memory_enabled=False),
        )
        built.append(turn)
        return turn

    coordinator = WebCoordinator(repository, tmp_path / "web.db", build)
    yield coordinator, provider, repository, build, built
    provider.release.set()
    coordinator.shutdown()


def completed(coordinator):
    assert coordinator._worker_done.wait(5)


def error_code(code, function, *args):
    with pytest.raises(WebError) as captured:
        function(*args)
    assert captured.value.code == code
    return captured.value


def test_turn_survives_client_disappearance_and_reconnects_by_id(setup_web):
    web, provider, repository, _, _ = setup_web
    session = web.create_session()
    provider.release.clear()
    receipt = web.submit_turn(session["id"], "request-1", "Hola ficticio")
    assert receipt["state"] == "running"
    assert provider.entered.wait(5)
    assert web.active_request() == web.get_request("request-1")
    provider.release.set()  # No client awaits the result or closes the session.
    completed(web)
    result = web.get_request("request-1")
    assert result["state"] == "completed"
    assert result["result"]["response_text"].startswith("Respuesta ficticia")
    detail = web.get_session(session["id"])
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["session"]["closed_at"] is None
    persisted = repository.load_session(session["id"])
    assert persisted.messages[0].metadata["interface_source"] == "web"
    assert persisted.memory_policy == "enabled"  # Local preference unchanged.
    assert web.active_request() is None


@pytest.mark.parametrize("error_type,expected_state", [(RuntimeError, "failed"), (KeyboardInterrupt, "interrupted")])
def test_startup_factory_failure_leaves_receipt_without_phantom_turn_or_close(setup_web, error_type, expected_state):
    web, provider, repository, build, built = setup_web
    session = web.create_session()
    attempts = []

    def fail_before_turn(session):
        attempts.append(session.id)
        raise error_type("fallo sintético de configuración")

    web._build_turn = fail_before_turn
    web.submit_turn(session["id"], "startup-failed", "hola")
    completed(web)
    result = web.get_request("startup-failed")
    assert result["state"] == expected_state
    assert result["result"]["user_message_id"] is None
    assert result["result"]["response_message_id"] is None
    assert web.active_request() is None and provider.prompts == [] and built == []
    saved = repository.load_session(session["id"])
    assert saved.messages == [] and saved.closed_at is None
    assert web.submit_turn(session["id"], "startup-failed", "hola") == result
    assert attempts == [session["id"]]
    web._build_turn = build
    web.submit_turn(session["id"], "new-request", "nuevo intento explícito")
    completed(web)
    assert web.get_request("new-request")["state"] == "completed"
    assert len(provider.prompts) == 1 and len(repository.load_session(session["id"]).messages) == 2


def test_duplicate_concurrent_posts_have_one_cognitive_turn(setup_web):
    web, provider, _, _, _ = setup_web
    session = web.create_session()
    provider.release.clear()
    barrier = Barrier(3)
    results = []

    def post():
        barrier.wait()
        results.append(web.submit_turn(session["id"], "same-id", "Mismo texto"))

    clients = [Thread(target=post) for _ in range(2)]
    for client in clients:
        client.start()
    barrier.wait()
    for client in clients:
        client.join(5)
        assert not client.is_alive()
    assert len(results) == 2 and results[0] == results[1]
    assert provider.entered.wait(5)
    assert len(provider.prompts) == 1
    error_code("request_conflict", web.submit_turn, session["id"], "same-id", "Otro texto")
    error_code("busy", web.submit_turn, session["id"], "other-id", "Mismo texto")
    error_code("busy", web.close_session, session["id"])
    error_code("busy", web.create_session)
    provider.release.set()
    completed(web)
    assert web.submit_turn(session["id"], "same-id", "Mismo texto")["state"] == "completed"
    assert len(provider.prompts) == 1


def test_id_is_bound_to_exact_text_and_session(setup_web):
    web, _, _, _, _ = setup_web
    first, second = web.create_session(), web.create_session()
    web.submit_turn(first["id"], "id-1", "texto")
    completed(web)
    error_code("request_conflict", web.submit_turn, second["id"], "id-1", "texto")
    error_code("request_conflict", web.submit_turn, first["id"], "id-1", "texto ")


def test_receipt_and_response_survive_restart_without_duplicate_content(setup_web, tmp_path):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    web.submit_turn(session["id"], "durable-id", "No copiar este texto al ledger")
    completed(web)
    old = web.get_request("durable-id")
    web.shutdown()
    connection = sqlite3.connect(tmp_path / "web.db")
    try:
        dumped = "\n".join(connection.iterdump())
    finally:
        connection.close()
    assert "No copiar" not in dumped and "Respuesta ficticia" not in dumped
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        assert restarted.get_request("durable-id") == old
        assert restarted.submit_turn(session["id"], "durable-id", "No copiar este texto al ledger") == old
        assert len(provider.prompts) == 1
    finally:
        restarted.shutdown()


def test_orphan_running_is_interrupted_never_replayed(setup_web, tmp_path):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    web.receipts.begin("orphan", session["id"], sha256(b"hola").hexdigest())
    # A user message may have committed before the process died.
    repository.save_message(session["id"], Message(role="user", content="hola"))
    web.shutdown()
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        receipt = restarted.submit_turn(session["id"], "orphan", "hola")
        assert receipt["state"] == "interrupted"
        assert receipt["result"]["error_type"] == "server_interrupted"
        assert not provider.prompts
        assert len(restarted.get_session(session["id"])["messages"]) == 1
    finally:
        restarted.shutdown()


def test_two_web_instances_cannot_share_conversations_or_recover_live_receipt(setup_web, tmp_path):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    provider.release.clear()
    web.submit_turn(session["id"], "live", "hola")
    assert provider.entered.wait(5)
    for receipt_name in ("web.db", "other-web.db"):
        error_code("storage_in_use", WebCoordinator, repository, tmp_path / receipt_name, build)
    assert web.get_request("live")["state"] == "running"
    provider.release.set()
    completed(web)


def test_os_lock_released_after_process_exit(tmp_path):
    lock = tmp_path / "owner.lock"
    code = (
        "from pathlib import Path; from miku.web.receipts import ProcessFileLock; "
        "import sys; owner = ProcessFileLock(Path(sys.argv[1]))"
    )
    subprocess.run([sys.executable, "-B", "-c", code, str(lock)], check=True,
                   capture_output=True, timeout=20)
    acquired = ProcessFileLock(lock)
    try:
        with pytest.raises(StorageInUseError):
            ProcessFileLock(lock)
    finally:
        acquired.close()


def test_shutdown_waits_worker_and_keeps_exclusion_without_auto_close(setup_web, tmp_path):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    provider.release.clear()
    web.submit_turn(session["id"], "waiting", "hola")
    assert provider.entered.wait(5)
    started = Event()

    def stop():
        started.set()
        web.shutdown()

    closing = Thread(target=stop)
    closing.start()
    assert started.wait(5)
    error_code("storage_in_use", WebCoordinator, repository, tmp_path / "web.db", build)
    provider.release.set()
    closing.join(5)
    assert not closing.is_alive()
    assert repository.load_session(session["id"]).closed_at is None
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        assert restarted.get_request("waiting")["state"] == "completed"
    finally:
        restarted.shutdown()


def test_explicit_close_is_idempotent_and_closed_history_readable(setup_web, monkeypatch):
    web, provider, repository, _, _ = setup_web
    session = web.create_session()
    calls = []
    close = repository.close_session

    def observed(session_id):
        calls.append(session_id)
        close(session_id)

    monkeypatch.setattr(repository, "close_session", observed)
    closed = web.close_session(session["id"])
    assert closed["closed_at"] is not None
    assert web.close_session(session["id"]) == closed
    assert web.get_session(session["id"])["session"] == closed
    assert calls == [session["id"]] and not provider.prompts
    error_code("session_closed", web.submit_turn, session["id"], "closed-turn", "hola")


def test_failed_close_does_not_update_ram_or_retry(setup_web, monkeypatch):
    web, _, repository, _, built = setup_web
    session = web.create_session()
    calls = []

    def broken(session_id):
        calls.append(session_id)
        raise sqlite3.OperationalError("private-path-do-not-echo")

    monkeypatch.setattr(repository, "close_session", broken)
    error = error_code("close_uncertain", web.close_session, session["id"])
    assert "private-path" not in error.message
    error_code("close_uncertain", web.close_session, session["id"])
    error_code("close_uncertain", web.submit_turn, session["id"], "after-close", "hola")
    assert built[0].session.closed_at is None
    assert repository.load_session(session["id"]).closed_at is None
    assert len(calls) == 1


@pytest.mark.parametrize("exception", [RuntimeError("falla sintética"), KeyboardInterrupt()])
def test_failed_turn_does_not_close_and_is_not_replayed(setup_web, exception):
    web, provider, repository, _, _ = setup_web
    session = web.create_session()
    provider.error = exception
    web.submit_turn(session["id"], "failed", "hola")
    completed(web)
    state = "failed" if isinstance(exception, Exception) else "interrupted"
    assert web.get_request("failed")["state"] == state
    assert web.submit_turn(session["id"], "failed", "hola")["state"] == state
    assert len(provider.prompts) == 1
    assert repository.load_session(session["id"]).closed_at is None


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_write_failure_preserves_ram_and_never_repeats_turn(setup_web, monkeypatch, role):
    web, provider, repository, _, built = setup_web
    session = web.create_session()
    save = repository.save_message

    def broken(session_id, message):
        if message.role == role:
            raise sqlite3.OperationalError("falla escritura")
        save(session_id, message)

    monkeypatch.setattr(repository, "save_message", broken)
    web.submit_turn(session["id"], "write-error", "hola")
    completed(web)
    count = len(provider.prompts)
    assert web.submit_turn(session["id"], "write-error", "hola")["state"] == "failed"
    assert len(provider.prompts) == count
    restored = repository.load_session(session["id"])
    assert restored.messages == built[0].session.messages
    assert restored.closed_at is None


@pytest.mark.parametrize("after_commit", [False, True])
def test_uncertain_receipt_completion_blocks_writes_and_never_replays(
    setup_web, tmp_path, monkeypatch, after_commit,
):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    finish = web.receipts.finish

    def broken(*args, **kwargs):
        if after_commit:
            finish(*args, **kwargs)
        raise sqlite3.OperationalError("resultado incierto")

    monkeypatch.setattr(web.receipts, "finish", broken)
    web.submit_turn(session["id"], "uncertain", "hola")
    completed(web)
    assert web.get_request("uncertain")["state"] == ("completed" if after_commit else "interrupted")
    error_code("receipt_uncertain", web.submit_turn, session["id"], "another", "hola")
    assert len(repository.load_session(session["id"]).messages) == 2
    web.shutdown()
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        assert restarted.submit_turn(session["id"], "uncertain", "hola")["state"] == (
            "completed" if after_commit else "interrupted"
        )
        assert len(provider.prompts) == 1
    finally:
        restarted.shutdown()


def test_uncertain_receipt_acceptance_never_starts_provider(setup_web, monkeypatch):
    web, provider, _, _, _ = setup_web
    session = web.create_session()
    begin = web.receipts.begin

    def broken(*args):
        begin(*args)
        raise sqlite3.OperationalError("commit conocido sólo por SQLite")

    monkeypatch.setattr(web.receipts, "begin", broken)
    error_code("receipt_uncertain", web.submit_turn, session["id"], "begin-error", "hola")
    assert web.submit_turn(session["id"], "begin-error", "hola")["state"] == "interrupted"
    error_code("receipt_uncertain", web.create_session)
    assert not provider.prompts


def test_action_other_profile_and_branches_are_not_available(setup_web):
    web, _, repository, _, _ = setup_web
    ordinary = web.create_session()
    forbidden = [ChatSession(profile_name="miku", mode="action"), ChatSession(profile_name="other")]
    for session in forbidden:
        repository.create_session(session)
    anchor = Message(role="user", content="ancla")
    repository.save_message(ordinary["id"], anchor)
    forbidden.append(repository.create_branch(ordinary["id"], anchor.id))
    assert [session["id"] for session in web.list_sessions()] == [ordinary["id"]]
    for session in forbidden:
        error_code("session_not_found", web.get_session, session.id)
        error_code("session_not_found", web.submit_turn, session.id, "unavailable", "hola")
        error_code("session_not_found", web.close_session, session.id)


@pytest.mark.parametrize("setting", ["memory", "retrieval", "action", "archive", "scope"])
def test_unsafe_factory_cannot_grant_capabilities(setup_web, setting):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()

    def unsafe(session):
        turn = build(session)
        if setting == "memory":
            turn.miku.memory_enabled = True
        elif setting == "retrieval":
            turn.miku.context_builder.include_memory = True
        elif setting == "action":
            turn.miku.action_coordinator = object()
        elif setting == "archive":
            turn.archive_reader = object()
        else:
            turn.inspection_scope = object()
        return turn

    web._build_turn = unsafe
    web.submit_turn(session["id"], "unsafe", "confirmo /allow C:\\privado")
    completed(web)
    assert web.get_request("unsafe")["state"] == "failed"
    assert not provider.prompts
    assert repository.load_session(session["id"]).messages == []


@pytest.mark.parametrize("text", ["", " ", None, "x" * (MAX_TEXT_LENGTH + 1), "\ud800"])
def test_invalid_text_is_rejected_before_receipt_or_provider(setup_web, text):
    web, provider, _, _, _ = setup_web
    session = web.create_session()
    error_code("invalid_text", web.submit_turn, session["id"], "bad-text", text)
    error_code("request_not_found", web.get_request, "bad-text")
    assert not provider.prompts


@pytest.mark.parametrize("identifier", ["../secret", "", "x" * 129, None])
def test_invalid_ids_cannot_be_paths(setup_web, identifier):
    web, _, _, _, _ = setup_web
    error_code("invalid_id", web.get_session, identifier)
    error_code("invalid_id", web.get_request, identifier)


def test_receipt_store_refuses_other_database_and_does_not_corrupt_it(tmp_path):
    receipt_path = tmp_path / "receipts.db"
    ReceiptStore(receipt_path, tmp_path / "first.db")
    old = receipt_path.read_bytes()
    with pytest.raises(ValueError, match="otro almacenamiento"):
        ReceiptStore(receipt_path, tmp_path / "second.db")
    assert receipt_path.read_bytes() == old
    with sqlite3.connect(receipt_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_receipt_store_refuses_unrelated_existing_schema(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE valuable (id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="no es compatible"):
        ReceiptStore(path, tmp_path / "conversations.db")
    assert path.read_bytes() == before


def test_provider_diagnostic_is_redacted_from_history_and_receipt(setup_web):
    web, provider, repository, _, _ = setup_web
    session = web.create_session()
    provider.error = RuntimeError("private-endpoint /private/path credentials=secret")
    web.submit_turn(session["id"], "private-error", "hola")
    completed(web)
    receipt = web.get_request("private-error")
    history = web.get_session(session["id"])
    assert "private-endpoint" not in str(receipt) + str(history)
    assert receipt["result"]["error_type"] == "turn_error"
    assert "private-endpoint" in repository.load_session(session["id"]).messages[-1].content


@pytest.mark.parametrize("crash_phase", ["provider", "receipt"])
def test_real_process_crash_releases_lock_and_recovers_without_replay(
    setup_web, tmp_path, crash_phase,
):
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    web.shutdown()
    code = '''
import os, sys
from pathlib import Path
from core.database import SQLiteDatabase
from core.conversation import SQLiteConversationRepository
from core.context.context_manager import ContextManager
from core.context.profile_loader import ProfileLoader
from core.context.prompt_composer import PromptComposer
from miku.miku import Miku
from miku.turn import MikuTurnService
from miku.web.coordinator import WebCoordinator
root, session_id, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
repository = SQLiteConversationRepository(SQLiteDatabase(root / "conversations.db"))
class CrashingProvider:
    def ask(self, prompt):
        if phase == "provider":
            os._exit(23)
        return "Respuesta antes de morir"
def build(session):
    manager = ContextManager(repository, ProfileLoader(root / "profiles"),
                             root / "invariants.md", include_memory=False)
    return MikuTurnService(session=session, repository=repository,
        miku=Miku(provider=CrashingProvider(), context_builder=manager,
                  serializer=PromptComposer(), memory_enabled=False))
web = WebCoordinator(repository, root / "web.db", build)
if phase == "receipt":
    web.receipts.finish = lambda *args, **kwargs: os._exit(23)
web.submit_turn(session_id, "crashed", "texto crash")
web.shutdown()
'''
    child = subprocess.run(
        [sys.executable, "-B", "-c", code, str(tmp_path), session["id"], crash_phase],
        capture_output=True, timeout=20,
    )
    assert child.returncode == 23, child.stderr.decode(errors="replace")
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        assert restarted.get_request("crashed")["state"] == "interrupted"
        assert restarted.submit_turn(session["id"], "crashed", "texto crash")["state"] == "interrupted"
        assert not provider.prompts
        assert len(repository.load_session(session["id"]).messages) == (1 if crash_phase == "provider" else 2)
    finally:
        restarted.shutdown()


@pytest.mark.parametrize("after_start", [False, True])
def test_uncertain_worker_start_never_allows_another_turn(setup_web, monkeypatch, after_start):
    web, provider, repository, _, _ = setup_web
    session = web.create_session()
    start = Thread.start
    provider.release.clear()

    def uncertain_start(worker):
        if after_start:
            start(worker)
        raise RuntimeError("No se confirmó el arranque")

    monkeypatch.setattr(Thread, "start", uncertain_start)
    error_code("worker_unavailable", web.submit_turn, session["id"], "start-error", "hola")
    error_code("receipt_uncertain", web.submit_turn, session["id"], "another", "hola")
    assert web.submit_turn(session["id"], "start-error", "hola")["state"] == "failed"
    completed(web)
    assert not provider.prompts
    assert repository.load_session(session["id"]).closed_at is None


def test_thread_constructor_failure_is_durable_and_shutdown_can_finish(setup_web, monkeypatch, tmp_path):
    from miku.web import coordinator as coordinator_module

    web, provider, repository, build, _ = setup_web
    session = web.create_session()

    def no_thread(**kwargs):
        raise MemoryError("synthetic constructor failure")

    monkeypatch.setattr(coordinator_module, "Thread", no_thread)
    error_code("worker_unavailable", web.submit_turn, session["id"], "constructor-error", "hola")
    assert web.get_request("constructor-error")["state"] == "failed"
    assert web._worker_done.is_set()
    web.shutdown()
    restarted = WebCoordinator(repository, tmp_path / "web.db", build)
    try:
        assert restarted.submit_turn(session["id"], "constructor-error", "hola")["state"] == "failed"
        assert not provider.prompts
    finally:
        restarted.shutdown()


def test_late_native_thread_after_start_error_cannot_touch_storage(setup_web, monkeypatch, tmp_path):
    import threading

    if not hasattr(threading, "_start_new_thread"):
        pytest.skip("Reproduction targets the CPython 3.11/3.12 thread-start window")
    web, provider, repository, build, _ = setup_web
    session = web.create_session()
    original_start = threading._start_new_thread
    bootstrap_allowed, native_done = Event(), Event()

    def uncertain_native_start(function, args):
        def delayed():
            try:
                assert bootstrap_allowed.wait(10)
                function(*args)
            finally:
                native_done.set()

        original_start(delayed, ())
        raise KeyboardInterrupt()

    restarted = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading, "_start_new_thread", uncertain_native_start)
            error_code("worker_unavailable", web.submit_turn, session["id"], "late-start", "hola")
        assert not web._worker._started.is_set()
        web.shutdown()
        restarted = WebCoordinator(repository, tmp_path / "web.db", build)
        provider.release.clear()
        restarted.submit_turn(session["id"], "new-turn", "turno nuevo")
        assert provider.entered.wait(5)
        bootstrap_allowed.set()
        assert native_done.wait(5)
        assert len(provider.prompts) == 1
        assert len(repository.load_session(session["id"]).messages) == 1
        assert restarted.get_request("late-start")["state"] == "failed"
    finally:
        bootstrap_allowed.set()
        provider.release.set()
        assert native_done.wait(5)
        if restarted is not None:
            restarted.shutdown()


def test_shutdown_retains_locks_during_real_repeated_sigint(tmp_path):
    # Isolate actual SIGINT in a child process: pytest itself must not receive it.
    code = '''
from pathlib import Path
import _thread, runpy, signal, sys, threading, time
from miku.web.coordinator import WebCoordinator, WebError
namespace = runpy.run_path(sys.argv[1])
fixture = namespace["setup_web"].__wrapped__(Path(sys.argv[2]))
web, provider, repository, build, _ = next(fixture)
session = web.create_session()
provider.release.clear()
web.submit_turn(session["id"], "sigint", "hola")
assert provider.entered.wait(5)
interrupts = []
def interrupt(signum, frame):
    interrupts.append(signum)
    raise KeyboardInterrupt()
signal.signal(signal.SIGINT, interrupt)
observed = {}
checked = threading.Event()
def check_exclusion():
    try:
        time.sleep(0.7)
        observed["still_waiting"] = not web._closed
        try:
            second = WebCoordinator(repository, Path(sys.argv[2]) / "web.db", build)
        except WebError as error:
            observed["error"] = error.code
        else:
            observed["error"] = "lock_released_early"
            second.shutdown()
    finally:
        provider.release.set()
        checked.set()
checker = threading.Thread(target=check_exclusion)
checker.start()
timers = [threading.Timer(delay, _thread.interrupt_main) for delay in (0.05, 0.3)]
for timer in timers:
    timer.start()
try:
    web.shutdown()
    assert checked.wait(5)
    assert interrupts, "No real SIGINT was delivered"
    assert observed == {"still_waiting": True, "error": "storage_in_use"}, observed
    assert web._worker_done.is_set()
    assert len(repository.load_session(session["id"]).messages) == 2
    assert len(provider.prompts) == 1
    restarted = WebCoordinator(repository, Path(sys.argv[2]) / "web.db", build)
    try:
        assert restarted.get_request("sigint")["state"] == "completed"
    finally:
        restarted.shutdown()
finally:
    for timer in timers:
        timer.cancel()
    provider.release.set()
    checked.wait(5)
    fixture.close()
'''
    child = subprocess.run(
        [sys.executable, "-B", "-c", code, str(Path(__file__).resolve()), str(tmp_path)],
        capture_output=True, timeout=20,
    )
    assert child.returncode == 0, child.stdout.decode(errors="replace") + child.stderr.decode(errors="replace")
