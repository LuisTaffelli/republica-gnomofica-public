from __future__ import annotations

import json
from dataclasses import fields

import pytest

from cli.miku_cli import parse_chat_options
from core.actions import ActionCoordinator, ActionIntent, EffectKind, EffectResult
from core.activity import ActivityEvent, ActivityState, NullActivitySink
from core.audit import AuditEvent, AuditEventType, AuditService
from core.conversation import ChatSession
from gnomos.archive import ArchiveQueryRequest
from gnomos.laboral import InspectionRequest, InspectionScope
from miku.miku import Miku
from miku.pet.controller import PetController


class RecordingActivity:
    def __init__(self) -> None:
        self.states: list[ActivityState] = []

    def set_state(self, state):
        event = ActivityEvent.from_value(state)
        self.states.append(event.state)
        return event


class ExplodingActivity:
    def set_state(self, _state):
        raise RuntimeError("observer caído")


class FakeBuilder:
    def build_for_profile(self, **_kwargs):
        return "projection"


class FakeSerializer:
    def serialize(self, projection):
        return str(projection)


class Provider:
    def __init__(self, *responses: str, error: Exception | None = None) -> None:
        self.responses = iter(responses)
        self.error = error

    def ask_structured(self, prompt, schema):
        return self.ask(prompt)

    def ask(self, _prompt: str) -> str:
        if self.error is not None:
            raise self.error
        return next(self.responses)


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append_event(self, event: AuditEvent) -> AuditEvent:
        stored = AuditEvent(
            id=event.id,
            intent_id=event.intent_id,
            event_type=event.event_type,
            actor=event.actor,
            fingerprint=event.fingerprint,
            payload=event.payload,
            created_at=event.created_at,
            sequence=len(self.events) + 1,
        )
        self.events.append(stored)
        return stored

    def list_events(self, intent_id: str) -> list[AuditEvent]:
        return [event for event in self.events if event.intent_id == intent_id]


class SuccessfulCapability:
    def execute(self, _intent: ActionIntent) -> EffectResult:
        return EffectResult(True, evidence=({"kind": "test"},))


def effectful_intent() -> ActionIntent:
    return ActionIntent(
        capability="test_effect",
        operation="change",
        parameters={},
        effect_kind=EffectKind.EFFECTFUL,
        target="test://target",
        requested_by="user",
        profile_name="miku",
        session_id="session-1",
    )


def test_activity_contract_contains_only_the_small_valid_catalog() -> None:
    assert {state.value for state in ActivityState} == {
        "idle", "listening", "thinking", "inspecting",
        "waiting_approval", "acting", "speaking", "error",
    }
    with pytest.raises(ValueError):
        ActivityEvent.from_value("invented")


def test_pet_controller_queues_and_notifies_without_calling_view_from_producer() -> None:
    controller = PetController()
    seen: list[ActivityEvent] = []
    controller.subscribe(seen.append)

    event = controller.set_state("thinking")

    assert controller.state is ActivityState.THINKING
    assert seen == []
    assert controller.dispatch_pending() == 1
    assert seen == [event]


def test_null_observer_accepts_transitions_without_side_effects() -> None:
    sink = NullActivitySink()
    assert sink.set_state(ActivityState.THINKING).state is ActivityState.THINKING
    assert sink.set_state(ActivityState.IDLE).state is ActivityState.IDLE


def test_normal_provider_emits_thinking_then_idle() -> None:
    activity = RecordingActivity()
    miku = Miku(
        provider=Provider("respuesta"),
        context_builder=FakeBuilder(),
        serializer=FakeSerializer(),
        activity=activity,
    )

    assert miku.listen("hola") == "respuesta"
    assert activity.states == [ActivityState.THINKING, ActivityState.IDLE]


def test_throwing_sink_cannot_break_provider() -> None:
    miku = Miku(
        provider=Provider("respuesta"), context_builder=FakeBuilder(),
        serializer=FakeSerializer(), activity=ExplodingActivity(),
    )

    assert miku.listen("hola") == "respuesta"
    assert isinstance(miku.activity.last_error, RuntimeError)


