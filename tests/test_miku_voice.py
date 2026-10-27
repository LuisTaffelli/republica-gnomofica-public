from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from cli import miku_cli
from core.activity import ActivityState
from core.context.context_manager import ContextManager
from core.conversation import (
    SessionNotFoundError,
    SessionService,
    SQLiteConversationRepository,
)
from core.database import (
    SQLiteDatabase,
    initialize_conversation_database,
    initialize_memory_database,
)
from core.memory import SQLiteMemoryRepository
from core.memory import MemoryRecord
from miku.pet.controller import PetController
from miku.turn import TurnRequest, TurnResult, build_miku_turn_service
from miku.voice import (
    FakeSpeechInput,
    FakeSpeechOutput,
    NullSpeechOutput,
    Pyttsx3Output,
    SpeechBackendError,
    SpeechInput,
    SpeechInputError,
    SpeechInputResult,
    SpeechNoPhraseError,
    SpeechOutput,
    SpeechRecognitionInput,
    SpeechUnintelligibleError,
    VoiceInfo,
    VoiceDependencyError,
    VoiceLoop,
    create_default_voice_adapters,
)


class RecordingActivity:
    def __init__(self) -> None:
        self.states: list[ActivityState] = []

    def set_state(self, state):
        value = ActivityState(state)
        self.states.append(value)
        return SimpleNamespace(state=value)


class FakeTurnService:
    def __init__(self, results: list[TurnResult] | None = None) -> None:
        self.results = iter(results or [TurnResult("respuesta", "completed", "s1")])
        self.handled: list[TurnRequest] = []
        self.human_calls: list[TurnRequest] = []
        self.closed = False
        self.close_calls = 0

    def handle(self, request: TurnRequest) -> TurnResult:
        self.handled.append(request)
        return next(self.results)

    def handle_human(self, request: TurnRequest) -> TurnResult:
        self.human_calls.append(request)
        raise AssertionError("voice nunca debe usar la frontera humana")

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def run_loop(
    *,
    speech_input,
    speech_output=None,
    service=None,
    prompts=("", "salir"),
    activity=None,
):
    presented: list[str] = []
    answers = iter(prompts)
    loop = VoiceLoop(
        turn_service=service or FakeTurnService(),
        speech_input=speech_input,
        speech_output=speech_output or FakeSpeechOutput(),
        activity=activity or RecordingActivity(),
        prompt=lambda _message: next(answers),
        present=presented.append,
    )
    loop.run()
    return loop, presented


def test_voice_contracts_and_fakes_are_small_and_deterministic():
    speech_input = FakeSpeechInput(["hola"])
    speech_output = FakeSpeechOutput()
    assert isinstance(speech_input, SpeechInput)
    assert isinstance(speech_output, SpeechOutput)
    assert speech_input.transcribe() == SpeechInputResult("hola")
    speech_output.speak("respuesta")
    assert speech_output.spoken == ["respuesta"]


def test_transcription_is_visible_before_ordinary_turn_and_response_is_spoken():
    service = FakeTurnService()
    output = FakeSpeechOutput()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["hola Miku"]),
        speech_output=output,
        service=service,
    )
    assert service.handled == [TurnRequest("hola Miku", source="voice")]
    assert service.human_calls == []
    assert presented.index("Vos: hola Miku") < presented.index("Miku > respuesta")
    assert output.spoken == ["respuesta"]
    assert service.closed


def test_exact_transcript_reaches_turn_service_provider_result_and_tts(tmp_path):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    session = SessionService(repository).create(profile_name="miku", mode="temporary")

    class CapturingProvider:
        def __init__(self):
            self.prompts = []
        def ask(self, prompt):
            self.prompts.append(prompt)
            return f"respuesta-{len(self.prompts)}"

    provider = CapturingProvider()
    service = build_miku_turn_service(
        session=session,
        repository=repository,
        provider=provider,
        memory_enabled=False,
        memory_repository=SQLiteMemoryRepository(memory_db),
    )
    output = FakeSpeechOutput()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["a ver tremendo", "podrías anotar esto"]),
        speech_output=output,
        service=service,
        prompts=("", "", "salir"),
    )

    assert "CONVERSACIÓN ACTUAL:" in provider.prompts[0]
    assert "Visitante:\na ver tremendo" in provider.prompts[0]
    assert "Visitante:\npodrías anotar esto" in provider.prompts[1]
    assert "a ver tremendo" in provider.prompts[1]
    assert output.spoken == ["respuesta-1", "respuesta-2"]
    assert presented.index("Vos: podrías anotar esto") < presented.index(
        "Miku > respuesta-2"
    )


