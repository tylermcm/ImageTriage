from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from queue import SimpleQueue
from threading import Lock, Thread
from typing import Callable

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage, QImageReader

from . import memtrace


THUMBNAIL_CACHE_VERSION = 5

# WI-3.5: the disk cache grew unbounded (3.2 GB / 72,860 files with zero
# eviction, ever). Cap it; eviction is LRU by write time (thumbnails are
# content-keyed by path/size/mtime, so a file's mtime is when it was last
# actually (re)created, a reasonable recency proxy without a separate index).
DEFAULT_THUMBNAIL_CACHE_MAX_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB
_EVICTION_CHECK_INTERVAL = 200  # throttle: check every Nth save, not every save


def edit_state_for(path: str | Path) -> tuple[int, int]:
    """Cheap, cache-key-friendly fingerprint of ``path``'s editor sidecar.

    Returns ``(mtime_ns, size)`` of the resolved session file if one exists,
    else ``(0, 0)`` as a fixed sentinel for "no sidecar" -- this keeps an
    unedited photo's key shape/value unaffected by whether this field exists
    at all. Deliberately does not parse or hash the session contents (too
    expensive per thumbnail); a changed mtime/size is enough to know the
    cached thumbnail may be stale.
    """

    try:
        from . import edit_storage
        from .photocraft_bridge import saved_render_path

        # The newest render of the photo's PhotoCraft edits: the small display render when there is one
        # (a Camera Raw recipe produces only that), else the full-size one.
        rendered = saved_render_path(str(path))
        session_path = rendered if rendered is not None else edit_storage.resolve_session_for_read(path)
        stat = session_path.stat()
    except OSError:
        return (0, 0)
    except Exception:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


