import pytest
import requests

from cli import miku_cli
from core import database
from core.conversation import repository
from core.memory import repository as memory_repository
from core.audit import repository as audit_repository
from core.database import SQLiteDatabase, initialize_memory_database
from core.memory import SQLiteMemoryRepository


@pytest.fixture(autouse=True)
def forbid_network_and_development_databases(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("El test intentó acceder a red o bases de desarrollo.")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(database, "get_archive_database", forbidden)
    monkeypatch.setattr(repository, "get_conversation_database", forbidden)
    monkeypatch.setattr(memory_repository, "get_memory_database", forbidden)
    monkeypatch.setattr(audit_repository, "get_audit_database", forbidden)

    memory_database = SQLiteDatabase(tmp_path / "isolated-memory.db")
    initialize_memory_database(memory_database)
    monkeypatch.setattr(
        miku_cli,
        "SQLiteMemoryRepository",
        lambda: SQLiteMemoryRepository(memory_database),
    )
