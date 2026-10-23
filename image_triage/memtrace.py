"""Opt-in memory observability: what the process holds, which caches hold it, and what is being decoded right now.

Set ``IMAGE_TRIAGE_MEMTRACE=1`` before starting Image Triage. Everything here is an observer: it reads counters and writes
events into the performance log (``image_triage.perf``), on the same clock as every other ``preview.*`` / ``thumbnail.*``
event, so memory can be lined up with navigation latency, decode time and UI stalls. It changes no behaviour.

With the variable unset, ``ENABLED`` is False and:

* no thread, timer or file is created, and nothing samples memory;
* ``cache()``, ``jobs()`` and ``counter()`` return ``None``; call sites keep that in an attribute and pay one ``is None`` test
  per cache operation (and, for decode tasks, one extra function call per task);
* nothing is imported beyond the standard library.

What is measured, and how exact it is (every number says which):

``exact``      a byte count the owner already keeps, or a ``QImage.sizeInBytes()`` / ``ndarray.nbytes`` it can read;
``estimated``  derived (width * height * 4, ``sys.getsizeof`` of a sample, requested decode sizes). Never add an estimate to a
               process figure: the process figures come only from the operating system.

Process figures (``mem.sample``) are the OS's: private bytes (commit charge on Windows, Private_Clean + Private_Dirty on Linux),
working set / resident set, and the OS peak working set, for this process and for every descendant (PhotoCraft, AI workers),
split by role. Cache figures say what the *application* believes it holds; the gap between the two is the part nobody
accounts for (allocator retention, Qt internals, GPU staging, model weights...).

Optional extras: ``IMAGE_TRIAGE_MEMTRACE_INTERVAL_S`` (sample period, default 1.0), ``IMAGE_TRIAGE_MEMTRACE_TRACEMALLOC=1``
(Python-level allocations too; slows Python noticeably, so it is separate), ``IMAGE_TRIAGE_MEMTRACE_GPU=1`` (``nvidia-smi``
every 5 s; on Windows WDDM it reports total GPU memory, usually not per process).
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

ENV_VAR = "IMAGE_TRIAGE_MEMTRACE"
INTERVAL_ENV_VAR = "IMAGE_TRIAGE_MEMTRACE_INTERVAL_S"
TRACEMALLOC_ENV_VAR = "IMAGE_TRIAGE_MEMTRACE_TRACEMALLOC"
GPU_ENV_VAR = "IMAGE_TRIAGE_MEMTRACE_GPU"
GPU_PERIOD_S = 5.0
UI_HEARTBEAT_MS = 50


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


ENABLED: bool = _truthy(os.environ.get(ENV_VAR))


# --------------------------------------------------------------------------------------------------------------------------
# Operating-system process figures
# --------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessMemory:
    private_bytes: int
    working_set: int
    peak_working_set: int


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str


class _ProcessMemoryCountersEx(ctypes.Structure):
    # Fixed-width fields so the layout (80 bytes on 64-bit Windows) can be checked on any platform.
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_uint16 * 260),
    ]


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _windows_api():
    """The kernel32 entry points with explicit signatures (64-bit handles must not be truncated to int)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCountersEx), ctypes.c_uint32]
    kernel32.K32GetProcessMemoryInfo.restype = ctypes.c_int
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    return kernel32


_kernel32 = None


def _windows_kernel32():
    global _kernel32
    if _kernel32 is None:
        _kernel32 = _windows_api()
    return _kernel32


def _windows_process_memory(pid: int) -> ProcessMemory | None:
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return None
    try:
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return ProcessMemory(int(counters.PrivateUsage), int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize))
    finally:
        kernel32.CloseHandle(handle)


