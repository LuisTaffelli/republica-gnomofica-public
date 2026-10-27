from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from core import database

from .models import (
    BackupCreationResult,
    BackupManifest,
    BackupVerification,
    DomainBackup,
    RestoreResult,
)


MANIFEST_NAME = "manifest.json"
EXPECTED_DOMAINS = ("conversations", "memory", "audit", "archive")
DOMAIN_FILENAMES = {
    "conversations": "conversations.db",
    "memory": "memory.db",
    "audit": "audit.db",
    "archive": "swarm.db",
}
REQUIRED_TABLES = {
    "conversations": ("chat_sessions", "messages"),
    "memory": ("personal_memories",),
    "audit": ("action_audit_events",),
    "archive": ("notes", "note_links"),
}
CONSISTENCY_DESCRIPTION = (
    "consistent SQLite snapshot per domain; no distributed atomic snapshot"
)
ACTION_BACKUPS_DESCRIPTION = (
    "excluded: data/action_backups/file_edits is a separate file-edit recovery layer"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackupError(RuntimeError):
    pass


class BackupService:
    """Backup local de las cuatro persistencias, sin autoridad sobre sus dominios."""

    def __init__(self, sources: Mapping[str, str | Path] | None = None) -> None:
        configured = sources or {
            "conversations": database.CONVERSATIONS_DB_PATH,
            "memory": database.MEMORY_DB_PATH,
            "audit": database.AUDIT_DB_PATH,
            "archive": database.ARCHIVE_DB_PATH,
        }
        if set(configured) != set(EXPECTED_DOMAINS):
            raise ValueError("BackupService requiere los cuatro dominios conocidos.")
        self.sources = {name: Path(configured[name]) for name in EXPECTED_DOMAINS}

    def create(self, destination: str | Path) -> BackupCreationResult:
        root = Path(destination).expanduser().resolve()
        if root.exists():
            raise BackupError(f"El destino ya existe y no será sobrescrito: {root}")
        root.mkdir(parents=True)
        domains_dir = root / "domains"
        domains_dir.mkdir()
        results: list[DomainBackup] = []

        for domain in EXPECTED_DOMAINS:
            source = self.sources[domain]
            relative = Path("domains") / DOMAIN_FILENAMES[domain]
            target = root / relative
            try:
                self._backup_sqlite(source, target)
                self._validate_sqlite(target, domain)
                results.append(
                    DomainBackup(
                        domain=domain,
                        logical_source=f"data/{DOMAIN_FILENAMES[domain]}",
                        backup_file=relative.as_posix(),
                        status="complete",
                        size_bytes=target.stat().st_size,
                        sha256=self._hash(target),
                    )
                )
            except Exception as error:
                results.append(
                    DomainBackup(
                        domain=domain,
                        logical_source=f"data/{DOMAIN_FILENAMES[domain]}",
                        backup_file=relative.as_posix() if target.exists() else None,
                        status="failed",
                        size_bytes=target.stat().st_size if target.exists() else None,
                        sha256=self._hash(target) if target.is_file() else None,
                        error=f"{type(error).__name__}: {error}",
                    )
                )

        complete = all(item.status == "complete" for item in results)
        manifest = BackupManifest(
            manifest_version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="complete" if complete else "incomplete",
            consistency=CONSISTENCY_DESCRIPTION,
            action_backups=ACTION_BACKUPS_DESCRIPTION,
            domains=tuple(results),
        )
        self._write_manifest(root / MANIFEST_NAME, manifest)
        return BackupCreationResult(str(root), manifest)

    def verify(self, backup: str | Path) -> BackupVerification:
        root = Path(backup).expanduser().resolve()
        errors: list[str] = []
        try:
            manifest = self._load_manifest(root / MANIFEST_NAME)
        except Exception as error:
            return BackupVerification(
                str(root), False, False,
                (f"Manifiesto inválido: {type(error).__name__}: {error}",),
            )

        complete = manifest.status == "complete"
        if not complete:
            errors.append("El manifiesto declara el backup incompleto.")
        by_name = {item.domain: item for item in manifest.domains}
        for domain in EXPECTED_DOMAINS:
            item = by_name[domain]
            if item.status != "complete":
                errors.append(f"{domain}: estado {item.status}.")
                continue
            assert item.backup_file is not None
            path = root / item.backup_file
            if not path.is_file():
                errors.append(f"{domain}: falta {item.backup_file}.")
                continue
            if path.stat().st_size != item.size_bytes:
                errors.append(f"{domain}: tamaño no coincide.")
                continue
            if self._hash(path) != item.sha256:
                errors.append(f"{domain}: SHA-256 no coincide.")
                continue
            try:
                self._validate_sqlite(path, domain)
            except Exception as error:
                errors.append(f"{domain}: SQLite inválido ({error}).")
        return BackupVerification(
            str(root), not errors, complete and not errors, tuple(errors), manifest
        )

    def restore(self, backup: str | Path, destination: str | Path) -> RestoreResult:
        verification = self.verify(backup)
        if not verification.valid or not verification.complete:
            detail = "; ".join(verification.errors) or "backup incompleto"
            raise BackupError(f"Restauración rechazada: {detail}")
        assert verification.manifest is not None
        source_root = Path(verification.location)
        target_root = Path(destination).expanduser().resolve()
        if target_root.exists():
            raise BackupError(f"El destino ya existe y no será sobrescrito: {target_root}")
        target_root.mkdir(parents=True)
        restored: list[DomainBackup] = []
        try:
            for item in verification.manifest.domains:
                assert item.backup_file is not None
                source = source_root / item.backup_file
                target = target_root / DOMAIN_FILENAMES[item.domain]
                shutil.copyfile(source, target)
                if self._hash(target) != item.sha256:
                    raise BackupError(f"{item.domain}: hash restaurado no coincide.")
                self._validate_sqlite(target, item.domain)
                restored.append(item)
        except Exception:
            # Se conserva el destino parcial como evidencia; no hay rollback distribuido.
            raise
        return RestoreResult(str(target_root), tuple(restored))

    @staticmethod
    def _backup_sqlite(source: Path, target: Path) -> None:
        if not source.is_file():
            raise FileNotFoundError("No existe la base del dominio.")
        source_uri = f"{source.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(target)) as target_connection:
                source_connection.backup(target_connection)

    @staticmethod
    def _validate_sqlite(path: Path, domain: str) -> None:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise BackupError(f"integrity_check={result[0] if result else 'sin resultado'}")
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = set(REQUIRED_TABLES[domain]) - tables
            if missing:
                raise BackupError("faltan tablas requeridas: " + ", ".join(sorted(missing)))

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _write_manifest(path: Path, manifest: BackupManifest) -> None:
        path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _load_manifest(path: Path) -> BackupManifest:
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected_keys = {
            "manifest_version", "created_at", "status", "consistency",
            "action_backups", "domains",
        }
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise ValueError("estructura raíz desconocida o incompleta")
        if raw["manifest_version"] != 1:
            raise ValueError("versión de manifiesto no soportada")
        if raw["status"] not in {"complete", "incomplete"}:
            raise ValueError("estado de manifiesto inválido")
        if raw["consistency"] != CONSISTENCY_DESCRIPTION:
            raise ValueError("declaración de consistencia inválida")
        if raw["action_backups"] != ACTION_BACKUPS_DESCRIPTION:
            raise ValueError("declaración de action backups inválida")
        if not isinstance(raw["created_at"], str):
            raise ValueError("timestamp inválido")
        datetime.fromisoformat(raw["created_at"])
        rows = raw["domains"]
        if not isinstance(rows, list) or len(rows) != len(EXPECTED_DOMAINS):
            raise ValueError("cantidad de dominios inválida")
        domains: list[DomainBackup] = []
        expected_domain_keys = {
            "domain", "logical_source", "backup_file", "status",
            "size_bytes", "sha256", "error",
        }
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected_domain_keys:
                raise ValueError("entrada de dominio inválida")
            domain = row["domain"]
            if domain not in EXPECTED_DOMAINS:
                raise ValueError("dominio desconocido")
            expected_logical_source = f"data/{DOMAIN_FILENAMES[domain]}"
            if row["logical_source"] != expected_logical_source:
                raise ValueError("origen lógico de dominio inválido")
            status = row["status"]
            if status not in {"complete", "failed"}:
                raise ValueError("estado de dominio inválido")
            backup_file = row["backup_file"]
            if backup_file is not None:
                relative = Path(backup_file)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("ruta de backup insegura")
            if status == "complete" and backup_file != (
                Path("domains") / DOMAIN_FILENAMES[domain]
            ).as_posix():
                raise ValueError("archivo de dominio inválido")
            size = row["size_bytes"]
            digest = row["sha256"]
            if status == "complete":
                if not isinstance(size, int) or size < 0:
                    raise ValueError("tamaño inválido")
                if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    raise ValueError("SHA-256 inválido")
                if backup_file is None or row["error"] is not None:
                    raise ValueError("dominio completo inconsistente")
            domains.append(DomainBackup(**row))
        if tuple(item.domain for item in domains) != EXPECTED_DOMAINS:
            raise ValueError("dominios faltantes, duplicados o desordenados")
        actually_complete = all(item.status == "complete" for item in domains)
        if (raw["status"] == "complete") != actually_complete:
            raise ValueError("estado global inconsistente")
        return BackupManifest(
            manifest_version=1,
            created_at=raw["created_at"],
            status=raw["status"],
            consistency=raw["consistency"],
            action_backups=raw["action_backups"],
            domains=tuple(domains),
        )
