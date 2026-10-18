"""Disk-backed document sessions with versioned undo/redo.

Layout on disk (one folder per uploaded document):

    <data_dir>/sessions/<sid>/
        meta.json        name, current version, undo/redo stacks
        v000001.pdf      full PDF for every version still reachable by undo/redo
        .lock            cross-process lock file

Why disk and not memory:
  * survives restarts / redeploys,
  * works with several worker processes (each worker keeps a small LRU of
    parsed documents and reloads when the on-disk version moved on),
  * memory stays bounded no matter how many users upload files.

Concurrency: MuPDF is not thread-safe, so all PDF work in a process runs under
one global lock. A per-session file lock serialises writers across processes.
Lock order is always: session file lock -> global PDF lock.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

import pymupdf as fitz

from .config import settings

T = TypeVar("T")
PDF_LOCK = threading.RLock()
_SID_RE = re.compile(r"^[0-9a-f]{32}$")


class SessionNotFound(Exception):
    pass


class PasswordRequired(Exception):
    pass


class InvalidDocument(Exception):
    pass


# ---------------------------------------------------------------------------
# cross-process file lock (no third-party dependency)
# ---------------------------------------------------------------------------
if os.name == "nt":
    import msvcrt

    def _lock_fd(fd: int) -> None:
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.05)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


@contextmanager
def _session_lock(path: Path) -> Iterator[None]:
    key = str(path)
    with _thread_locks_guard:
        tlock = _thread_locks.setdefault(key, threading.RLock())
    with tlock:  # threads of this process
        fd = os.open(str(path / ".lock"), os.O_RDWR | os.O_CREAT)
        try:
            _lock_fd(fd)  # other processes
            try:
                yield
            finally:
                _unlock_fd(fd)
        finally:
            os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_document(data: bytes, filename: str = "", password: str | None = None) -> fitz.Document:
    """Open anything MuPDF understands; non-PDF inputs (images, XPS, EPUB…) become PDF."""
    ext = Path(filename).suffix.lower().lstrip(".") or None
    try:
        doc = fitz.open(stream=data, filetype=ext if ext and ext != "pdf" else "pdf")
    except Exception:
        try:
            doc = fitz.open(stream=data)
        except Exception as e:  # noqa: BLE001
            raise InvalidDocument(f"Could not open file: {e}") from e
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise PasswordRequired()
    if not doc.is_pdf:
        pdf_bytes = doc.convert_to_pdf()
        doc.close()
        doc = fitz.open("pdf", pdf_bytes)
    elif doc.is_encrypted or doc.metadata.get("encryption"):
        # store decrypted so every edit + undo snapshot works without the password
        clean = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
        doc.close()
        doc = fitz.open("pdf", clean)
    if doc.page_count == 0:
        raise InvalidDocument("Document has no pages")
    return doc


class SessionStore:
    def __init__(self, root: Path):
        self.root = root / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: OrderedDict[str, tuple[int, fitz.Document]] = OrderedDict()
        self._stop = threading.Event()

    # ------------------------------------------------------------------ paths
    def _dir(self, sid: str) -> Path:
        if not _SID_RE.match(sid or ""):
            raise SessionNotFound(sid)
        d = self.root / sid
        if not (d / "meta.json").is_file():
            raise SessionNotFound(sid)
        return d

    @staticmethod
    def _vfile(d: Path, version: int) -> Path:
        return d / f"v{version:06d}.pdf"

    @staticmethod
    def _read_meta(d: Path) -> dict:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))

    @staticmethod
    def _write_meta(d: Path, meta: dict) -> None:
        meta["touched"] = time.time()
        _atomic_write(d / "meta.json", json.dumps(meta).encode("utf-8"))

    # ------------------------------------------------------------------ cache
    def _cache_get(self, sid: str, version: int) -> fitz.Document | None:
        hit = self._cache.get(sid)
        if hit and hit[0] == version:
            self._cache.move_to_end(sid)
            return hit[1]
        return None

    def _cache_put(self, sid: str, version: int, doc: fitz.Document) -> None:
        old = self._cache.pop(sid, None)
        if old and old[1] is not doc:
            old[1].close()
        self._cache[sid] = (version, doc)
        while len(self._cache) > settings.doc_cache_size:
            _, (_, d) = self._cache.popitem(last=False)
            d.close()

    def _cache_drop(self, sid: str) -> None:
        old = self._cache.pop(sid, None)
        if old:
            try:
                old[1].close()
            except Exception:
                pass

    def _open_version(self, sid: str, d: Path, version: int) -> fitz.Document:
        doc = self._cache_get(sid, version)
        if doc is None:
            doc = fitz.open("pdf", self._vfile(d, version).read_bytes())
            self._cache_put(sid, version, doc)
        return doc

    # ------------------------------------------------------------------ API
    def create(self, data: bytes, filename: str, password: str | None = None) -> str:
        with PDF_LOCK:
            doc = load_document(data, filename, password)
            pdf_bytes = doc.tobytes(garbage=1)
        sid = uuid.uuid4().hex
        d = self.root / sid
        d.mkdir(parents=True)
        _atomic_write(self._vfile(d, 1), pdf_bytes)
        name = Path(filename or "document.pdf").stem + ".pdf"
        self._write_meta(d, {"id": sid, "name": name, "created": time.time(),
                             "version": 1, "next": 2, "undo": [], "redo": []})
        with PDF_LOCK:
            self._cache_put(sid, 1, doc)
        return sid

    def meta(self, sid: str) -> dict:
        return self._read_meta(self._dir(sid))

    def read(self, sid: str, fn: Callable[[fitz.Document, dict], T]) -> T:
        """Run a read-only function against the current version."""
        d = self._dir(sid)
        with _session_lock(d):
            meta = self._read_meta(d)
            with PDF_LOCK:
                doc = self._open_version(sid, d, meta["version"])
                return fn(doc, meta)

    def mutate(self, sid: str, fn: Callable[[fitz.Document], T]) -> tuple[T, dict]:
        """Apply an edit, persist it as a new version, push the old one to undo."""
        d = self._dir(sid)
        with _session_lock(d):
            meta = self._read_meta(d)
            with PDF_LOCK:
                doc = self._open_version(sid, d, meta["version"])
                try:
                    result = fn(doc)
                    data = doc.tobytes(garbage=1)
                except Exception:
                    self._cache_drop(sid)  # doc may be half-modified
                    raise
            new_v = meta["next"]
            _atomic_write(self._vfile(d, new_v), data)
            meta["undo"].append(meta["version"])
            for v in meta["redo"]:
                self._vfile(d, v).unlink(missing_ok=True)
            meta["redo"] = []
            while len(meta["undo"]) > settings.max_undo:
                self._vfile(d, meta["undo"].pop(0)).unlink(missing_ok=True)
            meta["version"], meta["next"] = new_v, new_v + 1
            self._write_meta(d, meta)
            with PDF_LOCK:
                self._cache_drop(sid)
                self._cache_put(sid, new_v, fitz.open("pdf", data))
            return result, meta

    def undo(self, sid: str) -> dict:
        return self._step(sid, "undo", "redo")

    def redo(self, sid: str) -> dict:
        return self._step(sid, "redo", "undo")

    def _step(self, sid: str, src: str, dst: str) -> dict:
        d = self._dir(sid)
        with _session_lock(d):
            meta = self._read_meta(d)
            if meta[src]:
                meta[dst].append(meta["version"])
                meta["version"] = meta[src].pop()
                self._write_meta(d, meta)
            return meta

    def rename(self, sid: str, name: str) -> dict:
        d = self._dir(sid)
        with _session_lock(d):
            meta = self._read_meta(d)
            name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "document"
            meta["name"] = name if name.lower().endswith(".pdf") else name + ".pdf"
            self._write_meta(d, meta)
            return meta

    def delete(self, sid: str) -> None:
        d = self._dir(sid)
        with PDF_LOCK:
            self._cache_drop(sid)
        shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------- cleanup
    def cleanup(self) -> int:
        cutoff = time.time() - settings.session_ttl_minutes * 60
        removed = 0
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            try:
                touched = self._read_meta(d).get("touched", 0)
            except Exception:
                touched = d.stat().st_mtime
            if touched < cutoff:
                with PDF_LOCK:
                    self._cache_drop(d.name)
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    def start_cleanup_thread(self) -> None:
        def loop():
            while not self._stop.wait(settings.cleanup_interval_seconds):
                try:
                    self.cleanup()
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True, name="session-cleanup").start()

    def stop(self) -> None:
        self._stop.set()


store = SessionStore(settings.data_dir)
