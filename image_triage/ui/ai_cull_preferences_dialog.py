from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..dino_prefilter import DINOPrefilterMode, DINOPrefilterSettings
from ..phash_prefilter import PHashExecutionMode, PHashPrefilterSettings


@dataclass(slots=True, frozen=True)
class GuidedCullPreset:
    key: str
    label: str
    keep_top_percent: int
    review_band_percent: int
    detect_duplicates: bool = True
    detect_highlights: bool = True
    detect_blurry: bool = True
    detect_closed_eyes: bool = True
    dino_aggressiveness_percent: int = 85


@dataclass(slots=True, frozen=True)
class GuidedCullPreferences:
    category_key: str
    keep_top_percent: int
    review_band_percent: int
    base_score_weight_percent: int
    dino_prefilter_settings: DINOPrefilterSettings
    phash_prefilter_settings: PHashPrefilterSettings
    detect_highlights: bool
    detect_blurry: bool
    detect_closed_eyes: bool


GUIDED_CULL_PRESETS: tuple[GuidedCullPreset, ...] = (
    GuidedCullPreset("general", "Something Else", 10, 10, dino_aggressiveness_percent=88),
    GuidedCullPreset("weddings", "Weddings & Engagements", 18, 12, dino_aggressiveness_percent=92),
    GuidedCullPreset("portrait", "Portrait & Headshots", 14, 12, dino_aggressiveness_percent=90),
    GuidedCullPreset("family", "Family Portraits", 18, 12, dino_aggressiveness_percent=90),
    GuidedCullPreset("boudoir", "Boudoir Photography", 14, 12, dino_aggressiveness_percent=92),
    GuidedCullPreset("sports", "Sports Photography", 12, 10, dino_aggressiveness_percent=86),
    GuidedCullPreset("school_portrait", "School Portrait", 10, 8, dino_aggressiveness_percent=92),
    GuidedCullPreset("school_events", "School Events", 16, 12, dino_aggressiveness_percent=88),
    GuidedCullPreset("newborn", "Newborn Photography", 18, 12, dino_aggressiveness_percent=92),
    GuidedCullPreset("wildlife", "Wildlife & Action", 10, 10, dino_aggressiveness_percent=86),
    GuidedCullPreset("landscape", "Landscape & Travel", 12, 12, detect_closed_eyes=False, dino_aggressiveness_percent=90),
    GuidedCullPreset("architecture", "Architecture & Interiors", 12, 10, detect_closed_eyes=False, dino_aggressiveness_percent=90),
)


