from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Event, Lock

from core.activity import ActivityEvent, ActivityState


StateListener = Callable[[ActivityEvent], None]


class PetController:
    """Adaptador thread-safe entre la frontera de actividad y la vista Tk."""

    def __init__(self, initial_state: ActivityState = ActivityState.IDLE) -> None:
        self._state = initial_state
        # La vista sólo necesita el estado más reciente, no replay.
        self._events: Queue[ActivityEvent] = Queue(maxsize=1)
        self._listeners: list[StateListener] = []
        self._lock = Lock()
        self._visual_active = Event()
        self._visual_active.set()
        self._stop_requested = Event()
        self._worker_done = Event()
        self._worker_error: Exception | None = None

    @property
    def state(self) -> ActivityState:
        with self._lock:
            return self._state

    @property
    def close_requested(self) -> bool:
        return self._worker_done.is_set()

    @property
    def visual_active(self) -> bool:
        return self._visual_active.is_set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    @property
    def worker_error(self) -> Exception | None:
        with self._lock:
            return self._worker_error

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def set_state(self, state: ActivityState | str) -> ActivityEvent:
        event = ActivityEvent.from_value(state)
        with self._lock:
            self._state = event.state
            if not self._visual_active.is_set():
                return event
            try:
                self._events.put_nowait(event)
            except Full:
                try:
                    self._events.get_nowait()
                except Empty:
                    pass
                self._events.put_nowait(event)
        return event

    def dispatch_pending(self) -> int:
        """Notifica en el hilo caller; la vista lo invoca desde ``after``."""
        dispatched = 0
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                return dispatched
            for listener in tuple(self._listeners):
                listener(event)
            dispatched += 1

    def request_close(self) -> None:
        self._worker_done.set()

    def request_stop(self) -> None:
        """Solicita salida en un límite seguro; no cierra ni interrumpe el turno."""
        self._stop_requested.set()

    def wait_for_worker(self, timeout: float) -> bool:
        return self._worker_done.wait(timeout)

    def close_visual(self) -> None:
        with self._lock:
            self._visual_active.clear()
            while True:
                try:
                    self._events.get_nowait()
                except Empty:
                    return

    def report_worker_error(self, error: Exception) -> None:
        with self._lock:
            self._worker_error = error
