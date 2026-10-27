from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core import database
from core.activity import ActivitySink, NULL_ACTIVITY
from core.actions import (
    FileEditScope,
    build_file_edit_coordinator,
    prepare_text_patch,
)
from core.audit import AuditService, SQLiteAuditRepository
from core.config import create_provider
from core.context.context_manager import ContextManager
from core.context.debug import render_context_debug
from core.context.prompt_composer import PromptComposer

from core.conversation import (
    ChatSession,
    MemoryPolicy,
    Message,
    SessionNotFoundError,
    SessionService,
    SQLiteConversationRepository,
)
from core.memory import (
    SQLiteMemoryRepository,
)
from gnomos.archive import ArchiveReader, SQLiteArchiveReader
from gnomos.laboral import InspectionScope
from miku.miku import Miku
from miku.turn import MikuTurnService, TurnRequest, build_miku_turn_service
from miku.voice import (
    SpeechInput,
    SpeechOutput,
    VoiceDependencyError,
    VoiceLoop,
    create_default_speech_output,
    create_default_voice_adapters,
)
from providers import TextProvider


EXIT_COMMANDS = {"salir", "exit", "quit"}


class _ExplicitActionProvider:
    def ask(self, prompt: str) -> str:
        raise RuntimeError(
            "El flujo explícito de edición no permite decisiones del modelo."
        )


@dataclass(slots=True)
class ChatOptions:
    temporary: bool = False
    no_memory: bool = False
    action: bool = False
    session_id: str | None = None
    inspection_target: str | None = None
    additional_allowed_targets: tuple[str, ...] = ()
    archive: bool = False
    pet: bool = False
    pet_topmost: bool = True
    context_debug: bool = False


@dataclass(slots=True)
class VoiceOptions:
    chat: ChatOptions
    speech_language: str = "es-AR"
    list_voices: bool = False
    tts_voice: str | None = None
    tts_rate: int | None = None
    no_tts: bool = False
    debug: bool = False


@dataclass(slots=True)
class BranchOptions:
    parent_session_id: str
    parent_message_id: str
    temporary: bool = False
    no_memory: bool = False


def print_miku_usage() -> None:
    print("Uso:")
    print("  miku chat")
    print("  miku chat --temporary")
    print("  miku chat --no-memory")
    print("  miku chat --session <id>")
    print("  miku chat --action")
    print("  miku chat --inspect <target> [--allow <target>]...")
    print("  miku chat --archive")
    print("  miku chat --context-debug")
    print("  miku chat --pet [opciones de chat]")
    print("  miku voice [opciones de chat] [--pet] [opciones de voz]")
    print("  miku voice --list-voices")
    print("  miku voice --tts-voice <id-o-indice> [--tts-rate <valor>]")
    print("  miku voice --no-tts [--speech-language <locale>] [--voice-debug]")
    print("  miku voice --context-debug [--pet]")
    print("  miku pet [opciones de chat] [--no-topmost]")
    print("  miku edit <archivo-absoluto>")
    print("  miku edit <archivo-absoluto> --pet")
    print()
    print("  miku sessions")
    print("  miku messages <session-id>")
    print()
    print(
        "  miku branch <session-id> <message-id>"
    )
    print(
        "  miku branch <session-id> <message-id> "
        "--temporary"
    )
    print(
        "  miku branch <session-id> <message-id> "
        "--no-memory"
    )


