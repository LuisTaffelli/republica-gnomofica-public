"""Orden durable y activación v2, exclusivamente sobre bases sintéticas."""
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from cli import miku_cli
from core import database as db_module
from core.conversation import ChatSession, Message, SQLiteConversationRepository
from core.conversation import repository as repository_module
from core.database import SQLiteDatabase, initialize_conversation_database
from core.database import get_conversation_database as open_default_database


STAMP = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
HIGH = "ffffffff-ffff-4fff-bfff-ffffffffffff"
LOW = "00000000-0000-4000-8000-000000000001"


def make_repository(tmp_path):
    database = SQLiteDatabase(tmp_path / "conversation.db")
    initialize_conversation_database(database)
    return database, SQLiteConversationRepository(database)


def make_legacy(tmp_path):
    database = SQLiteDatabase(tmp_path / "legacy.db")
    with database.connect() as connection:
        db_module._migration_001_conversation_schema(connection)
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'legacy')")
        connection.execute(
            "INSERT INTO chat_sessions(id, profile_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("parent", "synthetic", STAMP.isoformat(), STAMP.isoformat()),
        )
        for id_, role in ((HIGH, "user"), (LOW, "assistant")):
            connection.execute(
                "INSERT INTO messages(id, session_id, role, content, timestamp) VALUES (?, 'parent', ?, ?, ?)",
                (id_, role, role + " sintético", STAMP.isoformat()),
            )
        connection.execute(
            "INSERT INTO chat_sessions(id, profile_name, parent_session_id, parent_message_id, created_at, updated_at) "
            "VALUES ('child', 'synthetic', 'parent', ?, ?, ?)", (HIGH, STAMP.isoformat(), STAMP.isoformat()),
        )
        connection.execute(
            "INSERT INTO conversation_summaries(session_id, content, through_message_id, created_at) "
            "VALUES ('parent', 'resumen sintético', ?, ?)", (HIGH, STAMP.isoformat()),
        )
    return database


def roles(messages):
    return [message.role for message in messages]


def raw_snapshot(database):
    with sqlite3.connect(database.path) as connection:
        return list(connection.iterdump())


def test_equal_timestamps_preserve_insertion_in_all_read_paths_and_branches(tmp_path):
    database, repository = make_repository(tmp_path)
    session = ChatSession(profile_name="synthetic", memory_policy="disabled")
    repository.create_session(session)
    messages = [
        Message(role="user", content="pregunta", id=HIGH, timestamp=STAMP),
        Message(role="assistant", content="respuesta", id=LOW, timestamp=STAMP),
    ]
    for message in messages:
        repository.save_message(session.id, message)
        session.extend([message])
    assert repository.load_session(session.id).messages == session.messages
    assert roles(repository.load_recent_messages(session.id, 1)) == ["assistant"]
    assert repository.load_recent_messages(session.id, 2) == messages
    assert repository.load_recent_messages(session.id, 0) == []
    assert repository.load_messages_after(session.id, None) == messages
    assert repository.load_messages_after(session.id, HIGH, 1) == messages[1:]
    assert repository.load_messages_after(session.id, LOW) == []
    assert repository.load_messages_until(session.id, HIGH) == messages[:1]
    assert repository.load_messages_until(session.id, LOW) == messages
    branch = repository.create_branch(session.id, HIGH)
    assert repository.load_context_messages(branch.id) == messages[:1]
    with database.connect() as connection:
        assert [row[0] for row in connection.execute("SELECT ordinal FROM messages ORDER BY ordinal")] == [1, 2]
    # El ordinal es interno a persistencia; no cambia el contrato de Message.
    assert "ordinal" not in messages[0].to_dict()


def test_timestamp_remains_primary_order_and_ordinals_are_per_session(tmp_path):
    database, repository = make_repository(tmp_path)
    for session_id in ("one", "two"):
        session = ChatSession(id=session_id, profile_name="synthetic")
        repository.create_session(session)
        newer = Message(role="assistant", content="posterior", timestamp=STAMP + timedelta(seconds=1))
        older = Message(role="user", content="anterior", timestamp=STAMP)
        repository.save_message(session.id, newer)
        repository.save_message(session.id, older)
        assert repository.load_session(session.id).messages == [older, newer]
        assert repository.load_messages_after(session.id, older.id) == [newer]
        assert repository.load_messages_until(session.id, older.id) == [older]
    with database.connect() as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT session_id, ordinal FROM messages ORDER BY session_id, ordinal"
        )] == [("one", 1), ("one", 2), ("two", 1), ("two", 2)]


