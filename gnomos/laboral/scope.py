from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ScopeViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AllowedTarget:
    path: Path
    is_directory: bool


@dataclass(frozen=True, slots=True)
class InspectionScope:
    root: Path
    additional_allowed_targets: tuple[Path, ...] = ()
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.read_only:
            raise ValueError("El scope de inspección debe ser read-only.")
        root = self._prepare_authorized(self.root)
        additional = tuple(
            self._prepare_authorized(path)
            for path in self.additional_allowed_targets
        )
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "additional_allowed_targets", additional)

    @staticmethod
    def _prepare_authorized(value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"El target autorizado debe ser absoluto: {path}")
        resolved = path.resolve(strict=True)
        if not (resolved.is_file() or resolved.is_dir()):
            raise ValueError(f"El target autorizado no es archivo ni directorio: {resolved}")
        return resolved

    @property
    def targets(self) -> tuple[Path, ...]:
        return (self.root, *self.additional_allowed_targets)

    @property
    def serialized_targets(self) -> tuple[str, ...]:
        return tuple(str(path) for path in self.targets)

    def resolve_for_read(self, value: str | Path) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ScopeViolation(f"Se rechazó una ruta relativa o ambigua: {candidate}")
        if os.name == "nt" and any(":" in part for part in candidate.parts[1:]):
            raise ScopeViolation(f"Se rechazó una ruta con stream alternativo: {candidate}")

        # resolve() sigue symlinks/junctions existentes. La comparación posterior
        # ocurre contra targets también resueltos y evita escapes por alias.
        resolved = candidate.resolve(strict=False)
        for allowed in self.targets:
            if allowed.is_file() and resolved == allowed:
                return resolved
            if allowed.is_dir() and (resolved == allowed or resolved.is_relative_to(allowed)):
                return resolved
        raise ScopeViolation(f"Fuera del scope read-only autorizado: {resolved}")
