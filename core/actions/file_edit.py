from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.audit import AuditService
from core.activity import ActivitySink, NULL_ACTIVITY
from core.paths import DATA_DIR

from .coordinator import ActionCoordinator
from .models import ActionIntent, EffectKind, EffectResult


FILE_EDIT_CAPABILITY = "filesystem_edit"
APPLY_TEXT_PATCH = "apply_text_patch"
UTF8 = "utf-8"
MAX_FILE_BYTES = 1_000_000
MAX_PATCH_CHARS = 20_000
MAX_PRESENTATION_CHARS = 8_000
FILE_EDIT_BACKUP_DIR = DATA_DIR / "action_backups" / "file_edits"


class FileEditValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileEditScope:
    """Autoriza exactamente un archivo existente, nunca sus hermanos."""

    file: Path

    def __post_init__(self) -> None:
        candidate = self._absolute_without_traversal(self.file)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise FileEditValidationError(
                f"El archivo autorizado no existe o no puede resolverse: {candidate}"
            ) from error
        if not resolved.is_file():
            raise FileEditValidationError(
                f"La edición requiere un único archivo existente: {resolved}"
            )
        object.__setattr__(self, "file", resolved)

    def resolve_for_write(self, value: str | Path) -> Path:
        candidate = self._absolute_without_traversal(value)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise FileEditValidationError(
                f"El target de edición no existe o no puede resolverse: {candidate}"
            ) from error
        if resolved != self.file or not resolved.is_file():
            raise FileEditValidationError(
                f"Fuera del scope de escritura autorizado: {resolved}"
            )
        return resolved

    @staticmethod
    def _absolute_without_traversal(value: str | Path) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise FileEditValidationError(
                f"Se rechazó una ruta relativa o ambigua: {candidate}"
            )
        if ".." in candidate.parts:
            raise FileEditValidationError(
                f"Se rechazó una ruta con traversal: {candidate}"
            )
        if os.name == "nt" and any(":" in part for part in candidate.parts[1:]):
            raise FileEditValidationError(
                f"Se rechazó una ruta con stream alternativo: {candidate}"
            )
        if _contains_link_or_reparse(candidate):
            raise FileEditValidationError(
                "Se rechazó un symlink, junction o reparse point en el target "
                f"o sus ancestros: {candidate}"
            )
        return candidate


@dataclass(frozen=True, slots=True)
class FileEditPlan:
    intent: ActionIntent
    presentation: str
    full_presentation: str
    presentation_truncated: bool


def prepare_text_patch(
    *,
    scope: FileEditScope,
    target: str | Path,
    expected_text: str,
    replacement_text: str,
    requested_by: str,
    profile_name: str,
    session_id: str | None = None,
    message_id: str | None = None,
) -> FileEditPlan:
    """Valida una propuesta y captura su precondición sin producir efectos."""
    resolved = scope.resolve_for_write(target)
    _validate_patch_text(expected_text, replacement_text)
    original_bytes = _read_bounded(resolved)
    original_text = _decode_utf8(original_bytes, resolved)
    occurrences = original_text.count(expected_text)
    if occurrences != 1:
        raise FileEditValidationError(
            "El texto esperado debe aparecer exactamente una vez; "
            f"se encontraron {occurrences} coincidencias."
        )
    final_text = original_text.replace(expected_text, replacement_text, 1)
    before_hash = _sha256(original_bytes)
    intent = ActionIntent(
        capability=FILE_EDIT_CAPABILITY,
        operation=APPLY_TEXT_PATCH,
        parameters={
            "encoding": UTF8,
            "expected_sha256": before_hash,
            "expected_text": expected_text,
            "replacement_text": replacement_text,
        },
        effect_kind=EffectKind.EFFECTFUL,
        target=str(resolved),
        requested_by=requested_by,
        profile_name=profile_name,
        session_id=session_id,
        message_id=message_id,
        redacted_fields=("expected_text", "replacement_text"),
    )
    presentation, full_presentation, truncated = _render_presentations(
        resolved,
        original_text,
        final_text,
        before_hash,
        expected_text,
        replacement_text,
    )
    return FileEditPlan(intent, presentation, full_presentation, truncated)