def test_empty_transcription_does_not_create_turn():
    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["   "]), service=service
    )
    assert service.handled == []
    assert any("no se creó ningún turno" in line for line in presented)


def test_stt_failure_is_visible_and_next_capture_still_works():
    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput([SpeechInputError("micrófono"), "hola"]),
        service=service,
        prompts=("", "", "salir"),
    )
    assert len(service.handled) == 1
    assert any("micrófono" in line for line in presented)


@pytest.mark.parametrize(
    "error",
    (
        SpeechNoPhraseError("sin frase"),
        SpeechUnintelligibleError("audio incomprensible"),
        SpeechBackendError("backend caído"),
    ),
)
def test_each_stt_failure_kind_skips_turn_and_returns_to_prompt(error):
    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput([error, "recuperado"]),
        service=service,
        prompts=("", "", "salir"),
    )
    assert service.handled == [TurnRequest("recuperado", source="voice")]
    assert any(str(error) in line for line in presented)


def test_typed_push_to_talk_control_does_not_start_capture():
    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["no debe consumirse"]),
        service=service,
        prompts=("texto accidental", "salir"),
    )
    assert service.handled == []
    assert any("sólo controla la captura" in line for line in presented)


def test_voice_debug_traces_only_boundary_values():
    service = FakeTurnService()
    presented = []
    answers = iter(("", "salir"))
    VoiceLoop(
        turn_service=service,
        speech_input=FakeSpeechInput(["texto actual"]),
        speech_output=FakeSpeechOutput(),
        prompt=lambda _message: next(answers),
        present=presented.append,
        debug=True,
    ).run()
    debug = [line for line in presented if line.startswith("[voice-debug]")]
    assert debug == [
        "[voice-debug] STT -> 'texto actual'",
        "[voice-debug] TurnRequest -> 'texto actual'",
        "[voice-debug] TurnResult -> 'respuesta'",
    ]
    assert all("INVARIANTES" not in line and "MEMORIA" not in line for line in debug)


def test_tts_failure_keeps_text_response_and_session_continues():
    class BrokenOutput:
        def speak(self, text):
            raise RuntimeError("speaker")

    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=BrokenOutput(),
        service=service,
    )
    assert "Miku > respuesta" in presented
    assert any("TTS falló: speaker" in line for line in presented)


def test_voice_reports_pending_authority_boundaries_without_calling_human_route():
    service = FakeTurnService([
        TurnResult(
            "sigue pendiente", "completed", "s1",
            pending_memory=True, pending_action=True,
        )
    ])
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["sí"]), service=service
    )
    assert service.handled == [TurnRequest("sí", source="voice")]
    assert service.human_calls == []
    assert any("no puede aprobar" in line for line in presented)
    assert any("confirmación de memoria" in line for line in presented)


def test_voice_activity_covers_listening_thinking_speaking_and_finishes_idle(tmp_path):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    session = SessionService(repository).create(profile_name="miku", mode="temporary")
    activity = RecordingActivity()

    class Provider:
        def ask(self, prompt):
            return "respuesta real"

    service = build_miku_turn_service(
        session=session,
        repository=repository,
        provider=Provider(),
        activity=activity,
        memory_enabled=False,
        memory_repository=SQLiteMemoryRepository(memory_db),
    )
    output = FakeSpeechOutput()
    run_loop(
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=output,
        service=service,
        activity=activity,
    )
    assert ActivityState.LISTENING in activity.states
    assert ActivityState.THINKING in activity.states
    assert ActivityState.SPEAKING in activity.states
    assert activity.states[-1] is ActivityState.IDLE
    assert output.spoken == ["respuesta real"]


