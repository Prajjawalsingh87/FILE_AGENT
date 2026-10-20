"""Upload, list and delete user files in the data/ sandbox.

Uploads are deliberately strict: plain-text extensions only, at most
``MAX_UPLOAD_BYTES`` per file, valid UTF-8 without NUL bytes, a conservative
file-name alphabet (no directories, no ``..``, no hidden files) and a cap on the
total size and number of files in data/. The BM25 search index notices new or
removed files on its next query (it re-scans ``stat`` info), so no explicit
re-index call is needed.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.tools.file_tools import DATA_DIR

ALLOWED_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".toml",
     ".ini", ".cfg", ".xml", ".rst", ".py", ".js", ".ts", ".css", ".html", ".sql", ".sh"}
)
MAX_UPLOAD_BYTES = 1_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_STORED_FILES = 200
MAX_NAME_LENGTH = 100

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {
    f"lpt{i}" for i in range(1, 10)
}


class UploadError(ValueError):
    """Validation failure; ``status`` is the HTTP status the API should return."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StoredFile:
    name: str
    size: int
    modified: float

    def to_dict(self) -> dict:
        return {"name": self.name, "size": self.size, "modified": self.modified}


def validate_filename(raw: str | None) -> str:
    """Return a safe, bare file name or raise UploadError.

    Rejects directories/path separators, ``..``, hidden names, control and
    unusual characters, reserved Windows device names and disallowed extensions.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UploadError("File name is required.")
    name = raw.strip()
    if "/" in name or "\\" in name or "\x00" in name:
        raise UploadError("File name must not contain path separators.")
    if name in {".", ".."} or ".." in name:
        raise UploadError("File name must not contain '..'.")
    if len(name) > MAX_NAME_LENGTH:
        raise UploadError(f"File name is longer than {MAX_NAME_LENGTH} characters.")
    if not _NAME_RE.fullmatch(name):
        raise UploadError(
            "File name may only use letters, digits, spaces, '.', '_' and '-', "
            "and must start with a letter or digit."
        )
    stem, ext = os.path.splitext(name)
    if stem.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise UploadError("File name is reserved.")
    if ext.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadError(f"File type '{ext or '(none)'}' is not allowed. Allowed: {allowed}.", 415)
    return name


def validate_content(data: bytes) -> str:
    """Check size and that the bytes are UTF-8 text; return the decoded text."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"File is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.", 413)
    if b"\x00" in data:
        raise UploadError("Binary files are not allowed (file contains NUL bytes).", 415)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("File must be UTF-8 encoded text.", 415) from exc


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _files(self) -> list[tuple[Path, os.stat_result]]:
        out: list[tuple[Path, os.stat_result]] = []
        if not self.root.is_dir():
            return out
        for entry in sorted(self.root.iterdir()):
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
                continue
            try:
                out.append((entry, entry.stat()))
            except OSError:
                continue
        return out

    def list(self) -> list[StoredFile]:
        return [StoredFile(p.name, st.st_size, st.st_mtime) for p, st in self._files()]

    def usage(self) -> dict:
        files = self._files()
        return {
            "files": len(files),
            "bytes": sum(st.st_size for _, st in files),
            "max_files": MAX_STORED_FILES,
            "max_bytes": MAX_TOTAL_BYTES,
            "max_file_bytes": MAX_UPLOAD_BYTES,
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        }

    def _path(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if path.parent != self.root.resolve():
            raise UploadError("File must stay inside the data directory.")
        return path

    def save(self, raw_name: str | None, data: bytes, overwrite: bool = False) -> StoredFile:
        name = validate_filename(raw_name)
        validate_content(data)
        with self._lock:
            path = self._path(name)
            if path.is_symlink():
                raise UploadError("Refusing to overwrite a symlink.")
            exists = path.exists()
            if exists and not overwrite:
                raise UploadError(f"'{name}' already exists. Delete it first or overwrite.", 409)
            files = self._files()
            current = sum(st.st_size for p, st in files if p.name != name)
            count = len([p for p, _ in files if p.name != name])
            if count + 1 > MAX_STORED_FILES:
                raise UploadError(f"Storage is full ({MAX_STORED_FILES} files max).", 507)
            if current + len(data) > MAX_TOTAL_BYTES:
                raise UploadError(
                    f"Storage cap reached ({MAX_TOTAL_BYTES // 1_000_000} MB total).", 507
                )
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{name}.upload")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                raise UploadError(f"Could not save '{name}': {exc.strerror or exc}", 500) from exc
            st = path.stat()
            return StoredFile(name, st.st_size, st.st_mtime)

    def delete(self, raw_name: str) -> None:
        if not isinstance(raw_name, str) or "/" in raw_name or "\\" in raw_name:
            raise UploadError("File name must not contain path separators.")
        if raw_name.startswith(".") or ".." in raw_name or not raw_name.strip():
            raise UploadError("Invalid file name.")
        with self._lock:
            path = self._path(raw_name)
            if path.is_symlink() or not path.is_file():
                raise UploadError(f"File not found: {raw_name}", 404)
            path.unlink()


_default: FileStore | None = None
_default_lock = threading.Lock()


def get_store() -> FileStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = FileStore(DATA_DIR)
        return _default
