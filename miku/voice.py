from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import sqlite3
from typing import Any, Callable, Protocol, runtime_checkable

from core.activity import (
    ActivitySink,
    ActivityState,
    NULL_ACTIVITY,
    best_effort_activity,
)
from core.conversation import SessionNotFoundError
from miku.turn import MikuTurnService, TurnRequest


class VoiceDependencyError(RuntimeError):
    """La instalación base no posee las integraciones opcionales de audio."""


class SpeechInputError(RuntimeError):
    """Una captura o transcripción falló sin invalidar la sesión completa."""


class SpeechNoPhraseError(SpeechInputError):
    """No comenzó ninguna frase dentro del tiempo de espera."""


class SpeechUnintelligibleError(SpeechInputError):
    """El backend recibió audio pero no pudo convertirlo en texto."""


class SpeechBackendError(SpeechInputError):
    """La captura o el servicio de reconocimiento no están disponibles."""


@dataclass(frozen=True, slots=True)
class SpeechInputResult:
    text: str


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    index: int
    id: str
    name: str
    languages: tuple[str, ...] = ()


@runtime_checkable
class SpeechInput(Protocol):
    """Captura y transcribe exactamente una frase activada por el usuario."""

    def transcribe(self) -> SpeechInputResult: ...


@runtime_checkable
class SpeechOutput(Protocol):
    """Sintetiza y reproduce texto sin poseer la respuesta conversacional."""

    def speak(self, text: str) -> None: ...


class FakeSpeechInput:
    def __init__(self, results: list[str | Exception] | tuple[str | Exception, ...]) -> None:
        self._results = iter(results)

    def transcribe(self) -> SpeechInputResult:
        value = next(self._results)
        if isinstance(value, Exception):
            raise value
        return SpeechInputResult(value)


class FakeSpeechOutput:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class NullSpeechOutput:
    def speak(self, text: str) -> None:
        del text