def test_activity_failure_cannot_break_voice():
    class BrokenActivity:
        def set_state(self, state):
            raise RuntimeError("pet rota")

    output = FakeSpeechOutput()
    service = FakeTurnService()
    run_loop(
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=output,
        service=service,
        activity=BrokenActivity(),
    )
    assert output.spoken == ["respuesta"]


def test_optional_adapters_capture_once_and_keep_tts_separate():
    calls = []

    class Recognizer:
        def adjust_for_ambient_noise(self, source, *, duration):
            calls.append((source, duration))
        def listen(self, source, **kwargs):
            calls.append((source, kwargs))
            return "audio"
        def recognize_google(self, audio, *, language):
            calls.append((audio, language))
            return "texto"

    class Microphone:
        def __enter__(self): return "mic"
        def __exit__(self, *_args): return None

    class WaitTimeoutError(Exception): pass
    class UnknownValueError(Exception): pass
    class RequestError(Exception): pass

    speech_input = SpeechRecognitionInput(
        SimpleNamespace(
            Recognizer=Recognizer,
            Microphone=Microphone,
            WaitTimeoutError=WaitTimeoutError,
            UnknownValueError=UnknownValueError,
            RequestError=RequestError,
        )
    )
    assert speech_input.transcribe() == SpeechInputResult("texto")
    assert calls[0] == ("mic", 0.5)
    assert calls[1][1]["phrase_time_limit"] == 30

    spoken = []
    engine = SimpleNamespace(say=spoken.append, runAndWait=lambda: spoken.append("played"))
    engine.getProperty = lambda name: []
    engine.setProperty = lambda name, value: spoken.append((name, value))
    Pyttsx3Output(SimpleNamespace(init=lambda: engine)).speak("respuesta")
    assert spoken == ["respuesta", "played"]


@pytest.mark.parametrize(
    ("stage", "error_name", "expected"),
    (
        ("listen", "WaitTimeoutError", SpeechNoPhraseError),
        ("recognize", "UnknownValueError", SpeechUnintelligibleError),
        ("recognize", "RequestError", SpeechBackendError),
    ),
)
def test_stt_error_mapping_does_not_create_text(stage, error_name, expected):
    class WaitTimeoutError(Exception): pass
    class UnknownValueError(Exception): pass
    class RequestError(Exception): pass
    errors = {
        "WaitTimeoutError": WaitTimeoutError,
        "UnknownValueError": UnknownValueError,
        "RequestError": RequestError,
    }

    class Recognizer:
        def listen(self, source, **kwargs):
            if stage == "listen":
                raise errors[error_name]("fallo")
            return "audio"
        def recognize_google(self, audio, *, language):
            if stage == "recognize":
                raise errors[error_name]("fallo")
            return "texto"

    class Microphone:
        def __enter__(self): return "mic"
        def __exit__(self, *_args): return None

    adapter = SpeechRecognitionInput(
        SimpleNamespace(
            Recognizer=Recognizer,
            Microphone=Microphone,
            WaitTimeoutError=WaitTimeoutError,
            UnknownValueError=UnknownValueError,
            RequestError=RequestError,
        ),
        ambient_noise_duration=0,
    )
    with pytest.raises(expected):
        adapter.transcribe()


def test_stt_language_defaults_to_argentina_and_is_configurable():
    default = miku_cli.parse_voice_options([], environment={})
    configured = miku_cli.parse_voice_options(
        ["--speech-language", "es-UY"], environment={}
    )
    from_environment = miku_cli.parse_voice_options(
        [], environment={"REPUBLICA_SPEECH_LANGUAGE": "es-CL"}
    )
    assert default.speech_language == "es-AR"
    assert configured.speech_language == "es-UY"
    assert from_environment.speech_language == "es-CL"


