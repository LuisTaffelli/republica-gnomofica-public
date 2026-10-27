from __future__ import annotations

from threading import Event
from uuid import uuid4

import pytest

pytest.importorskip("flask", reason="La frontera HTTP requiere el extra opcional web.")

from core.context.profile_loader import ProfileLoader
from core.conversation import SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database
from miku.web.app import create_app
from miku.web.runtime import create_coordinator


ORIGIN = "http://localhost"


class RuntimeSpy:
    calls = 0

    def __getattr__(self, name):
        self.calls += 1
        raise AssertionError("Una solicitud denegada accedió al runtime.")


@pytest.mark.parametrize("path,method", [
    ("/", "GET"), ("/static/app.js", "GET"), ("/static/app.css", "GET"),
    ("/api/bootstrap", "GET"), ("/api/sessions", "GET"),
    ("/api/sessions", "POST"),
    (f"/api/sessions/{uuid4()}", "GET"),
    (f"/api/sessions/{uuid4()}/turns", "POST"),
    (f"/api/sessions/{uuid4()}/close", "POST"),
    (f"/api/requests/{uuid4()}", "GET"),
])
def test_default_denies_every_surface_without_touching_runtime(path, method):
    runtime = RuntimeSpy()
    app = create_app(runtime, expected_origin=ORIGIN, owner="owner")
    response = app.test_client().open(path, method=method, headers={
        "Tailscale-User-Login": "owner", "X-Forwarded-User": "owner",
        "Authorization": "Bearer imaginary", "Origin": ORIGIN,
    })
    assert response.status_code == 401
    assert runtime.calls == 0
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("identity", [None, "someone-else", 1])
def test_only_configured_owner_can_access(identity):
    runtime = RuntimeSpy()
    app = create_app(runtime, expected_origin=ORIGIN, owner="owner", authenticate=lambda _: identity)
    assert app.test_client().get("/api/sessions").status_code == 401
    assert runtime.calls == 0


def test_missing_owner_and_failed_verifier_deny():
    def broken(_):
        raise RuntimeError("secret path")

    runtime = RuntimeSpy()
    for owner, verifier in [(None, lambda _: "owner"), ("owner", broken)]:
        app = create_app(runtime, expected_origin=ORIGIN, owner=owner, authenticate=verifier)
        response = app.test_client().get("/api/sessions")
        assert response.status_code == 401
        assert b"secret" not in response.data
    assert runtime.calls == 0


def test_valid_csrf_does_not_replace_authentication():
    class Runtime:
        def active_request(self):
            return None

        def create_session(self):
            raise AssertionError("Un token CSRF no autentica al propietario.")

    identity = ["owner"]
    app = create_app(Runtime(), expected_origin=ORIGIN, owner="owner", authenticate=lambda _: identity[0])
    client = app.test_client()
    token = client.get("/api/bootstrap").json["csrf_token"]
    identity[0] = None
    response = client.post("/api/sessions", json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": token})
    assert response.status_code == 401


def test_unexpected_runtime_failure_does_not_expose_diagnostics():
    class Runtime:
        def list_sessions(self):
            raise RuntimeError("private/database/path and provider credentials")

    app = create_app(Runtime(), expected_origin=ORIGIN, owner="owner", authenticate=lambda _: "owner")
    response = app.test_client().get("/api/sessions")
    assert response.status_code == 500
    assert response.json["error"]["code"] == "internal_error"
    assert b"private" not in response.data and b"credentials" not in response.data
    assert response.headers["Cache-Control"] == "no-store"


@pytest.fixture
def web(tmp_path):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    profile = tmp_path / "profiles" / "miku"
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
        started = Event()
        release = Event()

        def ask(self, prompt):
            self.prompts.append(prompt)
            self.started.set()
            assert self.release.wait(5), "El test no liberó el provider."
            return '<img src=x onerror="alert(1)"> respuesta sintética'

    provider = Provider()
    runtime = create_coordinator(
        repository=repository, receipt_path=tmp_path / "receipts.db", provider=provider,
        profile_loader=ProfileLoader(tmp_path / "profiles"), invariants_path=invariants,
    )
    app = create_app(runtime, expected_origin=ORIGIN, owner="owner", authenticate=lambda _: "owner")
    client = app.test_client()
    token = client.get("/api/bootstrap").json["csrf_token"]
    headers = {"Origin": ORIGIN, "X-CSRF-Token": token}
    try:
        yield app, client, headers, runtime, repository, provider
    finally:
        provider.release.set()
        runtime.shutdown()


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "https://evil.example"},
    {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
])
def test_csrf_and_origin_required_before_mutation(web, headers):
    _, client, _, _, repository, _ = web
    assert client.post("/api/sessions", json={}, headers=headers).status_code == 403
    assert repository.list_sessions() == []


