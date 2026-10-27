from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from core.context.profile_loader import ProfileLoader
from core.context.projection import ContextItem, ContextProjection, ConversationProjection
from core.conversation import ChatSession, ConversationRepository
from core.memory import MemoryRetriever
from core.paths import PROJECT_ROOT


class ContextManager:
    """Selecciona fuentes y ensambla contexto sin serializar ni persistir."""

    def __init__(
        self,
        repository: ConversationRepository,
        profile_loader: ProfileLoader | None = None,
        invariants_path: str | Path = PROJECT_ROOT / "docs" / "INVARIANTS.md",
        memory_retriever: MemoryRetriever | None = None,
        include_memory: bool = True,
    ) -> None:
        self.repository = repository
        self.profile_loader = profile_loader or ProfileLoader()
        self.invariants_path = Path(invariants_path)
        self.memory_retriever = memory_retriever
        self.include_memory = include_memory
        self.last_memory_error: Exception | None = None

    def build_for_profile(
        self,
        profile_name: str,
        message: str,
        session: ChatSession | None = None,
        task_context: Sequence[ContextItem] = (),
    ) -> ContextProjection:
        self.last_memory_error = None
        profile = self.profile_loader.load(profile_name)
        profile_dir: Path = profile["profile_dir"]
        human_display_name = str(profile.get("human_display_name", "usuario")).strip()

        # documents sigue declarando fuentes disponibles, no seleccionadas.
        return ContextProjection(
            invariants=self._read_required(self.invariants_path, "invariants"),
            identity=self._read_required(profile_dir / profile["identity"], "identity"),
            instructions=self._read_required(profile_dir / profile["prompt"], "instructions"),
            memory=(
                self._select_memory(
                    profile_name, message, session, human_display_name
                )
                if self.include_memory else ()
            ),
            conversation=self._select_conversation(session, message),
            task=tuple(task_context),
        )

    def _select_memory(
        self,
        profile_name: str,
        message: str,
        session: ChatSession | None,
        human_display_name: str,
    ) -> tuple[ContextItem, ...]:
        if self.memory_retriever is None:
            return ()
        if session is not None and not session.allows_memory:
            return ()
        window = [item.content for item in session.messages[-6:]] if session else []
        try:
            records = self.memory_retriever.retrieve(profile_name, message, window)
        except Exception as error:
            self.last_memory_error = error
            return ()
        return tuple(
            ContextItem(
                content=(
                    "subject=user | "
                    f"display_name={human_display_name or 'usuario'}\n"
                    f"fact={record.content}"
                ),
                source=(
                    f"memory:{record.id};subject=user;status={record.status};"
                    f"category={record.category}"
                ),
            )
            for record in records
        )

    @staticmethod
    def _read_required(path: Path, layer: str) -> ContextItem:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Falta la fuente fundamental {layer}: {path}")
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError(f"La fuente fundamental {layer} está vacía: {path}")
        source = (
            path.relative_to(PROJECT_ROOT).as_posix()
            if path.is_relative_to(PROJECT_ROOT)
            else path.as_posix()
        )
        return ContextItem(content=content, source=source)

    def _select_conversation(
        self,
        session: ChatSession | None,
        message: str,
    ) -> ConversationProjection:
        messages = list(session.messages) if session is not None else []
        current_source = "conversation:current-input"

        # Conserva la política anterior: la CLI ya agregó la entrada actual.
        if (
            messages
            and messages[-1].role == "user"
            and messages[-1].content.strip() == message.strip()
        ):
            current_source = f"conversation:{messages.pop().id}"

        summary = None
        if session is not None and session.is_persistent:
            summary = self.repository.load_latest_summary(session.id)

        return ConversationProjection(
            current_message=ContextItem(message, current_source),
            messages=tuple(messages[-20:]),
            summary=summary,
        )