def _windows_processes() -> list[ProcessInfo]:
    kernel32 = _windows_kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return []
    found: list[ProcessInfo] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            raw = bytes(bytearray(ctypes.string_at(ctypes.addressof(entry.szExeFile), 520)))
            name = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
            found.append(ProcessInfo(int(entry.th32ProcessID), int(entry.th32ParentProcessID), name))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _kib_field(text: str, field: str) -> int:
    for line in text.splitlines():
        if line.startswith(field + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return 0


def _linux_process_memory(pid: int) -> ProcessMemory | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as handle:
            status = handle.read()
    except OSError:
        return None
    resident = _kib_field(status, "VmRSS")
    peak = _kib_field(status, "VmHWM")
    private = 0
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii", errors="replace") as handle:
            rollup = handle.read()
        private = _kib_field(rollup, "Private_Clean") + _kib_field(rollup, "Private_Dirty")
    except OSError:
        private = _kib_field(status, "RssAnon")  # no smaps_rollup (old kernel, or not our process): anonymous resident
    return ProcessMemory(private, resident, peak)


def _linux_processes() -> list[ProcessInfo]:
    found: list[ProcessInfo] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        # "pid (comm) state ppid ..." where comm may contain spaces and parentheses.
        close = text.rfind(")")
        open_ = text.find("(")
        if close < 0 or open_ < 0:
            continue
        rest = text[close + 2 :].split()
        if len(rest) < 2 or not rest[1].lstrip("-").isdigit():
            continue
        found.append(ProcessInfo(int(name), int(rest[1]), text[open_ + 1 : close]))
    return found


def process_memory(pid: int | None = None) -> ProcessMemory | None:
    """The OS's memory figures for ``pid`` (default: this process); None when they cannot be read."""
    pid = os.getpid() if pid is None else pid
    try:
        if sys.platform == "win32":
            return _windows_process_memory(pid)
        if sys.platform.startswith("linux"):
            return _linux_process_memory(pid)
    except Exception:  # an observer never raises into the program
        return None
    return None


def list_processes() -> list[ProcessInfo]:
    try:
        if sys.platform == "win32":
            return _windows_processes()
        if sys.platform.startswith("linux"):
            return _linux_processes()
    except Exception:
        return []
    return []


def descendants(root_pid: int, processes: list[ProcessInfo]) -> list[ProcessInfo]:
    """Every process below ``root_pid`` (children, their children...), not including it."""
    children: dict[int, list[ProcessInfo]] = {}
    for info in processes:
        children.setdefault(info.parent_pid, []).append(info)
    result: list[ProcessInfo] = []
    seen = {root_pid}
    stack = [root_pid]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child.pid in seen:
                continue
            seen.add(child.pid)
            result.append(child)
            stack.append(child.pid)
    return result


_AI_NAME_PARTS = ("python", "ai_python_runner", "ai_runtime", "aiculler")


def classify_process(name: str) -> str:
    """``photocraft`` (the editor), ``ai`` (Python workers and the AI runtime installer) or ``other``."""
    lowered = (name or "").lower()
    if "photocraft" in lowered:
        return "photocraft"
    if any(part in lowered for part in _AI_NAME_PARTS):
        return "ai"
    return "other"


def tree_memory(root_pid: int | None = None) -> dict[str, Any]:
    """This process and its descendants, by role: ``{"own": {...}, "roles": {role: {n, private, ws, peak_ws}}, "total_*": ...}``."""
    root = os.getpid() if root_pid is None else root_pid
    own = process_memory(root)
    roles: dict[str, dict[str, int]] = {}
    for info in descendants(root, list_processes()):
        memory = process_memory(info.pid)
        if memory is None:
            continue
        bucket = roles.setdefault(classify_process(info.name), {"n": 0, "private": 0, "ws": 0, "peak_ws": 0})
        bucket["n"] += 1
        bucket["private"] += memory.private_bytes
        bucket["ws"] += memory.working_set
        bucket["peak_ws"] += memory.peak_working_set
    own_private = own.private_bytes if own else 0
    own_ws = own.working_set if own else 0
    return {
        "own": {
            "private": own_private,
            "ws": own_ws,
            "peak_ws": own.peak_working_set if own else 0,
        },
        "roles": roles,
        "total_private": own_private + sum(r["private"] for r in roles.values()),
        "total_ws": own_ws + sum(r["ws"] for r in roles.values()),
    }


# --------------------------------------------------------------------------------------------------------------------------
# Registered caches, decode jobs and counters
# --------------------------------------------------------------------------------------------------------------------------

_registry_lock = threading.Lock()
_caches: dict[str, "CacheStats"] = {}
_jobs: dict[str, "JobStats"] = {}
_counters: dict[str, "EventCounter"] = {}
_gauges: dict[str, "_Gauge"] = {}


class CacheStats:
    """Counters for one cache. ``kind`` says whether its byte figure is ``exact`` or ``estimated``.

    The owner calls ``hit()`` / ``miss()`` / ``evicted()`` / ``observe(bytes, entries)`` as it works; ``observe`` is what keeps
    ``peak_bytes`` honest between samples (call it when the cache is at its fullest, before trimming). The sampler also reads the
    live size through ``gauge``. A gauge may return a third item, a dict of extra figures; ``unique_bytes`` (distinct pixel buffers,
    for a cache that stores one image under several keys) also gets a peak.
    """

    __slots__ = ("name", "kind", "hits", "misses", "evictions", "invalidations", "puts", "bytes", "entries", "peak_bytes",
                 "peak_entries", "_gauge", "_lock", "shared_with", "extra", "peak_unique_bytes")

    def __init__(self, name: str, kind: str, shared_with: str = "") -> None:
        self.name = name
        self.kind = kind
        self.shared_with = shared_with
        self.hits = self.misses = self.evictions = self.invalidations = self.puts = 0
        self.bytes = self.entries = self.peak_bytes = self.peak_entries = 0
        self._gauge: Callable[[], tuple] | None = None
        self._lock = threading.Lock()
        self.extra: dict[str, Any] = {}
        self.peak_unique_bytes = 0

    def hit(self) -> None:
        with self._lock:
            self.hits += 1

    def miss(self) -> None:
        with self._lock:
            self.misses += 1

    def evicted(self, count: int = 1) -> None:
        with self._lock:
            self.evictions += count

    def invalidated(self, count: int = 1) -> None:
        """Entries dropped because they went stale or were cleared on purpose (not because the cache was full)."""
        with self._lock:
            self.invalidations += count

    def put(self) -> None:
        with self._lock:
            self.puts += 1

    def observe(self, size_bytes: int, entries: int) -> None:
        with self._lock:
            self.bytes = size_bytes
            self.entries = entries
            if size_bytes > self.peak_bytes:
                self.peak_bytes = size_bytes
            if entries > self.peak_entries:
                self.peak_entries = entries

    def set_gauge(self, gauge: Callable[[], tuple]) -> None:
        self._gauge = gauge

    def snapshot(self) -> dict[str, Any]:
        gauge = self._gauge
        if gauge is not None:
            try:
                reading = gauge()
                size_bytes, entries = reading[0], reading[1]
                if len(reading) > 2 and reading[2]:
                    self.extra = dict(reading[2])
                    self.peak_unique_bytes = max(self.peak_unique_bytes, int(self.extra.get("unique_bytes", 0)))
            except Exception:  # RuntimeError from a dict that changed size mid-read, or an owner already deleted
                size_bytes, entries = self.bytes, self.entries
            self.observe(int(size_bytes), int(entries))
        with self._lock:
            lookups = self.hits + self.misses
            row: dict[str, Any] = {
                "kind": self.kind,
                "bytes": self.bytes,
                "entries": self.entries,
                "peak_bytes": self.peak_bytes,
                "peak_entries": self.peak_entries,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "invalidations": self.invalidations,
                "puts": self.puts,
            }
            if self.extra:
                row.update(self.extra)
                row["peak_unique_bytes"] = self.peak_unique_bytes
            if lookups:
                row["hit_rate"] = round(self.hits / lookups, 4)
            if self.shared_with:
                row["shared_with"] = self.shared_with
            return row


def _unique_name(table: dict[str, Any], name: str) -> str:
    if name not in table:
        return name
    index = 2
    while f"{name}#{index}" in table:
        index += 1
    return f"{name}#{index}"


def cache(name: str, kind: str = "exact", *, shared_with: str = "") -> "CacheStats | None":
    """Register a cache and return its counters (None when instrumentation is off). A second instance gets ``name#2``."""
    if not ENABLED:
        return None
    with _registry_lock:
        stats = CacheStats(_unique_name(_caches, name), kind, shared_with)
        _caches[stats.name] = stats
        return stats


def cache_with_gauge(name: str, kind: str, owner: object, read: Callable[[Any], tuple], *,
                     shared_with: str = "") -> "CacheStats | None":
    """A cache whose size is read from ``owner`` by ``read(owner) -> (bytes, entries[, extra dict])``.

    ``owner`` is held weakly: the observer must never keep a cache, or the images in it, alive. Once the owner is gone the
    last figures and the peaks stay in the report.
    """
    stats = cache(name, kind, shared_with=shared_with)
    if stats is None:
        return None
    reference = weakref.ref(owner)

    def gauge() -> tuple:
        target = reference()
        if target is None:
            return stats.bytes, stats.entries
        return read(target)

    stats.set_gauge(gauge)
    return stats


class JobStats:
    """Decode jobs of one kind: how many are waiting and running, and the buffers they are (estimated to be) building."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self.queued_now = self.running_now = 0
        self.est_bytes_now = 0
        self.peak_queued = self.peak_running = self.peak_est_bytes = 0
        self.started_total = self.finished_total = 0
        self.max_result_bytes = 0  # exact: the largest finished image (QImage.sizeInBytes())

    def queued(self) -> None:
        with self._lock:
            self.queued_now += 1
            self.peak_queued = max(self.peak_queued, self.queued_now)

    def started(self, est_bytes: int) -> None:
        with self._lock:
            self.queued_now = max(0, self.queued_now - 1)
            self.running_now += 1
            self.est_bytes_now += est_bytes
            self.started_total += 1
            self.peak_running = max(self.peak_running, self.running_now)
            self.peak_est_bytes = max(self.peak_est_bytes, self.est_bytes_now)

    def finished(self, est_bytes: int) -> None:
        with self._lock:
            self.running_now = max(0, self.running_now - 1)
            self.est_bytes_now = max(0, self.est_bytes_now - est_bytes)
            self.finished_total += 1

    def result(self, size_bytes: int) -> None:
        with self._lock:
            if size_bytes > self.max_result_bytes:
                self.max_result_bytes = size_bytes

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "queued": self.queued_now,
                "running": self.running_now,
                "est_bytes_in_flight": self.est_bytes_now,
                "peak_queued": self.peak_queued,
                "peak_running": self.peak_running,
                "peak_est_bytes_in_flight": self.peak_est_bytes,
                "started": self.started_total,
                "finished": self.finished_total,
                "max_result_bytes": self.max_result_bytes,
            }


def jobs(name: str) -> "JobStats | None":
    if not ENABLED:
        return None
    with _registry_lock:
        existing = _jobs.get(name)
        if existing is None:
            existing = _jobs[name] = JobStats(name)
        return existing


class EventCounter:
    """Counts occurrences of a memory-relevant code path and the estimated transient bytes each one costs."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self.count = 0
        self.total_est_bytes = 0
        self.max_est_bytes = 0

    def add(self, est_bytes: int = 0) -> None:
        with self._lock:
            self.count += 1
            self.total_est_bytes += est_bytes
            if est_bytes > self.max_est_bytes:
                self.max_est_bytes = est_bytes

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"count": self.count, "total_est_bytes": self.total_est_bytes, "max_est_bytes": self.max_est_bytes}