class SpeechRecognitionInput:
    """Micrófono local con reconocimiento remoto de Google vía SpeechRecognition."""

    def __init__(
        self,
        speech_recognition: Any,
        *,
        language: str = "es-AR",
        timeout: float = 10,
        phrase_time_limit: float = 30,
        ambient_noise_duration: float = 0.5,
    ) -> None:
        self._sr = speech_recognition
        self._recognizer = speech_recognition.Recognizer()
        self._calibrated = False
        self.language = language
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        self.ambient_noise_duration = ambient_noise_duration

    def transcribe(self) -> SpeechInputResult:
        try:
            with self._sr.Microphone() as source:
                if not self._calibrated and self.ambient_noise_duration > 0:
                    self._recognizer.adjust_for_ambient_noise(
                        source, duration=self.ambient_noise_duration
                    )
                    self._calibrated = True
                audio = self._recognizer.listen(
                    source,
                    timeout=self.timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
        except self._sr.WaitTimeoutError as error:
            raise SpeechNoPhraseError(
                "no se detectó una frase antes del timeout"
            ) from error
        except OSError as error:
            raise SpeechBackendError(f"el micrófono no está disponible: {error}") from error
        except Exception as error:
            raise SpeechBackendError(
                f"falló la captura de audio: {str(error) or type(error).__name__}"
            ) from error

        try:
            text = self._recognizer.recognize_google(audio, language=self.language)
        except self._sr.UnknownValueError as error:
            raise SpeechUnintelligibleError("el audio no se pudo entender") from error
        except self._sr.RequestError as error:
            raise SpeechBackendError(
                f"el backend de STT no está disponible: {error}"
            ) from error
        except Exception as error:
            raise SpeechBackendError(str(error) or type(error).__name__) from error
        return SpeechInputResult(text)


class Pyttsx3Output:
    """TTS local a través del motor de voz disponible en el sistema."""

    def __init__(
        self,
        pyttsx3: Any,
        *,
        voice: str | None = None,
        rate: int | None = None,
    ) -> None:
        self._pyttsx3 = pyttsx3
        self.voice = voice
        self.rate = rate

    def speak(self, text: str) -> None:
        engine = self._pyttsx3.init()
        self._configure_engine(engine)
        engine.say(text)
        engine.runAndWait()

    def list_voices(self) -> tuple[VoiceInfo, ...]:
        engine = self._pyttsx3.init()
        return tuple(
            VoiceInfo(
                index=index,
                id=str(item.id),
                name=str(getattr(item, "name", item.id)),
                languages=_normalize_languages(getattr(item, "languages", ())),
            )
            for index, item in enumerate(engine.getProperty("voices") or ())
        )

    def _configure_engine(self, engine: Any) -> None:
        if self.voice is not None:
            voices = tuple(engine.getProperty("voices") or ())
            selected = None
            if self.voice.isdecimal():
                index = int(self.voice)
                if 0 <= index < len(voices):
                    selected = voices[index]
            else:
                selected = next(
                    (item for item in voices if str(item.id) == self.voice), None
                )
            if selected is None:
                raise ValueError(
                    f"No existe la voz TTS '{self.voice}'. Usá --list-voices."
                )
            engine.setProperty("voice", selected.id)
        if self.rate is not None:
            engine.setProperty("rate", self.rate)


def _normalize_languages(values: Any) -> tuple[str, ...]:
    languages: list[str] = []
    for value in values or ():
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        normalized = str(value).lstrip("\x00\x01\x02\x03\x04\x05").strip()
        if normalized:
            languages.append(normalized)
    return tuple(languages)


def create_default_speech_input(*, language: str = "es-AR") -> SpeechInput:
    missing: list[str] = []
    modules: dict[str, Any] = {}
    for import_name, install_name in (
        ("speech_recognition", "SpeechRecognition[audio]"),
        ("pyaudio", "PyAudio"),
    ):
        try:
            modules[import_name] = import_module(import_name)
        except ImportError:
            missing.append(install_name)
    if missing:
        packages = ", ".join(missing)
        raise VoiceDependencyError(
            "faltan dependencias opcionales de voz "
            f"({packages}). Instalá el extra con: pip install -e \".[voice]\""
        )
    return SpeechRecognitionInput(modules["speech_recognition"], language=language)


def create_default_speech_output(
    *, voice: str | None = None, rate: int | None = None
) -> Pyttsx3Output:
    try:
        pyttsx3 = import_module("pyttsx3")
    except ImportError as error:
        raise VoiceDependencyError(
            "falta la dependencia opcional de TTS (pyttsx3). "
            "Instalá el extra con: pip install -e \".[voice]\""
        ) from error
    return Pyttsx3Output(pyttsx3, voice=voice, rate=rate)


def create_default_voice_adapters(
    *,
    language: str = "es-AR",
    tts_voice: str | None = None,
    tts_rate: int | None = None,
    no_tts: bool = False,
) -> tuple[SpeechInput, SpeechOutput]:
    return (
        create_default_speech_input(language=language),
        NullSpeechOutput()
        if no_tts
        else create_default_speech_output(voice=tts_voice, rate=tts_rate),
    )


class VoiceLoop:
    """Push-to-talk de consola; delega toda semántica a MikuTurnService."""

    def __init__(
        self,
        *,
        turn_service: MikuTurnService,
        speech_input: SpeechInput,
        speech_output: SpeechOutput,
        activity: ActivitySink = NULL_ACTIVITY,
        prompt: Callable[[str], str] = input,
        present: Callable[[str], None] = print,
        debug: bool = False,
        speak_responses: bool = True,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.turn_service = turn_service
        self.speech_input = speech_input
        self.speech_output = speech_output
        self.activity = best_effort_activity(activity)
        self.prompt = prompt
        self.present = present
        self.debug = debug
        self.speak_responses = speak_responses
        self.stop_requested = stop_requested or (lambda: False)
        self._started = False
        self._close_allowed = True
        self._closed = False

    def run(self) -> bool:
        """Ejecuta una vez; True confirma cierre, False conserva incertidumbre."""
        if self._started:
            return self._closed
        self._started = True
        try:
            self.present(
                "Voz de Miku lista. Presioná Enter para hablar; "
                "escribí 'salir' para terminar."
            )
            while not self.stop_requested():
                command = self.prompt("[Enter para hablar] > ").strip().casefold()
                if self.stop_requested() or command in {"salir", "exit", "quit"}:
                    break
                if command:
                    self.present(
                        "Ese prompt sólo controla la captura: presioná Enter "
                        "sin escribir texto, o escribí 'salir'."
                    )
                    continue
                if not self._capture_turn():
                    break
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self.activity.set_state(ActivityState.IDLE)
            if self._close_allowed:
                self._closed = self._close_session()
        return self._closed

    def _close_session(self) -> bool:
        try:
            self.turn_service.close()
        except (sqlite3.Error, OSError, SessionNotFoundError) as error:
            self._exit_notice(
                f"No pude confirmar el cierre de la sesión: {error}. "
                "No se reintentó el cierre."
            )
            return False
        except (KeyboardInterrupt, EOFError):
            self._exit_notice(
                "Se interrumpió el cierre de la sesión y no pude confirmarlo. "
                "No se reintentó el cierre."
            )
            return False
        return True

    def _exit_notice(self, text: str) -> None:
        try:
            self.present(text)
        except (KeyboardInterrupt, EOFError, OSError):
            # El aviso no debe reiniciar el loop ni provocar otra escritura.
            pass

    def _capture_turn(self) -> bool:
        if self.stop_requested():
            return False
        self.activity.set_state(ActivityState.LISTENING)
        try:
            transcription = self.speech_input.transcribe().text.strip()
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as error:
            self.activity.set_state(ActivityState.ERROR)
            self.present(f"No pude transcribir el audio: {error}")
            self.activity.set_state(ActivityState.IDLE)
            return True

        if self.stop_requested():
            return False

        if not transcription:
            self.present("No se detectó una transcripción; no se creó ningún turno.")
            self.activity.set_state(ActivityState.IDLE)
            return True

        # La transcripción visible precede deliberadamente al procesamiento.
        self.present(f"Vos: {transcription}")
        if self.debug:
            self.present(f"[voice-debug] STT -> {transcription!r}")
        request = TurnRequest(text=transcription, source="voice")
        if self.debug:
            self.present(f"[voice-debug] TurnRequest -> {request.text!r}")
        if self.stop_requested():
            return False
        # Sólo el worker posee el cierre. Si handle no retorna, no conocemos
        # qué escrituras se confirmaron y no agregamos una escritura de cierre.
        self._close_allowed = False
        try:
            result = self.turn_service.handle(request)
        except (sqlite3.Error, OSError, SessionNotFoundError) as error:
            self._exit_notice(
                f"No pude confirmar la persistencia del turno: {error}. "
                "Se detuvo esta ejecución sin reintentar ni intentar cerrar la sesión."
            )
            return False
        except (KeyboardInterrupt, EOFError):
            self._exit_notice(
                "El turno se interrumpió y su resultado no está confirmado. "
                "Se detuvo esta ejecución sin reintentar ni intentar cerrar la sesión."
            )
            return False
        self._close_allowed = True
        if self.debug:
            self.present(f"[voice-debug] TurnResult -> {result.response_text!r}")
        self.present(f"Miku > {result.response_text}")
        if result.secondary_diagnostic is not None:
            self.present(f"Aviso de memoria: {result.secondary_diagnostic}")
        if result.pending_action:
            self.present(
                "La voz no puede aprobar la acción pendiente. "
                "Revisala y respondé por teclado en el chat confiable con "
                f"`python main.py miku chat --session {result.session_id}`."
            )
        if result.pending_memory:
            self.present(
                "La voz no puede resolver la confirmación de memoria pendiente. "
                "Respondé por teclado en el chat confiable con "
                f"`python main.py miku chat --session {result.session_id}`."
            )

        if self.stop_requested():
            return False
        if not self.speak_responses:
            self.activity.set_state(ActivityState.IDLE)
            return True
        self.activity.set_state(ActivityState.SPEAKING)
        try:
            self.speech_output.speak(result.response_text)
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as error:
            self.activity.set_state(ActivityState.ERROR)
            self.present(f"La respuesta quedó visible, pero TTS falló: {error}")
        finally:
            self.activity.set_state(ActivityState.IDLE)
        return True
