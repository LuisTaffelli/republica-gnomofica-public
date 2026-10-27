from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


DomainStatus = Literal["complete", "failed"]


@dataclass(frozen=True, slots=True)
class DomainBackup:
    domain: str
    logical_source: str
    backup_file: str | None
    status: DomainStatus
    size_bytes: int | None
    sha256: str | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BackupManifest:
    manifest_version: int
    created_at: str
    status: Literal["complete", "incomplete"]
    consistency: str
    action_backups: str
    domains: tuple[DomainBackup, ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["domains"] = [asdict(domain) for domain in self.domains]
        return result


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    location: str
    manifest: BackupManifest


@dataclass(frozen=True, slots=True)
class BackupVerification:
    location: str
    valid: bool
    complete: bool
    errors: tuple[str, ...]
    manifest: BackupManifest | None = None


@dataclass(frozen=True, slots=True)
class RestoreResult:
    location: str
    domains: tuple[DomainBackup, ...]