def counter(name: str) -> "EventCounter | None":
    if not ENABLED:
        return None
    with _registry_lock:
        existing = _counters.get(name)
        if existing is None:
            existing = _counters[name] = EventCounter(name)
        return existing


class _Gauge:
    """A live figure with no counters (a queue depth, a dict length). ``owner`` is held weakly."""

    def __init__(self, name: str, owner: object, read: Callable[[Any], int]) -> None:
        self.name = name
        self._reference = weakref.ref(owner)
        self._read = read
        self.last = 0
        self.peak = 0

    def snapshot(self) -> dict[str, int]:
        target = self._reference()
        if target is not None:
            try:
                self.last = int(self._read(target))
            except Exception:
                pass
        self.peak = max(self.peak, self.last)
        return {"value": self.last, "peak": self.peak}


def gauge(name: str, owner: object, read: Callable[[Any], int]) -> None:
    if not ENABLED:
        return
    with _registry_lock:
        key = _unique_name(_gauges, name)
        _gauges[key] = _Gauge(key, owner, read)


# --------------------------------------------------------------------------------------------------------------------------
# Estimates (always labelled as such where they are reported)
# --------------------------------------------------------------------------------------------------------------------------


def estimate_object_bytes(values: Any, sample: int = 64) -> int:
    """Rough ``sys.getsizeof`` cost of a collection's values, extrapolated from a sample. An estimate, shallow at two levels."""
    try:
        items = list(values)
    except RuntimeError:
        return 0
    if not items:
        return 0
    step = max(1, len(items) // sample)
    picked = items[::step][:sample]
    total = 0
    for item in picked:
        total += sys.getsizeof(item)
        fields = getattr(item, "__dict__", None)
        if fields is None:
            for slot in getattr(type(item), "__slots__", ()) or ():
                total += sys.getsizeof(getattr(item, slot, None))
        else:
            total += sys.getsizeof(fields) + sum(sys.getsizeof(v) for v in fields.values())
    return int(total / len(picked) * len(items))


def image_bytes(image: Any) -> int:
    """``QImage.sizeInBytes()`` (exact for its pixel buffer); 0 for anything unreadable."""
    try:
        return int(image.sizeInBytes())
    except Exception:
        return 0


# --------------------------------------------------------------------------------------------------------------------------
# Sampler and events
# --------------------------------------------------------------------------------------------------------------------------


def _log(event: str, **fields: object) -> None:
    from .perf import perf_logger

    perf_logger().log(event, **fields)


class _UiHeartbeat:
    """Measures how late the GUI thread runs a 50 ms timer: the largest lateness since the last read is the longest UI stall."""

    def __init__(self, app: Any) -> None:
        from PySide6.QtCore import QTimer

        self._lock = threading.Lock()
        self._max_ms = 0.0
        self._last = time.perf_counter()
        self._timer = QTimer(app)
        self._timer.setInterval(UI_HEARTBEAT_MS)
        self._timer.timeout.connect(self._beat)
        self._timer.start()

    def _beat(self) -> None:
        now = time.perf_counter()
        late = (now - self._last) * 1000.0 - UI_HEARTBEAT_MS
        self._last = now
        with self._lock:
            if late > self._max_ms:
                self._max_ms = late

    def take_max_ms(self) -> float:
        with self._lock:
            value, self._max_ms = self._max_ms, 0.0
        return round(max(0.0, value), 1)

    def stop(self) -> None:
        try:
            self._timer.stop()
        except RuntimeError:
            pass


class _Sampler:
    def __init__(self, interval_s: float, heartbeat: _UiHeartbeat | None) -> None:
        self.interval_s = max(0.1, interval_s)
        self.heartbeat = heartbeat
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="memtrace-sampler", daemon=True)
        self.use_tracemalloc = _truthy(os.environ.get(TRACEMALLOC_ENV_VAR))
        self.use_gpu = _truthy(os.environ.get(GPU_ENV_VAR))
        self._last_gpu = 0.0
        self._gpu: dict[str, Any] | None = None
        self.peak_total_private = 0
        self.peak_own_private = 0

    def start(self) -> None:
        if self.use_tracemalloc:
            import tracemalloc

            if not tracemalloc.is_tracing():
                tracemalloc.start(1)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                self.sample_once()
            except Exception as error:  # the observer must never take the program down
                _log("mem.error", error=repr(error))
            self.stop_event.wait(max(0.05, self.interval_s - (time.perf_counter() - started)))

    def sample_once(self, event: str = "mem.sample", **extra: object) -> dict[str, Any]:
        record = snapshot(self.heartbeat, self)
        self.peak_total_private = max(self.peak_total_private, record["tree"]["total_private"])
        self.peak_own_private = max(self.peak_own_private, record["tree"]["own"]["private"])
        _log(event, **record, **extra)
        return record

    def gpu(self) -> dict[str, Any] | None:
        if not self.use_gpu:
            return None
        now = time.monotonic()
        if now - self._last_gpu >= GPU_PERIOD_S:
            self._last_gpu = now
            self._gpu = _read_nvidia_smi()
        return self._gpu


