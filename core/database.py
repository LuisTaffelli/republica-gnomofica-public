from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.paths import DATA_DIR


ARCHIVE_DB_PATH = DATA_DIR / "swarm.db"
CONVERSATIONS_DB_PATH = DATA_DIR / "conversations.db"
MEMORY_DB_PATH = DATA_DIR / "memory.db"
AUDIT_DB_PATH = DATA_DIR / "audit.db"

CONVERSATION_SCHEMA_VERSION = 2
MEMORY_SCHEMA_VERSION = 1
AUDIT_SCHEMA_VERSION = 1


class SQLiteDatabase:
    """Administrador reutilizable de conexiones SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def raw_connection(self) -> sqlite3.Connection:
        """Conexión manual para código legado que administra commit/close."""
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


_archive_database: SQLiteDatabase | None = None
_conversation_database: SQLiteDatabase | None = None
_memory_database: SQLiteDatabase | None = None
_audit_database: SQLiteDatabase | None = None


def get_archive_database() -> SQLiteDatabase:
    """Base exclusiva de GNOMO-ARCHIVE."""
    global _archive_database

    if _archive_database is None:
        _archive_database = SQLiteDatabase(ARCHIVE_DB_PATH)

    return _archive_database


def get_conversation_database() -> SQLiteDatabase:
    """Base exclusiva de conversaciones."""
    global _conversation_database

    if _conversation_database is None:
        candidate = SQLiteDatabase(CONVERSATIONS_DB_PATH)
        initialize_conversation_database(candidate)
        _conversation_database = candidate

    return _conversation_database


def get_memory_database() -> SQLiteDatabase:
    """Base exclusiva de memoria personal de Miku."""
    global _memory_database

    if _memory_database is None:
        _memory_database = SQLiteDatabase(MEMORY_DB_PATH)
        initialize_memory_database(_memory_database)

    return _memory_database


def get_audit_database() -> SQLiteDatabase:
    """Base exclusiva de evidencia de acciones y efectos.

    Es deliberadamente lazy: hoy no existen capabilities effectful de producción.
    """
    global _audit_database

    if _audit_database is None:
        _audit_database = SQLiteDatabase(AUDIT_DB_PATH)
        initialize_audit_database(_audit_database)

    return _audit_database


# Compatibilidad con archive.py existente.
def get_connection() -> sqlite3.Connection:
    """Abre data/swarm.db.

    Se conserva este nombre para no obligar a refactorizar GNOMO-ARCHIVE
    en la misma entrega.
    """
    return get_archive_database().raw_connection()


def initialize() -> None:
    """Inicializa los dominios usados por la CLI sin mezclar sus esquemas.

    Auditoría permanece lazy hasta que exista un ActionCoordinator real.
    """
    initialize_archive_database()
    get_conversation_database()
    get_memory_database()


def initialize_memory_database(database: SQLiteDatabase) -> None:
    """Crea o migra únicamente data/memory.db."""
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        current_version = int(row["version"])
        migrations = {1: _migration_001_memory_schema}
        for version in range(current_version + 1, MEMORY_SCHEMA_VERSION + 1):
            migration = migrations.get(version)
            if migration is None:
                raise RuntimeError(f"No existe la migración de memoria {version}.")
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (?, CURRENT_TIMESTAMP)",
                (version,),
            )


def initialize_audit_database(database: SQLiteDatabase) -> None:
    """Crea o migra únicamente data/audit.db."""
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        current_version = int(row["version"])
        migrations = {1: _migration_001_audit_schema}
        for version in range(current_version + 1, AUDIT_SCHEMA_VERSION + 1):
            migration = migrations.get(version)
            if migration is None:
                raise RuntimeError(f"No existe la migración de auditoría {version}.")
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (?, CURRENT_TIMESTAMP)",
                (version,),
            )


def _migration_001_audit_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS action_audit_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            intent_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'ACTION_PROPOSED', 'ACTION_APPROVED', 'ACTION_DENIED',
                'EXECUTION_STARTED', 'EXECUTION_SUCCEEDED',
                'EXECUTION_FAILED', 'ACTION_CANCELLED'
            )),
            actor TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(intent_id, event_type)
        );

        CREATE INDEX IF NOT EXISTS idx_action_audit_intent_sequence
        ON action_audit_events(intent_id, sequence);
        """
    )