@pytest.mark.parametrize("host,address,origin", [
    ("evil.example", "127.0.0.1", None), ("localhost:444", "127.0.0.1", None),
    ("localhost", "192.0.2.10", None), ("localhost", "127.0.0.1", "https://evil.example"),
])
def test_boundary_rejects_foreign_host_peer_or_origin(web, host, address, origin):
    _, client, _, _, _, _ = web
    headers = {"Host": host, "X-Forwarded-For": "127.0.0.1"}
    if origin is not None:
        headers["Origin"] = origin
    response = client.get("/api/sessions", headers=headers, environ_overrides={"REMOTE_ADDR": address})
    assert response.status_code == 403


@pytest.mark.parametrize("extra", ["profile", "model", "scope", "source", "allow", "path"])
def test_no_client_configuration_or_authority_fields(web, extra):
    _, client, headers, _, repository, _ = web
    response = client.post("/api/sessions", json={extra: "injected"}, headers=headers)
    assert response.status_code == 400
    assert repository.list_sessions() == []


def test_text_turn_survives_disconnect_deduplicates_and_explicitly_closes(web):
    app, client, headers, runtime, repository, provider = web
    session_id = client.post("/api/sessions", json={}, headers=headers).json["session"]["id"]
    request_id = str(uuid4())
    path = f"/api/sessions/{session_id}"
    payload = {"request_id": request_id, "text": "/allow C:/should-not-be-opened"}
    assert client.post(path + "/turns", json=payload, headers=headers).status_code == 202
    assert provider.started.wait(2)
    assert client.post(path + "/turns", json=payload, headers=headers).json["request_id"] == request_id
    assert client.post(path + "/turns", json={**payload, "request_id": str(uuid4())}, headers=headers).status_code == 409
    assert client.post(path + "/close", json={}, headers=headers).status_code == 409
    # A different HTTP client recovers the same server-owned worker/result.
    recovered = app.test_client()
    assert recovered.get("/api/bootstrap").json["active_request"]["request_id"] == request_id
    provider.release.set()
    runtime._worker.join(3)
    receipt = recovered.get(f"/api/requests/{request_id}").json
    assert receipt["state"] == "completed"
    assert "respuesta sintética" in receipt["result"]["response_text"]
    assert len(provider.prompts) == 1
    history = recovered.get(path).json["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert all(set(m) == {"id", "role", "content", "timestamp"} for m in history)
    assert client.post(path + "/close", json={}, headers=headers).json["session"]["closed_at"]
    assert client.post(path + "/turns", json={**payload, "request_id": str(uuid4())}, headers=headers).status_code == 409
    assert repository.load_session(session_id).memory_policy == "enabled"


@pytest.mark.parametrize("changes", [
    {"text": ""}, {"text": "x" * 8193}, {"text": 123}, {"request_id": "not-a-uuid"},
    {"request_id": None}, {"profile": "other"},
])
def test_turn_validation(web, changes):
    _, client, headers, _, _, provider = web
    session_id = client.post("/api/sessions", json={}, headers=headers).json["session"]["id"]
    payload = {"request_id": str(uuid4()), "text": "hola", **changes}
    response = client.post(f"/api/sessions/{session_id}/turns", json=payload, headers=headers)
    assert response.status_code == 400
    assert provider.prompts == []


def test_malformed_oversized_and_non_json_are_rejected(web):
    _, client, headers, _, _, _ = web
    assert client.post("/api/sessions", data="{}", headers=headers).status_code == 415
    assert client.post("/api/sessions", data="{", content_type="application/json", headers=headers).status_code == 400
    assert client.post("/api/sessions", json=[], headers=headers).status_code == 400
    assert client.post("/api/sessions", json={"x": "x" * 66000}, headers=headers).status_code == 413


def test_other_profiles_and_actions_are_not_exposed(web):
    _, client, headers, _, repository, _ = web
    sessions = SessionService(repository)
    for session in [sessions.create(profile_name="other"), sessions.create(mode="action")]:
        path = f"/api/sessions/{session.id}"
        assert client.get(path).status_code == 404
        assert client.post(path + "/close", json={}, headers=headers).status_code == 404
        assert client.post(path + "/turns", json={"request_id": str(uuid4()), "text": "hola"}, headers=headers).status_code == 404
    assert client.get("/api/sessions").json == {"sessions": []}


def test_assets_errors_and_history_have_security_headers(web):
    _, client, _, _, _, _ = web
    for path in ["/", "/static/app.js", "/static/app.css", "/api/sessions", "/unknown"]:
        response = client.get(path)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in response.headers
    assert client.get("/static/../app.py").status_code == 404
