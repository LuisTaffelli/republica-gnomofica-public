"""One text turn at a time, independently of the lifetime of an HTTP request."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from threading import Event, Lock, Thread
from typing import Callable

from core.conversation import ChatSession, SessionService, SQLiteConversationRepository
from core.conversation.repository import SessionNotFoundError
from miku.turn import MikuTurnService, TurnRequest

from .receipts import ProcessFileLock, ReceiptStore, StorageInUseError


MAX_TEXT_LENGTH = 8192
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class WebError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class WebCoordinator:
    def __init__(
        self, repository: SQLiteConversationRepository, receipt_path: str | Path,
        build_turn: Callable[[ChatSession], MikuTurnService], *, profile_name: str = "miku",
    ) -> None:
        if profile_name != "miku":
            raise ValueError("La web inicial sólo admite el perfil fijo miku.")
        self.repository = repository
        self.sessions = SessionService(repository)
        self._build_turn = build_turn
        self._mutex = Lock()
        self._worker: Thread | None = None
        self._worker_done: Event | None = None
        self._active_request: str | None = None
        self._stopping = False
        self._closed = False
        self._uncertain = False
        self._closing_failed: set[str] = set()
        self._locks: list[ProcessFileLock] = []
        path = Path(receipt_path).resolve()
        conversation_path = repository.database.path.resolve()
        if path == conversation_path:
            raise ValueError("Los recibos requieren una base separada.")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            for locked in (conversation_path, path):
                self._locks.append(ProcessFileLock(Path(str(locked) + ".web.lock")))
            self.receipts = ReceiptStore(path, conversation_path)
            # Only the owner may recover receipts left by a dead server.
            self.receipts.interrupt_running()
        except BaseException as error:
            self._release_locks()
            if isinstance(error, StorageInUseError):
                raise WebError(409, "storage_in_use", str(error)) from error
            raise

    def list_sessions(self) -> list[dict]:
        with self._mutex:
            self._available()
            return [self._summary(session) for session in self.repository.list_sessions(limit=200)
                    if self._eligible(session)]

    def create_session(self) -> dict:
        with self._mutex:
            self._available(write=True)
            # The runtime disables memory; do not change the saved local policy.
            session = self.sessions.create(profile_name="miku")
            return self._summary(session)

    def get_session(self, session_id: str) -> dict:
        with self._mutex:
            self._available()
            session = self._session(session_id)
            return {
                "session": self._summary(session),
                "messages": [{"id": message.id, "role": message.role,
                              "content": self._content(message), "timestamp": message.timestamp.isoformat()}
                             for message in session.messages],
            }

    def submit_turn(self, session_id: str, request_id: str, text: str) -> dict:
        self._identifier(request_id)
        self._identifier(session_id)
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise WebError(400, "invalid_text", "El mensaje está vacío o supera 8192 caracteres.")
        try:
            digest = sha256(text.encode("utf-8")).hexdigest()
        except UnicodeEncodeError as error:
            raise WebError(400, "invalid_text", "El mensaje contiene texto inválido.") from error
        with self._mutex:
            self._available()
            existing = self.receipts.get(request_id)
            if existing is not None:
                if existing["session_id"] != session_id or existing["payload_hash"] != digest:
                    raise WebError(409, "request_conflict", "Ese envío ya corresponde a otro mensaje.")
                self._session(session_id)
                return self._receipt(existing)
            self._available(write=True)
            session = self._session(session_id)
            if session.closed_at is not None:
                raise WebError(409, "session_closed", "La conversación está cerrada; creá otra para continuar.")
            if session_id in self._closing_failed:
                raise WebError(409, "close_uncertain", "El cierre no quedó confirmado; revisá el historial.")
            try:
                self.receipts.begin(request_id, session_id, digest)
            except Exception as error:
                self._uncertain = True
                raise WebError(503, "receipt_uncertain", "No se pudo confirmar el envío; no se reintentará.") from error
            self._active_request = request_id
            allowed, done = Event(), Event()
            self._worker_done = done
            try:
                self._worker = Thread(
                    target=self._run_guarded, args=(allowed, done, session_id, request_id, text),
                    name="miku-web-turn", daemon=False,
                )
                self._worker.start()
                allowed.set()
            except BaseException:
                # The target must acquire this mutex before checking the gate.
                # A native thread may exist even if start() did not confirm it;
                # cancelling here proves it cannot touch repository/provider.
                allowed.clear()
                self._uncertain = True
                self._finish(request_id, "failed", result_status="error", error_type="worker_unavailable")
                self._active_request = None
                done.set()
                raise WebError(503, "worker_unavailable", "No se pudo confirmar el inicio; no se reintentará.")
            return self._receipt(self.receipts.get(request_id))

    def get_request(self, request_id: str) -> dict:
        self._identifier(request_id)
        with self._mutex:
            self._available()
            receipt = self.receipts.get(request_id)
            if receipt is None:
                raise WebError(404, "request_not_found", "No existe ese envío.")
            self._session(receipt["session_id"])
            return self._receipt(receipt)

    def active_request(self) -> dict | None:
        with self._mutex:
            self._available()
            return (self._receipt(self.receipts.get(self._active_request))
                    if self._active_request is not None else None)

    def close_session(self, session_id: str) -> dict:
        with self._mutex:
            self._available(write=True)
            session = self._session(session_id)
            if session.closed_at is not None:
                return self._summary(session)
            if session_id in self._closing_failed:
                raise WebError(409, "close_uncertain", "El cierre no quedó confirmado; no se reintentará.")
            try:
                turn = self._safe_turn(session)
                turn.close()
            except BaseException as error:
                self._closing_failed.add(session_id)
                raise WebError(503, "close_uncertain", "No se pudo confirmar el cierre; revisá el historial.") from error
            return self._summary(self._session(session_id))

    def shutdown(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._stopping = True
            done = self._worker_done
        # A client disconnect does not call shutdown. Server shutdown waits for
        # the provider and persistence, without inventing a session close.
        # On CPython a KeyboardInterrupt during Thread.join() can mark a live
        # thread stopped. Only the target's finally confirms work has finished.
        while True:
            try:
                if done is None or done.wait(timeout=0.2):
                    break
            except KeyboardInterrupt:
                continue
        with self._mutex:
            self._release_locks()
            self._closed = True

    def _run_guarded(
        self, allowed: Event, done: Event, session_id: str, request_id: str, text: str,
    ) -> None:
        try:
            with self._mutex:
                may_run = allowed.is_set()
            if may_run:
                self._run(session_id, request_id, text)
        finally:
            done.set()

    def _run(self, session_id: str, request_id: str, text: str) -> None:
        try:
            session = self.sessions.resume(session_id)
            if not self._eligible(session) or session.closed_at is not None:
                raise RuntimeError("La sesión dejó de estar disponible.")
            turn = self._safe_turn(session)
            result = turn.handle(TurnRequest(text, source="web"))
            state = "completed" if result.status == "completed" else "failed"
            values = dict(
                user_message_id=result.user_message_id,
                response_message_id=result.response_message_id,
                result_status=result.status,
                error_type="turn_error" if state == "failed" else None,
            )
        except BaseException as error:
            state = "failed" if isinstance(error, Exception) else "interrupted"
            values = {"result_status": "error", "error_type": "turn_uncertain"}
        with self._mutex:
            self._finish(request_id, state, **values)
            self._active_request = None

    def _finish(self, request_id: str, state: str, **values) -> None:
        try:
            self.receipts.finish(request_id, state, **values)
        except BaseException:
            # A receipt may have committed even if its caller saw an error.
            # Freeze writes; never execute the request again to find out.
            self._uncertain = True

    def _safe_turn(self, session: ChatSession) -> MikuTurnService:
        turn = self._build_turn(session)
        if (not isinstance(turn, MikuTurnService)
                or turn.session is not session or turn.repository is not self.repository
                or turn.inspection_scope is not None or turn.archive_reader is not None
                or turn.miku.archive_reader is not None or turn.miku.action_coordinator is not None
                or turn.miku.memory_enabled or turn.miku.context_builder.include_memory):
            raise RuntimeError("La composición web debe ser conversación sin memoria ni herramientas.")
        return turn

    def _receipt(self, receipt: dict) -> dict:
        result = None
        if receipt["state"] != "running":
            response = None
            if receipt["response_message_id"] is not None:
                session = self._session(receipt["session_id"])
                response = next((message for message in session.messages
                                 if message.id == receipt["response_message_id"]), None)
            result = {
                "response_text": self._content(response) if response is not None else "",
                "status": receipt["result_status"] or "error",
                "error_type": receipt["error_type"],
                "user_message_id": receipt["user_message_id"],
                "response_message_id": receipt["response_message_id"],
            }
            if receipt["state"] == "completed" and response is None:
                result.update(status="error", error_type="result_unavailable")
        public_state = receipt["state"]
        if result is not None and result["error_type"] == "result_unavailable":
            public_state = "failed"
        if self._uncertain and public_state == "running":
            public_state = "interrupted"
            result = {"response_text": "", "status": "error", "error_type": "receipt_uncertain",
                      "user_message_id": None, "response_message_id": None}
        return {"request_id": receipt["request_id"], "session_id": receipt["session_id"],
                "state": public_state, "result": result}

    def _session(self, session_id: str) -> ChatSession:
        self._identifier(session_id)
        try:
            session = self.repository.load_session(session_id)
        except SessionNotFoundError as error:
            raise WebError(404, "session_not_found", "No existe esa conversación disponible.") from error
        if not self._eligible(session):
            raise WebError(404, "session_not_found", "No existe esa conversación disponible.")
        return session

    @staticmethod
    def _eligible(session: ChatSession) -> bool:
        return session.profile_name == "miku" and session.mode == "persistent" and not session.is_branch

    @staticmethod
    def _identifier(value: str) -> None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise WebError(400, "invalid_id", "Identificador inválido.")

    def _available(self, *, write: bool = False) -> None:
        if self._closed or self._stopping:
            raise WebError(503, "server_stopping", "La web está terminando; volvé a conectar luego.")
        if write and self._uncertain:
            raise WebError(503, "receipt_uncertain", "La escritura no quedó confirmada; revisá el historial y reiniciá la web.")
        if write and self._active_request is not None:
            raise WebError(409, "busy", "Miku está respondiendo; esperá a que termine el turno.")

    @staticmethod
    def _content(message) -> str:
        if message.metadata.get("kind") == "provider_error":
            return "No fue posible generar una respuesta. Revisá el servicio local."
        return message.content

    @staticmethod
    def _summary(session: ChatSession) -> dict:
        return {"id": session.id, "title": session.title,
                "created_at": session.created_at.isoformat(), "updated_at": session.updated_at.isoformat(),
                "closed_at": session.closed_at.isoformat() if session.closed_at else None}

    def _release_locks(self) -> None:
        for held in reversed(self._locks):
            held.close()
        self._locks.clear()
