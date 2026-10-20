"""Keeps a BM25 index of the data/ directory fresh.

Before each query the directory is re-scanned with ``os.stat`` only (cheap); if
any file was added, removed or changed (path, size or mtime differ) the index is
rebuilt. Files written by the agent tools are therefore searchable immediately.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from app.search.bm25 import BM25Index, SearchHit
from app.tools.file_tools import DATA_DIR

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 5_000

Signature = tuple[tuple[str, int, int], ...]


class DataDirSearch:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._index = BM25Index()
        self._signature: Signature | None = None
        self.builds = 0

    def _scan(self) -> list[tuple[str, Path, os.stat_result]]:
        entries: list[tuple[str, Path, os.stat_result]] = []
        if not self.root.is_dir():
            return entries
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size > MAX_FILE_BYTES:
                    continue
                entries.append((path.relative_to(self.root).as_posix(), path, stat))
                if len(entries) >= MAX_FILES:
                    return entries
        return entries

    def _refresh(self) -> None:
        entries = self._scan()
        signature: Signature = tuple((rel, st.st_size, st.st_mtime_ns) for rel, _, st in entries)
        if signature == self._signature:
            return
        index = BM25Index()
        for rel, path, _ in entries:
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:  # skip binary files
                continue
            index.add(rel, raw.decode("utf-8", errors="replace"))
        self._index, self._signature = index, signature
        self.builds += 1

    def search(self, query: str, limit: int = 10, offset: int = 0) -> tuple[int, list[SearchHit]]:
        with self._lock:
            self._refresh()
            index = self._index
        return index.search(query, limit=limit, offset=offset)

    @property
    def document_count(self) -> int:
        with self._lock:
            self._refresh()
            return len(self._index)


_default: DataDirSearch | None = None
_default_lock = threading.Lock()


def get_search() -> DataDirSearch:
    global _default
    with _default_lock:
        if _default is None:
            _default = DataDirSearch(DATA_DIR)
        return _default