def _read_nvidia_smi() -> dict[str, Any] | None:
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        total = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3.0, creationflags=flags,
        )
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3.0, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    result: dict[str, Any] = {}
    first = total.stdout.strip().splitlines()[:1]
    if first:
        used, _, capacity = first[0].partition(",")
        if used.strip().isdigit():
            result["used_mib"] = int(used)
        if capacity.strip().isdigit():
            result["total_mib"] = int(capacity)
    per_process: dict[str, int] = {}
    for line in apps.stdout.strip().splitlines():
        pid, _, used = line.partition(",")
        if pid.strip().isdigit() and used.strip().isdigit():
            per_process[pid.strip()] = int(used)
    if per_process:
        result["per_process_mib"] = per_process
    return result or None


def snapshot(heartbeat: _UiHeartbeat | None = None, sampler: _Sampler | None = None) -> dict[str, Any]:
    """One reading of everything: process tree, caches, decode jobs, counters, gauges."""
    import gc

    with _registry_lock:
        cache_list = list(_caches.values())
        job_list = list(_jobs.values())
        counter_list = list(_counters.values())
        gauge_list = list(_gauges.values())
    record: dict[str, Any] = {
        "tree": tree_memory(),
        "caches": {stats.name: stats.snapshot() for stats in cache_list},
        "jobs": {item.name: item.snapshot() for item in job_list},
        "counters": {item.name: item.snapshot() for item in counter_list},
        "gauges": {item.name: item.snapshot() for item in gauge_list},
        "py_blocks": sys.getallocatedblocks(),
        "gc_counts": list(gc.get_count()),
        "threads": threading.active_count(),
    }
    if heartbeat is not None:
        record["ui_lag_ms_max"] = heartbeat.take_max_ms()
    if sampler is not None and sampler.use_tracemalloc:
        import tracemalloc

        current, peak = tracemalloc.get_traced_memory()
        record["tracemalloc"] = {"current": current, "peak": peak}
    if sampler is not None:
        gpu = sampler.gpu()
        if gpu is not None:
            record["gpu"] = gpu
    return record