def handle_miku_command(args: list[str]) -> None:
    if not args:
        print_miku_usage()
        return

    command = args[0]
    try:
        repository = SQLiteConversationRepository()
    except (sqlite3.Error, OSError, database.ConversationSchemaError) as error:
        _print_chat_exit_notice(f"No pude abrir la base de conversaciones: {error}.")
        return
    except KeyboardInterrupt:
        _print_chat_exit_notice("Se interrumpió el arranque antes de abrir una sesión.")
        return
    service = SessionService(repository)

    try:
        if command in {"chat", "pet", "voice"}:
            voice_options = parse_voice_options(args[1:]) if command == "voice" else None
            options = (
                voice_options.chat
                if voice_options is not None
                else parse_chat_options(
                    (["--pet", *args[1:]]) if command == "pet" else args[1:]
                )
            )
            if voice_options is not None and voice_options.list_voices:
                show_tts_voices()
                return
            voice_adapters: tuple[SpeechInput, SpeechOutput] | None = None
            if voice_options is not None:
                try:
                    voice_adapters = create_default_voice_adapters(
                        language=voice_options.speech_language,
                        tts_voice=voice_options.tts_voice,
                        tts_rate=voice_options.tts_rate,
                        no_tts=voice_options.no_tts,
                    )
                except (Exception, KeyboardInterrupt) as error:
                    _print_chat_exit_notice(f"Voz no disponible: {str(error) or type(error).__name__}")
                    return
            try:
                inspection_scope = (
                    InspectionScope(
                        root=Path(options.inspection_target).expanduser().resolve(),
                        additional_allowed_targets=tuple(
                            Path(path).expanduser().resolve()
                            for path in options.additional_allowed_targets
                        ),
                    )
                    if options.inspection_target is not None else None
                )
                archive_reader = prepare_archive_reader() if options.archive else None
            except (Exception, KeyboardInterrupt) as error:
                _print_chat_exit_notice(
                    f"No pude preparar el arranque: {str(error) or type(error).__name__}. "
                    "No se abrió ninguna sesión."
                )
                return
            try:
                session = prepare_chat_session(
                    options=options,
                    service=service,
                )
            except SessionNotFoundError as error:
                _print_chat_exit_notice(f"{error}. Consultá 'miku sessions' para ver las sesiones guardadas.")
                return
            except (sqlite3.Error, OSError, KeyboardInterrupt) as error:
                _print_chat_exit_notice(
                    f"No pude confirmar la apertura de la sesión: {str(error) or type(error).__name__}. "
                    "No se inició el chat ni se reintentó la apertura o un cierre."
                )
                return

            if command == "voice" and options.pet:
                try:
                    from miku.pet.controller import PetController

                    controller = PetController()
                    voice_loop = build_voice_loop(
                        session=session,
                        repository=repository,
                        inspection_scope=inspection_scope,
                        archive_reader=archive_reader,
                        activity=controller,
                        memory_enabled=not options.no_memory,
                        adapters=voice_adapters,
                        debug=bool(voice_options and voice_options.debug),
                        speak_responses=not bool(voice_options and voice_options.no_tts),
                        context_debug=options.context_debug,
                        stop_requested=lambda: controller.stop_requested,
                    )
                except (Exception, KeyboardInterrupt) as error:
                    _handle_startup_error(session, repository, error)
                    return

                def start_prepared_voice(_activity: ActivitySink) -> None:
                    run_prepared_voice(
                        loop=voice_loop,
                        session=session,
                        memory_enabled=not options.no_memory,
                    )

                _run_conversation_with_pet(
                    start_prepared_voice,
                    session=session,
                    repository=repository,
                    topmost=options.pet_topmost,
                    controller=controller,
                    turn_service=voice_loop.turn_service,
                )
                return

            def start_chat(
                activity: ActivitySink = NULL_ACTIVITY,
                stop_requested: Callable[[], bool] | None = None,
            ) -> None:
                arguments = {
                    "session": session,
                    "repository": repository,
                    "inspection_scope": inspection_scope,
                    "archive_reader": archive_reader,
                    "activity": activity,
                    "memory_enabled": not options.no_memory,
                    "context_debug": options.context_debug,
                    "stop_requested": stop_requested,
                }
                if command == "voice":
                    run_voice(
                        **arguments,
                        adapters=voice_adapters,
                        debug=bool(voice_options and voice_options.debug),
                        speak_responses=not bool(
                            voice_options and voice_options.no_tts
                        ),
                    )
                else:
                    run_chat(**arguments)

            if options.pet:
                _run_conversation_with_pet(
                    lambda controller: start_chat(
                        controller, stop_requested=lambda: controller.stop_requested
                    ),
                    session=session,
                    repository=repository,
                    topmost=options.pet_topmost,
                )
            else:
                start_chat()
            return

        if command == "sessions":
            show_sessions(repository)
            return

        if command == "edit":
            pet_requested = len(args) == 3 and args[2] == "--pet"
            if len(args) != 2 and not pet_requested:
                raise ValueError("edit requiere un archivo absoluto y acepta --pet.")
            session = service.create(
                profile_name="miku", mode="action", memory_policy="disabled"
            )
            def start_edit(activity: ActivitySink = NULL_ACTIVITY) -> None:
                run_file_edit(
                    session=session,
                    repository=repository,
                    target=args[1],
                    activity=activity,
                )

            if pet_requested:
                from miku.pet.runtime import run_with_pet
                run_with_pet(start_edit)
            else:
                start_edit()
            return

        if command == "messages":
            if len(args) < 2:
                raise ValueError(
                    "messages requiere un session-id."
                )
            show_messages(repository, args[1])
            return

        if command == "branch":
            options = parse_branch_options(args[1:])
            session = prepare_branch_session(
                options=options,
                service=service,
            )
            run_chat(
                session=session,
                repository=repository,
            )
            return

        print(
            f"Comando de Miku desconocido: {command}"
        )
        print_miku_usage()

    except ValueError as error:
        print(f"Error: {error}")
        print()
        print_miku_usage()