class EditStateMemo:
    """``edit_state_for`` for callers that ask about the same photos again and again.

    ``edit_state_for`` looks at the disk (five file lookups: 1.4 ms on a card, tens of ms while photos are being read from it). The grid
    asked about every visible photo, twice, on every repaint, and the filmstrip about a dozen on every step: more than half of the UI
    thread's time while flipping through photos. So each answer is remembered for ``TTL_S``; after that the old answer is still given
    and a thread of its own looks again, and ``on_changed(path)`` is called (on that thread) if the answer turned out different.

    ``exact`` finds a photo's first answer on the calling thread (what the grid's keys need: a thumbnail made now is stored under it);
    ``peek`` never touches the disk on the calling thread and says "no edits" until the first answer has been found (right for nearly
    every photo; what the filmstrip uses). ``stale`` says that an answer may be out of date, which makes the next ask look again.
    """

    TTL_S = 10.0
    MAX_ENTRIES = 4096

    def __init__(
        self,
        probe: Callable[[str], tuple[int, int]] | None = None,
        on_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._probe = probe
        self._on_changed = on_changed
        self._known: dict[str, tuple[float, tuple[int, int]]] = {}
        self._pending: set[str] = set()
        self._queue: SimpleQueue[str] = SimpleQueue()
        self._thread: Thread | None = None
        self._lock = Lock()
        memtrace.gauge("edit_state_memo.entries", self, lambda memo: len(memo._known))

    def peek(self, path: str | Path) -> tuple[int, int]:
        key = str(path)
        with self._lock:
            known = self._known.get(key)
        if known is None or time.monotonic() - known[0] >= self.TTL_S:
            self._look_again(key)
        return known[1] if known is not None else (0, 0)

    def exact(self, path: str | Path) -> tuple[int, int]:
        key = str(path)
        with self._lock:
            known = self._known.get(key)
        if known is None:
            state = self._find(key)
            self._remember(key, state)
            return state
        if time.monotonic() - known[0] >= self.TTL_S:
            self._look_again(key)
        return known[1]

    def stale(self, path: str | Path | None = None) -> None:
        """The edits of ``path`` (of every photo, without it) may have changed: the next ask looks again, and gives the old answer
        meanwhile."""
        with self._lock:
            for key in list(self._known) if path is None else [str(path)]:
                known = self._known.get(key)
                if known is not None:
                    self._known[key] = (float("-inf"), known[1])

    def _find(self, key: str) -> tuple[int, int]:
        try:
            return (self._probe or edit_state_for)(key)
        except Exception:  # edit_state_for answers "no edits" for anything it cannot read; this is for the unforeseen
            return (0, 0)

    def _remember(self, key: str, state: tuple[int, int]) -> tuple[int, int] | None:
        with self._lock:
            previous = self._known.get(key)
            if previous is None and len(self._known) >= self.MAX_ENTRIES:
                self._known.pop(next(iter(self._known)))
            self._known[key] = (time.monotonic(), state)
        return previous[1] if previous is not None else None

    def _look_again(self, key: str) -> None:
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            if self._thread is None:
                # Never keeps the program from closing: a photo on a drive that has gone away can hold a lookup for many seconds.
                self._thread = Thread(target=self._run, name="edit-state-memo", daemon=True)
                self._thread.start()
        self._queue.put(key)

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            state = self._find(key)
            previous = self._remember(key, state)
            with self._lock:
                self._pending.discard(key)
            if state != (previous if previous is not None else (0, 0)) and self._on_changed is not None:
                try:
                    self._on_changed(key)
                except RuntimeError:  # its receiver has been deleted: the program is closing
                    return


@dataclass(slots=True, frozen=True)
class ThumbnailKey:
    path: str
    modified_ns: int
    file_size: int
    width: int
    height: int
    version: int = THUMBNAIL_CACHE_VERSION
    edit_state: tuple[int, int] = (0, 0)

    def digest(self) -> str:
        payload = (
            f"{self.version}|{self.path}|{self.modified_ns}|{self.file_size}|"
            f"{self.width}|{self.height}|{self.edit_state[0]}|{self.edit_state[1]}"
        )
        return sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


class MemoryThumbnailCache:
    def __init__(self, max_bytes: int = 256 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes
        self._current_bytes = 0
        self._entries: OrderedDict[ThumbnailKey, tuple[QImage, int]] = OrderedDict()
        self._lock = Lock()
        # QImage.sizeInBytes() per entry, which is the pixel buffer itself: exact (see memtrace).
        self._trace = memtrace.cache_with_gauge(
            "thumbnail.memory_cache", "exact", self, lambda cache: (cache._current_bytes, len(cache._entries))
        )

    def get(self, key: ThumbnailKey) -> QImage | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                if self._trace is not None:
                    self._trace.miss()
                return None
            self._entries.move_to_end(key)
            if self._trace is not None:
                self._trace.hit()
            return entry[0]

    def put(self, key: ThumbnailKey, image: QImage) -> None:
        if image.isNull():
            return

        cost = max(1, image.sizeInBytes())
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._current_bytes -= existing[1]

            if cost > self._max_bytes:
                self._entries.clear()
                self._current_bytes = 0
                return

            self._entries[key] = (image, cost)
            self._current_bytes += cost
            self._entries.move_to_end(key)
            if self._trace is not None:
                self._trace.put()
                self._trace.observe(self._current_bytes, len(self._entries))  # the peak is before the trim
            self._trim()

    def _trim(self) -> None:
        while self._current_bytes > self._max_bytes and self._entries:
            _, (_, cost) = self._entries.popitem(last=False)
            self._current_bytes -= cost
            if self._trace is not None:
                self._trace.evicted()


class DiskThumbnailCache:
    def __init__(
        self,
        root: str | Path | None = None,
        max_bytes: int = DEFAULT_THUMBNAIL_CACHE_MAX_BYTES,
    ) -> None:
        cache_root = root
        if cache_root is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            cache_root = Path(base) / "thumbs"
        self.root = Path(cache_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._save_count = 0
        self._eviction_lock = Lock()
        # Also sweep once at startup: a cache built up before this cap existed
        # (or from a previous, larger cap) should trend down over time too,
        # not only once 200 more thumbnails get saved.
        self._trigger_background_eviction()

    def _file_path(self, key: ThumbnailKey) -> Path:
        digest = key.digest()
        shard = digest[:2]
        return self.root / shard / f"{digest}.jpg"

    def load(self, key: ThumbnailKey) -> QImage | None:
        target = self._file_path(key)
        if not target.exists():
            return None

        reader = QImageReader(str(target))
        image = reader.read()
        if image.isNull():
            return None
        return image

    def save(self, key: ThumbnailKey, image: QImage) -> None:
        if image.isNull():
            return

        target = self._file_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(target), "JPEG", quality=88)

        self._save_count += 1
        if self._save_count % _EVICTION_CHECK_INTERVAL == 0:
            self._trigger_background_eviction()

    def _trigger_background_eviction(self) -> None:
        if not self._eviction_lock.acquire(blocking=False):
            return  # an eviction sweep is already running

        def _run() -> None:
            try:
                self.enforce_size_cap()
            finally:
                self._eviction_lock.release()

        Thread(target=_run, name="thumbnail-cache-eviction", daemon=True).start()

    def enforce_size_cap(self, max_bytes: int | None = None) -> int:
        """Delete the least-recently-written thumbnails until the cache is
        back under its size cap. Returns the number of bytes freed. Safe to
        call directly (e.g. from a "trim cache now" action); callers on a
        background thread should prefer `_trigger_background_eviction`."""
        cap = self.max_bytes if max_bytes is None else max_bytes
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for path in self.root.glob("*/*.jpg"):
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            entries.append((stat.st_mtime, stat.st_size, path))

        if total <= cap:
            return 0

        entries.sort(key=lambda entry: entry[0])
        freed = 0
        for _, size, path in entries:
            if total - freed <= cap:
                break
            try:
                path.unlink()
            except OSError:
                continue
            freed += size
        return freed