def _migration_001_memory_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS personal_memories (
            id TEXT PRIMARY KEY,
            profile_name TEXT NOT NULL,
            category TEXT NOT NULL
                CHECK(category IN ('preference', 'personal_fact', 'shared_context')),
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'retired')),
            source_type TEXT NOT NULL,
            source_session_id TEXT,
            source_message_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            related_memory_id TEXT,
            relation TEXT
                CHECK(relation IS NULL OR relation IN (
                    'corrects', 'replaces', 'contextualizes'
                )),
            FOREIGN KEY(related_memory_id)
                REFERENCES personal_memories(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_personal_memories_profile_status
        ON personal_memories(profile_name, status, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_personal_memories_source
        ON personal_memories(source_session_id, source_message_id);
        """
    )


def initialize_archive_database() -> None:
    """Crea únicamente las tablas pertenecientes a GNOMO-ARCHIVE."""
    connection = get_connection()

    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS note_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_note_id INTEGER NOT NULL,
                to_note_id INTEGER NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(from_note_id, to_note_id),
                FOREIGN KEY(from_note_id) REFERENCES notes(id),
                FOREIGN KEY(to_note_id) REFERENCES notes(id)
            );
            """
        )

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(notes)"
            ).fetchall()
        }

        if "deleted_at" not in columns:
            connection.execute(
                "ALTER TABLE notes "
                "ADD COLUMN deleted_at TIMESTAMP"
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class ConversationSchemaError(RuntimeError):
    """La apertura no está autorizada a cambiar el esquema conversacional."""


def _conversation_version(connection: sqlite3.Connection) -> int:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    if not tables:
        return 0
    if "schema_migrations" not in tables:
        raise ConversationSchemaError("Base conversacional sin versión reconocida; no se modificó.")
    version = int(connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0])
    if version == 0 and tables != {"schema_migrations"}:
        raise ConversationSchemaError("Esquema conversacional incompleto; no se modificó.")
    return version


