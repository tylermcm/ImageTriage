from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QMainWindow, QProgressDialog


@dataclass(slots=True, frozen=True)
class JobSpec:
    title: str
    preparing_label: str
    running_label: str
    indeterminate_label: str = "Working..."
    window_modality: Qt.WindowModality = Qt.WindowModality.WindowModal
    stays_on_top: bool = False
    minimum_width: int = 420


class JobController(QObject):
    def __init__(self, window: QMainWindow, spec: JobSpec) -> None:
        super().__init__(window)
        self._window = window
        self._spec = spec
        self._dialog: QProgressDialog | None = None

    def start(self, total_steps: int) -> QProgressDialog:
        dialog = self._ensure_dialog()
        upper = max(1, int(total_steps))
        dialog.setRange(0, upper)
        if dialog.value() > upper:
            dialog.setValue(0)
        dialog.setLabelText(self._spec.preparing_label)
        return dialog

    def progress(self, current: int, total: int, message: str = "") -> None:
        dialog = self._ensure_dialog()
        upper = max(1, int(total))
        dialog.setRange(0, upper)
        dialog.setValue(min(max(int(current), 0), upper))
        dialog.setLabelText(message or self._spec.running_label)

    def indeterminate(self, message: str = "") -> None:
        dialog = self._ensure_dialog()
        dialog.setRange(0, 0)
        dialog.setValue(0)
        dialog.setLabelText(message or self._spec.indeterminate_label)

    def close(self) -> None:
        if self._dialog is None:
            return
        self._dialog.close()
        self._dialog = None

    def _ensure_dialog(self) -> QProgressDialog:
        if self._dialog is None:
            dialog = QProgressDialog(self._window)
            dialog.setWindowTitle(self._spec.title)
            dialog.setWindowModality(self._spec.window_modality)
            dialog.setCancelButton(None)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setMinimumWidth(max(280, int(self._spec.minimum_width)))
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._spec.stays_on_top)
            self._dialog = dialog
        else:
            self._dialog.setWindowTitle(self._spec.title)
            self._dialog.setWindowModality(self._spec.window_modality)
            self._dialog.setMinimumWidth(max(280, int(self._spec.minimum_width)))
            self._dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._spec.stays_on_top)
        self._dialog.show()
        if hasattr(self._window, "_center_window_dialog"):
            # Keep dialog placement behavior consistent with existing UI helpers.
            self._window._center_window_dialog(self._dialog)  # type: ignore[attr-defined]
        self._dialog.raise_()
        return self._dialog
