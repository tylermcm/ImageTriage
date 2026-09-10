"""Capability readiness UI: the Demo Ready preflight, Repair AI and diagnostics.

The old flow announced "AI Setup Complete" as soon as the installer process
exited zero, without checking a single capability, and the editor mask models
were installed by a separate path that Settings knew nothing about. Everything
here reads ``image_triage.ai_health``, so what the user is told matches what the
features will actually do (docs/ai_runtime_failure_map.md, root cause D).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai_health import CapabilityHealth, ai_health
from ..ai_manifest import DEFAULT_CAPABILITY_ORDER


class AIReadinessSignals(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)


class AIReadinessTask(QRunnable):
    """Run every capability check off the UI thread.

    Each check spawns a probe subprocess, so this must never run on the GUI
    thread even though the results are cached afterwards.
    """

    def __init__(
        self,
        *,
        device: str = "auto",
        capability_keys: tuple[str, ...] = DEFAULT_CAPABILITY_ORDER,
        deep_models: bool = False,
        use_cache: bool = False,
        thorough: bool = False,
    ) -> None:
        super().__init__()
        self.signals = AIReadinessSignals()
        self._device = device
        self._keys = capability_keys
        self._deep_models = deep_models
        self._use_cache = use_cache
        self._thorough = thorough
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            def report(name: str, index: int, total: int) -> None:
                self.signals.progress.emit(f"Checking {name} ({index + 1} of {total})...")

            results = ai_health().check_all(
                self._keys,
                device=self._device,
                use_cache=self._use_cache,
                deep_models=self._deep_models,
                thorough=self._thorough,
                progress_callback=report,
            )
            self.signals.finished.emit(dict(results))
        except Exception as exc:  # pragma: no cover - defensive
            self.signals.failed.emit(str(exc))


class AIRepairSignals(QObject):
    progress = Signal(str)
    # results, runtime_reinstall_required
    finished = Signal(dict, bool)
    failed = Signal(str)


class AIRepairTask(QRunnable):
    """Repair the model bundles behind a set of capabilities, then re-check."""

    def __init__(self, capability_keys: tuple[str, ...], *, device: str = "auto") -> None:
        super().__init__()
        self.signals = AIRepairSignals()
        self._keys = capability_keys
        self._device = device
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            results, runtime_required = ai_health().repair_all(
                self._keys,
                device=self._device,
                progress_callback=self.signals.progress.emit,
                cancel_check=lambda: self._cancelled,
            )
            self.signals.finished.emit(results, runtime_required)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class AIBundleInstallSignals(QObject):
    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)


class AIBundleInstallTask(QRunnable):
    """Download every model bundle the selected capabilities still need.

    Set Up AI runs this after the runtime and the culling models, so that what
    the following verification checks is exactly what setup installed.
    """

    def __init__(self, capability_keys: tuple[str, ...]) -> None:
        super().__init__()
        self.signals = AIBundleInstallSignals()
        self._keys = capability_keys
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            installed = ai_health().install_missing_bundles(
                self._keys,
                progress_callback=self.signals.progress.emit,
                cancel_check=lambda: self._cancelled,
            )
            self.signals.finished.emit(list(installed))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


def summarize(results: dict[str, CapabilityHealth]) -> str:
    """One honest sentence about the whole selected feature set."""
    if not results:
        return "No AI capabilities were checked."
    ready = [item for item in results.values() if item.ready]
    failed = [item for item in results.values() if not item.ready]
    if not failed:
        devices = {item.selected_device or "cpu" for item in ready}
        where = "/".join(sorted(devices)).upper()
        return f"All {len(ready)} AI features are ready on {where}."
    required_failed = [item for item in failed if not item.optional]
    lead = (
        f"{len(ready)} of {len(results)} AI features are ready. "
        f"{len(failed)} need attention"
    )
    if required_failed:
        lead += f", including {required_failed[0].name}"
    return lead + "."


class AIReadinessDialog(QDialog):
    """Per-capability readiness, with the reason and the next action for each."""

    def __init__(
        self,
        results: dict[str, CapabilityHealth],
        *,
        parent: QWidget | None = None,
        title: str = "AI Readiness",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(720, 460)
        self._results = results

        layout = QVBoxLayout(self)
        headline = QLabel(summarize(results), self)
        headline.setWordWrap(True)
        headline.setStyleSheet("font-weight: 600;")
        layout.addWidget(headline)

        tree = QTreeWidget(self)
        tree.setColumnCount(3)
        tree.setHeaderLabels(["Feature", "State", "What to do"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        for health in results.values():
            item = QTreeWidgetItem(
                [
                    health.name,
                    (
                        f"Ready ({(health.selected_device or 'cpu').upper()})"
                        if health.ready
                        else f"Failed at {health.stage_label}"
                    ),
                    "" if health.ready else health.remediation,
                ]
            )
            if not health.ready:
                item.setToolTip(0, health.message)
                item.setToolTip(2, health.detail or health.remediation)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        layout.addWidget(tree, 1)

        detail = QPlainTextEdit(self)
        detail.setReadOnly(True)
        detail.setPlainText(ai_health().diagnostics_text(results))
        detail.setMaximumHeight(180)
        layout.addWidget(detail)
        self._detail = detail

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        copy_button = buttons.addButton("Copy Diagnostics", QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(self._copy_diagnostics)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def diagnostics_text(self) -> str:
        return self._detail.toPlainText()

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._detail.toPlainText(), mode=clipboard.Mode.Clipboard)


def failure_message(health: CapabilityHealth) -> str:
    """The concise, actionable text a feature shows when it cannot run.

    Never a traceback: the stage, the cause, the profile and the next step. The
    full traceback stays in the diagnostics bundle.
    """
    lines = [health.headline()]
    if health.stage != "ready":
        lines.append(f"Stage: {health.stage_label}")
    if health.profile_id:
        device = health.selected_device or health.requested_device
        lines.append(f"Runtime: {health.profile_id} ({device})")
    if health.remediation:
        lines.append("")
        lines.append(health.remediation)
    return "\n".join(lines)


__all__ = [
    "AIBundleInstallTask",
    "AIReadinessDialog",
    "AIReadinessTask",
    "AIRepairTask",
    "failure_message",
    "summarize",
]