_sampler: _Sampler | None = None
_heartbeat: _UiHeartbeat | None = None
_start_lock = threading.Lock()


def start(app: Any = None) -> bool:
    """Begin sampling (once). Call after the ``QApplication`` exists. Returns whether instrumentation is running."""
    global _sampler, _heartbeat
    if not ENABLED:
        return False
    with _start_lock:
        if _sampler is not None:
            return True
        from .perf import perf_logger

        perf_logger().set_enabled(True, reason="memtrace")
        if app is not None:
            try:
                _heartbeat = _UiHeartbeat(app)
            except Exception:
                _heartbeat = None
        try:
            interval = float(os.environ.get(INTERVAL_ENV_VAR, "1.0"))
        except ValueError:
            interval = 1.0
        _sampler = _Sampler(interval, _heartbeat)
        _log(
            "mem.start",
            platform=sys.platform,
            python=sys.version.split()[0],
            interval_s=_sampler.interval_s,
            tracemalloc=_sampler.use_tracemalloc,
            gpu=_sampler.use_gpu,
            argv=sys.argv[1:],
        )
        _sampler.sample_once("mem.sample", phase="start")
        _sampler.start()
        import atexit

        atexit.register(stop)
        return True


def stop() -> None:
    """Take a last reading (with the OS peaks) and stop sampling."""
    global _sampler, _heartbeat
    with _start_lock:
        sampler, _sampler = _sampler, None
        heartbeat, _heartbeat = _heartbeat, None
    if sampler is None:
        return
    sampler.stop()
    try:
        sampler.sample_once("mem.final", peak_total_private=sampler.peak_total_private, peak_own_private=sampler.peak_own_private)
    except Exception:
        pass
    if heartbeat is not None:
        heartbeat.stop()


