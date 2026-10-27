"""HTTP adapter for one privately authenticated owner.

There is deliberately no header-based authenticator or public development bypass.
The deployment boundary must supply a verifier before this adapter grants access.
"""
from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from flask import Flask, Request, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .coordinator import MAX_TEXT_LENGTH, WebCoordinator, WebError


MAX_BODY_LENGTH = 65536
Authenticator = Callable[[Request], str | None]


def create_app(
    coordinator: WebCoordinator,
    *,
    expected_origin: str,
    owner: str | None = None,
    authenticate: Authenticator | None = None,
) -> Flask:
    """Build an inert app; absent/failed identity verification always denies access.

    ``authenticate`` is trusted server configuration, never client input. This
    cut does not implement or activate a Tailscale/proxy identity verifier.
    """
    origin = urlsplit(expected_origin)
    if (
        origin.scheme not in {"http", "https"}
        or not origin.hostname or origin.username or origin.password
        or origin.path or origin.query or origin.fragment
        or expected_origin != f"{origin.scheme}://{origin.netloc}"
    ):
        raise ValueError("Se requiere un origen explícito, sin path ni credenciales.")
    # Validate the port eagerly, rather than during an incoming request.
    origin.port
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=MAX_BODY_LENGTH)
    app.extensions["miku_web_coordinator"] = coordinator
    csrf_token = secrets.token_urlsafe(32)
    static_dir = Path(__file__).with_name("static")

    def error(code: str, message: str, status: int):
        return jsonify(error={"code": code, "message": message}), status

    @app.before_request
    def guard():
        try:
            is_loopback = ipaddress.ip_address(request.remote_addr or "").is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback or request.host != origin.netloc:
            return error("invalid_boundary", "Acceso no permitido.", 403)
        try:
            principal = authenticate(request) if authenticate is not None else None
        except Exception:
            principal = None
        if not owner or not isinstance(principal, str) or principal != owner:
            return error("unauthorized", "No se pudo verificar la identidad autorizada.", 401)
        supplied_origin = request.headers.get("Origin")
        if supplied_origin is not None and supplied_origin != expected_origin:
            return error("invalid_origin", "Origen no permitido.", 403)
        if request.method not in {"GET", "HEAD"}:
            if supplied_origin != expected_origin:
                return error("invalid_origin", "Se requiere el origen autorizado.", 403)
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token.encode("utf-8"), csrf_token.encode("ascii")):
                return error("invalid_csrf", "Actualizá la página antes de continuar.", 403)
            if request.mimetype != "application/json":
                return error("invalid_content_type", "Se requiere JSON.", 415)

    @app.after_request
    def protect(response):
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; font-src 'self'; "
                "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        })
        return response

    @app.errorhandler(WebError)
    def web_error(exception):
        return error(exception.code, exception.message, exception.status)

    @app.errorhandler(HTTPException)
    def http_error(exception):
        return error("invalid_request", "Solicitud no válida.", exception.code or 400)

    @app.errorhandler(Exception)
    def internal_error(exception):
        # Do not serialize exception text, database paths or provider diagnostics.
        return error("internal_error", "No se pudo completar la operación.", 500)

    def body(fields: set[str]):
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != fields:
            raise WebError(400, "invalid_body", "Los campos de la solicitud no son válidos.")
        return value

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/static/<name>")
    def asset(name):
        if name not in {"app.css", "app.js"}:
            return error("not_found", "No encontrado.", 404)
        return send_from_directory(static_dir, name)

    @app.get("/api/bootstrap")
    def bootstrap():
        return jsonify(csrf_token=csrf_token, active_request=coordinator.active_request())

    @app.get("/api/sessions")
    def sessions():
        return jsonify(sessions=coordinator.list_sessions())

    @app.post("/api/sessions")
    def create_session():
        body(set())
        return jsonify(session=coordinator.create_session()), 201

    @app.get("/api/sessions/<uuid:session_id>")
    def session(session_id):
        return jsonify(coordinator.get_session(str(session_id)))

    @app.post("/api/sessions/<uuid:session_id>/turns")
    def turn(session_id):
        value = body({"request_id", "text"})
        text = value["text"]
        request_id = value["request_id"]
        try:
            valid_id = isinstance(request_id, str) and str(UUID(request_id)) == request_id
        except ValueError:
            valid_id = False
        if not valid_id or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise WebError(400, "invalid_turn", "El envío requiere un ID y texto de hasta 8192 caracteres.")
        receipt = coordinator.submit_turn(str(session_id), request_id, text)
        return jsonify(receipt), 202 if receipt["state"] == "running" else 200

    @app.get("/api/requests/<uuid:request_id>")
    def receipt(request_id):
        return jsonify(coordinator.get_request(str(request_id)))

    @app.post("/api/sessions/<uuid:session_id>/close")
    def close_session(session_id):
        body(set())
        return jsonify(session=coordinator.close_session(str(session_id)))

    return app
