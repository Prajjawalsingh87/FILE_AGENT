from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def _resolve_safe_path(name: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("File name must be a non-empty string.")

    candidate = Path(name.strip())

    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed.")

    if any(part == ".." for part in candidate.parts):
        raise ValueError("Unsafe paths are not allowed.")

    safe_path = (DATA_DIR / candidate).resolve()

    try:
        safe_path.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise ValueError("File path must stay inside the data directory.") from exc

    return safe_path


def create_file(name: str, content: str) -> str:
    path = _resolve_safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Created file: {path.name}"


def read_file(name: str) -> str:
    path = _resolve_safe_path(name)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {name}")

    return path.read_text(encoding="utf-8")


def append_file(name: str, content: str) -> str:
    path = _resolve_safe_path(name)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {name}")

    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(content)

    return f"Appended to file: {path.name}"
