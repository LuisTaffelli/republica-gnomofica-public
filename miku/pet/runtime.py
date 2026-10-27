from __future__ import annotations

from collections.abc import Callable
import sys
import tkinter as tk
from threading import Lock, Thread

from .controller import PetController


class PetStartupError(RuntimeError):
    """Startup failed before the worker was authorized to call its callback."""


def run_with_pet(
    worker: Callable[[PetController], None],
    *,
    topmost: bool = True,
    controller: PetController | None = None,
) -> None:
    """Mantiene Tk en main thread y el flujo bloqueante de CLI en otro hilo."""
    try:
        from .view import run_pet_window
        controller = controller or PetController()
    except (Exception, KeyboardInterrupt) as error:
        raise PetStartupError(str(error) or type(error).__name__) from error
    startup_lock = Lock()
    allowed = False

    def run_worker() -> None:
        try:
            with startup_lock:
                may_run = allowed
            if not may_run:
                return
            worker(controller)
        except KeyboardInterrupt:
            _print_notice(
                "El flujo de Miku se interrumpió; no se confirmó el cierre de la sesión."
            )
        except Exception as error:
            controller.report_worker_error(error)
        finally:
            controller.request_close()

    with startup_lock:
        try:
            thread = Thread(target=run_worker, name="miku-cli", daemon=True)
            thread.start()
            allowed = True
        except (Exception, KeyboardInterrupt) as error:
            # start() can fail even after launching a native thread. Keep the
            # gate closed under the same lock; a late target cannot run the CLI.
            allowed = False
            controller.request_close()
            controller.close_visual()
            raise PetStartupError(str(error) or type(error).__name__) from error
    try:
        run_pet_window(controller, topmost=topmost)
    except KeyboardInterrupt:
        _request_stop(controller)
    except tk.TclError as error:
        # Un arranque gráfico fallido no cancela el flujo de consola.
        _print_notice(f"Pet no disponible: {error}")
    finally:
        controller.close_visual()
        # Sólo el worker termina el lifecycle. El Event lo confirma incluso si
        # Ctrl+C interrumpe repetidamente la espera del hilo principal.
        while True:
            try:
                if controller.wait_for_worker(0.1):
                    break
            except KeyboardInterrupt:
                _request_stop(controller)
    if controller.worker_error is not None:
        _print_notice(f"El flujo de Miku terminó con error: {controller.worker_error}")


def _request_stop(controller: PetController) -> None:
    already_requested = controller.stop_requested
    controller.request_stop()
    if not already_requested:
        _print_notice(
            "Interrupción solicitada. Esperando que termine la operación actual "
            "para cerrar; si la consola espera entrada, presioná Enter o enviá EOF."
        )


def _print_notice(text: str) -> None:
    try:
        print(text, file=sys.stderr)
    except (KeyboardInterrupt, EOFError, OSError):
        # No repetir un aviso interrumpido ni abandonar la espera del worker.
        pass
