"""A log of what goes wrong in the shell, written beside the app's other data, for the cases that leave nothing to read (the app runs without a console).

It records Qt and QML warnings and errors, uncaught Python exceptions (in any thread), a native crash, and a stall of the window: a watchdog notes when the
interface thread has not run for a few seconds and writes where every thread is at that moment, so a freeze can be told from a slow job. Nothing here may
break the app: every step is wrapped, and without a writable log folder it does nothing.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path

LOG_NAME = "qml-shell.log"
LOG_LIMIT_BYTES = 4 * 1024 * 1024         # a log bigger than this is started afresh the next time the app opens
STALL_SECONDS = 3.0                       # the interface thread is stalled when it has not run a timer for this long
HEARTBEAT_MS = 250

_state: dict[str, object] = {"file": None, "beat": time.monotonic(), "stalled": False}


def log_path() -> Path:
    return Path.home() / ".image-triage" / "logs" / LOG_NAME


def _write(text: str) -> None:
    handle = _state["file"]
    if handle is None:
        return
    try:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        handle.flush()
    except Exception:
        pass


def install(app=None) -> Path | None:
    """Start logging. `app` is the Qt application: with it, the interface heartbeat and the stall watchdog run too. Returns the log file's path."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if path.exists() and path.stat().st_size > LOG_LIMIT_BYTES else "a"
        handle = open(path, mode, encoding="utf-8", buffering=1)
        _state["file"] = handle
        _write(f"--- shell started, pid {os.getpid()}, python {sys.version.split()[0]}")
    except Exception:
        return None
    try:
        faulthandler.enable(file=handle, all_threads=True)
    except Exception:
        pass
    try:
        previous = sys.excepthook

        def hook(kind, value, trace):
            _write("uncaught exception:\n" + "".join(traceback.format_exception(kind, value, trace)))
            previous(kind, value, trace)

        sys.excepthook = hook
        previous_thread = threading.excepthook

        def thread_hook(args):
            _write(f"uncaught exception in thread {getattr(args.thread, 'name', '?')}:\n" + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
            previous_thread(args)

        threading.excepthook = thread_hook
    except Exception:
        pass
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        names = {QtMsgType.QtWarningMsg: "warning", QtMsgType.QtCriticalMsg: "critical", QtMsgType.QtFatalMsg: "fatal"}

        def handler(kind, context, message):
            name = names.get(kind)
            if name is not None:
                where = f" ({context.file}:{context.line})" if getattr(context, "file", None) else ""
                _write(f"qt {name}: {message}{where}")

        qInstallMessageHandler(handler)
    except Exception:
        pass
    if app is not None:
        _watch(app)
    return path


def _watch(app) -> None:
    try:
        from PySide6.QtCore import QTimer

        def beat() -> None:
            now = time.monotonic()
            if _state["stalled"]:
                _write(f"interface thread running again after {now - float(_state['beat']):.1f} s")
                _state["stalled"] = False
            _state["beat"] = now

        timer = QTimer(app)
        timer.setInterval(HEARTBEAT_MS)
        timer.timeout.connect(beat)
        timer.start()
        _state["timer"] = timer

        def watch() -> None:
            while True:
                time.sleep(1.0)
                gap = time.monotonic() - float(_state["beat"])
                if gap >= STALL_SECONDS and not _state["stalled"]:
                    _state["stalled"] = True
                    _write(f"interface thread has not run for {gap:.1f} s; where every thread is now:")
                    try:
                        faulthandler.dump_traceback(file=_state["file"], all_threads=True)
                    except Exception:
                        pass

        threading.Thread(target=watch, name="stall-watchdog", daemon=True).start()
    except Exception:
        pass
