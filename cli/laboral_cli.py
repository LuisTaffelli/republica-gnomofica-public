from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from gnomos.laboral import (
    CapabilityRequest,
    InspectionScope,
    LaboralCapabilities,
)


def print_laboral_usage() -> None:
    print("Uso:")
    print("  laboral inspect <archivo>")
    print("  laboral list <directorio> [profundidad]")
    print("  laboral read <archivo> [línea-inicio] [línea-fin]")
    print("  laboral search <target> <texto>")
    print("  laboral python <archivo>")


def _handle_laboral_command(args: list[str]) -> None:
    if not args:
        print_laboral_usage()
        return

    command = args[0]

    if command == "inspect":
        if len(args) != 2:
            print("Uso: laboral inspect <archivo>")
            return
        target = str(Path(args[1]).expanduser().resolve())
        path = Path(target)
        if not path.is_file():
            print("⛏️ GNOMO-LABORAL: No encontré ese archivo.")
            return
        capabilities = LaboralCapabilities(InspectionScope(path))
        capability = "inspect_python" if path.suffix.lower() == ".py" else "read_file"
        result = capabilities.execute(CapabilityRequest(capability, {"path": target}))
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    elif command in {"list", "read", "search", "python"}:
        if len(args) < 2:
            print_laboral_usage()
            return
        maximum_arguments = {"list": 3, "read": 4, "search": 3, "python": 2}
        if len(args) > maximum_arguments[command]:
            raise ValueError(f"Demasiados argumentos para laboral {command}.")
        target = str(Path(args[1]).expanduser().resolve())
        capabilities = LaboralCapabilities(InspectionScope(Path(target)))
        if command == "list":
            arguments = {"path": target}
            if len(args) > 2:
                arguments["depth"] = int(args[2])
            capability = "list_tree"
        elif command == "read":
            arguments = {"path": target}
            if len(args) > 2:
                arguments["line_start"] = int(args[2])
            if len(args) > 3:
                arguments["line_end"] = int(args[3])
            capability = "read_file"
        elif command == "search":
            if len(args) < 3:
                print("Uso: laboral search <target> <texto>")
                return
            arguments = {"path": target, "query": args[2]}
            capability = "search_text"
        else:
            arguments = {"path": target}
            capability = "inspect_python"
        result = capabilities.execute(CapabilityRequest(capability, arguments))
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Comando laboral desconocido: {command}")
        print_laboral_usage()


def handle_laboral_command(args: list[str]) -> None:
    try:
        _handle_laboral_command(args)
    except (OSError, ValueError) as error:
        print(f"Error: {error}")
