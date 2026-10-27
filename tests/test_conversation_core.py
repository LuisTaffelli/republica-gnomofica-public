from __future__ import annotations

from dataclasses import dataclass, field

from core.context.context_manager import ContextManager
from core.context.prompt_composer import PromptComposer
from core.conversation import ChatSession
from core.database import ARCHIVE_DB_PATH, CONVERSATIONS_DB_PATH
from core.paths import DATA_DIR, PROJECT_ROOT
from miku.miku import Miku


@dataclass
class FakeProvider:
    response: str
    prompts: list[str] = field(default_factory=list)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class SummarylessRepository:
    def load_latest_summary(self, session_id: str) -> None:
        return None


def test_miku_uses_replaceable_provider_and_passes_existing_context() -> None:
    provider = FakeProvider("Respuesta falsa")
    session = ChatSession(profile_name="miku", mode="temporary")
    session.add_user("Mensaje anterior")
    session.add_assistant("Respuesta anterior")
    session.add_user("Mensaje actual")

    miku = Miku(
        provider=provider,
        context_builder=ContextManager(SummarylessRepository()),
        serializer=PromptComposer(),
    )

    response = miku.listen("Mensaje actual", session)

    assert response == "Respuesta falsa"
    assert len(provider.prompts) == 1
    prompt = provider.prompts[0]
    assert "--- IDENTIDAD ---" in prompt
    assert "Mensaje anterior" in prompt
    assert "Respuesta anterior" in prompt
    assert prompt.count("Mensaje actual") == 1


def test_default_profile_path_is_independent_from_working_directory(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    projection = ContextManager(SummarylessRepository()).build_for_profile(
        profile_name="miku",
        message="¿Seguís ahí?",
    )

    prompt = PromptComposer().serialize(projection)

    assert "--- IDENTIDAD ---" in prompt
    assert "¿Seguís ahí?" in prompt
    assert DATA_DIR == PROJECT_ROOT / "data"
    assert ARCHIVE_DB_PATH == DATA_DIR / "swarm.db"
    assert CONVERSATIONS_DB_PATH == DATA_DIR / "conversations.db"
