from __future__ import annotations

import sys
from collections.abc import Callable
from importlib import import_module

from cli.delegated_read_cli import handle_delegated_read_command


CommandHandler = Callable[[list[str]], int | None]


def _lazy_call(module: str, name: str):
    """Import only the explicitly selected domain, including for installed scripts."""
    def call(*args):
        return getattr(import_module(module), name)(*args)
    return call


handle_archive_command = _lazy_call("cli.archive_cli", "handle_archive_command")
handle_audit_command = _lazy_call("cli.audit_cli", "handle_audit_command")
handle_backup_command = _lazy_call("cli.backup_cli", "handle_backup_command")
handle_laboral_command = _lazy_call("cli.laboral_cli", "handle_laboral_command")
handle_memory_command = _lazy_call("cli.memory_cli", "handle_memory_command")
handle_miku_command = _lazy_call("cli.miku_cli", "handle_miku_command")
handle_delegated_turn_command = _lazy_call("cli.delegated_turn_cli", "handle_delegated_turn_command")
print_miku_usage = _lazy_call("cli.miku_cli", "print_miku_usage")
initialize_archive_database = _lazy_call("core.database", "initialize_archive_database")


COMMANDS: dict[str, CommandHandler] = {
    "task-read": handle_delegated_read_command,
    "task-turn": handle_delegated_turn_command,
    "archive": handle_archive_command,
    "audit": handle_audit_command,
    "backup": handle_backup_command,
    "laboral": handle_laboral_command,
    "memory": handle_memory_command,
    "miku": handle_miku_command,
}


def print_usage() -> None:
    print("Uso:")
    print("  python main.py archive <comando>")
    print("  python main.py audit <comando>")
    print("  python main.py backup <comando>")
    print("  python main.py laboral <comando>")
    print("  python main.py task-read --target ARCHIVO [--timeout-seconds 5]")
    print("  python main.py task-turn --model MODELO_LOCAL [--timeout-seconds 60]")
    print("  python main.py memory <comando>")
    print("  python main.py miku <comando>")


def miku_main() -> None:
    """Entrada directa a Miku, con chat como comando predeterminado."""
    args = sys.argv[1:]
    if args in (["-h"], ["--help"]):
        print_miku_usage()
        return
    if not args or args[0].startswith("-"):
        args = ["chat", *args]
    handle_miku_command(args)


def main() -> int | None:
    if len(sys.argv) < 2:
        print_usage()
        return

    command = sys.argv[1]
    args = sys.argv[2:]

    handler = COMMANDS.get(command)

    if handler is None:
        print(f"Comando desconocido: {command}")
        print_usage()
        return

    # Cada dominio se inicializa de forma lazy. Miku arma conversación/memoria
    # al crear sus repositorios y Archive sólo cuando su comando lo necesita.
    if command == "archive":
        initialize_archive_database()

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