def test_tts_voice_and_rate_are_applied_and_voices_are_described():
    properties = []
    voices = [
        SimpleNamespace(id="voice-en", name="English", languages=[b"\x05en-US"]),
        SimpleNamespace(id="voice-es", name="Español", languages=[b"\x05es-AR"]),
    ]
    engine = SimpleNamespace(
        getProperty=lambda name: voices if name == "voices" else None,
        setProperty=lambda name, value: properties.append((name, value)),
        say=lambda text: None,
        runAndWait=lambda: None,
    )
    output = Pyttsx3Output(
        SimpleNamespace(init=lambda: engine), voice="1", rate=165
    )
    assert output.list_voices() == (
        VoiceInfo(0, "voice-en", "English", ("en-US",)),
        VoiceInfo(1, "voice-es", "Español", ("es-AR",)),
    )
    output.speak("hola")
    assert properties == [("voice", "voice-es"), ("rate", 165)]


def test_no_tts_avoids_tts_dependency_and_keeps_text(monkeypatch):
    fake_input = FakeSpeechInput(["hola"])
    monkeypatch.setattr(
        "miku.voice.create_default_speech_input", lambda **_kwargs: fake_input
    )
    monkeypatch.setattr(
        "miku.voice.create_default_speech_output",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no debe cargar TTS")),
    )
    speech_input, speech_output = __import__(
        "miku.voice", fromlist=["create_default_voice_adapters"]
    ).create_default_voice_adapters(no_tts=True)
    assert speech_input is fake_input
    assert isinstance(speech_output, NullSpeechOutput)

    service = FakeTurnService()
    _loop, presented = run_loop(
        speech_input=speech_input,
        speech_output=speech_output,
        service=service,
    )
    assert "Miku > respuesta" in presented


def test_no_tts_skips_speaking_activity():
    activity = RecordingActivity()
    service = FakeTurnService()
    answers = iter(("", "salir"))
    VoiceLoop(
        turn_service=service,
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=NullSpeechOutput(),
        activity=activity,
        prompt=lambda _message: next(answers),
        present=lambda _line: None,
        speak_responses=False,
    ).run()
    assert ActivityState.SPEAKING not in activity.states
    assert activity.states[-1] is ActivityState.IDLE


def test_missing_audio_dependencies_are_lazy_and_have_clear_install_hint(monkeypatch):
    def missing(_name):
        raise ImportError

    monkeypatch.setattr("miku.voice.import_module", missing)
    with pytest.raises(VoiceDependencyError, match=r"\[voice\]"):
        create_default_voice_adapters()


def test_cli_voice_dependency_failure_is_local_to_voice(tmp_path, monkeypatch, capsys):
    database = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(database)
    repository = SQLiteConversationRepository(database)
    session = SessionService(repository).create(profile_name="miku", mode="temporary")
    monkeypatch.setattr(
        miku_cli,
        "create_default_voice_adapters",
        lambda **_kwargs: (_ for _ in ()).throw(VoiceDependencyError("instalá voice")),
    )
    miku_cli.run_voice(session=session, repository=repository)
    assert "Voz no disponible" in capsys.readouterr().out


def test_voice_command_preflights_dependencies_before_creating_session(
    monkeypatch, capsys
):
    created = []
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: object())
    monkeypatch.setattr(
        miku_cli,
        "SessionService",
        lambda _repository: SimpleNamespace(create=lambda **_kwargs: created.append(True)),
    )
    monkeypatch.setattr(
        miku_cli,
        "create_default_voice_adapters",
        lambda **_kwargs: (_ for _ in ()).throw(VoiceDependencyError("instalá voice")),
    )
    miku_cli.handle_miku_command(["voice"])
    assert created == []
    assert "Voz no disponible" in capsys.readouterr().out


def test_voice_uses_same_pet_option_without_changing_chat_semantics():
    options = miku_cli.parse_chat_options(["--pet", "--temporary"])
    assert options.pet and options.temporary


