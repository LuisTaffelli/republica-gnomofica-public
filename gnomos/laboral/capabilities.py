from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Callable

from gnomos.laboral.detectors.python import inspect_python
from gnomos.laboral.models import CapabilityRequest, CapabilityResult, Evidence
from gnomos.laboral.scope import InspectionScope, ScopeViolation


DEFAULT_EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "build", "dist", "coverage", ".coverage", ".tox",
})
DEFAULT_EXCLUDED_SUFFIXES = frozenset({
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".dll", ".exe",
    ".so", ".dylib", ".zip", ".tar", ".gz", ".7z", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".mov", ".avi", ".pdf",
})


class LaboralCapabilities:
    """Primitivas read-only. No expone shell ni operaciones de escritura."""

    def __init__(
        self,
        scope: InspectionScope,
        *,
        max_tree_entries: int = 200,
        max_tree_depth: int = 4,
        max_read_chars: int = 20_000,
        max_search_results: int = 100,
        max_search_files: int = 1_000,
        max_search_file_bytes: int = 1_000_000,
        max_ast_evidence: int = 200,
    ) -> None:
        self.scope = scope
        self.max_tree_entries = max_tree_entries
        self.max_tree_depth = max_tree_depth
        self.max_read_chars = max_read_chars
        self.max_search_results = max_search_results
        self.max_search_files = max_search_files
        self.max_search_file_bytes = max_search_file_bytes
        self.max_ast_evidence = max_ast_evidence
        self._registry: dict[str, Callable[[dict[str, Any]], CapabilityResult]] = {
            "list_tree": self._list_tree,
            "read_file": self._read_file,
            "search_text": self._search_text,
            "inspect_python": self._inspect_python,
        }
        self._argument_contracts = {
            "list_tree": ({"path"}, {"depth"}),
            "read_file": ({"path"}, {"line_start", "line_end"}),
            "search_text": ({"path", "query"}, set()),
            "inspect_python": ({"path"}, set()),
        }

    @property
    def capability_names(self) -> tuple[str, ...]:
        return tuple(self._registry)

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        if not isinstance(request.capability, str):
            return CapabilityResult(
                operation=str(request.capability), target=None, success=False,
                errors=("capability debe ser un string.",),
            )
        handler = self._registry.get(request.capability)
        if handler is None:
            return CapabilityResult(
                operation=request.capability,
                target=self._argument_target(request.arguments),
                success=False,
                errors=(f"Capacidad desconocida o no autorizada: {request.capability}",),
            )
        if not isinstance(request.arguments, dict):
            return CapabilityResult(
                operation=request.capability, target=None, success=False,
                errors=("Los argumentos deben ser un objeto.",),
            )
        try:
            self._validate_arguments(request.capability, request.arguments)
            return handler(request.arguments)
        except (ScopeViolation, ValueError, OSError) as error:
            return CapabilityResult(
                operation=request.capability,
                target=self._argument_target(request.arguments),
                success=False,
                errors=(str(error),),
            )

    def _validate_arguments(self, capability: str, arguments: dict[str, Any]) -> None:
        required, optional = self._argument_contracts[capability]
        missing = required - arguments.keys()
        if missing:
            raise ValueError("Faltan argumentos requeridos: " + ", ".join(sorted(missing)))
        unknown = arguments.keys() - required - optional
        if unknown:
            raise ValueError("Argumentos desconocidos: " + ", ".join(sorted(unknown)))
        path = arguments["path"]
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path debe ser un string no vacío.")
        if capability == "list_tree" and "depth" in arguments:
            depth = arguments["depth"]
            if not isinstance(depth, int) or isinstance(depth, bool):
                raise ValueError("depth debe ser un entero.")
            if not 0 <= depth <= self.max_tree_depth:
                raise ValueError(f"depth debe estar entre 0 y {self.max_tree_depth}.")
        if capability == "read_file":
            for name in ("line_start", "line_end"):
                if name not in arguments:
                    continue
                value = arguments[name]
                if name == "line_end" and value is None:
                    continue
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{name} debe ser un entero.")
            start = arguments.get("line_start", 1)
            end = arguments.get("line_end")
            if start < 1:
                raise ValueError("line_start debe ser un entero positivo.")
            if end is not None and end < start:
                raise ValueError("line_end debe ser mayor o igual a line_start.")
        if capability == "search_text":
            query = arguments["query"]
            if not isinstance(query, str) or not query:
                raise ValueError("query debe ser un string no vacío.")
            if len(query) > 500:
                raise ValueError("query supera el límite de 500 caracteres.")

    @staticmethod
    def _argument_target(arguments: dict[str, Any]) -> str | None:
        value = arguments.get("path") if isinstance(arguments, dict) else None
        return str(value) if value is not None else None

    def _path(self, arguments: dict[str, Any]) -> Path:
        value = arguments.get("path")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Se requiere el argumento string 'path'.")
        return self.scope.resolve_for_read(value)

    @staticmethod
    def _excluded(path: Path) -> bool:
        return path.name in DEFAULT_EXCLUDED_DIRECTORIES or path.suffix.lower() in DEFAULT_EXCLUDED_SUFFIXES

    @staticmethod
    def _link_like(
        path: Path,
        entry: os.DirEntry[str],
        inspection_errors: list[str] | None = None,
    ) -> bool:
        try:
            if entry.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if is_junction is not None and is_junction():
                return True
            # Python 3.11 no expone Path.is_junction(). El atributo Win32 está
            # disponible en stat_result sin seguir el reparse point.
            attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        except OSError as error:
            if inspection_errors is not None:
                inspection_errors.append(f"No se pudo inspeccionar {path}: {error}")
            return True
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

    def _walk_files(self, root: Path, traversal_errors: list[str]):
        if root.is_file():
            yield root
            return
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as scan:
                    entries = sorted(scan, key=lambda item: item.name.lower())
            except OSError as error:
                traversal_errors.append(f"No se pudo recorrer {directory}: {error}")
                continue
            for entry in entries:
                path = Path(entry.path)
                if self._excluded(path) or self._link_like(path, entry, traversal_errors):
                    continue
                self.scope.resolve_for_read(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path

    def _list_tree(self, arguments: dict[str, Any]) -> CapabilityResult:
        root = self._path(arguments)
        depth = arguments.get("depth", self.max_tree_depth)
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= self.max_tree_depth:
            raise ValueError(f"depth debe estar entre 0 y {self.max_tree_depth}.")
        if not root.exists():
            raise ValueError(f"No existe: {root}")
        if root.is_file():
            entries = [{"path": str(root), "type": "file"}]
            skipped: list[str] = []
        else:
            entries = []
            skipped = []
            pending = [(root, 0)]
            while pending and len(entries) < self.max_tree_entries:
                directory, level = pending.pop()
                with os.scandir(directory) as scan:
                    directory_entries = sorted(scan, key=lambda item: item.name.lower())
                for entry in directory_entries:
                    path = Path(entry.path)
                    if self._excluded(path):
                        skipped.append(str(path))
                        continue
                    if self._link_like(path, entry, skipped):
                        skipped.append(f"{path} (symlink/junction no recorrido)")
                        continue
                    self.scope.resolve_for_read(path)
                    kind = "directory" if entry.is_dir(follow_symlinks=False) else "file"
                    entries.append({"path": str(path), "type": kind})
                    if len(entries) >= self.max_tree_entries:
                        break
                    if kind == "directory" and level < depth:
                        pending.append((path, level + 1))
            truncated = bool(pending) or len(entries) >= self.max_tree_entries
        evidence = tuple(Evidence(path=item["path"], kind="tree-entry") for item in entries)
        return CapabilityResult(
            operation="list_tree", target=str(root), success=True,
            data={"entries": entries, "skipped": skipped}, evidence=evidence,
            truncated=truncated if root.is_dir() else False,
        )

    def _read_file(self, arguments: dict[str, Any]) -> CapabilityResult:
        path = self._path(arguments)
        if not path.is_file():
            raise ValueError(f"No es un archivo: {path}")
        start = arguments.get("line_start", 1)
        end = arguments.get("line_end")
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise ValueError("line_start debe ser un entero positivo.")
        if end is not None and (not isinstance(end, int) or isinstance(end, bool) or end < start):
            raise ValueError("line_end debe ser un entero mayor o igual a line_start.")
        with path.open("rb") as binary:
            if b"\x00" in binary.read(4096):
                raise ValueError(f"Se rechazó contenido binario: {path}")
        selected: list[str] = []
        selected_chars = 0
        truncated = False
        total_lines: int | None = 0
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for number, line in enumerate(source, 1):
                total_lines = number
                if number < start:
                    continue
                if end is not None and number > end:
                    break
                clean = line.rstrip("\r\n")
                extra = len(clean) + (1 if selected else 0)
                if selected_chars + extra > self.max_read_chars:
                    remaining = self.max_read_chars - selected_chars - (1 if selected else 0)
                    if selected and remaining >= 0:
                        selected.append(clean[:remaining])
                    elif remaining > 0:
                        selected.append(clean[:remaining])
                    truncated = True
                    total_lines = None
                    break
                selected.append(clean)
                selected_chars += extra
        if total_lines is not None and end is not None and total_lines > end:
            # Se detuvo al completar el rango; el total del archivo no fue recorrido.
            total_lines = None
        content = "\n".join(selected)
        represented_lines = content.count("\n") + (1 if content or selected else 0)
        actual_end = start + max(represented_lines - 1, 0)
        evidence_items = (
            Evidence(
                path=str(path), line_start=start, line_end=actual_end,
                excerpt=content, kind="file-content",
            ),
        ) if selected else ()
        return CapabilityResult(
            operation="read_file", target=str(path), success=True,
            data={"content": content, "line_start": start,
                  "line_end": actual_end if selected else None,
                  "total_lines": total_lines}, evidence=evidence_items, truncated=truncated,
        )

    def _search_text(self, arguments: dict[str, Any]) -> CapabilityResult:
        root = self._path(arguments)
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError("Se requiere el argumento string no vacío 'query'.")
        if len(query) > 500:
            raise ValueError("query supera el límite de 500 caracteres.")
        matches: list[dict[str, Any]] = []
        skipped: list[str] = []
        truncated = False
        searched_files = 0
        traversal_errors: list[str] = []
        for path in self._walk_files(root, traversal_errors):
            if searched_files >= self.max_search_files:
                truncated = True
                break
            searched_files += 1
            try:
                if path.stat().st_size > self.max_search_file_bytes:
                    skipped.append(str(path))
                    continue
                raw = path.read_bytes()
                if b"\x00" in raw[:4096]:
                    skipped.append(str(path))
                    continue
                for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        matches.append({"path": str(path), "line": number, "excerpt": line[:500]})
                        if len(matches) >= self.max_search_results:
                            truncated = True
                            break
            except OSError as error:
                skipped.append(f"{path}: {error}")
            if truncated:
                break
        evidence = tuple(
            Evidence(path=item["path"], line_start=item["line"], line_end=item["line"],
                     excerpt=item["excerpt"], kind="text-match")
            for item in matches
        )
        return CapabilityResult(
            operation="search_text", target=str(root), success=True,
            data={"query": query, "matches": matches,
                  "skipped": [*skipped, *traversal_errors],
                  "searched_files": searched_files},
            evidence=evidence, truncated=truncated,
        )

    def _inspect_python(self, arguments: dict[str, Any]) -> CapabilityResult:
        path = self._path(arguments)
        if not path.is_file() or path.suffix.lower() != ".py":
            raise ValueError(f"inspect_python requiere un archivo .py: {path}")
        if path.stat().st_size > self.max_search_file_bytes:
            raise ValueError(f"Archivo demasiado grande para análisis AST: {path}")
        report = inspect_python(path)
        evidence: list[Evidence] = []
        for item in report.get("classes", ()):
            evidence.append(Evidence(str(path), item["line"], item["line"], item["name"], kind="class"))
        for item in report.get("functions", ()):
            evidence.append(Evidence(str(path), item["line"], item["line"], item["name"], kind="function"))
        for item in report.get("imports", ()):
            evidence.append(Evidence(str(path), symbol=item, kind="import"))
        truncated = len(evidence) > self.max_ast_evidence
        return CapabilityResult(
            operation="inspect_python", target=str(path), success=True,
            data=report, evidence=tuple(evidence[:self.max_ast_evidence]),
            truncated=truncated,
        )
