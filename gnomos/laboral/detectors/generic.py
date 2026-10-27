from pathlib import Path


def inspect_generic(path):
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    return {
        "path": str(file_path),
        "name": file_path.name,
        "extension": file_path.suffix,
        "language": "generic",
        "line_count": len(lines),
        "char_count": len(content),
        "signals": {
            "has_sql": any(word in content.upper() for word in ["SELECT ", "INSERT ", "UPDATE ", "DELETE "]),
            "has_url": "http://" in content or "https://" in content,
            "has_json": "{" in content and "}" in content,
        },
        "preview": "\n".join(lines[:20]),
    }