def test_list_voices_does_not_create_conversation_or_provider(monkeypatch, capsys):
    created = []
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: object())
    monkeypatch.setattr(
        miku_cli,
        "SessionService",
        lambda _repository: SimpleNamespace(create=lambda **_kwargs: created.append(True)),
    )
    monkeypatch.setattr(
        miku_cli,
        "create_provider",
        lambda: (_ for _ in ()).throw(AssertionError("no debe crear provider")),
    )
    monkeypatch.setattr(
        miku_cli,
        "create_default_speech_output",
        lambda: SimpleNamespace(
            list_voices=lambda: (VoiceInfo(0, "id-es", "Español", ("es-AR",)),)
        ),
    )
    miku_cli.handle_miku_command(["voice", "--list-voices"])
    output = capsys.readouterr().out
    assert created == []
    assert "Español" in output and "es-AR" in output


def test_voice_and_voice_pet_have_equivalent_session_memory_and_projection(tmp_path):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    memory_repository = SQLiteMemoryRepository(memory_db)
    memory_repository.save(MemoryRecord(
        profile_name="miku",
        category="personal_fact",
        content="subject=user; hazelnut allergy; Visitante es alérgico a la avellana",
        source_type="test",
    ))
    original = SessionService(repository).create(profile_name="miku")
    for message in (
        original.add_user("historial inicial"),
        original.add_assistant("respuesta inicial"),
    ):
        repository.save_message(original.id, message)
    plain_session = repository.load_session(original.id)
    pet_session = repository.load_session(original.id)
    plain_projections = []
    pet_projections = []

    def recording_factory(bucket):
        class RecordingContextManager(ContextManager):
            def build_for_profile(self, *args, **kwargs):
                projection = super().build_for_profile(*args, **kwargs)
                bucket.append(projection)
                return projection
        return RecordingContextManager

    class Provider:
        def __init__(self): self.prompts = []
        def ask(self, prompt):
            self.prompts.append(prompt)
            return "respuesta equivalente"

    transcript = "te acordás a que soy alérgico"
    plain_provider = Provider()
    pet_provider = Provider()
    plain_loop = miku_cli.build_voice_loop(
        session=plain_session,
        repository=repository,
        adapters=(FakeSpeechInput([transcript]), FakeSpeechOutput()),
        provider=plain_provider,
        memory_repository=memory_repository,
        context_manager_factory=recording_factory(plain_projections),
    )
    pet_loop = miku_cli.build_voice_loop(
        session=pet_session,
        repository=repository,
        adapters=(FakeSpeechInput([transcript]), FakeSpeechOutput()),
        activity=PetController(),
        provider=pet_provider,
        memory_repository=memory_repository,
        context_manager_factory=recording_factory(pet_projections),
    )
    for loop in (plain_loop, pet_loop):
        answers = iter(("", "salir"))
        loop.prompt = lambda _message, answers=answers: next(answers)
        loop.present = lambda _line: None
        loop.run()

    plain = plain_projections[0]
    pet = pet_projections[0]
    assert plain_session.id == pet_session.id == original.id
    assert plain_session.memory_policy == pet_session.memory_policy == "enabled"
    assert [item.content for item in plain.memory] == [
        item.content for item in pet.memory
    ]
    assert "hazelnut allergy" in plain.memory[0].content
    assert plain.conversation.current_message.content == transcript
    assert pet.conversation.current_message.content == transcript
    assert [(item.role, item.content) for item in plain.conversation.messages] == [
        (item.role, item.content) for item in pet.conversation.messages
    ]
    assert plain.task == pet.task
    assert "hazelnut allergy" in plain_provider.prompts[0]
    assert "hazelnut allergy" in pet_provider.prompts[0]


