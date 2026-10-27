"""Composition for the text-only web interface; no personal-memory store is opened."""
from __future__ import annotations

from pathlib import Path

from core.context.context_manager import ContextManager
from core.context.profile_loader import ProfileLoader
from core.context.prompt_composer import PromptComposer
from core.conversation import SQLiteConversationRepository
from core.paths import PROJECT_ROOT
from miku.miku import Miku
from miku.turn import MikuTurnService
from providers import TextProvider

from .coordinator import WebCoordinator


def create_coordinator(
    *,
    repository: SQLiteConversationRepository,
    receipt_path: Path,
    provider: TextProvider,
    profile_loader: ProfileLoader | None = None,
    invariants_path: Path = PROJECT_ROOT / "docs" / "INVARIANTS.md",
) -> WebCoordinator:
    """Use server-owned dependencies; HTTP cannot select paths, profiles or models."""
    def build_turn(session):
        return MikuTurnService(
            session=session,
            repository=repository,
            miku=Miku(
                provider=provider,
                context_builder=ContextManager(
                    repository=repository,
                    profile_loader=profile_loader,
                    invariants_path=invariants_path,
                    include_memory=False,
                    memory_retriever=None,
                ),
                serializer=PromptComposer(),
                memory_enabled=False,
                memory_service=None,
            ),
        )

    return WebCoordinator(
        repository=repository, receipt_path=receipt_path, build_turn=build_turn,
    )


def serve_loopback(app, *, port: int = 8765) -> None:
    """Explicit server start, without exposure/proxy configuration or auto-start.

    Authentication remains the responsibility of create_app's trusted verifier.
    No default database, runtime, identity or development bypass is constructed.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Puerto no válido.")
    from waitress import serve

    coordinator = app.extensions["miku_web_coordinator"]
    try:
        serve(
            app, host="127.0.0.1", port=port, threads=4,
            max_request_body_size=65536, max_request_header_size=16384,
            expose_tracebacks=False, clear_untrusted_proxy_headers=True,
        )
    finally:
        coordinator.shutdown()
