from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Iterator


class PerformanceLogger:
    """Small JSONL performance logger kept off the hot path unless enabled."""

    def __init__(self) -> None:
        self._enabled = False
        self._path: Path | None = None
        self._handle = None
        self._lock = threading.Lock()
        self._session_started = time.perf_counter()
        self._write_count = 0
        self._focus_prefixes: tuple[str, ...] = ()

    def set_focus(self, prefixes: "tuple[str, ...] | list[str] | None") -> None:
        """Restrict logging to events whose name starts with one of ``prefixes``
        (empty restores logging everything). This mutes the app-wide
        instrumentation without touching it, so we can record only what we're
        actively profiling and keep the log file small."""
        self._focus_prefixes = tuple(p for p in (prefixes or ()) if p)

    @property
    def focus_prefixes(self) -> tuple[str, ...]:
        return self._focus_prefixes

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def is_writing(self) -> bool:
        return self._enabled and self._handle is not None and self._path is not None

    def set_enabled(self, enabled: bool, *, reason: str = "") -> None:
        normalized = bool(enabled)
        if normalized and self.is_writing:
            return
        if normalized:
            self._open()
            self._enabled = True
            self.log("perf.enabled", reason=reason, path=str(self._path or ""), log_dir=str((_default_log_dir())))
            self.flush()
            return
        if not self._enabled and self._handle is None:
            return
        self.log("perf.disabled", reason=reason)
        self.flush()
        self._enabled = False
        self._close()

    def log(self, event: str, **fields: object) -> None:
        if not self._enabled:
            return
        if self._focus_prefixes and not event.startswith(self._focus_prefixes):
            return
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_ms": round((time.perf_counter() - self._session_started) * 1000.0, 3),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "event": event,
        }
        payload.update({key: _safe_value(value) for key, value in fields.items()})
        self._write(payload)

    def duration(self, event: str, duration_ms: float, **fields: object) -> None:
        if not self._enabled:
            return
        self.log(event, duration_ms=round(float(duration_ms), 3), **fields)

    @contextmanager
    def span(self, event: str, **fields: object) -> Iterator[None]:
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.duration(f"{event}.failed", (time.perf_counter() - start) * 1000.0, error=str(exc), **fields)
            raise
        else:
            self.duration(event, (time.perf_counter() - start) * 1000.0, **fields)

    def flush(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.flush()

    def _open(self) -> None:
        log_dir = _default_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"performance_{stamp}_{os.getpid()}.jsonl"
        self._handle = self._path.open("a", encoding="utf-8", buffering=1)
        self._write_count = 0

    def _close(self) -> None:
        with self._lock:
            handle = self._handle
            self._handle = None
            if handle is not None:
                handle.flush()
                handle.close()

    def _write(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if self._handle is None:
                return
            self._handle.write(line + "\n")
            self._write_count += 1
            if self._write_count % 100 == 0:
                self._handle.flush()


def _default_log_dir() -> Path:
    override = os.environ.get("IMAGE_TRIAGE_LOG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        user_profile = os.environ.get("USERPROFILE")
        if not user_profile:
            home_drive = os.environ.get("HOMEDRIVE", "")
            home_path = os.environ.get("HOMEPATH", "")
            if home_drive and home_path:
                user_profile = home_drive + home_path
        if not user_profile:
            user_profile = os.environ.get("HOME", "")
        if user_profile:
            # Microsoft Store Python virtualizes writes to AppData\Local. LocalLow
            # remains visible to Explorer while keeping logs out of image folders.
            return Path(user_profile) / "AppData" / "LocalLow" / "ImageTriage" / "logs"
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "ImageTriage" / "logs"
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile) / "AppData" / "Local" / "ImageTriage" / "logs"
    home_drive = os.environ.get("HOMEDRIVE", "")
    home_path = os.environ.get("HOMEPATH", "")
    if home_drive and home_path:
        return Path(home_drive + home_path) / "AppData" / "Local" / "ImageTriage" / "logs"
    temp_root = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_root:
        return Path(temp_root) / "ImageTriage" / "logs"
    return Path.cwd() / "logs"


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 500:
            return value[:497] + "..."
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        rendered = [_safe_value(item) for item in items[:20]]
        if len(items) > 20:
            rendered.append(f"+{len(items) - 20} more")
        return rendered
    if isinstance(value, dict):
        items = list(value.items())[:20]
        result = {str(key): _safe_value(item_value) for key, item_value in items}
        if len(value) > 20:
            result["__truncated__"] = len(value) - 20
        return result
    text = repr(value)
    return text[:497] + "..." if len(text) > 500 else text


_PERFORMANCE_LOGGER = PerformanceLogger()


def perf_logger() -> PerformanceLogger:
    return _PERFORMANCE_LOGGER


def performance_log_dir() -> Path:
    return _default_log_dir()