def test_context_debug_is_available_for_voice_and_voice_pet(tmp_path, capsys):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(conversation_db)
    repository = SQLiteConversationRepository(conversation_db)
    sessions = SessionService(repository)

    class Provider:
        def ask(self, _prompt):
            return "respuesta"

    loops = [
        miku_cli.build_voice_loop(
            session=sessions.create(profile_name="miku", mode="temporary"),
            repository=repository,
            adapters=(FakeSpeechInput(["hola"]), FakeSpeechOutput()),
            provider=Provider(),
            memory_enabled=False,
            context_debug=True,
            **({} if index == 0 else {"activity": PetController()}),
        )
        for index in range(2)
    ]
    for loop in loops:
        answers = iter(("", "salir"))
        loop.prompt = lambda _message, answers=answers: next(answers)
        loop.present = lambda _line: None
        loop.run()

    assert capsys.readouterr().out.count("[context-debug]") == 2
    assert miku_cli.parse_voice_options(
        ["--context-debug", "--pet"], environment={}
    ).chat.context_debug


def test_voice_pet_composes_runtime_once_on_main_thread(monkeypatch, tmp_path):
    import threading

    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    initialize_conversation_database(conversation_db)
    repository = SQLiteConversationRepository(conversation_db)
    events = []
    fake_loop = SimpleNamespace(turn_service=object(), run=lambda: events.append("loop-run"))
    monkeypatch.setattr(miku_cli, "SQLiteConversationRepository", lambda: repository)
    monkeypatch.setattr(
        miku_cli,
        "create_default_voice_adapters",
        lambda **_kwargs: (FakeSpeechInput([]), FakeSpeechOutput()),
    )

    def fake_build(**kwargs):
        events.append(("build", threading.current_thread().name, kwargs["session"]))
        return fake_loop

    def fake_pet(worker, **kwargs):
        events.append(("pet", kwargs["controller"]))
        worker(kwargs["controller"])

    monkeypatch.setattr(miku_cli, "build_voice_loop", fake_build)
    monkeypatch.setattr("miku.pet.runtime.run_with_pet", fake_pet)
    monkeypatch.setattr(
        miku_cli,
        "run_prepared_voice",
        lambda **_kwargs: events.append("prepared-run"),
    )
    miku_cli.handle_miku_command(["voice", "--pet", "--temporary"])

    builds = [event for event in events if isinstance(event, tuple) and event[0] == "build"]
    assert len(builds) == 1
    assert builds[0][1] == "MainThread"
    assert events.index(builds[0]) < next(
        index for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "pet"
    )
    assert events[-1] == "prepared-run"


def run_lifecycle(loop):
    try:
        return loop.run()
    except KeyboardInterrupt as error:
        raise AssertionError("La interrupción escapó del lifecycle de voz") from error


@pytest.mark.parametrize("stage", ("greeting", "prompt", "capture", "transcript", "response", "tts"))
@pytest.mark.parametrize("error_type", (KeyboardInterrupt, EOFError))
def test_voice_lifecycle_interruption_outside_turn_closes_once(stage, error_type):
    service = FakeTurnService()
    activity = RecordingActivity()
    output = []
    answers = iter(("", "salir"))

    def interrupt_at(current):
        if stage == current:
            raise error_type()

    def prompt(_text):
        interrupt_at("prompt")
        return next(answers)

    def transcribe():
        interrupt_at("capture")
        return SpeechInputResult("hola")

    def present(text):
        output.append(text)
        if text.startswith("Voz de Miku"):
            interrupt_at("greeting")
        elif text.startswith("Vos:"):
            interrupt_at("transcript")
        elif text.startswith("Miku >"):
            interrupt_at("response")

    loop = VoiceLoop(
        turn_service=service,
        speech_input=SimpleNamespace(transcribe=transcribe),
        speech_output=SimpleNamespace(speak=lambda _text: interrupt_at("tts")),
        activity=activity,
        prompt=prompt,
        present=present,
    )
    assert run_lifecycle(loop) is True
    assert service.close_calls == 1
    assert len(service.handled) == (1 if stage in {"response", "tts"} else 0)
    assert activity.states[-1] is ActivityState.IDLE
    assert run_lifecycle(loop) is True
    assert service.close_calls == 1