def build_file_edit_coordinator(
    *,
    scope: FileEditScope,
    audit: AuditService,
    activity: ActivitySink = NULL_ACTIVITY,
    recovery_root: str | Path = FILE_EDIT_BACKUP_DIR,
) -> ActionCoordinator:
    """Construye la única entrada productiva a la capability interna de escritura."""
    return ActionCoordinator(
        audit=audit,
        capabilities={
            FILE_EDIT_CAPABILITY: _ApplyTextPatchCapability(
                scope, _FileEditRecoveryStore(Path(recovery_root))
            )
        },
        activity=activity,
    )


@dataclass(frozen=True, slots=True)
class _RecoveryArtifact:
    reference: str
    original_sha256: str


class _FileEditRecoveryStore:
    """Storage pequeño: un original inmutable y autocontenido por intent."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def preserve(
        self, *, intent: ActionIntent, target: Path, content: bytes
    ) -> _RecoveryArtifact:
        original_hash = _sha256(content)
        expected_hash = str(intent.parameters["expected_sha256"])
        if original_hash != expected_hash:
            raise FileEditValidationError(
                "El contenido a recuperar no coincide con el hash original del intent."
            )
        directory_name = f"{intent.id}-{intent.fingerprint}"
        final_directory = self.root / directory_name
        if final_directory.exists():
            raise FileEditValidationError(
                f"Ya existe recuperación para este intent: {directory_name}"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=self.root))
        try:
            original_path = temporary / "original.utf8"
            with original_path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                original_path.chmod(0o600)
            except OSError:
                pass
            if _sha256(_read_bounded(original_path)) != original_hash:
                raise OSError("La copia de recuperación no superó su verificación SHA-256.")
            metadata = {
                "version": 1,
                "intent_id": intent.id,
                "fingerprint": intent.fingerprint,
                "target": str(target),
                "original_sha256": original_hash,
                "original_bytes": len(content),
            }
            metadata_path = temporary / "metadata.json"
            with metadata_path.open("x", encoding=UTF8, newline="\n") as stream:
                json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                metadata_path.chmod(0o600)
                temporary.chmod(0o700)
            except OSError:
                pass
            os.replace(temporary, final_directory)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return _RecoveryArtifact(
            reference=f"file-edit-backup:{directory_name}",
            original_sha256=original_hash,
        )


class _ApplyTextPatchCapability:
    def __init__(self, scope: FileEditScope, recovery: _FileEditRecoveryStore) -> None:
        self._scope = scope
        self._recovery = recovery

    def validate(self, intent: ActionIntent) -> None:
        if intent.capability != FILE_EDIT_CAPABILITY:
            raise FileEditValidationError("Capability de edición inválida.")
        if intent.operation != APPLY_TEXT_PATCH:
            raise FileEditValidationError("Operación de edición inválida.")
        if intent.effect_kind is not EffectKind.EFFECTFUL:
            raise FileEditValidationError("La edición debe declararse effectful.")
        expected_keys = {
            "encoding", "expected_sha256", "expected_text", "replacement_text"
        }
        if set(intent.parameters) != expected_keys:
            raise FileEditValidationError("Parámetros inválidos para apply_text_patch.")
        if intent.parameters["encoding"] != UTF8:
            raise FileEditValidationError("apply_text_patch sólo admite UTF-8.")
        expected_hash = intent.parameters["expected_sha256"]
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise FileEditValidationError("La precondición SHA-256 es inválida.")
        expected_text = intent.parameters["expected_text"]
        replacement_text = intent.parameters["replacement_text"]
        if not isinstance(expected_text, str) or not isinstance(replacement_text, str):
            raise FileEditValidationError("El patch debe contener texto UTF-8.")
        _validate_patch_text(expected_text, replacement_text)
        self._scope.resolve_for_write(intent.target)

    def execute(self, intent: ActionIntent) -> EffectResult:
        target: Path | None = None
        recovery: _RecoveryArtifact | None = None
        expected_hash = str(intent.parameters.get("expected_sha256", ""))
        try:
            self.validate(intent)
            target = self._scope.resolve_for_write(intent.target)
            current_bytes = _read_bounded(target)
            current_hash = _sha256(current_bytes)
            if current_hash != expected_hash:
                return _precondition_failure(target, expected_hash, current_hash)
            current_text = _decode_utf8(current_bytes, target)
            expected_text = str(intent.parameters["expected_text"])
            replacement_text = str(intent.parameters["replacement_text"])
            if current_text.count(expected_text) != 1:
                return EffectResult(
                    success=False,
                    evidence=({
                        "kind": "filesystem_write",
                        "target": str(target),
                        "sha256_expected_before": expected_hash,
                        "sha256_actual_before": current_hash,
                        "write_performed": False,
                    },),
                    error="La precondición textual dejó de cumplirse; se requiere una propuesta nueva.",
                )
            final_text = current_text.replace(expected_text, replacement_text, 1)
            final_bytes = final_text.encode(UTF8)
            if len(final_bytes) > MAX_FILE_BYTES:
                return EffectResult(
                    success=False,
                    evidence=({
                        "kind": "filesystem_write",
                        "target": str(target),
                        "sha256_expected_before": expected_hash,
                        "write_performed": False,
                    },),
                    error=f"El resultado supera el límite de {MAX_FILE_BYTES} bytes.",
                )
            after_hash = _sha256(final_bytes)
            temp_path = self._write_temp(target, final_bytes)
            try:
                # Segunda lectura inmediatamente antes del replace: reduce la ventana
                # local sin prometer locking o atomicidad distribuida.
                checked_target = self._scope.resolve_for_write(intent.target)
                latest_hash = _sha256(_read_bounded(checked_target))
                if latest_hash != expected_hash:
                    return _precondition_failure(target, expected_hash, latest_hash)
                recovery = self._recovery.preserve(
                    intent=intent, target=target, content=current_bytes
                )
                # El backup introduce trabajo I/O; esta última comprobación acorta
                # nuevamente la ventana TOCTOU antes del replace, sin ofrecer lock.
                checked_target = self._scope.resolve_for_write(intent.target)
                latest_hash = _sha256(_read_bounded(checked_target))
                if latest_hash != expected_hash:
                    return _precondition_failure(
                        target, expected_hash, latest_hash, recovery=recovery
                    )
                os.replace(temp_path, target)
                temp_path = None
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            return EffectResult(
                success=True,
                evidence=({
                    "kind": "filesystem_write",
                    "operation": APPLY_TEXT_PATCH,
                    "target": str(target),
                    "encoding": UTF8,
                    "sha256_before": current_hash,
                    "sha256_after": after_hash,
                    "bytes_before": len(current_bytes),
                    "bytes_after": len(final_bytes),
                    "replaced_chars": len(expected_text),
                    "replacement_chars": len(replacement_text),
                    "write_strategy": "same_directory_temp_then_os_replace",
                    "recovery_reference": recovery.reference,
                    "recovery_sha256": recovery.original_sha256,
                },),
                reversible=False,
            )
        except (FileEditValidationError, OSError) as error:
            evidence = ({
                "kind": "filesystem_write",
                "target": str(target or intent.target),
                "sha256_expected_before": expected_hash,
                "write_performed": False,
                **(
                    {
                        "recovery_reference": recovery.reference,
                        "recovery_sha256": recovery.original_sha256,
                    }
                    if recovery is not None
                    else {}
                ),
            },)
            return EffectResult(
                success=False,
                evidence=evidence,
                error=str(error),
                reversible=False,
            )

    @staticmethod
    def _write_temp(target: Path, content: bytes) -> Path:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{target.name}.republic-", suffix=".tmp", dir=target.parent
        )
        temp_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            shutil.copymode(target, temp_path)
            return temp_path
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temp_path.unlink(missing_ok=True)
            raise


def _validate_patch_text(expected_text: str, replacement_text: str) -> None:
    if not isinstance(expected_text, str) or not expected_text:
        raise FileEditValidationError("El texto esperado debe ser un string no vacío.")
    if not isinstance(replacement_text, str):
        raise FileEditValidationError("El replacement debe ser texto.")
    if expected_text == replacement_text:
        raise FileEditValidationError("El patch debe producir un cambio real.")
    if len(expected_text) > MAX_PATCH_CHARS or len(replacement_text) > MAX_PATCH_CHARS:
        raise FileEditValidationError(
            f"Cada lado del patch admite hasta {MAX_PATCH_CHARS} caracteres."
        )


def _read_bounded(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise FileEditValidationError(f"No se pudo medir el archivo: {path}") from error
    if size > MAX_FILE_BYTES:
        raise FileEditValidationError(
            f"El archivo supera el límite de {MAX_FILE_BYTES} bytes."
        )
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise FileEditValidationError(
            f"El archivo supera el límite de {MAX_FILE_BYTES} bytes."
        )
    return content


def _decode_utf8(content: bytes, path: Path) -> str:
    try:
        return content.decode(UTF8)
    except UnicodeDecodeError as error:
        raise FileEditValidationError(f"El archivo no es UTF-8 válido: {path}") from error


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        if os.name == "nt":
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            return bool(attributes & reparse_flag)
    except OSError:
        return False
    return False


def _contains_link_or_reparse(path: Path) -> bool:
    """Comprueba cada componente existente antes de resolver el path."""
    chain = [path, *path.parents]
    return any(_is_link_or_reparse(item) for item in chain if item.exists())


def _precondition_failure(
    target: Path,
    expected_hash: str,
    actual_hash: str,
    *,
    recovery: _RecoveryArtifact | None = None,
) -> EffectResult:
    return EffectResult(
        success=False,
        evidence=({
            "kind": "filesystem_write",
            "target": str(target),
            "sha256_expected_before": expected_hash,
            "sha256_actual_before": actual_hash,
            "write_performed": False,
            **(
                {
                    "recovery_reference": recovery.reference,
                    "recovery_sha256": recovery.original_sha256,
                }
                if recovery is not None
                else {}
            ),
        },),
        error="El archivo cambió desde la propuesta; se requiere una propuesta nueva.",
    )


def _render_presentations(
    target: Path,
    before: str,
    after: str,
    before_hash: str,
    expected_text: str,
    replacement_text: str,
) -> tuple[str, str, bool]:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
        )
    )
    full_diff = diff or "(cambio textual sin representación de líneas)"
    truncated = len(full_diff) > MAX_PRESENTATION_CHARS
    visible_diff = full_diff
    if truncated:
        visible_diff = (
            full_diff[:MAX_PRESENTATION_CHARS]
            + "\n... diff truncado. Escribí 'ver propuesta completa' para "
            "revisar los textos exactos; "
            "la aprobación queda bloqueada hasta entonces.\n"
        )
    header = (
        f"Archivo objetivo: {target}\n"
        f"Operación: {APPLY_TEXT_PATCH} (acción con efecto)\n"
        f"SHA-256 original: {before_hash}\n"
        f"Tamaño: {len(before.encode(UTF8))} -> {len(after.encode(UTF8))} bytes\n"
        "Cambio propuesto:\n"
    )
    presentation = f"{header}```diff\n{visible_diff}```"
    if truncated:
        full_presentation = (
            f"{header}Propuesta material completa (strings JSON exactos):\n"
            f"expected_text ({len(expected_text)} caracteres): "
            f"{json.dumps(expected_text, ensure_ascii=False)}\n"
            f"replacement_text ({len(replacement_text)} caracteres): "
            f"{json.dumps(replacement_text, ensure_ascii=False)}"
        )
    else:
        full_presentation = presentation
    return presentation, full_presentation, truncated
