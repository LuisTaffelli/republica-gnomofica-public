from pathlib import Path
from .detectors.generic import inspect_generic
from .detectors.python import inspect_python


def inspect_file(path):
    file_path = Path(path)

    if not file_path.exists():
        return None

    if file_path.suffix == ".py":
        return inspect_python(file_path)

    return inspect_generic(file_path)


def print_inspection(report):
    print("\n⛏️ GNOMO-LABORAL — INSPECCIÓN TÉCNICA\n")

    print(f"Archivo: {report['path']}")
    print(f"Lenguaje: {report['language']}")
    print(f"Líneas: {report['line_count']}")
    print(f"Caracteres: {report['char_count']}")

    if report.get("imports"):
        print("\nImports:")
        for item in report["imports"]:
            print(f"- {item}")

    if report.get("classes"):
        print("\nClases:")
        for cls in report["classes"]:
            print(f"- {cls['name']} línea {cls['line']}")

    if report.get("functions"):
        print("\nFunciones detectadas:")
        for fn in report["functions"]:
            args = ", ".join(fn["args"])
            print(f"- {fn['name']}({args}) línea {fn['line']}")

    if report.get("signals"):
        print("\nSeñales:")
        for key, value in report["signals"].items():
            print(f"- {key}: {value}")

    if report.get("parse_error"):
        print("\nError de parseo:")
        print(report["parse_error"])