@pytest.mark.parametrize(
    "error",
    (
        sqlite3.OperationalError("storage"), OSError("storage"),
        SessionNotFoundError("session"), KeyboardInterrupt(), EOFError(),
    ),
)
def test_voice_lifecycle_uncertain_turn_stops_without_close_or_retry(error):
    service = FakeTurnService()
    output = FakeSpeechOutput()
    presented = []

    def handle(request):
        service.handled.append(request)
        raise error

    service.handle = handle
    loop = VoiceLoop(
        turn_service=service,
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=output,
        prompt=lambda _text: "",
        present=presented.append,
    )
    assert run_lifecycle(loop) is False
    assert service.close_calls == 0
    assert len(service.handled) == 1
    assert output.spoken == []
    assert any("sin reintentar ni intentar cerrar" in line for line in presented)
    assert run_lifecycle(loop) is False
    assert service.close_calls == 0
    assert len(service.handled) == 1


@pytest.mark.parametrize("notice_error", (KeyboardInterrupt(), EOFError(), OSError("output")))
def test_voice_lifecycle_interrupted_notice_never_retries_or_closes(notice_error):
    service = FakeTurnService()
    notices = []

    def handle(_request):
        raise sqlite3.OperationalError("original-storage")

    def present(text):
        if "original-storage" in text:
            notices.append(text)
            raise notice_error

    service.handle = handle
    loop = VoiceLoop(
        turn_service=service,
        speech_input=FakeSpeechInput(["hola"]),
        speech_output=FakeSpeechOutput(),
        prompt=lambda _text: "",
        present=present,
    )
    assert run_lifecycle(loop) is False
    assert len(notices) == 1
    assert service.close_calls == 0


@pytest.mark.parametrize(
    "error",
    (sqlite3.OperationalError("close"), OSError("close"),
     SessionNotFoundError("session"), KeyboardInterrupt(), EOFError()),
)
def test_voice_lifecycle_close_failure_is_reported_once(error):
    service = FakeTurnService()
    presented = []

    def close():
        service.close_calls += 1
        raise error

    service.close = close
    loop = VoiceLoop(
        turn_service=service,
        speech_input=FakeSpeechInput([]),
        speech_output=FakeSpeechOutput(),
        prompt=lambda _text: "salir",
        present=presented.append,
    )
    assert run_lifecycle(loop) is False
    assert service.close_calls == 1
    assert not service.closed
    assert any("No se reintentó el cierre" in line for line in presented)
    assert run_lifecycle(loop) is False
    assert service.close_calls == 1


@pytest.mark.parametrize("stage", ("before_prompt", "prompt", "capture", "transcript", "turn", "tts"))
def test_voice_lifecycle_cooperative_stop_respects_completed_turn(stage):
    stop = [stage == "before_prompt"]
    service = FakeTurnService()
    output = []
    captures = []
    prompts = []
    presented = []

    def stop_at(current):
        if stage == current:
            stop[0] = True

    def prompt(_text):
        prompts.append(True)
        stop_at("prompt")
        return ""

    def transcribe():
        captures.append(True)
        stop_at("capture")
        return SpeechInputResult("hola")

    def present(text):
        presented.append(text)
        if text.startswith("Vos:"):
            stop_at("transcript")

    def handle(request):
        service.handled.append(request)
        stop_at("turn")
        return TurnResult("respuesta", "completed", "s1")

    def speak(text):
        output.append(text)
        stop_at("tts")

    service.handle = handle
    loop = VoiceLoop(
        turn_service=service,
        speech_input=SimpleNamespace(transcribe=transcribe),
        speech_output=SimpleNamespace(speak=speak),
        prompt=prompt,
        present=present,
        stop_requested=lambda: stop[0],
    )
    assert run_lifecycle(loop) is True
    assert service.close_calls == 1
    assert len(prompts) == (0 if stage == "before_prompt" else 1)
    assert len(captures) == (0 if stage in {"before_prompt", "prompt"} else 1)
    assert len(service.handled) == (1 if stage in {"turn", "tts"} else 0)
    assert output == (["respuesta"] if stage == "tts" else [])
    if stage == "turn":
        assert "Miku > respuesta" in presented


