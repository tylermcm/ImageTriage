from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
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
    toolbar_style: str = "text"
    compact_cards_enabled: bool = False
    free_smooth_scroll_enabled: bool = False
    preview_preload_batch_size: int = 10
    show_hidden_folders: bool = False
    auto_advance_enabled: bool = True
    burst_groups_enabled: bool = False
    burst_stacks_enabled: bool = False
    catalog_cache_enabled: bool = True
    watch_current_folder: bool = True
    ai_auto_profile_enabled: bool = False
    ai_embed_batch_size: int = 0
    ai_semantic_sidecar_enabled: bool = True
    presets: tuple[WorkflowPreset, ...] = ()


def _compact_catalog_summary(summary: str) -> str:
    lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
    if not lines:
        return "Catalog database has not been created yet."
    wanted_prefixes = (
        "Catalog cache reads:",
        "Folder watch:",
        "Indexed files:",
        "Indexed image bundles:",
        "Cached review features:",
    )
    compact = [line for line in lines if line.startswith(wanted_prefixes)]
    return "\n".join(compact[:5]) if compact else lines[0]


class WorkflowSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        sessions: list[str],
        current_session: str,
        winner_mode: WinnerMode,
        delete_mode: DeleteMode,
        toolbar_style: str = "text",
        compact_cards_enabled: bool = False,
        free_smooth_scroll_enabled: bool = False,
        preview_preload_batch_size: int = 10,
        show_hidden_folders: bool = False,
        auto_advance_enabled: bool = True,
        burst_groups_enabled: bool = False,
        burst_stacks_enabled: bool = False,
        catalog_cache_enabled: bool = True,
        watch_current_folder: bool = True,
        ai_auto_profile_enabled: bool = False,
        ai_embed_batch_size: int = 0,
        ai_semantic_sidecar_enabled: bool = True,
        catalog_summary_text: str = "",
        presets: list[WorkflowPreset] | None = None,
        preset_save_callback: Callable[[tuple[WorkflowPreset, ...]], None] | None = None,
        file_associations_callback: Callable[[], None] | None = None,
        keyboard_shortcuts_callback: Callable[[], None] | None = None,
        toolbar_callback: Callable[[], None] | None = None,
        reset_layout_callback: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(600, 430)
        self.resize(630, 550)
        self._presets = list(presets or [])
        self._preset_save_callback = preset_save_callback
        self._updating_session = False

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        self.section_list = QListWidget(body)
        self.section_list.setObjectName("settingsSectionList")
        self.section_list.setFixedWidth(140)
        self.section_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.section_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.pages = QStackedWidget(body)
        self.pages.setObjectName("settingsPages")
        self.pages.setMinimumWidth(420)
        body_layout.addWidget(self.section_list)
        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)

        self.session_combo = QComboBox()
        self.session_combo.setObjectName("settingsSessionCombo")
        self.session_combo.setEditable(True)
        self.session_combo.setMinimumWidth(160)
        self.session_combo.setMaximumWidth(220)
        self.session_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._refresh_session_combo(sessions=sessions, current_session=current_session)
        self.session_combo.setCurrentText(current_session)
        self.session_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.session_combo.currentTextChanged.connect(self._handle_session_text_changed)
        if self.session_combo.lineEdit() is not None:
            self.session_combo.lineEdit().setObjectName("settingsSessionLineEdit")
            self.session_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.session_combo.lineEdit().setTextMargins(0, 0, 0, 0)
            self.session_combo.lineEdit().editingFinished.connect(self._normalize_session_text)

        self.winner_mode_combo = QComboBox()
        self.winner_mode_combo.setMinimumWidth(240)
        self.winner_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for mode in WinnerMode:
            self.winner_mode_combo.addItem(mode.value, mode)
        self.winner_mode_combo.setCurrentIndex(max(0, self.winner_mode_combo.findData(winner_mode)))

        self.delete_mode_combo = QComboBox()
        self.delete_mode_combo.setMinimumWidth(240)
        self.delete_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for mode in DeleteMode:
            self.delete_mode_combo.addItem(mode.value, mode)
        self.delete_mode_combo.setCurrentIndex(max(0, self.delete_mode_combo.findData(delete_mode)))

        self.toolbar_style_combo = QComboBox()
        self.toolbar_style_combo.setMinimumWidth(180)
        self.toolbar_style_combo.addItem("Text", "text")
        self.toolbar_style_combo.addItem("Icons", "icons")
        self.toolbar_style_combo.addItem("Large icons", "large_icons")
        toolbar_index = self.toolbar_style_combo.findData(toolbar_style)
        self.toolbar_style_combo.setCurrentIndex(max(0, toolbar_index))

        session_row = QWidget()
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(8)
        session_layout.addWidget(self.session_combo)
        self.save_preset_button = QPushButton("Save Preset")
        self.save_preset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.save_preset_button.clicked.connect(self._save_current_preset)
        session_layout.addWidget(self.save_preset_button)
        session_layout.addStretch(1)

        general_page, general_layout = self._build_settings_page("General")
        self._add_form_row(general_layout, "Session", session_row)
        self._add_form_row(general_layout, "Accepted images", self.winner_mode_combo)
        self._add_form_row(general_layout, "Delete behavior", self.delete_mode_combo)
        self.preset_status_label = QLabel("")
        self.preset_status_label.setObjectName("mutedText")
        self.preset_status_label.setStyleSheet("font-size: 11px;")
        general_layout.addWidget(self.preset_status_label)
        general_layout.addStretch(1)
        self._add_settings_page("General", general_page)

        self.compact_cards_checkbox = QCheckBox("Use compact image cards")
        self.compact_cards_checkbox.setChecked(compact_cards_enabled)

        self.free_smooth_scroll_checkbox = QCheckBox("Use free smooth scrolling")
        self.free_smooth_scroll_checkbox.setChecked(free_smooth_scroll_enabled)

        self.preview_preload_batch_spin = QSpinBox()
        self.preview_preload_batch_spin.setRange(0, 128)
        self.preview_preload_batch_spin.setSingleStep(2)
        self.preview_preload_batch_spin.setSpecialValueText("Off")
        self.preview_preload_batch_spin.setSuffix(" images")
        self.preview_preload_batch_spin.setValue(max(0, min(128, int(preview_preload_batch_size))))
        self.preview_preload_batch_spin.setMinimumWidth(120)
        self.preview_preload_batch_spin.setToolTip(
            "Nearby images to preload while using the popout preview. Higher values can improve rapid navigation but use more CPU and RAM."
        )

        self.show_hidden_folders_checkbox = QCheckBox("Show hidden folders")
        self.show_hidden_folders_checkbox.setChecked(show_hidden_folders)

        self.auto_advance_checkbox = QCheckBox("Advance after Accept or Reject")
        self.auto_advance_checkbox.setChecked(auto_advance_enabled)

        self.burst_groups_checkbox = QCheckBox("Group burst sequences")
        self.burst_groups_checkbox.setChecked(burst_groups_enabled)

        self.burst_stacks_checkbox = QCheckBox("Stack similar burst frames")
        self.burst_stacks_checkbox.setChecked(burst_stacks_enabled)

        interface_page, interface_layout = self._build_settings_page("Interface")
        self._add_form_row(interface_layout, "Toolbar", self.toolbar_style_combo)
        self._add_checkbox_row(interface_layout, "Grid", self.compact_cards_checkbox)
        self._add_checkbox_row(interface_layout, "Scrolling", self.free_smooth_scroll_checkbox)
        self._add_form_row(interface_layout, "Preview preload", self.preview_preload_batch_spin)
        self._add_checkbox_row(interface_layout, "Folders", self.show_hidden_folders_checkbox)
        self._add_checkbox_row(interface_layout, "Review", self.auto_advance_checkbox)
        self._add_checkbox_row(interface_layout, "Bursts", self.burst_groups_checkbox)
        self._add_checkbox_row(interface_layout, "Stacks", self.burst_stacks_checkbox)
        interface_layout.addStretch(1)
        self._add_settings_page("Interface", interface_page)

        self.catalog_cache_checkbox = QCheckBox("Use catalog cache for faster folder open")
        self.catalog_cache_checkbox.setChecked(catalog_cache_enabled)

        self.watch_current_folder_checkbox = QCheckBox("Refresh the open folder when files change on disk")
        self.watch_current_folder_checkbox.setChecked(watch_current_folder)

        self.catalog_summary_label = QLabel(_compact_catalog_summary(catalog_summary_text))
        self.catalog_summary_label.setWordWrap(True)
        self.catalog_summary_label.setObjectName("mutedText")
        self.catalog_summary_label.setStyleSheet("font-size: 11px;")
        folders_page, folders_layout = self._build_settings_page("Folders")
        self._add_checkbox_row(folders_layout, "Catalog cache", self.catalog_cache_checkbox)
        self._add_checkbox_row(folders_layout, "Watch folder", self.watch_current_folder_checkbox)
        self._add_text_row(folders_layout, "Catalog", self.catalog_summary_label)
        folders_layout.addStretch(1)
        self._add_settings_page("Folders", folders_page)

        self.ai_embed_batch_size_spin = QSpinBox()
        self.ai_embed_batch_size_spin.setRange(0, 256)
        self.ai_embed_batch_size_spin.setSingleStep(8)
        self.ai_embed_batch_size_spin.setSpecialValueText("Auto")
        self.ai_embed_batch_size_spin.setValue(max(0, int(ai_embed_batch_size)))
        self.ai_embed_batch_size_spin.setMinimumWidth(120)

        self.ai_semantic_sidecar_checkbox = QCheckBox("Run semantic classification during AI Review")
        self.ai_semantic_sidecar_checkbox.setChecked(ai_semantic_sidecar_enabled)

        self.ai_auto_profile_checkbox = QCheckBox("Suggest a training profile before training")
        self.ai_auto_profile_checkbox.setChecked(ai_auto_profile_enabled)

        ai_page, ai_layout = self._build_settings_page("AI")
        self._add_form_row(ai_layout, "Embedding batch size", self.ai_embed_batch_size_spin)
        self._add_checkbox_row(ai_layout, "Semantic sidecar", self.ai_semantic_sidecar_checkbox)
        self._add_checkbox_row(ai_layout, "Training profile", self.ai_auto_profile_checkbox)
        ai_layout.addStretch(1)
        self._add_settings_page("AI", ai_page)

        workspace_page, workspace_layout = self._build_settings_page("Workspace")
        self._add_button_row(workspace_layout, "Toolbar", "Customize Toolbar...", toolbar_callback)
        self._add_button_row(workspace_layout, "Layout", "Reset Window Layout", reset_layout_callback)
        workspace_layout.addStretch(1)
        self._add_settings_page("Workspace", workspace_page)

        tools_page, tools_layout = self._build_settings_page("Tools")
        self._add_button_row(tools_layout, "File types", "File Associations...", file_associations_callback)
        self._add_button_row(tools_layout, "Shortcuts", "Keyboard Shortcuts...", keyboard_shortcuts_callback)
        tools_layout.addStretch(1)
        self._add_settings_page("Tools", tools_page)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        self.section_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.section_list.setCurrentRow(0)
        self._refresh_preset_dropdown()

    def _build_settings_page(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 2, 8, 8)
        layout.setSpacing(12)
        content.setMinimumWidth(390)
        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)
        scroll.setWidget(content)
        return scroll, layout

    def _add_settings_page(self, title: str, page: QWidget) -> None:
        item = QListWidgetItem(title)
        self.section_list.addItem(item)
        self.pages.addWidget(page)

    def _row_frame(self) -> tuple[QWidget, QHBoxLayout]:
        row = QWidget()
        row.setObjectName("settingsRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        return row, layout

    def _add_form_row(self, layout: QVBoxLayout, label_text: str, field: QWidget) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(106)
        label.setObjectName("sectionLabel")
        row_layout.addWidget(label)
        row_layout.addWidget(field, 1)
        layout.addWidget(row)

    def _add_checkbox_row(self, layout: QVBoxLayout, label_text: str, checkbox: QCheckBox) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(106)
        label.setObjectName("sectionLabel")
        row_layout.addWidget(label)
        row_layout.addWidget(checkbox, 1)
        layout.addWidget(row)

    def _add_text_row(self, layout: QVBoxLayout, label_text: str, value: QLabel) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(106)
        label.setObjectName("sectionLabel")
        row_layout.addWidget(label)
        row_layout.addWidget(value, 1)
        layout.addWidget(row)

    def _add_button_row(
        self,
        layout: QVBoxLayout,
        label_text: str,
        button_text: str,
        callback: Callable[[], None] | None,
    ) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(106)
        label.setObjectName("sectionLabel")
        button = QPushButton(button_text)
        button.setMinimumWidth(180)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setEnabled(callback is not None)
        if callback is not None:
            button.clicked.connect(callback)
        row_layout.addWidget(label)
        row_layout.addWidget(button)
        row_layout.addStretch(1)
        layout.addWidget(row)

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
            toolbar_style=str(self.toolbar_style_combo.currentData() or "text"),
            compact_cards_enabled=self.compact_cards_checkbox.isChecked(),
            free_smooth_scroll_enabled=self.free_smooth_scroll_checkbox.isChecked(),
            preview_preload_batch_size=max(0, int(self.preview_preload_batch_spin.value())),
            show_hidden_folders=self.show_hidden_folders_checkbox.isChecked(),
            auto_advance_enabled=self.auto_advance_checkbox.isChecked(),
            burst_groups_enabled=self.burst_groups_checkbox.isChecked(),
            burst_stacks_enabled=self.burst_stacks_checkbox.isChecked(),
            catalog_cache_enabled=self.catalog_cache_checkbox.isChecked(),
            watch_current_folder=self.watch_current_folder_checkbox.isChecked(),
            ai_auto_profile_enabled=self.ai_auto_profile_checkbox.isChecked(),
            ai_embed_batch_size=max(0, int(self.ai_embed_batch_size_spin.value())),
            ai_semantic_sidecar_enabled=self.ai_semantic_sidecar_checkbox.isChecked(),
            presets=tuple(self._presets) if include_presets else (),
        )
