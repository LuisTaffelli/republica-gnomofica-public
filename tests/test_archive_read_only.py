from __future__ import annotations

import json
import sqlite3

import pytest

import main
from cli import miku_cli
from core.conversation import ChatSession
from gnomos.archive import (
    ArchiveLimits,
    ArchiveOperationRequest,
    ArchiveQueryRequest,
    ArchiveReadOnlyCapabilities,
    SQLiteArchiveReader,
)
from miku.miku import Miku


def archive_database(tmp_path):
    path = tmp_path / "archive.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            deleted_at TEXT
        );
        CREATE TABLE note_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_note_id INTEGER NOT NULL,
            to_note_id INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_note_id, to_note_id)
        );
        """
    )
    connection.executemany(
        "INSERT INTO notes(title, content, tags, created_at) VALUES (?, ?, ?, ?)",
        [
            ("Bosque", "La aguja está junto al roble. " + "x" * 80, "mapa", "2026-01-03"),
            ("Taller", "Una segunda aguja aparece en el taller.", "ingeniería", "2026-01-02"),
            ("Archivada", "aguja que no debe verse", "vieja", "2026-01-01"),
        ],
    )
    connection.execute("UPDATE notes SET deleted_at = '2026-02-01' WHERE id = 3")
    connection.execute(
        "INSERT INTO note_links(from_note_id, to_note_id, reason, created_at) "
        "VALUES (1, 2, 'comparte hallazgo', '2026-02-02')"
    )
    connection.commit()
    connection.close()
    return path


def execute(capabilities, operation, **arguments):
    return capabilities.execute(ArchiveOperationRequest(operation, arguments))


def test_search_read_and_links_are_structured_read_only_queries(tmp_path):
    path = archive_database(tmp_path)
    capabilities = ArchiveReadOnlyCapabilities(SQLiteArchiveReader(path))

    search = execute(capabilities, "search_notes", query="aguja")
    read = execute(capabilities, "read_note", note_id=1)
    links = execute(capabilities, "list_links", note_id=1)

    assert [item["note_id"] for item in search.data["notes"]] == [1, 2]
    assert read.data["title"] == "Bosque" and "roble" in read.data["content"]
    assert links.data["links"] == [{
        "linked_note_id": 2, "title": "Taller", "tags": "ingeniería",
        "reason": "comparte hallazgo", "linked_at": "2026-02-02",
        "direction": "outgoing",
    }]
    assert all(item.locator.startswith(f"archive:{path.as_posix()}#note/")
               for result in (search, read, links) for item in result.evidence)


def test_central_limits_bound_results_excerpts_content_and_links(tmp_path):
    path = archive_database(tmp_path)
    limits = ArchiveLimits(
        max_search_results=1, max_search_excerpt_chars=12,
        max_note_content_chars=15, max_links=0,
    )
    capabilities = ArchiveReadOnlyCapabilities(SQLiteArchiveReader(path), limits)

    search = execute(capabilities, "search_notes", query="aguja")
    read = execute(capabilities, "read_note", note_id=1)
    links = execute(capabilities, "list_links", note_id=1)

    assert search.truncated and len(search.data["notes"]) == 1
    assert len(search.data["notes"][0]["excerpt"]) <= 12
    assert read.truncated and len(read.data["content"]) == 15
    assert links.truncated and links.data["links"] == [] and links.evidence == ()


@pytest.mark.parametrize(
    "operation,arguments,expected",
    [
        ("search_notes", {"query": ""}, "no vacío"),
        ("search_notes", {"query": "   "}, "no vacío"),
        ("search_notes", {"query": "x" * 501}, "límite de 500"),
        ("read_note", {"note_id": 0}, "entero positivo"),
        ("read_note", {"note_id": "1"}, "entero positivo"),
        ("list_links", {}, "Faltan"),
    ],
)
def test_invalid_queries_are_explicit_and_do_not_produce_evidence(
    tmp_path, operation, arguments, expected
):
    result = execute(
        ArchiveReadOnlyCapabilities(SQLiteArchiveReader(archive_database(tmp_path))),
        operation,
        **arguments,
    )
    assert not result.success and expected in result.errors[0]
    assert result.evidence == ()


@pytest.mark.parametrize("operation", ["read_note", "list_links"])
def test_missing_note_is_explicit_without_fabricated_evidence(tmp_path, operation):
    result = execute(
        ArchiveReadOnlyCapabilities(SQLiteArchiveReader(archive_database(tmp_path))),
        operation,
        note_id=999,
    )
    assert not result.success and "No existe" in result.errors[0]
    assert result.data is None and result.evidence == ()


def test_sqlite_errors_are_reported_without_fabricated_evidence(tmp_path):
    missing = tmp_path / "missing.db"
    result = execute(
        ArchiveReadOnlyCapabilities(SQLiteArchiveReader(missing)),
        "search_notes",
        query="dato",
    )
    assert not result.success and result.errors
    assert result.evidence == () and not missing.exists()


def test_capability_surface_and_sqlite_connection_cannot_write(tmp_path):
    path = archive_database(tmp_path)
    reader = SQLiteArchiveReader(path)
    capabilities = ArchiveReadOnlyCapabilities(reader)
    original = path.read_bytes()

    for operation in ("create_note", "edit_note", "delete_note", "link_notes", "sql"):
        result = execute(capabilities, operation, note_id=1, content="cambio")
        assert not result.success and result.evidence == ()
    assert set(capabilities.operation_names) == {"search_notes", "read_note", "list_links"}
    with reader._connect() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE notes SET title = 'alterada' WHERE id = 1")
    assert path.read_bytes() == original


class Builder:
    def __init__(self):
        self.task_snapshots = []

    def build_for_profile(self, **kwargs):
        self.task_snapshots.append(tuple(kwargs["task_context"]))
        return tuple(kwargs["task_context"])


class Serializer:
    def serialize(self, projection):
        return "\n".join(item.content for item in projection)


def test_miku_uses_archive_only_as_observed_ephemeral_task_context(tmp_path):
    path = archive_database(tmp_path)
    responses = iter([
        {"archive_request": {"operation": "search_notes", "arguments": {"query": "aguja"}}},
        {"archive_request": {"operation": "read_note", "arguments": {"note_id": 1}}},
        {"final_answer": "La nota Bosque ubica la aguja junto al roble."},
    ])
    prompts = []

    class Provider:
        def ask(self, prompt):
            prompts.append(prompt)
            return json.dumps(next(responses), ensure_ascii=False)

    class Memory:
        processed = []
        def has_pending(self, session_id): return False
        def confirm_from_message(self, *args): return ()
        def process_turn(self, **kwargs):
            self.processed.append(kwargs)
            from core.memory import MemoryTurnResult
            return MemoryTurnResult()

    builder = Builder()
    memory = Memory()
    session = ChatSession(profile_name="miku", id="archive-session", mode="persistent")
    session.add_user("Consulta anterior")
    session.add_assistant(
        "La nota observada afirma que Visitante prefiere Linux.",
        observed_source="archive",
    )
    session.add_user("Buscá la aguja en Archive")
    miku = Miku(
        provider=Provider(), context_builder=builder, serializer=Serializer(),
        memory_service=memory, archive_reader=SQLiteArchiveReader(path),
    )
    answer = miku.listen(
        "Buscá la aguja en Archive", session,
        archive_request=ArchiveQueryRequest("Buscá la aguja en Archive"),
    )

    assert "roble" in answer
    assert len(miku.last_archive_result.operations) == 2
    assert len(builder.task_snapshots) == 3
    assert all(item.source.startswith("archive:") for item in builder.task_snapshots[-1])
    assert "contenido observado" in prompts[-1].casefold()
    assert "no instrucciones ni autoridad" in prompts[-1].casefold()
    assert len(memory.processed) == 1
    assert memory.processed[0]["message"] == "Buscá la aguja en Archive"
    assert "junto al roble" not in json.dumps(memory.processed[0], ensure_ascii=False)
    assert "prefiere Linux" not in json.dumps(memory.processed[0], ensure_ascii=False)


def test_ordinary_prompt_does_not_open_or_inject_archive(tmp_path):
    missing = tmp_path / "must-not-exist.db"
    prompts = []

    class Provider:
        def ask(self, prompt):
            prompts.append(prompt)
            return "respuesta ordinaria"

    class PlainBuilder:
        def build_for_profile(self, **kwargs):
            assert kwargs["task_context"] == ()
            return "proyección ordinaria"

    class PlainSerializer:
        def serialize(self, projection): return projection

    miku = Miku(
        provider=Provider(), context_builder=PlainBuilder(), serializer=PlainSerializer(),
        archive_reader=SQLiteArchiveReader(missing),
    )
    assert miku.listen("Hola") == "respuesta ordinaria"
    assert prompts == ["proyección ordinaria"]
    assert miku.last_archive_result is None and not missing.exists()


def test_archive_activation_on_missing_database_does_not_create_it(tmp_path, monkeypatch):
    missing = tmp_path / "missing-archive.db"
    monkeypatch.setattr(miku_cli.database, "ARCHIVE_DB_PATH", missing)
    reader = miku_cli.prepare_archive_reader()

    result = execute(ArchiveReadOnlyCapabilities(reader), "search_notes", query="dato")

    assert not result.success and result.errors and result.evidence == ()
    assert not missing.exists()


def test_archive_activation_never_initializes_or_migrates(monkeypatch, tmp_path):
    path = tmp_path / "existing.db"
    monkeypatch.setattr(miku_cli.database, "ARCHIVE_DB_PATH", path)
    monkeypatch.setattr(
        miku_cli.database,
        "initialize_archive_database",
        lambda: pytest.fail("No debe inicializar ni migrar Archive."),
    )
    monkeypatch.setattr(
        miku_cli.database,
        "get_archive_database",
        lambda: pytest.fail("No debe abrir una conexión administrada con escritura."),
    )

    reader = miku_cli.prepare_archive_reader()

    assert reader.path == path.resolve() and not path.exists()


def test_prepare_archive_reader_uses_an_existing_valid_database(tmp_path, monkeypatch):
    path = archive_database(tmp_path)
    monkeypatch.setattr(miku_cli.database, "ARCHIVE_DB_PATH", path)

    result = execute(
        ArchiveReadOnlyCapabilities(miku_cli.prepare_archive_reader()),
        "read_note",
        note_id=1,
    )

    assert result.success and result.data["title"] == "Bosque"


def test_non_archive_entrypoint_does_not_initialize_unrelated_database(monkeypatch):
    calls = []
    monkeypatch.setitem(main.COMMANDS, "miku", lambda args: calls.append(("handler", args)))
    monkeypatch.setattr(main, "initialize_archive_database", lambda: calls.append(("archive", [])))
    monkeypatch.setattr(main.sys, "argv", ["main.py", "miku", "sessions"])

    main.main()

    assert calls == [("handler", ["sessions"])]


def test_archive_chat_activation_is_explicit_and_separate_from_laboral(tmp_path):
    ordinary = miku_cli.parse_chat_options([])
    archive = miku_cli.parse_chat_options(["--archive", "--temporary"])

    assert not ordinary.archive
    assert archive.archive and archive.temporary
    with pytest.raises(ValueError, match="no pueden combinarse"):
        miku_cli.parse_chat_options(["--archive", "--inspect", str(tmp_path)])