class GuidedAICullPreferencesDialog(QDialog):
    def __init__(
        self,
        *,
        folder_name: str,
        image_count: int,
        keep_top_percent: int,
        review_band_percent: int,
        base_score_weight_percent: int,
        dino_prefilter_settings: DINOPrefilterSettings,
        phash_prefilter_settings: PHashPrefilterSettings,
        face_quality_available: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("guidedAICullPreferencesDialog")
        self.setWindowTitle("AI Automated Cull Preferences")
        self.setModal(False)
        self.resize(520, 600)
        self._image_count = max(0, int(image_count))
        self._base_score_weight_percent = max(0, min(100, int(base_score_weight_percent)))
        self._initial_dino_settings = dino_prefilter_settings.normalized()
        self._initial_phash_settings = phash_prefilter_settings.normalized()
        self._face_quality_available = bool(face_quality_available)

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

        customize_card = self._card(root)
        customize_layout = QVBoxLayout(customize_card)
        customize_layout.setContentsMargins(12, 10, 12, 12)
        customize_layout.setSpacing(10)
        customize_title = QLabel("Customize", customize_card)
        customize_title.setObjectName("dialogTitle")
        customize_layout.addWidget(customize_title)

        self.detect_duplicates_checkbox = self._checkbox("Detect Duplicates", customize_card)
        self.detect_highlights_checkbox = self._checkbox("Detect Highlights", customize_card)
        self.detect_blurry_checkbox = self._checkbox("Detect Blurry Photos", customize_card)
        self.detect_closed_eyes_checkbox = self._checkbox("Detect Closed Eyes / Face Issues", customize_card)
        self.detect_closed_eyes_checkbox.setEnabled(False)
        self.closed_eyes_note = QLabel(
            "InsightFace models are installed." if self._face_quality_available else "Install InsightFace models to enable face issue checks.",
            customize_card,
        )
        self.closed_eyes_note.setObjectName("mutedText")

        customize_layout.addWidget(self.detect_duplicates_checkbox)
        customize_layout.addWidget(self.detect_highlights_checkbox)
        customize_layout.addWidget(self.detect_blurry_checkbox)
        customize_layout.addWidget(self.detect_closed_eyes_checkbox)
        customize_layout.addWidget(self.closed_eyes_note)

        blur_row = QGridLayout()
        blur_row.setContentsMargins(0, 4, 0, 0)
        blur_row.setHorizontalSpacing(10)
        self.blur_slider = QSlider(Qt.Orientation.Horizontal, customize_card)
        self.blur_slider.setRange(60, 98)
        self.blur_slider.setSingleStep(1)
        self.blur_slider.setPageStep(4)
        self.blur_slider.setValue(max(60, min(98, int(self._initial_dino_settings.aggressiveness_percent))))
        blur_row.addWidget(self.blur_slider, 0, 0, 1, 3)
        for column, label in enumerate(("LENIENT", "MODERATE", "STRICT")):
            text = QLabel(label, customize_card)
            text.setObjectName("mutedText")
            alignment = Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignCenter if column == 1 else Qt.AlignmentFlag.AlignRight
            text.setAlignment(alignment)
            blur_row.addWidget(text, 1, column)
        customize_layout.addLayout(blur_row)

        self.ratings_checkbox = self._checkbox("Use current AI Review buckets", customize_card)
        self.ratings_checkbox.setChecked(True)
        self.ratings_checkbox.setEnabled(False)
        customize_layout.addWidget(self.ratings_checkbox)

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
        self.blur_slider.valueChanged.connect(self._update_blur_enabled)
        self.detect_blurry_checkbox.toggled.connect(self._update_blur_enabled)
        self._apply_initial_values(keep_top_percent=keep_top_percent, review_band_percent=review_band_percent)
        self._update_start_state()

    def result_preferences(self) -> GuidedCullPreferences:
        keep = max(1, min(50, int(self.keep_slider.value())))
        review = self._review_band_for_current_preset()
        duplicates = self.detect_duplicates_checkbox.isChecked()
        highlights = self.detect_highlights_checkbox.isChecked()
        blurry = self.detect_blurry_checkbox.isChecked()
        closed_eyes = self.detect_closed_eyes_checkbox.isChecked() and self._face_quality_available

        dino_enabled = blurry
        dino_settings = DINOPrefilterSettings(
            enabled=dino_enabled,
            mode=DINOPrefilterMode.SOFT_QUARANTINE,
            aggressiveness_percent=max(1, min(100, int(self.blur_slider.value()))),
            technical_trash_enabled=blurry,
            duplicate_trash_enabled=False,
            phash_duplicate_enabled=False,
            phash_hamming_threshold=self._initial_dino_settings.phash_hamming_threshold,
            low_information_enabled=False,
            rescue_ai_high_score_enabled=True,
            rescue_user_keep_enabled=True,
            rescue_semantic_unique_enabled=True,
            rescue_best_representative_enabled=True,
            diagnostics_enabled=True,
        ).normalized()
        phash_settings = PHashPrefilterSettings(
            enabled=duplicates,
            mode=DINOPrefilterMode.SOFT_QUARANTINE,
            execution_mode=PHashExecutionMode.BEFORE_AI,
            hamming_threshold=self._initial_phash_settings.hamming_threshold,
            cache_enabled=self._initial_phash_settings.cache_enabled,
            diagnostics_enabled=True,
        ).normalized()
        base_score_weight = 65 if (highlights or blurry or closed_eyes) else 40
        return GuidedCullPreferences(
            category_key=str(self.category_combo.currentData() or "general"),
            keep_top_percent=keep,
            review_band_percent=review,
            base_score_weight_percent=base_score_weight,
            dino_prefilter_settings=dino_settings,
            phash_prefilter_settings=phash_settings,
            detect_highlights=highlights,
            detect_blurry=blurry,
            detect_closed_eyes=closed_eyes,
        )

    def _apply_initial_values(self, *, keep_top_percent: int, review_band_percent: int) -> None:
        self.keep_slider.setValue(max(1, min(50, int(keep_top_percent))))
        self.detect_duplicates_checkbox.setChecked(self._initial_phash_settings.enabled)
        self.detect_highlights_checkbox.setChecked(self._base_score_weight_percent >= 50)
        self.detect_blurry_checkbox.setChecked(
            self._initial_dino_settings.enabled and self._initial_dino_settings.technical_trash_enabled
        )
        self.detect_closed_eyes_checkbox.setChecked(self._face_quality_available)
        self._manual_review_band_percent = max(0, min(30, int(review_band_percent)))
        self._update_keep_summary()
        self._update_blur_enabled()

    def _apply_selected_preset(self) -> None:
        preset = self._current_preset()
        self.keep_slider.setValue(preset.keep_top_percent)
        self._manual_review_band_percent = preset.review_band_percent
        self.detect_duplicates_checkbox.setChecked(preset.detect_duplicates)
        self.detect_highlights_checkbox.setChecked(preset.detect_highlights)
        self.detect_blurry_checkbox.setChecked(preset.detect_blurry)
        self.detect_closed_eyes_checkbox.setChecked(preset.detect_closed_eyes and self._face_quality_available)
        self.blur_slider.setValue(preset.dino_aggressiveness_percent)
        self._update_keep_summary()
        self._update_blur_enabled()

    def _current_preset(self) -> GuidedCullPreset:
        key = str(self.category_combo.currentData() or "general")
        return next((preset for preset in GUIDED_CULL_PRESETS if preset.key == key), GUIDED_CULL_PRESETS[0])

    def _review_band_for_current_preset(self) -> int:
        return max(0, min(30, int(getattr(self, "_manual_review_band_percent", self._current_preset().review_band_percent))))

    def _update_keep_summary(self) -> None:
        keep = max(1, min(50, int(self.keep_slider.value())))
        self.keep_value_label.setText(f"{keep}% Amount of Selected Photos")
        estimated = round(self._image_count * keep / 100.0)
        self.keep_count_label.setText(f"about {estimated} of {self._image_count}" if self._image_count else "")

    def _update_blur_enabled(self) -> None:
        enabled = self.detect_blurry_checkbox.isChecked()
        self.blur_slider.setEnabled(enabled)

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