def parse_chat_options(args: list[str]) -> ChatOptions:
    options = ChatOptions()
    index = 0

    while index < len(args):
        argument = args[index]

        if argument == "--temporary":
            options.temporary = True
        elif argument == "--no-memory":
            options.no_memory = True
        elif argument == "--action":
            options.action = True
        elif argument == "--archive":
            options.archive = True
        elif argument == "--pet":
            options.pet = True
        elif argument == "--no-topmost":
            options.pet_topmost = False
        elif argument == "--context-debug":
            options.context_debug = True
        elif argument == "--session":
            index += 1
            if index >= len(args):
                raise ValueError(
                    "--session requiere un ID."
                )
            session_id = args[index].strip()
            if not session_id:
                raise ValueError("--session requiere un ID no vacío.")
            if options.session_id is not None and options.session_id != session_id:
                raise ValueError("--session no puede seleccionar dos IDs distintos.")
            options.session_id = session_id
        elif argument in {"--inspect", "--allow"}:
            index += 1
            if index >= len(args):
                raise ValueError(f"{argument} requiere un path.")
            if argument == "--inspect":
                options.inspection_target = args[index]
            else:
                options.additional_allowed_targets += (args[index],)
        else:
            raise ValueError(
                f"Opción desconocida: {argument}"
            )

        index += 1

    selected_modes = sum(
        bool(value)
        for value in (
            options.temporary,
            options.action,
            options.session_id,
        )
    )

    if selected_modes > 1:
        raise ValueError(
            "--temporary, --action y --session "
            "no pueden combinarse."
        )
    if options.additional_allowed_targets and options.inspection_target is None:
        raise ValueError("--allow requiere --inspect.")
    if options.archive and options.inspection_target is not None:
        raise ValueError("--archive y --inspect no pueden combinarse.")
    if not options.pet and not options.pet_topmost:
        raise ValueError("--no-topmost requiere iniciar la Pet.")

    return options