def test_create_session_with_initial_messages_assigns_ordinals_in_list_order(tmp_path):
    _, repository = make_repository(tmp_path)
    messages = [Message(role="user", content="uno", id=HIGH, timestamp=STAMP),
                Message(role="assistant", content="dos", id=LOW, timestamp=STAMP)]
    session = ChatSession(profile_name="synthetic", messages=messages)
    repository.create_session(session)
    assert repository.load_session(session.id).messages == messages


def test_v1_requires_explicit_opt_in_before_any_writable_connection(tmp_path, monkeypatch):
    database = make_legacy(tmp_path)
    before = database.path.read_bytes()
    monkeypatch.setattr(database, "connect", lambda: pytest.fail("No abrir v1 para escritura"))
    for _ in range(2):
        with pytest.raises(db_module.ConversationSchemaError, match="migración explícita"):
            initialize_conversation_database(database)
        with pytest.raises(db_module.ConversationSchemaError, match="migración explícita"):
            SQLiteConversationRepository(database)
    assert database.path.read_bytes() == before


def test_normal_cli_never_activates_v1_migration_or_caches_failed_open(tmp_path, monkeypatch, capsys):
    database = make_legacy(tmp_path)
    before = hashlib.sha256(database.path.read_bytes()).hexdigest()
    monkeypatch.setattr(db_module, "CONVERSATIONS_DB_PATH", database.path)
    monkeypatch.setattr(db_module, "_conversation_database", None)
    monkeypatch.setattr(repository_module, "get_conversation_database", open_default_database)
    monkeypatch.setattr(miku_cli, "create_provider", lambda: pytest.fail("No iniciar cognición"))
    for _ in range(2):
        miku_cli.handle_miku_command(["chat"])
        assert db_module._conversation_database is None
    assert capsys.readouterr().out.count("migración explícita") == 2
    assert hashlib.sha256(database.path.read_bytes()).hexdigest() == before


def test_v1_migration_preserves_legacy_order_ids_anchors_and_is_idempotent(tmp_path):
    database = make_legacy(tmp_path)
    with database.connect() as connection:
        old_rows = [tuple(row) for row in connection.execute(
            "SELECT id,session_id,role,content,timestamp,metadata_json FROM messages ORDER BY timestamp,id")]
        sessions = [tuple(row) for row in connection.execute("SELECT * FROM chat_sessions ORDER BY id")]
        summaries = [tuple(row) for row in connection.execute("SELECT * FROM conversation_summaries ORDER BY id")]
    initialize_conversation_database(database, allow_migration=True)
    repository = SQLiteConversationRepository(database)
    assert [message.id for message in repository.load_session("parent").messages] == [LOW, HIGH]
    assert roles(repository.load_recent_messages("parent", 1)) == ["user"]
    assert repository.load_messages_after("parent", HIGH) == []
    assert [message.id for message in repository.load_messages_until("parent", HIGH)] == [LOW, HIGH]
    assert [message.id for message in repository.load_context_messages("child")] == [LOW, HIGH]
    assert repository.load_latest_summary("parent").through_message_id == HIGH
    with database.connect() as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT id,session_id,role,content,timestamp,metadata_json FROM messages ORDER BY timestamp,ordinal")] == old_rows
        assert [tuple(row) for row in connection.execute("SELECT * FROM chat_sessions ORDER BY id")] == sessions
        assert [tuple(row) for row in connection.execute("SELECT * FROM conversation_summaries ORDER BY id")] == summaries
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
    before = raw_snapshot(database)
    initialize_conversation_database(database)
    initialize_conversation_database(database, allow_migration=True)
    assert raw_snapshot(database) == before
    new = Message(role="tool", content="nuevo", id="00000000-0000-4000-8000-000000000000", timestamp=STAMP)
    repository.save_message("parent", new)
    assert [m.id for m in repository.load_messages_after("parent", HIGH)] == [new.id]


@pytest.mark.parametrize("phase", ["after_column", "after_migration"])
def test_failed_v1_migration_rolls_back_schema_data_and_version(tmp_path, monkeypatch, phase):
    database = make_legacy(tmp_path)
    before = raw_snapshot(database)
    original = db_module._migration_002_conversation_order

    def fail(connection):
        if phase == "after_column":
            connection.execute("ALTER TABLE messages ADD COLUMN ordinal INTEGER")
        else:
            original(connection)
        raise sqlite3.OperationalError("fallo sintético de migración")

    monkeypatch.setattr(db_module, "_migration_002_conversation_order", fail)
    with pytest.raises(sqlite3.OperationalError, match="fallo sintético"):
        initialize_conversation_database(database, allow_migration=True)
    assert raw_snapshot(database) == before