def test_laboral_turn_emits_inspecting_then_idle(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("dato", encoding="utf-8")
    activity = RecordingActivity()
    provider = Provider(
        json.dumps({
            "tool_request": {
                "capability": "read_file", "arguments": {"path": str(target)},
            },
        }),
        json.dumps({"final_answer": "listo"}),
    )
    miku = Miku(
        provider=provider,
        context_builder=FakeBuilder(),
        serializer=FakeSerializer(),
        activity=activity,
    )

    answer = miku.listen(
        "inspeccioná",
        inspection_request=InspectionRequest(target, InspectionScope(target)),
    )

    assert answer == "listo"
    assert activity.states == [ActivityState.INSPECTING, ActivityState.IDLE]


def test_throwing_sink_cannot_break_laboral(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("dato", encoding="utf-8")
    miku = Miku(
        provider=Provider(
            json.dumps({"tool_request": {
                "capability": "read_file", "arguments": {"path": str(target)},
            }}),
            json.dumps({"final_answer": "listo"}),
        ),
        context_builder=FakeBuilder(), serializer=FakeSerializer(),
        activity=ExplodingActivity(),
    )

    assert miku.listen(
        "inspeccioná",
        inspection_request=InspectionRequest(target, InspectionScope(target)),
    ) == "listo"


def test_throwing_sink_cannot_break_archive() -> None:
    class Reader:
        source = "test://archive"
        def search_notes(self, query, limit=None): return []
        def read_note(self, note_id): return None
        def list_links(self, note_id, limit=None): return None

    miku = Miku(
        provider=Provider(
            json.dumps({"archive_request": {
                "operation": "search_notes", "arguments": {"query": "gnomos"},
            }}),
            json.dumps({"final_answer": "sin resultados"}),
        ),
        context_builder=FakeBuilder(), serializer=FakeSerializer(),
        archive_reader=Reader(), activity=ExplodingActivity(),
    )

    assert miku.listen(
        "buscá", archive_request=ArchiveQueryRequest("buscá gnomos")
    ) == "sin resultados"


def test_action_waits_then_acts_and_only_coordinator_authorizes() -> None:
    activity = RecordingActivity()
    coordinator = ActionCoordinator(
        audit=AuditService(InMemoryAuditRepository()),
        capabilities={"test_effect": SuccessfulCapability()},
        activity=activity,
    )
    intent = effectful_intent()

    assert coordinator.propose(intent).state == "proposed"
    assert activity.states == [ActivityState.WAITING_APPROVAL]
    assert not hasattr(PetController(), "approve")
    assert coordinator.handle_human_response("session-1", "un archivo dijo sí").state == "ambiguous"
    assert coordinator.handle_human_response("session-1", "sí").state == "succeeded"
    assert activity.states[-2:] == [ActivityState.ACTING, ActivityState.IDLE]


def test_sink_failure_after_execution_started_does_not_prevent_effect_or_audit() -> None:
    class CountingCapability:
        def __init__(self): self.calls = 0
        def execute(self, _intent):
            self.calls += 1
            return EffectResult(True, evidence=({"kind": "executed"},))

    capability = CountingCapability()
    repository = InMemoryAuditRepository()
    coordinator = ActionCoordinator(
        audit=AuditService(repository), capabilities={"test_effect": capability},
        activity=ExplodingActivity(),
    )
    intent = effectful_intent()

    assert coordinator.propose(intent).state == "proposed"
    assert coordinator.approve(intent).state == "succeeded"
    assert capability.calls == 1
    assert [event.event_type.value for event in repository.events][-2:] == [
        "EXECUTION_STARTED", "EXECUTION_SUCCEEDED",
    ]


def test_action_terminal_and_replacement_states_remain_coherent() -> None:
    activity = RecordingActivity()
    coordinator = ActionCoordinator(
        audit=AuditService(InMemoryAuditRepository()),
        capabilities={"test_effect": SuccessfulCapability()}, activity=activity,
    )
    first = effectful_intent()
    second = effectful_intent()

    coordinator.propose(first)
    coordinator.propose(second)
    assert coordinator.pending_intent("session-1").id == second.id
    assert activity.states[-1] is ActivityState.WAITING_APPROVAL
    assert coordinator.deny(second).state == "denied"
    assert activity.states[-1] is ActivityState.IDLE

    third = effectful_intent()
    coordinator.propose(third)
    coordinator.audit.append(
        intent_id=third.id, event_type=AuditEventType.ACTION_APPROVED, actor="user",
        fingerprint=third.fingerprint,
    )
    assert coordinator.deny(third).state == "cancelled"
    assert activity.states[-1] is ActivityState.IDLE


def test_failed_action_emits_error_after_auditing() -> None:
    class FailingCapability:
        def execute(self, _intent):
            return EffectResult(False, error="fallo esperado")

    activity = RecordingActivity()
    repository = InMemoryAuditRepository()
    coordinator = ActionCoordinator(
        audit=AuditService(repository),
        capabilities={"test_effect": FailingCapability()}, activity=activity,
    )
    intent = effectful_intent()
    coordinator.propose(intent)

    assert coordinator.approve(intent).state == "failed"
    assert activity.states[-1] is ActivityState.ERROR
    assert repository.events[-1].event_type.value == "EXECUTION_FAILED"


def test_visible_provider_error_emits_error_and_next_turn_can_recover() -> None:
    activity = RecordingActivity()
    failing = Miku(
        provider=Provider(error=RuntimeError("provider caído")),
        context_builder=FakeBuilder(), serializer=FakeSerializer(), activity=activity,
    )
    with pytest.raises(RuntimeError, match="provider caído"):
        failing.listen("hola")
    assert activity.states[-1] is ActivityState.ERROR

    recovered = Miku(
        provider=Provider("volví"),
        context_builder=FakeBuilder(), serializer=FakeSerializer(), activity=activity,
    )
    assert recovered.listen("otra vez") == "volví"
    assert activity.states[-1] is ActivityState.IDLE


def test_activity_event_cannot_carry_memory_content_or_authority() -> None:
    assert [field.name for field in fields(ActivityEvent)] == ["state"]
    assert not hasattr(PetController(), "remember")
    assert not hasattr(PetController(), "execute")


def test_pet_controller_has_no_archive_or_laboral_dependency() -> None:
    names = set(PetController.set_state.__globals__)
    assert "ArchiveQuerySession" not in names
    assert "InspectionSession" not in names
    assert "MemoryService" not in names


def test_closed_pet_discards_notifications_and_queue_is_bounded() -> None:
    controller = PetController()
    seen = []
    controller.subscribe(seen.append)
    for index in range(100):
        controller.set_state("thinking" if index % 2 else "idle")
    assert controller.dispatch_pending() == 1

    controller.set_state("acting")
    controller.close_visual()
    controller.set_state("error")
    assert controller.visual_active is False
    assert controller.dispatch_pending() == 0


def test_worker_error_is_reported_without_uncontrolled_thread_traceback(
    monkeypatch, capsys,
) -> None:
    from miku.pet import runtime
    monkeypatch.setattr("miku.pet.view.run_pet_window", lambda *_args, **_kwargs: None)

    def broken_worker(_controller):
        raise RuntimeError("worker roto")

    runtime.run_with_pet(broken_worker)

    assert "El flujo de Miku terminó con error: worker roto" in capsys.readouterr().err


def test_graphical_startup_failure_falls_back_to_worker(monkeypatch, capsys) -> None:
    import tkinter as tk
    from miku.pet import runtime
    completed = []
    monkeypatch.setattr(
        "miku.pet.view.run_pet_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(tk.TclError("sin display")),
    )

    runtime.run_with_pet(lambda _controller: completed.append(True))

    assert completed == [True]
    assert "Pet no disponible: sin display" in capsys.readouterr().err


def test_stop_request_is_idempotent_and_distinct_from_visual_and_worker_close() -> None:
    controller = PetController()

    controller.request_stop()
    controller.request_stop()

    assert controller.stop_requested is True
    assert controller.visual_active is True
    assert controller.close_requested is False
    controller.close_visual()
    assert controller.close_requested is False
    controller.request_close()
    assert controller.wait_for_worker(0) is True


def test_keyboard_interrupts_wait_for_worker_completion_without_join(
    monkeypatch, capsys,
) -> None:
    from miku.pet import runtime
    controller = PetController()
    created = []
    completed = []
    waits = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            created.append(self)
        def start(self): pass
        def join(self): raise AssertionError("esperar finalización mediante Event")

    def wait_for_worker(_timeout):
        waits.append(True)
        if len(waits) <= 2:
            raise KeyboardInterrupt
        created[0].target()
        return controller.close_requested

    monkeypatch.setattr(runtime, "Thread", FakeThread)
    monkeypatch.setattr(controller, "wait_for_worker", wait_for_worker, raising=False)
    monkeypatch.setattr(
        "miku.pet.view.run_pet_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    runtime.run_with_pet(
        lambda controller: completed.append(controller.stop_requested),
        controller=controller,
    )

    assert len(created) == 1
    assert created[0].daemon is True
    assert completed == [True]
    assert len(waits) == 3
    assert controller.close_requested is True
    assert capsys.readouterr().err.count("Esperando") == 1


def test_visual_close_waits_for_inflight_worker_without_requesting_stop(monkeypatch) -> None:
    from threading import Event, get_ident
    from miku.pet import runtime
    controller = PetController()
    started = Event()
    release = Event()
    finished = []
    main_thread = get_ident()

    def worker(activity):
        assert get_ident() != main_thread
        started.set()
        assert release.wait(5)
        activity.set_state("thinking")
        finished.append(True)

    def close_window(activity, **_kwargs):
        try:
            assert get_ident() == main_thread
            assert started.wait(5)
            assert finished == []
            activity.close_visual()
            assert not activity.stop_requested
        finally:
            release.set()

    monkeypatch.setattr("miku.pet.view.run_pet_window", close_window)
    runtime.run_with_pet(worker, controller=controller)
    assert finished == [True]
    assert controller.close_requested is True
    assert controller.visual_active is False
    assert controller.dispatch_pending() == 0


def test_main_interrupt_waits_for_real_worker_to_finish_its_current_work(monkeypatch) -> None:
    from threading import Event
    from miku.pet import runtime
    controller = PetController()
    started = Event()
    release = Event()
    completed = []
    actual_wait = controller.wait_for_worker

    def worker(activity):
        started.set()
        assert release.wait(5)
        completed.append("response persisted")
        assert activity.stop_requested
        completed.append("session closed")

    def interrupt_window(_controller, **_kwargs):
        assert started.wait(5)
        raise KeyboardInterrupt

    def wait_for_worker(timeout):
        assert controller.stop_requested
        release.set()
        return actual_wait(timeout)

    monkeypatch.setattr("miku.pet.view.run_pet_window", interrupt_window)
    monkeypatch.setattr(controller, "wait_for_worker", wait_for_worker)
    try:
        runtime.run_with_pet(worker, controller=controller)
    finally:
        release.set()

    assert completed == ["response persisted", "session closed"]
    assert controller.close_requested
    assert controller.worker_error is None


@pytest.mark.parametrize("notice_error", [KeyboardInterrupt, EOFError, OSError])
def test_interrupting_stop_notice_still_waits_and_does_not_repeat_notice(
    monkeypatch, notice_error,
) -> None:
    from miku.pet import runtime
    controller = PetController()
    targets = []
    notices = []

    class FakeThread:
        def __init__(self, *, target, **_kwargs): targets.append(target)
        def start(self): pass

    def interrupted_notice(text, **_kwargs):
        notices.append(text)
        raise notice_error

    def wait_for_worker(_timeout):
        assert controller.stop_requested
        targets[0]()
        return controller.close_requested

    monkeypatch.setattr(runtime, "Thread", FakeThread)
    monkeypatch.setattr("builtins.print", interrupted_notice)
    monkeypatch.setattr(controller, "wait_for_worker", wait_for_worker)
    monkeypatch.setattr(
        "miku.pet.view.run_pet_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    runtime.run_with_pet(lambda _controller: None, controller=controller)

    assert len(notices) == 1
    assert controller.close_requested


def test_worker_keyboard_interrupt_has_controlled_diagnostic(monkeypatch, capsys) -> None:
    import threading
    from miku.pet import runtime
    thread_errors = []
    controller = PetController()
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    monkeypatch.setattr("miku.pet.view.run_pet_window", lambda *_args, **_kwargs: None)

    def interrupted_worker(_controller):
        raise KeyboardInterrupt

    runtime.run_with_pet(interrupted_worker, controller=controller)

    assert thread_errors == []
    assert controller.close_requested is True
    assert "no se confirmó el cierre" in capsys.readouterr().err


@pytest.mark.parametrize("notice_error", [EOFError, OSError])
def test_worker_interruption_notice_failure_cannot_escape_thread(
    monkeypatch, notice_error,
) -> None:
    import threading
    from miku.pet import runtime
    controller = PetController()
    thread_errors = []
    notices = []

    def failed_notice(text, **_kwargs):
        notices.append(text)
        raise notice_error

    def interrupted_worker(_controller):
        raise KeyboardInterrupt

    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    monkeypatch.setattr(runtime, "print", failed_notice, raising=False)
    monkeypatch.setattr("miku.pet.view.run_pet_window", lambda *_args, **_kwargs: None)

    runtime.run_with_pet(interrupted_worker, controller=controller)

    assert len(notices) == 1
    assert thread_errors == []
    assert controller.close_requested


def test_pet_view_close_and_pending_poll_are_idempotent() -> None:
    from miku.pet.view import PetView
    controller = PetController()

    class FakeRoot:
        def __init__(self): self.destroy_calls = 0
        def destroy(self): self.destroy_calls += 1
        def after(self, *_args): raise AssertionError("Tk ya está cerrado")

    pet_view = object.__new__(PetView)
    pet_view.root = FakeRoot()
    pet_view.controller = controller
    pet_view._closed = False

    pet_view._close_visual()
    pet_view._close_visual()
    pet_view._poll()

    assert pet_view.root.destroy_calls == 1
    assert not controller.visual_active


def test_pet_view_destroys_root_if_view_construction_fails(monkeypatch) -> None:
    from miku.pet import view
    controller = PetController()

    class FakeRoot:
        def __init__(self): self.destroyed = False
        def winfo_exists(self): return not self.destroyed
        def destroy(self): self.destroyed = True

    root = FakeRoot()
    monkeypatch.setattr(view.tk, "Tk", lambda: root)
    monkeypatch.setattr(
        view, "PetView",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(view.tk.TclError("vista rota")),
    )

    with pytest.raises(view.tk.TclError, match="vista rota"):
        view.run_pet_window(controller)

    assert root.destroyed
    assert not controller.visual_active


def test_pet_view_destroys_tk_root_after_keyboard_interrupt(monkeypatch) -> None:
    from miku.pet import view
    controller = PetController()

    class FakeRoot:
        def __init__(self): self.destroyed = False
        def mainloop(self): raise KeyboardInterrupt
        def winfo_exists(self): return not self.destroyed
        def destroy(self): self.destroyed = True

    root = FakeRoot()
    monkeypatch.setattr(view.tk, "Tk", lambda: root)
    monkeypatch.setattr(view, "PetView", lambda *_args, **_kwargs: None)
    with pytest.raises(KeyboardInterrupt):
        view.run_pet_window(controller)
    assert root.destroyed
    assert controller.visual_active is False


def test_pet_cli_is_optional_and_preserves_existing_chat_options() -> None:
    plain = parse_chat_options(["--temporary"])
    pet = parse_chat_options(["--pet", "--temporary", "--no-topmost"])
    assert plain.pet is False
    assert plain.pet_topmost is True
    assert pet.pet is True
    assert pet.pet_topmost is False