def parse_voice_options(
    args: list[str], *, environment: Mapping[str, str] | None = None
) -> VoiceOptions:
    values = os.environ if environment is None else environment
    language = values.get("REPUBLICA_SPEECH_LANGUAGE", "es-AR").strip()
    if not language:
        raise ValueError("REPUBLICA_SPEECH_LANGUAGE no puede estar vacío.")
    remaining: list[str] = []
    options = VoiceOptions(chat=ChatOptions(), speech_language=language)
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--list-voices", "--no-tts", "--voice-debug"}:
            if argument == "--list-voices":
                options.list_voices = True
            elif argument == "--no-tts":
                options.no_tts = True
            else:
                options.debug = True
        elif argument in {"--tts-voice", "--tts-rate", "--speech-language"}:
            index += 1
            if index >= len(args):
                raise ValueError(f"{argument} requiere un valor.")
            value = args[index].strip()
            if not value:
                raise ValueError(f"{argument} no puede estar vacío.")
            if argument == "--tts-voice":
                options.tts_voice = value
            elif argument == "--speech-language":
                options.speech_language = value
            else:
                try:
                    options.tts_rate = int(value)
                except ValueError as error:
                    raise ValueError("--tts-rate requiere un entero.") from error
                if options.tts_rate <= 0:
                    raise ValueError("--tts-rate debe ser mayor que cero.")
        else:
            remaining.append(argument)
        index += 1

    options.chat = parse_chat_options(remaining)
    if options.no_tts and (options.tts_voice is not None or options.tts_rate is not None):
        raise ValueError("--no-tts no puede combinarse con opciones de TTS.")
    if options.list_voices and (
        remaining or options.no_tts or options.debug or options.tts_voice is not None
        or options.tts_rate is not None
    ):
        raise ValueError("--list-voices debe usarse sin otras opciones.")
    return options


def show_tts_voices() -> None:
    try:
        voices = create_default_speech_output().list_voices()
    except (VoiceDependencyError, RuntimeError) as error:
        print(f"No pude listar las voces TTS: {error}")
        return
    if not voices:
        print("pyttsx3 no informó voces instaladas.")
        return
    print("Voces TTS instaladas:")
    for voice in voices:
        languages = ", ".join(voice.languages) if voice.languages else "sin dato"
        print(f"  [{voice.index}] {voice.name}")
        print(f"      id: {voice.id}")
        print(f"      idiomas: {languages}")


def parse_branch_options(
    args: list[str],
) -> BranchOptions:
    valid_flags = {"--temporary", "--no-memory"}
    unknown_flags = {
        arg for arg in args
        if arg.startswith("--")
        and arg not in valid_flags
    }

    if unknown_flags:
        raise ValueError(
            "Opciones desconocidas: "
            + ", ".join(sorted(unknown_flags))
        )

    positional = [
        argument
        for argument in args
        if not argument.startswith("--")
    ]

    if len(positional) != 2:
        raise ValueError(
            "branch requiere exactamente "
            "session-id y message-id."
        )

    return BranchOptions(
        parent_session_id=positional[0],
        parent_message_id=positional[1],
        temporary="--temporary" in args,
        no_memory="--no-memory" in args,
    )


def prepare_chat_session(
    *,
    options: ChatOptions,
    service: SessionService,
) -> ChatSession:
    if options.session_id:
        return service.resume(options.session_id)

    if options.temporary:
        mode = "temporary"
    elif options.action:
        mode = "action"
    else:
        mode = "persistent"

    memory_policy: MemoryPolicy = (
        "disabled"
        if options.no_memory
        or options.temporary
        or options.action
        else "enabled"
    )

    return service.create(
        profile_name="miku",
        mode=mode,
        memory_policy=memory_policy,
    )


def prepare_branch_session(
    *,
    options: BranchOptions,
    service: SessionService,
) -> ChatSession:
    mode = (
        "temporary"
        if options.temporary
        else "persistent"
    )

    memory_policy: MemoryPolicy | None = (
        "disabled"
        if options.no_memory
        or options.temporary
        else None
    )

    return service.branch(
        parent_session_id=(
            options.parent_session_id
        ),
        parent_message_id=(
            options.parent_message_id
        ),
        mode=mode,
        memory_policy=memory_policy,
    )


