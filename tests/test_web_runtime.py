from uuid import uuid4

import pytest

from core.context.profile_loader import ProfileLoader
from core.conversation import SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from miku.web.runtime import create_coordinator, serve_loopback


def test_web_composition_never_opens_memory_or_enables_actions(tmp_path, monkeypatch):
    import miku.turn
    from core.memory import repository as memories

    memory_before = {p: p.read_bytes() for p in tmp_path.rglob("*memory*") if p.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError("La web no debe componer memoria personal.")

    monkeypatch.setattr(miku.turn, "SQLiteMemoryRepository", forbidden)
    monkeypatch.setattr(memories.SQLiteMemoryRepository, "__init__", forbidden)
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    profiles = tmp_path / "profiles"
    profile = profiles / "miku"
    profile.mkdir(parents=True)
    (profile / "profile.toml").write_text(
        'name="miku"\nidentity="identity.md"\nprompt="prompt.md"\n', encoding="utf-8",
    )
    (profile / "identity.md").write_text("Identidad sintética", encoding="utf-8")
    (profile / "prompt.md").write_text("Instrucciones sintéticas", encoding="utf-8")
    invariants = tmp_path / "invariants.md"
    invariants.write_text("Invariantes sintéticos", encoding="utf-8")

    class Provider:
        prompts = []

        def ask(self, prompt):
            self.prompts.append(prompt)
            return "respuesta sintética"

    provider = Provider()
    runtime = create_coordinator(
        repository=repository, receipt_path=tmp_path / "receipts.db", provider=provider,
        profile_loader=ProfileLoader(profiles), invariants_path=invariants,
    )
    try:
        session_id = runtime.create_session()["id"]
        service = runtime._build_turn(repository.load_session(session_id))
        assert service.miku.memory_service is None
        assert service.miku.context_builder.memory_retriever is None
        assert not service.miku.context_builder.include_memory
        assert not service.miku.memory_enabled
        assert service.miku.action_coordinator is None
        assert service.inspection_scope is None and service.archive_reader is None
        request_id = str(uuid4())
        runtime.submit_turn(session_id, request_id, "/allow C:/not-opened")
        runtime._worker.join(3)
        receipt = runtime.get_request(request_id)
        assert receipt["state"] == "completed"
        assert receipt["result"]["response_text"] == "respuesta sintética"
        assert len(provider.prompts) == 1
        assert "/allow C:/not-opened" in provider.prompts[0]
        assert "Identidad sintética" in provider.prompts[0]
        assert repository.load_session(session_id).memory_policy == "enabled"
    finally:
        runtime.shutdown()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*memory*") if p.is_file()} == memory_before


@pytest.mark.parametrize("port", [0, -1, 65536, "8765", True])
def test_invalid_port_rejected_before_importing_server(port):
    with pytest.raises(ValueError, match="Puerto"):
        serve_loopback(object(), port=port)


def test_server_only_binds_loopback_and_caps_input(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    closed = []
    monkeypatch.setitem(sys.modules, "waitress", SimpleNamespace(serve=lambda *args, **kwargs: calls.append((args, kwargs))))
    app = SimpleNamespace(extensions={"miku_web_coordinator": SimpleNamespace(shutdown=lambda: closed.append(True))})
    serve_loopback(app, port=8765)
    assert calls == [((app,), {
        "host": "127.0.0.1", "port": 8765, "threads": 4,
        "max_request_body_size": 65536, "max_request_header_size": 16384,
        "expose_tracebacks": False, "clear_untrusted_proxy_headers": True,
    })]
    assert closed == [True]


def test_server_shutdown_drains_coordinator_on_interrupt(monkeypatch):
    import sys
    from types import SimpleNamespace

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    closed = []
    monkeypatch.setitem(sys.modules, "waitress", SimpleNamespace(serve=interrupted))
    app = SimpleNamespace(extensions={"miku_web_coordinator": SimpleNamespace(shutdown=lambda: closed.append(True))})
    with pytest.raises(KeyboardInterrupt):
        serve_loopback(app)
    assert closed == [True]
