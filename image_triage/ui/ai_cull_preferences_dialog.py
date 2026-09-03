from __future__ import annotations

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
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..phash_prefilter import PHashPrefilterSettings


@dataclass(slots=True, frozen=True)
class GuidedCullPreset:
    key: str
    label: str
    keep_top_percent: int
    review_band_percent: int
    detect_duplicates: bool = True


@dataclass(slots=True, frozen=True)
class GuidedCullPreferences:
    keep_top_percent: int
    review_band_percent: int
    phash_prefilter_settings: PHashPrefilterSettings


GUIDED_CULL_PRESETS: tuple[GuidedCullPreset, ...] = (
    GuidedCullPreset("general", "Something Else", 10, 10),
    GuidedCullPreset("weddings", "Weddings & Engagements", 18, 12),
    GuidedCullPreset("portrait", "Portrait & Headshots", 14, 12),
    GuidedCullPreset("family", "Family Portraits", 18, 12),
    GuidedCullPreset("boudoir", "Boudoir Photography", 14, 12),
    GuidedCullPreset("sports", "Sports Photography", 12, 10),
    GuidedCullPreset("school_portrait", "School Portrait", 10, 8),
    GuidedCullPreset("school_events", "School Events", 16, 12),
    GuidedCullPreset("newborn", "Newborn Photography", 18, 12),
    GuidedCullPreset("wildlife", "Wildlife & Action", 10, 10),
    GuidedCullPreset("landscape", "Landscape & Travel", 12, 12),
    GuidedCullPreset("architecture", "Architecture & Interiors", 12, 10),
)


