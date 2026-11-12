"""Native CLI-Culler lifecycle for the QML application, without a Widgets window."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, Signal, Slot


def enabled(settings) -> bool:
    return (os.environ.get("IMAGE_TRIAGE_CLI_CULLER_VALIDATION") == "1"
            or bool(os.environ.get("IMAGE_TRIAGE_CLI_CULLER_RUNTIME"))
            or bool(settings and settings.value("ai/use_cli_culler_native", False, type=bool)))


class NativeCullRun(QObject):
    stage = Signal(int, float)
    progress = Signal(str, int, int)
    finished = Signal(object, bool, bool)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.running = False
        self._task = None
        self._signals = None
        self._runtime: dict = {}                    # what the engine said it runs on (its "runtime" event)
        self._warnings: list[str] = []              # what the engine warned about when it finished (its "complete" event)
        self.notes: list[str] = []                  # the last finished run's warnings, for the review page
        self.device_text = ""                       # where its ranking models ran ("the graphics card (name)", "the CPU"); "" when none ran
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    @property
    def alive(self) -> bool:
        """A task was started and has not said it ended yet (a cancelled one counts until its process has really stopped)."""
        return self._task is not None

    def _current(self) -> bool:
        sender = self.sender()
        return sender is None or sender is self._signals

    def start(self, *, folder: str, **unused) -> None:
        if self.running or self._task is not None:
            raise RuntimeError("Wait for the previous analysis to stop before starting another")
        from ..cli_culler_runtime import load_native_runtime, native_settings
        from ..cli_culler_task import CliCullerRunTask

        runtime = load_native_runtime()
        device = str(self.settings.value("ai/cli_culler_device", "auto"))
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError("Unknown culling device")
        namespace = runtime["namespace"] + ("-" + device if device != "auto" else "")
        task = CliCullerRunTask(folders=[Path(folder)], python=Path(runtime["python"]),
                               worker=Path(runtime["worker"]), app_path=Path(runtime["app_path"]),
                               settings=native_settings(self.settings, runtime), runtime_manifest=runtime,
                               device=device if device != "auto" else None, cache_namespace=namespace)
        queued = Qt.ConnectionType.QueuedConnection
        task.signals.progress.connect(self._progress, queued)
        task.signals.runtime.connect(self._runtime_reported, queued)
        task.signals.detail.connect(self._detail, queued)
        task.signals.finished.connect(self._finished, queued)
        task.signals.failed.connect(self._failed, queued)
        task.signals.cancelled.connect(self._cancelled, queued)
        self._task, self._signals, self.running = task, task.signals, True
        self._runtime, self._warnings, self.notes, self.device_text = {}, [], [], ""
        self._pool.start(task)

    def cancel(self):
        if self._task is not None:
            self._task.cancel()

    def shutdown(self):
        self.cancel()
        if not self._pool.waitForDone(5000):
            raise RuntimeError("The native culler did not stop during shutdown")

    @Slot(str, str, int, int, str)
    def _progress(self, folder, label, done, total, eta):
        if self.running and self._current():
            self.progress.emit(label, done, total)

    @Slot(dict)
    def _runtime_reported(self, event):
        if self._current():
            self._runtime = dict(event)

    @Slot(str, str)
    def _detail(self, folder, text):
        """The engine's closing report: {"warnings": [...], "models": {...}} as JSON."""
        if not self._current():
            return
        import json

        try:
            report = json.loads(text)
        except ValueError:
            return
        if isinstance(report, dict) and isinstance(report.get("warnings"), list):
            self._warnings = [str(item) for item in report["warnings"] if str(item).strip()]

    @Slot(str, str, str)
    def _finished(self, folder, active, unused):
        if not self._current():
            return
        from ..ai_results import load_ai_bundle

        try:
            bundle = load_ai_bundle(active)
            if bundle.native_document is None:
                raise ValueError("Native analysis did not produce native decisions")
        except Exception as error:
            self._failed(folder, str(error))
            return
        self._task, self._signals, self.running = None, None, False
        self.notes = list(self._warnings)
        device = str(self._runtime.get("device", "") or "")
        self.device_text = ""
        if device:                                  # only reported when a ranking model ran
            name = str(self._runtime.get("gpu", "") or "")
            self.device_text = ("the graphics card" + (f" ({name})" if name else "")) if device.startswith("cuda") else "the CPU"
        models = bundle.native_document.get("models", {})
        self.finished.emit(bundle, "faces" in models or "face_landmarker" in models,
                           any(name in models for name in ("maniqa", "appeal", "q_realign")))

    @Slot(str, str)
    def _failed(self, folder, message):
        if not self._current():
            return
        self._task, self._signals, self.running = None, None, False
        self.failed.emit(message)

    @Slot(str, str)
    def _cancelled(self, folder, message):
        if not self._current():
            return
        self._task, self._signals, self.running = None, None, False
        self.cancelled.emit()