def test_failed_message_write_rolls_back_ordinal_and_allows_next_append(tmp_path):
    database, repository = make_repository(tmp_path)
    session = ChatSession(profile_name="synthetic")
    repository.create_session(session)
    first = Message(role="user", content="uno", id=HIGH, timestamp=STAMP)
    repository.save_message(session.id, first)
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON chat_sessions BEGIN SELECT RAISE(ABORT, 'fallo sintético'); END")
    second = Message(role="assistant", content="dos", id=LOW, timestamp=STAMP)
    with pytest.raises(sqlite3.IntegrityError, match="fallo sintético"):
        repository.save_message(session.id, second)
    assert repository.load_session(session.id).messages == [first]
    with database.connect() as connection:
        connection.execute("DROP TRIGGER fail_update")
    repository.save_message(session.id, second)
    assert repository.load_session(session.id).messages == [first, second]
    with database.connect() as connection:
        assert [r[0] for r in connection.execute("SELECT ordinal FROM messages ORDER BY ordinal")] == [1, 2]


def test_order_survives_sqlite_backup_without_changing_message_ids(tmp_path):
    database, repository = make_repository(tmp_path)
    messages = [Message(role="user", content="uno", id=HIGH, timestamp=STAMP),
                Message(role="assistant", content="dos", id=LOW, timestamp=STAMP)]
    session = ChatSession(profile_name="synthetic", messages=messages)
    repository.create_session(session)
    copied = SQLiteDatabase(tmp_path / "copy.db")
    with sqlite3.connect(database.path) as source, sqlite3.connect(copied.path) as destination:
        source.backup(destination)
    assert SQLiteConversationRepository(copied).load_session(session.id).messages == messages


def test_failure_recording_v2_version_rolls_back_the_complete_migration(tmp_path):
    database = make_legacy(tmp_path)
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER reject_v2 BEFORE INSERT ON schema_migrations WHEN NEW.version=2 "
                           "BEGIN SELECT RAISE(ABORT, 'version rechazada'); END")
    before = raw_snapshot(database)
    with pytest.raises(sqlite3.IntegrityError, match="version rechazada"):
        initialize_conversation_database(database, allow_migration=True)
    assert raw_snapshot(database) == before


def test_failed_new_database_creation_leaves_no_partial_schema_or_cached_handle(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    monkeypatch.setattr(db_module, "CONVERSATIONS_DB_PATH", path)
    monkeypatch.setattr(db_module, "_conversation_database", None)
    original = db_module._migration_002_conversation_order

    def fail(connection):
        original(connection)
        raise sqlite3.OperationalError("creación interrumpida")

    monkeypatch.setattr(db_module, "_migration_002_conversation_order", fail)
    with pytest.raises(sqlite3.OperationalError, match="creación interrumpida"):
        open_default_database()
    assert db_module._conversation_database is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    monkeypatch.setattr(db_module, "_migration_002_conversation_order", original)
    database = open_default_database()
    assert db_module._conversation_database is database
    assert SQLiteConversationRepository(database).list_sessions() == []


def test_parallel_appends_allocate_distinct_ordinals_atomically(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database, repository = make_repository(tmp_path)
    session = ChatSession(profile_name="synthetic")
    repository.create_session(session)
    barrier = Barrier(2)

    def append(id_):
        barrier.wait(timeout=5)
        repository.save_message(session.id, Message(role="tool", content=id_, id=id_, timestamp=STAMP))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, (HIGH, LOW)))
    with database.connect() as connection:
        rows = list(connection.execute("SELECT id, ordinal FROM messages ORDER BY ordinal"))
    assert [row["ordinal"] for row in rows] == [1, 2]
    assert {row["id"] for row in rows} == {HIGH, LOW}
    assert [m.id for m in repository.load_session(session.id).messages] == [row["id"] for row in rows]


@pytest.mark.parametrize("version", [0, 3])
def test_unrecognized_existing_database_is_not_modified(tmp_path, version):
    database = SQLiteDatabase(tmp_path / "unknown.db")
    with sqlite3.connect(database.path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        if version:
            connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'synthetic')", (version,))
    before = database.path.read_bytes()
    with pytest.raises(db_module.ConversationSchemaError):
        initialize_conversation_database(database, allow_migration=True)
    assert database.path.read_bytes() == before
