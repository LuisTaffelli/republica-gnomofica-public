"""One bounded, stateless Laboral read; no model, session, or durable receipt."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import stat
import subprocess
import sys
from threading import Event, Thread
from time import monotonic


MAX_INPUT_BYTES = 4096
MAX_OUTPUT_BYTES = 16384
MAX_FILE_BYTES = 65536
MAX_READ_CHARS = 1024
MAX_TARGET_BYTES = 1024
MAX_LINE = 10000
MAX_RANGE_LINES = 100
DEFAULT_TIMEOUT = 5.0

# Only this fixed Python entry runs in the child. Neither stdin nor file content
# supplies executable code, options, imports, capabilities, or additional paths.
_WORKER_CODE = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from cli.delegated_read_cli import _worker_main; "
    "raise SystemExit(_worker_main(sys.argv[1]))"
)


class ReadInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str, request_id: str | None = None) -> dict:
    return {
        "version": 1, "request_id": request_id, "status": "error",
        "result": None, "error": {"code": code, "message": message},
    }


def _encode(receipt: dict) -> bytes:
    return (json.dumps(receipt, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def _bounded(receipt: dict, exit_code: int) -> tuple[bytes, int]:
    encoded = _encode(receipt)
    if len(encoded) > MAX_OUTPUT_BYTES:
        return _encode(_error("output_limit", "La respuesta supera el límite de salida.", receipt["request_id"])), 6
    return encoded, exit_code


def _write(encoded: bytes) -> bool:
    try:
        stream = getattr(sys.stdout, "buffer", sys.stdout)
        stream.write(encoded)
        stream.flush()
        return True
    except (OSError, KeyboardInterrupt):
        return False


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Campo duplicado.")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("Número JSON no finito.")


def _parse_request(raw: bytes) -> dict:
    if len(raw) > MAX_INPUT_BYTES:
        raise ReadInputError("input_limit", "La petición supera 4096 bytes.")
    try:
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ReadInputError("invalid_request", "Se requiere un único objeto JSON UTF-8 válido.") from None
    fields = {"version", "request_id", "line_start", "line_end"}
    if not isinstance(request, dict) or set(request) - fields or not {"version", "request_id"} <= request.keys():
        raise ReadInputError("invalid_request", "Campos admitidos: version, request_id, line_start, line_end.")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ReadInputError("invalid_request", "version debe ser 1.")
    request_id = request["request_id"]
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", request_id) is None:
        raise ReadInputError("invalid_request", "request_id requiere 1-64 caracteres ASCII alfanuméricos o . _ : -.")
    start = request.get("line_start", 1)
    if type(start) is not int or not 1 <= start <= MAX_LINE:
        raise ReadInputError("invalid_request", "line_start debe ser un entero entre 1 y 10000.")
    end = request.get("line_end", min(start + MAX_RANGE_LINES - 1, MAX_LINE))
    if type(end) is not int or not start <= end <= MAX_LINE or end - start + 1 > MAX_RANGE_LINES:
        raise ReadInputError("invalid_request", "El rango debe ser ascendente, de hasta 100 líneas y terminar antes de 10001.")
    return {"request_id": request_id, "line_start": start, "line_end": end}


def _authorized_scope(target: str):
    from gnomos.laboral import InspectionScope

    path = Path(target)
    if (len(target.encode("utf-8")) > MAX_TARGET_BYTES or not path.is_absolute()
            or path.anchor.startswith(("\\\\", "//")) or ".." in path.parts
            or any(":" in part for part in path.parts[1:])):
        raise ReadInputError("invalid_target", "Se requiere una ruta local absoluta de archivo, sin aliases ni streams.")
    # The launcher authorizes a named regular file, never a directory, link or
    # junction. This is for a controlled fixture, not a filesystem sandbox.
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReadInputError("invalid_target", "No se admiten symlinks ni reparse points en el target o sus ancestros.")
    if not stat.S_ISREG(info.st_mode):
        raise ReadInputError("invalid_target", "El target autorizado debe ser un archivo regular.")
    if info.st_size > MAX_FILE_BYTES:
        raise ReadInputError("file_limit", "El archivo sintético supera 65536 bytes.")
    return InspectionScope(path)


def _read_once(target: str, request: dict) -> tuple[dict, int]:
    from gnomos.laboral import CapabilityRequest, LaboralCapabilities

    scope = _authorized_scope(target)
    result = LaboralCapabilities(scope, max_read_chars=MAX_READ_CHARS).execute(
        CapabilityRequest("read_file", {
            "path": str(scope.root), "line_start": request["line_start"], "line_end": request["line_end"],
        })
    )
    failed = not result.success or bool(result.errors)
    status = "error" if failed else "partial" if result.truncated else "completed"
    return {
        "version": 1, "request_id": request["request_id"], "status": status,
        "result": asdict(result),
        "error": {"code": "read_failed", "message": "Laboral no pudo completar la lectura."} if failed else None,
    }, 3 if failed else 1 if result.truncated else 0


def _worker_main(target: str) -> int:
    request_id = None
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        request = _parse_request(stream.read(MAX_INPUT_BYTES + 1))
        request_id = request["request_id"]
        receipt, code = _read_once(target, request)
    except ReadInputError as error:
        receipt, code = _error(error.code, str(error), request_id), 2
    except OSError:
        receipt, code = _error("read_failed", "No se pudo acceder al archivo autorizado o a la entrada.", request_id), 3
    except KeyboardInterrupt:
        receipt, code = _error("interrupted", "La lectura fue interrumpida.", request_id), 130
    except Exception:
        receipt, code = _error("internal_error", "Falló el adaptador de lectura.", request_id), 5
    encoded, code = _bounded(receipt, code)
    return code if _write(encoded) else 6


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReadInputError("invalid_arguments", "Uso: republica task-read --target ARCHIVO [--timeout-seconds 5].")


def _launch_options(args: list[str]) -> tuple[str, float]:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--timeout-seconds", action="append", type=float)
    options = parser.parse_args(args)
    timeouts = options.timeout_seconds or [DEFAULT_TIMEOUT]
    if len(options.target) != 1 or len(timeouts) != 1:
        parser.error("Opciones repetidas.")
    target = options.target[0]
    try:
        if not target or "\x00" in target or len(target.encode("utf-8")) > MAX_TARGET_BYTES:
            parser.error("Target inválido.")
    except UnicodeError:
        parser.error("Target inválido.")
    timeout = timeouts[0]
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
        parser.error("Plazo inválido.")
    return target, timeout


def _stop_and_wait(process: subprocess.Popen) -> None:
    # Own only the fixed adapter worker; its composition starts no descendants.
    # Repeated Ctrl+C cannot release ownership before the OS confirms its exit.
    while True:
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
            return
        except KeyboardInterrupt:
            continue


def _validate_receipt(stdout: bytes, exit_code: int, *, allow_partial: bool) -> dict:
    receipt = json.loads(stdout.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if (not isinstance(receipt, dict)
            or set(receipt) != {"version", "request_id", "status", "result", "error"}
            or type(receipt["version"]) is not int or receipt["version"] != 1):
        raise ValueError("Envelope de worker inválido.")
    # Reject escaped lone surrogates and overflow floats (e.g. 1e999), too.
    json.dumps(receipt, ensure_ascii=False, allow_nan=False).encode("utf-8")
    status, request_id = receipt["status"], receipt["request_id"]
    codes = {"completed": {0}, "error": {2, 3, 5, 6, 130}}
    if allow_partial:
        codes["partial"] = {1}
    if not isinstance(status, str) or status not in codes or exit_code not in codes[status]:
        raise ValueError("Estado o código de worker inválido.")
    if request_id is not None and (not isinstance(request_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", request_id) is None):
        raise ValueError("Correlación de worker inválida.")
    if status != "error":
        if request_id is None or not isinstance(receipt["result"], dict) or receipt["error"] is not None:
            raise ValueError("Recibo de éxito inconsistente.")
    else:
        error = receipt["error"]
        if (not isinstance(error, dict) or set(error) != {"code", "message"}
                or any(not isinstance(error[key], str) or not error[key].strip() for key in ("code", "message"))
                or (receipt["result"] is not None and not isinstance(receipt["result"], dict))):
            raise ValueError("Recibo de error inconsistente.")
    return receipt


def _supervise(target: str, timeout: float, *, worker_code: str | None = None,
               allow_partial: bool = True) -> tuple[bytes, int]:
    # worker_code is selected only by another local adapter, never by stdin.
    process, reader = None, None
    deadline = monotonic() + timeout
    captured, read_errors = [], []
    ready = Event()
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _WORKER_CODE if worker_code is None else worker_code,
             str(Path(__file__).resolve().parents[1]), target],
            stdin=getattr(sys.stdin, "buffer", sys.stdin), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False,
        )
        # Anonymous pipes are not selectable on Windows. One owned reader caps
        # allocation before EOF; communicate() would accumulate arbitrary output.
        def read_stdout():
            try:
                captured.append(process.stdout.read(MAX_OUTPUT_BYTES + 1))
            except (OSError, ValueError):
                read_errors.append(True)
            finally:
                try:
                    process.stdout.close()
                except OSError:
                    read_errors.append(True)
                finally:
                    ready.set()

        reader = Thread(target=read_stdout, name="delegated-stdout", daemon=True)
        reader.start()
        if not ready.wait(max(0, deadline - monotonic())):
            raise subprocess.TimeoutExpired(process.args, timeout)
        if read_errors or not captured:
            raise ValueError("Falló el canal de salida del worker.")
        stdout = captured[0]
        if len(stdout) > MAX_OUTPUT_BYTES:
            return _bounded(_error("output_limit", "El worker superó el límite de salida."), 6)
        process.wait(timeout=max(0, deadline - monotonic()))
        receipt = _validate_receipt(stdout, process.returncode, allow_partial=allow_partial)
        return _bounded(receipt, process.returncode)
    except subprocess.TimeoutExpired:
        return _bounded(_error("deadline_exceeded", "Se agotó el plazo; el worker fue terminado y su salida confirmada."), 4)
    except KeyboardInterrupt:
        return _bounded(_error("interrupted", "Ejecución interrumpida; el worker fue terminado y su salida confirmada."), 130)
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError):
        return _bounded(_error("worker_failed", "No se pudo iniciar o validar el worker."), 5)
    finally:
        if process is not None:
            try:
                _stop_and_wait(process)
            finally:
                if reader is not None and reader.ident is not None:
                    # If termination itself is unconfirmed, do not block trying
                    # to close a pipe another thread still owns. Propagate failure.
                    if process.poll() is not None:
                        while True:
                            try:
                                reader.join(timeout=1)
                                break
                            except KeyboardInterrupt:
                                continue
                        if reader.is_alive():
                            raise OSError("No se pudo confirmar el cierre de stdout.")
                elif process.stdout is not None:
                    process.stdout.close()


def handle_delegated_read_command(args: list[str]) -> int:
    try:
        target, timeout = _launch_options(args)
        encoded, code = _supervise(target, timeout)
    except ReadInputError as error:
        encoded, code = _bounded(_error(error.code, str(error)), 2)
    except KeyboardInterrupt:
        encoded, code = _bounded(_error("interrupted", "Se interrumpió el arranque del adaptador."), 130)
    except OSError:
        encoded, code = _bounded(_error("termination_unconfirmed", "El sistema no permitió confirmar la terminación del worker; no reintentar automáticamente."), 5)
    return code if _write(encoded) else 6


if __name__ == "__main__":
    raise SystemExit(handle_delegated_read_command(sys.argv[1:]))