@pytest.mark.parametrize("failed_role", ("user", "assistant", "tool"))
@pytest.mark.parametrize("error_type", (sqlite3.OperationalError, KeyboardInterrupt))
def test_voice_lifecycle_storage_failure_keeps_confirmed_history_only(
    tmp_path, monkeypatch, failed_role, error_type,
):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    session = SessionService(repository).create(profile_name="miku", memory_policy="disabled")
    save_message = repository.save_message
    writes = []
    closes = []

    def save(session_id, message):
        writes.append(message.role)
        if message.role == failed_role:
            raise error_type("synthetic-write")
        save_message(session_id, message)

    monkeypatch.setattr(repository, "save_message", save)
    monkeypatch.setattr(repository, "close_session", lambda _id: closes.append(True))

    class Provider:
        def ask(self, _prompt):
            if failed_role == "tool":
                raise RuntimeError("synthetic-provider")
            return "respuesta"

    service = build_miku_turn_service(
        session=session, repository=repository, provider=Provider(),
        memory_enabled=False, memory_repository=SQLiteMemoryRepository(memory_db),
    )
    output = FakeSpeechOutput()
    presented = []
    loop = VoiceLoop(
        turn_service=service, speech_input=FakeSpeechInput(["hola"]),
        speech_output=output, prompt=lambda _text: "", present=presented.append,
    )
    assert run_lifecycle(loop) is False
    stored = repository.load_session(session.id)
    assert session.messages == stored.messages
    assert [message.role for message in stored.messages] == ([] if failed_role == "user" else ["user"])
    assert writes == (["user"] if failed_role == "user" else ["user", failed_role])
    assert session.closed_at is None and stored.closed_at is None
    assert closes == []
    assert output.spoken == []


def test_voice_lifecycle_provider_failure_is_persisted_then_session_closes(tmp_path):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    session = SessionService(repository).create(profile_name="miku", memory_policy="disabled")

    class Provider:
        def ask(self, _prompt):
            raise RuntimeError("synthetic-provider")

    service = build_miku_turn_service(
        session=session, repository=repository, provider=Provider(),
        memory_enabled=False, memory_repository=SQLiteMemoryRepository(memory_db),
    )
    output = FakeSpeechOutput()
    _loop, presented = run_loop(
        speech_input=FakeSpeechInput(["hola"]), speech_output=output, service=service,
    )
    stored = repository.load_session(session.id)
    assert session.messages == stored.messages
    assert [message.role for message in stored.messages] == ["user", "tool"]
    assert session.closed_at is not None and stored.closed_at is not None
    assert output.spoken == [stored.messages[-1].content]
    assert any("synthetic-provider" in line for line in presented)


def test_voice_lifecycle_close_failure_does_not_mark_ram_closed(tmp_path, monkeypatch):
    conversation_db = SQLiteDatabase(tmp_path / "conversations.db")
    memory_db = SQLiteDatabase(tmp_path / "memory.db")
    initialize_conversation_database(conversation_db)
    initialize_memory_database(memory_db)
    repository = SQLiteConversationRepository(conversation_db)
    session = SessionService(repository).create(profile_name="miku", memory_policy="disabled")
    attempts = []

    def close(_session_id):
        attempts.append(True)
        raise sqlite3.OperationalError("synthetic-close")

    monkeypatch.setattr(repository, "close_session", close)
    service = build_miku_turn_service(
        session=session, repository=repository,
        provider=SimpleNamespace(ask=lambda _prompt: "respuesta"),
        memory_enabled=False, memory_repository=SQLiteMemoryRepository(memory_db),
    )
    loop = VoiceLoop(
        turn_service=service, speech_input=FakeSpeechInput([]),
        speech_output=FakeSpeechOutput(), prompt=lambda _text: "salir",
        present=lambda _text: None,
    )
    assert run_lifecycle(loop) is False
    assert attempts == [True]
    assert session.closed_at is None
    assert repository.load_session(session.id).closed_at is None
    assert run_lifecycle(loop) is False
    assert attempts == [True]