def run_chat(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    inspection_scope: InspectionScope | None = None,
    archive_reader: ArchiveReader | None = None,
    activity: ActivitySink = NULL_ACTIVITY,
    memory_enabled: bool = True,
    context_debug: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    try:
        provider = create_provider()
        turn_service = build_miku_turn_service(
            session=session,
            repository=repository,
            provider=provider,
            inspection_scope=inspection_scope,
            archive_reader=archive_reader,
            activity=activity,
            memory_enabled=memory_enabled,
            memory_repository=SQLiteMemoryRepository(),
            context_manager_factory=ContextManager,
            context_observer=(
                (lambda projection: print(render_context_debug(projection)))
                if context_debug else None
            ),
        )
    except (Exception, KeyboardInterrupt) as error:
        _handle_startup_error(session, repository, error)
        return
    try:
        print_session_header(session, memory_enabled=memory_enabled)
    except (KeyboardInterrupt, EOFError, OSError):
        _close_turn_service(turn_service)
        return

    while stop_requested is None or not stop_requested():
        try:
            content = input("Visitante > ").strip()
        except (KeyboardInterrupt, EOFError, OSError):
            break

        if stop_requested is not None and stop_requested():
            break
        if content.lower() in EXIT_COMMANDS:
            break

        if not content:
            continue

        try:
            if content.startswith("/allow "):
                result = turn_service.handle_human(
                    TurnRequest(
                        text=content,
                        scope_extension=content.removeprefix("/allow ").strip(),
                    )
                )
            else:
                # La autoridad proviene de este input interactivo, no del TurnRequest.
                result = turn_service.handle_human(TurnRequest(text=content))
        except (sqlite3.Error, OSError, SessionNotFoundError) as error:
            _print_chat_exit_notice(
                f"No pude confirmar la persistencia del turno: {error}. "
                "Se detuvo esta ejecución sin reintentar ni intentar cerrar la sesión."
            )
            return
        except KeyboardInterrupt:
            _print_chat_exit_notice(
                "El turno se interrumpió y su resultado no está confirmado. "
                "Se detuvo esta ejecución sin reintentar ni intentar cerrar la sesión."
            )
            return

        try:
            print(f"\nMiku > {result.response_text}\n")
            if result.secondary_diagnostic is not None:
                print(
                    "Aviso de memoria: no pude completar la operación local "
                    f"({result.secondary_diagnostic}).\n"
                )
        except (KeyboardInterrupt, EOFError, OSError):
            # El turno ya retornó; interrumpir su presentación permite cerrar.
            break

    if not _close_turn_service(turn_service):
        return
    try:
        print_goodbye()
    except (KeyboardInterrupt, EOFError, OSError):
        # El cierre ya terminó; no repetirlo si se interrumpe la despedida.
        return


def _close_turn_service(turn_service: MikuTurnService) -> bool:
    try:
        turn_service.close()
    except (sqlite3.Error, OSError, SessionNotFoundError, RuntimeError) as error:
        _print_chat_exit_notice(
            f"No pude confirmar el cierre de la sesión: {error}. "
            "No se reintentó el cierre."
        )
        return False
    except KeyboardInterrupt:
        _print_chat_exit_notice(
            "Se interrumpió el cierre de la sesión y no pude confirmarlo. "
            "No se reintentó el cierre."
        )
        return False
    return True


def _print_chat_exit_notice(text: str) -> None:
    try:
        print(text)
    except (KeyboardInterrupt, EOFError, OSError):
        # La salida ya está decidida: no reintentar el aviso ni escribir un cierre.
        return


def _handle_startup_error(
    session: ChatSession, repository: SQLiteConversationRepository, error: BaseException,
    *, turn_service: MikuTurnService | None = None,
) -> None:
    """Only before a turn/worker is allowed to run; never a general finally."""
    label = "Voz no disponible" if isinstance(error, VoiceDependencyError) else "No pude completar el arranque de Miku"
    _print_chat_exit_notice(f"{label}: {str(error) or type(error).__name__}.")
    if session.closed_at is not None:
        # A failed resume must not rewrite a historical close marker.
        return
    if turn_service is not None:
        closed = _close_turn_service(turn_service)
    else:
        try:
            close_chat(session, repository)
            closed = True
        except (Exception, KeyboardInterrupt) as close_error:
            _print_chat_exit_notice(
                f"No pude confirmar el cierre tras fallar el arranque: {str(close_error) or type(close_error).__name__}. "
                "No se reintentó el cierre."
            )
            closed = False
    if closed:
        _print_chat_exit_notice(f"Sesión {session.id} cerrada sin iniciar turnos.")


def _run_conversation_with_pet(
    worker, *, session: ChatSession, repository: SQLiteConversationRepository,
    topmost: bool = True, controller=None, turn_service: MikuTurnService | None = None,
) -> None:
    try:
        from miku.pet.runtime import PetStartupError, run_with_pet
    except (Exception, KeyboardInterrupt) as error:
        _handle_startup_error(session, repository, error, turn_service=turn_service)
        return
    try:
        arguments = {"topmost": topmost}
        if controller is not None:
            arguments["controller"] = controller
        run_with_pet(worker, **arguments)
    except PetStartupError as error:
        # This exception guarantees the callback was never authorized to start.
        _handle_startup_error(session, repository, error, turn_service=turn_service)


def run_voice(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    inspection_scope: InspectionScope | None = None,
    archive_reader: ArchiveReader | None = None,
    activity: ActivitySink = NULL_ACTIVITY,
    memory_enabled: bool = True,
    adapters: tuple[SpeechInput, SpeechOutput] | None = None,
    debug: bool = False,
    speak_responses: bool = True,
    context_debug: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    try:
        if adapters is None:
            adapters = create_default_voice_adapters()
        loop = build_voice_loop(
            session=session,
            repository=repository,
            inspection_scope=inspection_scope,
            archive_reader=archive_reader,
            activity=activity,
            memory_enabled=memory_enabled,
            adapters=adapters,
            debug=debug,
            speak_responses=speak_responses,
            context_debug=context_debug,
            stop_requested=stop_requested,
        )
    except (Exception, KeyboardInterrupt) as error:
        _handle_startup_error(session, repository, error)
        return
    run_prepared_voice(
        loop=loop,
        session=session,
        memory_enabled=memory_enabled,
    )


def build_voice_loop(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    inspection_scope: InspectionScope | None = None,
    archive_reader: ArchiveReader | None = None,
    activity: ActivitySink = NULL_ACTIVITY,
    memory_enabled: bool = True,
    adapters: tuple[SpeechInput, SpeechOutput],
    debug: bool = False,
    speak_responses: bool = True,
    provider: TextProvider | None = None,
    memory_repository: SQLiteMemoryRepository | None = None,
    context_manager_factory=ContextManager,
    context_debug: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> VoiceLoop:
    speech_input, speech_output = adapters
    turn_service = build_miku_turn_service(
        session=session,
        repository=repository,
        provider=provider or create_provider(),
        inspection_scope=inspection_scope,
        archive_reader=archive_reader,
        activity=activity,
        memory_enabled=memory_enabled,
        memory_repository=memory_repository or SQLiteMemoryRepository(),
        context_manager_factory=context_manager_factory,
        context_observer=(
            (lambda projection: print(render_context_debug(projection)))
            if context_debug else None
        ),
    )
    return VoiceLoop(
        turn_service=turn_service,
        speech_input=speech_input,
        speech_output=speech_output,
        activity=activity,
        debug=debug,
        speak_responses=speak_responses,
        stop_requested=stop_requested,
    )


def run_prepared_voice(
    *, loop: VoiceLoop, session: ChatSession, memory_enabled: bool
) -> None:
    try:
        print_session_header(session, memory_enabled=memory_enabled)
    except (KeyboardInterrupt, EOFError, OSError):
        _close_turn_service(loop.turn_service)
        return
    if loop.run() is False:
        return
    try:
        print_goodbye()
    except (KeyboardInterrupt, EOFError, OSError):
        return


def run_file_edit(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    target: str,
    activity: ActivitySink = NULL_ACTIVITY,
    recovery_root: str | Path | None = None,
) -> None:
    """Interacción explícita; no interpreta lenguaje libre ni llama al modelo."""
    try:
        _run_file_edit_interaction(
            session=session,
            repository=repository,
            target=target,
            activity=activity,
            recovery_root=recovery_root,
        )
    finally:
        close_chat(session, repository)


def _run_file_edit_interaction(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    target: str,
    activity: ActivitySink = NULL_ACTIVITY,
    recovery_root: str | Path | None = None,
) -> None:
    scope = FileEditScope(Path(target))
    expected_text = input("Texto exacto esperado: ")
    replacement_text = input("Texto de reemplazo: ")
    request_message = session.add_user(
        f"Solicito editar {target} mediante apply_text_patch.",
        kind="file_edit_request",
    )
    persist_message_if_needed(
        session=session, repository=repository, message=request_message
    )
    plan = prepare_text_patch(
        scope=scope,
        target=target,
        expected_text=expected_text,
        replacement_text=replacement_text,
        requested_by="user",
        profile_name=session.profile_name,
        session_id=session.id,
        message_id=request_message.id,
    )
    coordinator_arguments = {
        "scope": scope,
        "audit": AuditService(SQLiteAuditRepository()),
        "activity": activity,
    }
    if recovery_root is not None:
        coordinator_arguments["recovery_root"] = recovery_root
    coordinator = build_file_edit_coordinator(
        **coordinator_arguments,
    )
    miku = Miku(
        provider=_ExplicitActionProvider(),
        context_builder=ContextManager(repository=repository),
        serializer=PromptComposer(),
        action_coordinator=coordinator,
        activity=activity,
    )
    proposal = miku.propose_action(plan.intent, presentation=plan.presentation)
    proposal_message = session.add_assistant(proposal, kind="action_proposal")
    persist_message_if_needed(
        session=session, repository=repository, message=proposal_message
    )
    print(f"\nMiku > {proposal}\n")

    full_review_seen = not plan.presentation_truncated
    while coordinator.pending_intent(session.id) is not None:
        confirmation = input("Visitante > ").strip()
        if confirmation.lower() in EXIT_COMMANDS:
            confirmation = "no"
        normalized = confirmation.casefold().strip(" .!¡?¿")
        reviewing = normalized in {"ver propuesta completa", "ver diff completo"}
        blocked_approval = (
            not full_review_seen
            and normalized in {"sí", "si", "yes", "aprobar", "apruebo", "confirmo"}
        )
        message_kind = "action_review" if reviewing else "action_authority"
        user_message = session.add_user(confirmation, kind=message_kind)
        persist_message_if_needed(
            session=session, repository=repository, message=user_message
        )
        if reviewing:
            full_review_seen = True
            response = (
                f"{plan.full_presentation}\n\n"
                "La propuesta completa quedó presentada. ¿La aprobás? (sí/no)"
            )
        elif blocked_approval:
            response = (
                "No puedo aceptar la aprobación todavía: el diff mostrado está "
                "truncado. Escribí 'ver propuesta completa' para revisar los textos "
                "exactos, "
                "o 'no' para rechazarlo."
            )
        else:
            response = miku.listen_to_human(confirmation, session)
        assistant_kind = "action_review" if reviewing or blocked_approval else "action_result"
        assistant_message = session.add_assistant(response, kind=assistant_kind)
        persist_message_if_needed(
            session=session, repository=repository, message=assistant_message
        )
        print(f"\nMiku > {response}\n")


def close_chat(
    session: ChatSession,
    repository: SQLiteConversationRepository,
) -> None:
    if session.is_persistent:
        repository.close_session(session.id)
    session.close()


def expand_inspection_scope(scope: InspectionScope, target: str) -> InspectionScope:
    if not target:
        raise ValueError("/allow requiere un path.")
    return InspectionScope(
        root=scope.root,
        additional_allowed_targets=(
            *scope.additional_allowed_targets,
            Path(target).expanduser().resolve(),
        ),
    )


def prepare_archive_reader() -> SQLiteArchiveReader:
    """Prepara acceso a un Archive existente sin crear ni migrar su schema."""
    return SQLiteArchiveReader(database.ARCHIVE_DB_PATH)


def persist_message_if_needed(
    *,
    session: ChatSession,
    repository: SQLiteConversationRepository,
    message: Message,
) -> None:
    if not session.is_persistent:
        return

    repository.save_message(
        session.id,
        message,
    )


def show_sessions(
    repository: SQLiteConversationRepository,
) -> None:
    sessions = repository.list_sessions(limit=30)

    if not sessions:
        print("No hay sesiones guardadas.")
        return

    print("Sesiones de Miku:\n")

    for session in sessions:
        branch_marker = (
            " [rama]"
            if session.is_branch
            else ""
        )

        memory_marker = {
            "enabled": "memoria activa",
            "review_only": "memoria revisable",
            "disabled": "sin memoria",
        }[session.memory_policy]

        print(f"- {session.id}{branch_marker}")
        print(f"  perfil: {session.profile_name}")
        print(f"  modo: {session.mode}")
        print(f"  política: {memory_marker}")
        print(f"  actualizado: {session.updated_at}")

        if session.title:
            print(f"  título: {session.title}")

        if session.parent_session_id:
            print(
                "  sesión padre: "
                f"{session.parent_session_id}"
            )

        print()


def show_messages(
    repository: SQLiteConversationRepository,
    session_id: str,
) -> None:
    session = repository.load_session(session_id)

    if not session.messages:
        print("La sesión no tiene mensajes.")
        return

    print(
        f"Mensajes de la sesión {session_id}:\n"
    )

    for index, message in enumerate(
        session.messages,
        start=1,
    ):
        preview = " ".join(message.content.split())

        if len(preview) > 80:
            preview = preview[:79] + "…"

        print(
            f"{index}. [{message.role}] {preview}"
        )
        print(f"   id: {message.id}")


def print_session_header(
    session: ChatSession, *, memory_enabled: bool = True
) -> None:
    if session.mode == "temporary":
        persistence_text = (
            "Chat temporal. No se guardará "
            "el historial. No puede reanudarse."
        )
    elif session.mode == "action":
        persistence_text = (
            "Interacción de acción. Se conservará el registro conversacional "
            "sin generar memoria. Este modo no concede autoridad: cada efecto "
            "requeriría auditoría previa y aprobación exacta."
        )
    elif not memory_enabled or session.memory_policy == "disabled":
        persistence_text = (
            "Chat persistente sin extracción "
            "de memoria."
        )
    else:
        persistence_text = (
            "Chat persistente con memoria personal. Los datos sensibles "
            "o ambiguos requerirán confirmación."
        )

    if session.is_branch:
        persistence_text += (
            "\nEsta conversación es una rama."
        )

    print(
        "Miku está escuchando. "
        "Escribí 'salir' para terminar."
    )
    print(f"Sesión: {session.id}")
    print(persistence_text)
    if session.is_persistent:
        print(f"Para retomar: miku --session {session.id}")
    print()


def print_goodbye() -> None:
    print(
        "\nMiku > Hasta luego, Visitante.\n\n"
        "La República seguirá aquí cuando "
        "quieras volver. "
        "¡Y no te olvides de tomar mate y agua!"
    )
