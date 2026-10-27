from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ActivityState(StrEnum):
    """Pequeño vocabulario de actividad observable de la República."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    INSPECTING = "inspecting"
    WAITING_APPROVAL = "waiting_approval"
    ACTING = "acting"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    state: ActivityState

    @classmethod
    def from_value(cls, value: ActivityState | str) -> "ActivityEvent":
        return cls(value if isinstance(value, ActivityState) else ActivityState(value))


class ActivitySink(Protocol):
    """Destino opcional de estados; no concede control sobre el productor."""

    def set_state(self, state: ActivityState | str) -> ActivityEvent: ...


class NullActivitySink:
    """Sink por defecto: República no requiere una UI conectada."""

    def set_state(self, state: ActivityState | str) -> ActivityEvent:
        return ActivityEvent.from_value(state)


NULL_ACTIVITY = NullActivitySink()


class BestEffortActivitySink:
    """Aísla a un observador opcional de la semántica del productor."""

    def __init__(self, sink: ActivitySink) -> None:
        self.sink = sink
        self.last_error: Exception | None = None

    def set_state(self, state: ActivityState | str) -> ActivityEvent:
        event = ActivityEvent.from_value(state)
        try:
            return self.sink.set_state(event.state)
        except Exception as error:
            self.last_error = error
            return event


def best_effort_activity(sink: ActivitySink) -> BestEffortActivitySink:
    if isinstance(sink, BestEffortActivitySink):
        return sink
    return BestEffortActivitySink(sink)
