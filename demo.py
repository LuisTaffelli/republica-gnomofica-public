"""Demostración sintética del turno real, sin red ni datos previos."""
from pathlib import Path
from tempfile import TemporaryDirectory

from core.conversation import SessionService, SQLiteConversationRepository
from core.database import SQLiteDatabase, initialize_conversation_database, initialize_memory_database
from core.memory import SQLiteMemoryRepository
from miku.turn import TurnRequest, build_miku_turn_service


class DemoProvider:
    """Respuesta fija: muestra el circuito del sistema, no inferencia de IA."""

    def ask(self, prompt: str) -> str:
        assert "catálogo de semillas imaginarias" in prompt
        return (
            "Podemos comenzar con tres semillas ficticias: Aurora, Bruma y Sol. "
            "Propongo registrar nombre y descripción, y que el humano revise el catálogo. "
            "Esta respuesta es una fixture de demostración; no la generó un modelo."
        )


def main() -> None:
    with TemporaryDirectory(prefix="republica-demo-") as temporary:
        root = Path(temporary)
        db = SQLiteDatabase(root / "conversations.db")
        initialize_conversation_database(db)
        repository = SQLiteConversationRepository(db)
        memory_db = SQLiteDatabase(root / "memory.db")
        initialize_memory_database(memory_db)
        sessions = SessionService(repository)
        session = sessions.create(profile_name="miku", mode="temporary")
        service = build_miku_turn_service(
            session=session,
            repository=repository,
            provider=DemoProvider(),
            memory_enabled=False,
            memory_repository=SQLiteMemoryRepository(memory_db),
        )
        message = "Armemos un catálogo de semillas imaginarias."
        print(f"Visitante > {message}")
        result = service.handle(TurnRequest(message, source="public-demo"))
        if result.status != "completed":
            raise RuntimeError("El turno de demostración no se completó.")
        assert len(session.messages) == 2
        assert repository.list_sessions() == []
        print(f"Miku > {result.response_text}")
        print("\nVerificado: turno completado; sesión temporal y memoria desactivada.")


if __name__ == "__main__":
    main()