class GuidedAICullPreferencesDialog(QDialog):
    def __init__(
        self,
        *,
        folder_name: str,
        image_count: int,
        keep_top_percent: int,
        review_band_percent: int,
        phash_prefilter_settings: PHashPrefilterSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("guidedAICullPreferencesDialog")
        self.setWindowTitle("AI Automated Cull Preferences")
        self.setModal(False)
        self.resize(520, 600)
        self._image_count = max(0, int(image_count))
        self._initial_phash_settings = phash_prefilter_settings.normalized()

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("AI Automated Cull Preferences", self)
        title.setObjectName("dialogTitle")
        title_font = title.font()
        title_font.setPointSize(max(title_font.pointSize() + 2, 12))
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title, 1)
        root.addLayout(header)

        if folder_name:
            folder_label = QLabel(folder_name, self)
            folder_label.setObjectName("mutedText")
            folder_label.setWordWrap(True)
            root.addWidget(folder_label)

        preset_label = QLabel("Starting preset", self)
        preset_label.setObjectName("dialogTitle")
        root.addWidget(preset_label)
        self.category_combo = QComboBox(self)
        for preset in GUIDED_CULL_PRESETS:
            self.category_combo.addItem(preset.label, preset.key)
        self.category_combo.setCurrentIndex(0)
        root.addWidget(self.category_combo)

        keep_card = self._card(root)
        keep_layout = QVBoxLayout(keep_card)
        keep_layout.setContentsMargins(12, 10, 12, 12)
        keep_layout.setSpacing(8)

        keep_header = QHBoxLayout()
        self.keep_value_label = QLabel("", keep_card)
        self.keep_value_label.setObjectName("dialogTitle")
        keep_header.addWidget(self.keep_value_label, 1)
        self.keep_count_label = QLabel("", keep_card)
        self.keep_count_label.setObjectName("mutedText")
        keep_header.addWidget(self.keep_count_label, 0)
        keep_layout.addLayout(keep_header)

        self.keep_slider = QSlider(Qt.Orientation.Horizontal, keep_card)
        self.keep_slider.setRange(1, 50)
        self.keep_slider.setSingleStep(1)
        self.keep_slider.setPageStep(5)
        self.keep_slider.setValue(max(1, min(50, int(keep_top_percent))))
        keep_layout.addWidget(self.keep_slider)

        scale_row = QHBoxLayout()
        for label in ("EXTREME", "FEW", "STANDARD", "MORE"):
            scale = QLabel(label, keep_card)
            scale.setObjectName("mutedText")
            scale_row.addWidget(scale, 1)
        keep_layout.addLayout(scale_row)

        review_card = self._card(root)
        review_layout = QVBoxLayout(review_card)
        review_layout.setContentsMargins(12, 10, 12, 12)
        review_layout.setSpacing(8)
        self.review_value_label = QLabel("", review_card)
        self.review_value_label.setObjectName("dialogTitle")
        review_layout.addWidget(self.review_value_label)
        self.review_slider = QSlider(Qt.Orientation.Horizontal, review_card)
        self.review_slider.setRange(0, 30)
        self.review_slider.setSingleStep(1)
        self.review_slider.setPageStep(5)
        self.review_slider.setValue(max(0, min(30, int(review_band_percent))))
        review_layout.addWidget(self.review_slider)
        review_note = QLabel(
            "Photos just below the keeper cutoff are held for your review instead of rejected.",
            review_card,
        )
        review_note.setObjectName("mutedText")
        review_note.setWordWrap(True)
        review_layout.addWidget(review_note)

        customize_card = self._card(root)
        customize_layout = QVBoxLayout(customize_card)
        customize_layout.setContentsMargins(12, 10, 12, 12)
        customize_layout.setSpacing(10)
        customize_title = QLabel("Included in every cull", customize_card)
        customize_title.setObjectName("dialogTitle")
        customize_layout.addWidget(customize_title)

        quality_note = QLabel(
            "CLIP ranks visual relevance and composition. TOPIQ checks technical quality, "
            "and InsightFace adds face and eye quality when faces are present.",
            customize_card,
        )
        quality_note.setObjectName("mutedText")
        quality_note.setWordWrap(True)
        customize_layout.addWidget(quality_note)
        self.detect_duplicates_checkbox = self._checkbox("Group near-duplicate photos", customize_card)
        customize_layout.addWidget(self.detect_duplicates_checkbox)

        root.addStretch(1)

        self.button_box = QDialogButtonBox(self)
        self.workflow_button = QPushButton("Open Workflow Center", self)
        self.button_box.addButton(self.workflow_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.close_button = self.button_box.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        self.start_button = self.button_box.addButton("Start Cull", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setDefault(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self.category_combo.currentIndexChanged.connect(self._apply_selected_preset)
        self.keep_slider.valueChanged.connect(self._update_keep_summary)
        self.review_slider.valueChanged.connect(self._update_review_summary)
        self._apply_initial_values(keep_top_percent=keep_top_percent, review_band_percent=review_band_percent)
        self._update_start_state()

    def result_preferences(self) -> GuidedCullPreferences:
        keep = max(1, min(50, int(self.keep_slider.value())))
        review = max(0, min(30, int(self.review_slider.value())))
        duplicates = self.detect_duplicates_checkbox.isChecked()
        phash_settings = PHashPrefilterSettings(
            enabled=duplicates,
            hamming_threshold=self._initial_phash_settings.hamming_threshold,
            cache_enabled=self._initial_phash_settings.cache_enabled,
            diagnostics_enabled=True,
        ).normalized()
        return GuidedCullPreferences(
            keep_top_percent=keep,
            review_band_percent=review,
            phash_prefilter_settings=phash_settings,
        )

    def _apply_initial_values(self, *, keep_top_percent: int, review_band_percent: int) -> None:
        self.keep_slider.setValue(max(1, min(50, int(keep_top_percent))))
        self.review_slider.setValue(max(0, min(30, int(review_band_percent))))
        self.detect_duplicates_checkbox.setChecked(self._initial_phash_settings.enabled)
        self._update_keep_summary()
        self._update_review_summary()

    def _apply_selected_preset(self) -> None:
        preset = self._current_preset()
        self.keep_slider.setValue(preset.keep_top_percent)
        self.review_slider.setValue(preset.review_band_percent)
        self.detect_duplicates_checkbox.setChecked(preset.detect_duplicates)
        self._update_keep_summary()
        self._update_review_summary()

    def _current_preset(self) -> GuidedCullPreset:
        key = str(self.category_combo.currentData() or "general")
        return next((preset for preset in GUIDED_CULL_PRESETS if preset.key == key), GUIDED_CULL_PRESETS[0])

    def _update_keep_summary(self) -> None:
        keep = max(1, min(50, int(self.keep_slider.value())))
        self.keep_value_label.setText(f"{keep}% Amount of Selected Photos")
        estimated = round(self._image_count * keep / 100.0)
        self.keep_count_label.setText(f"about {estimated} of {self._image_count}" if self._image_count else "")

    def _update_review_summary(self) -> None:
        review = max(0, min(30, int(self.review_slider.value())))
        self.review_value_label.setText(f"{review}% Needs Review band")

    def _update_start_state(self) -> None:
        self.start_button.setEnabled(self._image_count > 0)
        if self._image_count <= 0:
            self.start_button.setToolTip("Open a folder with images before starting a cull.")

    @staticmethod
    def _card(parent_layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        parent_layout.addWidget(card)
        return card

    @staticmethod
    def _checkbox(text: str, parent: QWidget) -> QCheckBox:
        checkbox = QCheckBox(text, parent)
        checkbox.setChecked(True)
        return checkbox
