from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import DeleteMode, WinnerMode


@dataclass(slots=True, frozen=True)
class WorkflowPreset:
    name: str
    session_id: str
    winner_mode: WinnerMode
    delete_mode: DeleteMode


@dataclass(slots=True, frozen=True)
class WorkflowSettingsResult:
    session_id: str
    winner_mode: WinnerMode
    delete_mode: DeleteMode
    catalog_cache_enabled: bool = True
    watch_current_folder: bool = True
    presets: tuple[WorkflowPreset, ...] = ()


class WorkflowSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        sessions: list[str],
        current_session: str,
        winner_mode: WinnerMode,
        delete_mode: DeleteMode,
        catalog_cache_enabled: bool = True,
        watch_current_folder: bool = True,
        catalog_summary_text: str = "",
        presets: list[WorkflowPreset] | None = None,
        preset_save_callback: Callable[[tuple[WorkflowPreset, ...]], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflow Settings")
        self.setModal(True)
        self.resize(460, 260)
        self._presets = list(presets or [])
        self._preset_save_callback = preset_save_callback
        self._updating_session = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        intro = QLabel(
            "Adjust how decisions are stored, how accepted images are handled, and how deletes behave."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        layout.addWidget(intro)

        workflow_group = QGroupBox("Workflow")
        workflow_layout = QFormLayout(workflow_group)
        workflow_layout.setContentsMargins(12, 14, 12, 12)
        workflow_layout.setSpacing(12)

        self.session_combo = QComboBox()
        self.session_combo.setEditable(True)
        self._refresh_session_combo(sessions=sessions, current_session=current_session)
        self.session_combo.setCurrentText(current_session)
        self.session_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.session_combo.currentTextChanged.connect(self._handle_session_text_changed)
        if self.session_combo.lineEdit() is not None:
            self.session_combo.lineEdit().editingFinished.connect(self._normalize_session_text)

        self.winner_mode_combo = QComboBox()
        for mode in WinnerMode:
            self.winner_mode_combo.addItem(mode.value, mode)
        self.winner_mode_combo.setCurrentIndex(max(0, self.winner_mode_combo.findData(winner_mode)))

        self.delete_mode_combo = QComboBox()
        for mode in DeleteMode:
            self.delete_mode_combo.addItem(mode.value, mode)
        self.delete_mode_combo.setCurrentIndex(max(0, self.delete_mode_combo.findData(delete_mode)))

        session_row = QWidget()
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(8)
        session_layout.addWidget(self.session_combo, 1)
        self.save_preset_button = QPushButton("Save Preset")
        self.save_preset_button.clicked.connect(self._save_current_preset)
        session_layout.addWidget(self.save_preset_button)

        workflow_layout.addRow("Session", session_row)
        workflow_layout.addRow("Accepted handling", self.winner_mode_combo)
        workflow_layout.addRow("Delete behavior", self.delete_mode_combo)
        layout.addWidget(workflow_group)

        catalog_group = QGroupBox("Catalog Cache")
        catalog_layout = QVBoxLayout(catalog_group)
        catalog_layout.setContentsMargins(12, 14, 12, 12)
        catalog_layout.setSpacing(10)

        self.catalog_cache_checkbox = QCheckBox("Use catalog cache for faster folder open")
        self.catalog_cache_checkbox.setChecked(catalog_cache_enabled)
        catalog_layout.addWidget(self.catalog_cache_checkbox)

        self.watch_current_folder_checkbox = QCheckBox("Refresh the open folder when files change on disk")
        self.watch_current_folder_checkbox.setChecked(watch_current_folder)
        catalog_layout.addWidget(self.watch_current_folder_checkbox)

        self.catalog_summary_label = QLabel(catalog_summary_text or "Catalog database has not been created yet.")
        self.catalog_summary_label.setWordWrap(True)
        self.catalog_summary_label.setObjectName("mutedText")
        self.catalog_summary_label.setStyleSheet("font-size: 11px;")
        catalog_layout.addWidget(self.catalog_summary_label)

        layout.addWidget(catalog_group)

        self.preset_status_label = QLabel("")
        self.preset_status_label.setObjectName("mutedText")
        self.preset_status_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.preset_status_label)

        notes = QWidget()
        notes_layout = QVBoxLayout(notes)
        notes_layout.setContentsMargins(2, 0, 2, 0)
        notes_layout.setSpacing(4)
        for text in (
            "Sessions keep separate accept/reject/rating/tag states for the same files.",
            "Link mode creates filesystem links for fast winner marking. Use Copy mode when a drive does not support links.",
            "Safe Trash moves files into Image Triage recovery storage so Ctrl+Z can restore them.",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setObjectName("mutedText")
            label.setStyleSheet("font-size: 11px;")
            notes_layout.addWidget(label)
        layout.addWidget(notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        self._refresh_preset_dropdown()

    def _refresh_session_combo(self, *, sessions: list[str], current_session: str) -> None:
        self._updating_session = True
        try:
            names: list[str] = []
            for name in [preset.name for preset in self._presets] + sessions:
                normalized = " ".join((name or "").split())
                if normalized and normalized not in names:
                    names.append(normalized)
            self.session_combo.clear()
            self.session_combo.addItems(names)
            self.session_combo.setCurrentText(current_session)
        finally:
            self._updating_session = False

    def _preset_for_name(self, name: str) -> WorkflowPreset | None:
        normalized = " ".join((name or "").split()).casefold()
        if not normalized:
            return None
        for preset in self._presets:
            if preset.name.casefold() == normalized:
                return preset
        return None

    def _refresh_preset_dropdown(self) -> None:
        current_text = self.session_combo.currentText()
        self._updating_session = True
        try:
            names: list[str] = []
            for name in [preset.name for preset in self._presets] + [current_text]:
                normalized = " ".join((name or "").split())
                if normalized and normalized not in names:
                    names.append(normalized)
            self.session_combo.clear()
            self.session_combo.addItems(names)
            self.session_combo.setCurrentText(current_text)
        finally:
            self._updating_session = False

    def _apply_preset(self, preset: WorkflowPreset) -> None:
        self.session_combo.setCurrentText(preset.session_id or preset.name)
        winner_index = self.winner_mode_combo.findData(preset.winner_mode)
        if winner_index >= 0:
            self.winner_mode_combo.setCurrentIndex(winner_index)
        delete_index = self.delete_mode_combo.findData(preset.delete_mode)
        if delete_index >= 0:
            self.delete_mode_combo.setCurrentIndex(delete_index)
        self.preset_status_label.setText(f"Loaded preset: {preset.name}")

    def _handle_session_text_changed(self, text: str) -> None:
        if self._updating_session:
            return
        preset = self._preset_for_name(text)
        if preset is None:
            return
        winner_index = self.winner_mode_combo.findData(preset.winner_mode)
        if winner_index >= 0:
            self.winner_mode_combo.setCurrentIndex(winner_index)
        delete_index = self.delete_mode_combo.findData(preset.delete_mode)
        if delete_index >= 0:
            self.delete_mode_combo.setCurrentIndex(delete_index)

    def _normalize_session_text(self) -> None:
        normalized = " ".join((self.session_combo.currentText() or "").split())
        if normalized and normalized != self.session_combo.currentText():
            self.session_combo.setCurrentText(normalized)

    def _save_current_preset(self) -> None:
        result = self.result_settings(include_presets=False)
        name = " ".join(result.session_id.split()) or "Default"
        preset = WorkflowPreset(
            name=name,
            session_id=name,
            winner_mode=result.winner_mode,
            delete_mode=result.delete_mode,
        )
        existing_index = next((index for index, item in enumerate(self._presets) if item.name.casefold() == name.casefold()), None)
        if existing_index is None:
            self._presets.append(preset)
            if self.session_combo.findText(name, Qt.MatchFlag.MatchFixedString) < 0:
                self.session_combo.addItem(name)
        else:
            self._presets[existing_index] = preset
        self.session_combo.setCurrentText(name)
        self._refresh_preset_dropdown()
        if self._preset_save_callback is not None:
            self._preset_save_callback(tuple(self._presets))
        self.preset_status_label.setText(f"Saved preset: {name}")

    def result_settings(self, *, include_presets: bool = True) -> WorkflowSettingsResult:
        session_id = (self.session_combo.currentText() or "").strip()
        winner_mode = self.winner_mode_combo.currentData()
        delete_mode = self.delete_mode_combo.currentData()
        if not isinstance(winner_mode, WinnerMode):
            winner_raw = str(winner_mode or "")
            winner_mode = next((mode for mode in WinnerMode if winner_raw in {mode.name, mode.value}), WinnerMode.COPY)
        if not isinstance(delete_mode, DeleteMode):
            delete_raw = str(delete_mode or "")
            delete_mode = next((mode for mode in DeleteMode if delete_raw in {mode.name, mode.value}), DeleteMode.SAFE_TRASH)
        return WorkflowSettingsResult(
            session_id=session_id or "Default",
            winner_mode=winner_mode,
            delete_mode=delete_mode,
            catalog_cache_enabled=self.catalog_cache_checkbox.isChecked(),
            watch_current_folder=self.watch_current_folder_checkbox.isChecked(),
            presets=tuple(self._presets) if include_presets else (),
        )