def _read_conversation_version(database: SQLiteDatabase) -> int:
    if not database.path.exists():
        return 0
    # La guardia actúa antes de PRAGMA journal_mode o cualquier DDL/DML.
    connection = sqlite3.connect(database.path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return _conversation_version(connection)
    finally:
        connection.close()


def _check_conversation_version(version: int, *, allow_migration: bool = False) -> None:
    if version == 1 and not allow_migration:
        raise ConversationSchemaError(
            "La base de conversaciones v1 requiere una migración explícita a v2. "
            "La apertura automática está bloqueada; la base no se modificó."
        )
    if version not in {0, 1, CONVERSATION_SCHEMA_VERSION}:
        raise ConversationSchemaError(f"Versión conversacional no soportada: {version}; no se modificó.")


def require_conversation_schema(database: SQLiteDatabase) -> None:
    version = _read_conversation_version(database)
    _check_conversation_version(version)
    if version != CONVERSATION_SCHEMA_VERSION:
        raise ConversationSchemaError("La base de conversaciones no está inicializada en v2.")


def initialize_conversation_database(
    database: SQLiteDatabase, *, allow_migration: bool = False,
) -> None:
    """Crea bases nuevas en v2; una v1 exige activación explícita por el caller."""
    current_version = _read_conversation_version(database)
    _check_conversation_version(current_version, allow_migration=allow_migration)
    if current_version == CONVERSATION_SCHEMA_VERSION:
        return
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Revalidar bajo el lock: otro initializer pudo avanzar.
        current_version = _conversation_version(connection)
        _check_conversation_version(current_version, allow_migration=allow_migration)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

        migrations = {
            1: _migration_001_conversation_schema,
            2: _migration_002_conversation_order,
        }

        for version in range(
            current_version + 1,
            CONVERSATION_SCHEMA_VERSION + 1,
        ):
            migration = migrations.get(version)
            if migration is None:
                raise RuntimeError(
                    f"No existe la migración conversacional {version}."
                )

            migration(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, applied_at)
                VALUES (?, CURRENT_TIMESTAMP)
                """,
                (version,),
            )


def _migration_001_conversation_schema(
    connection: sqlite3.Connection,
) -> None:
    # SQL estático sin triggers ni ';' internos; executescript haría commit
    # implícito y rompería el rollback del initializer.
    for statement in """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            profile_name TEXT NOT NULL,
            title TEXT,

            mode TEXT NOT NULL DEFAULT 'persistent'
                CHECK(mode IN ('persistent', 'action')),

            memory_policy TEXT NOT NULL DEFAULT 'enabled'
                CHECK(
                    memory_policy IN (
                        'enabled',
                        'review_only',
                        'disabled'
                    )
                ),

            parent_session_id TEXT,
            parent_message_id TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT,

            metadata_json TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY(parent_session_id)
                REFERENCES chat_sessions(id)
                ON DELETE SET NULL,

            FOREIGN KEY(parent_message_id)
                REFERENCES messages(id)
                ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_updated
        ON chat_sessions(updated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_sessions_parent
        ON chat_sessions(parent_session_id);

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,

            role TEXT NOT NULL
                CHECK(role IN ('user', 'assistant', 'system', 'tool')),

            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY(session_id)
                REFERENCES chat_sessions(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session_time
        ON messages(session_id, timestamp, id);

        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            through_message_id TEXT NOT NULL,
            created_at TEXT NOT NULL,

            FOREIGN KEY(session_id)
                REFERENCES chat_sessions(id)
                ON DELETE CASCADE,

            FOREIGN KEY(through_message_id)
                REFERENCES messages(id)
        );

        CREATE INDEX IF NOT EXISTS idx_summaries_session_created
        ON conversation_summaries(session_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            profile_name TEXT NOT NULL,

            kind TEXT NOT NULL
                CHECK(
                    kind IN (
                        'fact',
                        'preference',
                        'project',
                        'decision',
                        'relationship',
                        'open_question'
                    )
                ),

            content TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'candidate'
                CHECK(
                    status IN (
                        'candidate',
                        'active',
                        'rejected',
                        'superseded'
                    )
                ),

            confidence REAL NOT NULL DEFAULT 1.0
                CHECK(confidence >= 0.0 AND confidence <= 1.0),

            source_session_id TEXT,
            source_message_id TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',

            FOREIGN KEY(source_session_id)
                REFERENCES chat_sessions(id)
                ON DELETE SET NULL,

            FOREIGN KEY(source_message_id)
                REFERENCES messages(id)
                ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memories_profile_status
        ON memories(profile_name, status);

        CREATE INDEX IF NOT EXISTS idx_memories_source_session
        ON memories(source_session_id);
        """.split(";"):
        if statement.strip():
            connection.execute(statement)


def _migration_002_conversation_order(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE messages ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 1 "
        "CHECK(typeof(ordinal) = 'integer' AND ordinal > 0)"
    )
    # Preservar el orden observable v1, no reconstruir inserciones históricas.
    connection.execute("""
        WITH ordered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY session_id ORDER BY timestamp, id
            ) AS position FROM messages
        )
        UPDATE messages SET ordinal = (SELECT position FROM ordered WHERE ordered.id = messages.id)
    """)
    connection.execute("CREATE UNIQUE INDEX idx_messages_session_ordinal ON messages(session_id, ordinal)")
    connection.execute("DROP INDEX idx_messages_session_time")
    connection.execute("CREATE INDEX idx_messages_session_time ON messages(session_id, timestamp, ordinal)")
