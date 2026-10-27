import ast
from pathlib import Path
from .generic import inspect_generic


def inspect_python(path):
    report = inspect_generic(path)
    report["language"] = "python"

    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8", errors="replace")

    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        report["parse_error"] = str(error)
        return report

    imports = []
    functions = []
    classes = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)

        elif isinstance(node, ast.FunctionDef):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": [arg.arg for arg in node.args.args],
                "docstring": ast.get_docstring(node),
            })

        elif isinstance(node, ast.ClassDef):
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "docstring": ast.get_docstring(node),
            })

    report["imports"] = sorted(set(imports))
    report["functions"] = functions
    report["classes"] = classes

    return report