def mark(label: str, **fields: object) -> None:
    """An immediate reading labelled ``label`` (a workload phase boundary); a no-op when instrumentation is off."""
    if not ENABLED:
        return
    sampler = _sampler
    if sampler is None:
        _log("mem.mark", label=label, tree=tree_memory(), **fields)
        return
    sampler.sample_once("mem.mark", label=label, **fields)


@contextmanager
def measure(label: str, **fields: object) -> Iterator[None]:
    """Log what ``label`` cost: process memory before, after and (sampled every 10 ms) at its highest, plus the OS peak working
    set. Off, it is a bare ``yield``. This is how a short transient (loading the semantic matrix) shows up in a 1 s series."""
    if not ENABLED:
        yield
        return
    before = process_memory()
    peak = [before.private_bytes if before else 0]
    peak_ws = [before.working_set if before else 0]
    stop_event = threading.Event()

    def watch() -> None:
        while not stop_event.wait(0.01):
            reading = process_memory()
            if reading is not None:
                peak[0] = max(peak[0], reading.private_bytes)
                peak_ws[0] = max(peak_ws[0], reading.working_set)

    watcher = threading.Thread(target=watch, name="memtrace-measure", daemon=True)
    started = time.perf_counter()
    watcher.start()
    try:
        yield
    finally:
        stop_event.set()
        watcher.join(timeout=1.0)
        after = process_memory()
        _log(
            "mem.span",
            label=label,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
            private_before=before.private_bytes if before else 0,
            private_after=after.private_bytes if after else 0,
            private_peak_sampled=max(peak[0], after.private_bytes if after else 0),
            ws_before=before.working_set if before else 0,
            ws_after=after.working_set if after else 0,
            ws_peak_sampled=max(peak_ws[0], after.working_set if after else 0),
            os_peak_ws=after.peak_working_set if after else 0,
            **fields,
        )


def reset_for_tests() -> None:
    """Forget every registration (tests only)."""
    with _registry_lock:
        _caches.clear()
        _jobs.clear()
        _counters.clear()
        _gauges.clear()
