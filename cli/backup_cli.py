from __future__ import annotations

from core.backup import BackupError, BackupService


def print_backup_usage() -> None:
    print("Uso:")
    print("  backup create <destino-nuevo>")
    print("  backup verify <backup>")
    print("  backup restore <backup> <destino-nuevo>")


def handle_backup_command(args: list[str]) -> None:
    service = BackupService()
    try:
        if len(args) == 2 and args[0] == "create":
            result = service.create(args[1])
            print(f"Backup {result.manifest.status}: {result.location}")
            for domain in result.manifest.domains:
                detail = (
                    f"{domain.size_bytes} bytes, sha256={domain.sha256}"
                    if domain.status == "complete" else domain.error
                )
                print(f"- {domain.domain}: {domain.status} ({detail})")
            return
        if len(args) == 2 and args[0] == "verify":
            result = service.verify(args[1])
            print(f"Backup {'válido' if result.valid else 'inválido'}: {result.location}")
            for error in result.errors:
                print(f"- {error}")
            return
        if len(args) == 3 and args[0] == "restore":
            result = service.restore(args[1], args[2])
            print(f"Restauración verificada: {result.location}")
            for domain in result.domains:
                print(f"- {domain.domain}: {domain.sha256}")
            return
        print_backup_usage()
    except (BackupError, OSError, ValueError) as error:
        print(f"Error de backup: {error}")
