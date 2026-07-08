from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
import textwrap

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .aiculler_workflow import (
    DEFAULT_CLIP_MODEL_VARIANT,
    clip_model_variant_info,
    clip_model_variant_options,
    coerce_clip_model_variant,
)
from .dino_prefilter import (
    DINOPrefilterMode,
    DINOPrefilterSettings,
    coerce_dino_prefilter_mode,
    default_dino_prefilter_settings,
    dino_prefilter_mode_label,
)
from .models import DeleteMode, WinnerMode
from .phash_prefilter import (
    PHashExecutionMode,
    PHashPrefilterSettings,
    coerce_phash_execution_mode,
    default_phash_prefilter_settings,
    phash_execution_mode_label,
)
from .ui.help_dialog import build_help_button, show_paged_help
from .ui.help_topics import settings_help_pages
from .ui.shortcuts import SHORTCUT_REGISTRY


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
    toolbar_style: str = "icons"
    loupe_card_style: str = "detailed"
    ui_gamma: float = 1.0
    free_smooth_scroll_enabled: bool = False
    preview_preload_batch_size: int = 10
    show_hidden_folders: bool = False
    auto_advance_enabled: bool = True
    burst_groups_enabled: bool = False
    burst_stacks_enabled: bool = False
    catalog_cache_enabled: bool = True
    watch_current_folder: bool = True
    check_updates_on_startup: bool = True
    ai_embed_batch_size: int = 0
    ai_clip_model_variant: str = DEFAULT_CLIP_MODEL_VARIANT
    ai_review_detail_progress_enabled: bool = False
    ai_dispute_weight: int = 3
    ai_keep_top_percent: int = 10       # % of folder to mark as Keeper
    ai_review_band_percent: int = 10    # % below Keeper cutoff to mark as Review
    ai_base_score_weight_percent: int = 65  # blend weight (0=adapter only, 100=base only)
    ai_label_near_duplicate_threshold: float = 0.965
    dino_prefilter_settings: DINOPrefilterSettings = field(default_factory=default_dino_prefilter_settings)
    phash_prefilter_settings: PHashPrefilterSettings = field(default_factory=default_phash_prefilter_settings)
    presets: tuple[WorkflowPreset, ...] = ()
    # Keybind overrides: attr_name -> chord string. Empty / missing entries
    # mean "use the registered default."
    shortcut_overrides: dict[str, str] = field(default_factory=dict)


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


def _settings_tooltip(text: str, *, width: int = 54) -> str:
    paragraphs = [part.strip() for part in str(text or "").splitlines()]
    wrapped: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            wrapped.append("")
            continue
        wrapped.extend(
            textwrap.wrap(
                paragraph,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(wrapped)


class WorkflowSettingsDialog(QDialog):
    def __init__(
        self,
        *,
        sessions: list[str],
        current_session: str,
        winner_mode: WinnerMode,
        delete_mode: DeleteMode,
        toolbar_style: str = "icons",
        loupe_card_style: str = "detailed",
        ui_gamma: float = 1.0,
        free_smooth_scroll_enabled: bool = False,
        preview_preload_batch_size: int = 10,
        show_hidden_folders: bool = False,
        auto_advance_enabled: bool = True,
        burst_groups_enabled: bool = False,
        burst_stacks_enabled: bool = False,
        catalog_cache_enabled: bool = True,
        watch_current_folder: bool = True,
        check_updates_on_startup: bool = True,
        ai_embed_batch_size: int = 0,
        ai_clip_model_variant: str = DEFAULT_CLIP_MODEL_VARIANT,
        ai_review_detail_progress_enabled: bool = False,
        ai_dispute_weight: int = 3,
        ai_keep_top_percent: int = 10,
        ai_review_band_percent: int = 10,
        ai_base_score_weight_percent: int = 65,
        ai_label_near_duplicate_threshold: float = 0.965,
        dino_prefilter_settings: DINOPrefilterSettings | None = None,
        phash_prefilter_settings: PHashPrefilterSettings | None = None,
        catalog_summary_text: str = "",
        presets: list[WorkflowPreset] | None = None,
        preset_save_callback: Callable[[tuple[WorkflowPreset, ...]], None] | None = None,
        file_associations_callback: Callable[[], None] | None = None,
        keyboard_shortcuts_callback: Callable[[], None] | None = None,
        toolbar_callback: Callable[[], None] | None = None,
        reset_layout_callback: Callable[[], None] | None = None,
        shortcut_overrides: dict[str, str] | None = None,
        initial_section: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(640, 460)
        self.resize(720, 540)
        self._presets = list(presets or [])
        self._preset_save_callback = preset_save_callback
        self._updating_session = False
        dino_settings = (dino_prefilter_settings or default_dino_prefilter_settings()).normalized()
        phash_settings = (phash_prefilter_settings or default_phash_prefilter_settings()).normalized()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.section_list = QListWidget(body)
        self.section_list.setObjectName("settingsSectionList")
        self.section_list.setFixedWidth(168)
        self.section_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.section_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.section_list.setFrameShape(QFrame.Shape.NoFrame)
        self.section_list.setSpacing(2)

        self.pages = QStackedWidget(body)
        self.pages.setObjectName("settingsPages")
        self.pages.setMinimumWidth(480)
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
        self.session_combo.setToolTip(_settings_tooltip(
            "Named workspace preset used for review behavior like accepted-image handling and delete behavior."
        ))
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
        self.winner_mode_combo.setToolTip(_settings_tooltip(
            "What happens when you accept an image as a winner."
        ))

        self.delete_mode_combo = QComboBox()
        self.delete_mode_combo.setMinimumWidth(240)
        self.delete_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for mode in DeleteMode:
            self.delete_mode_combo.addItem(mode.value, mode)
        self.delete_mode_combo.setCurrentIndex(max(0, self.delete_mode_combo.findData(delete_mode)))
        self.delete_mode_combo.setToolTip(_settings_tooltip(
            "Where rejected or deleted images go when you delete from Image Triage."
        ))

        self.check_updates_on_startup_checkbox = QCheckBox("Check for app updates on startup")
        self.check_updates_on_startup_checkbox.setChecked(check_updates_on_startup)
        self.check_updates_on_startup_checkbox.setToolTip(_settings_tooltip(
            "Checks the configured GitHub release feed when Image Triage starts. If a newer MSI is available, the top-right download button lights up."
        ))

        self.toolbar_style_combo = QComboBox()
        self.toolbar_style_combo.setMinimumWidth(180)
        self.toolbar_style_combo.addItem("Icons", "icons")
        self.toolbar_style_combo.addItem("Large icons", "large_icons")
        self.toolbar_style_combo.addItem("Icons & Labels", "icon_text")
        self.toolbar_style_combo.addItem("Text", "text")
        toolbar_index = self.toolbar_style_combo.findData(toolbar_style)
        self.toolbar_style_combo.setCurrentIndex(max(0, toolbar_index))
        self.toolbar_style_combo.setToolTip(_settings_tooltip(
            "Choose whether toolbar buttons show text, icons, larger icons, or icons with labels."
        ))

        session_row = QWidget()
        session_row.setToolTip(_settings_tooltip(
            "Choose or name the settings preset used for this review session."
        ))
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(8)
        session_layout.addWidget(self.session_combo)
        self.save_preset_button = QPushButton("Save Preset")
        self.save_preset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.save_preset_button.setToolTip(_settings_tooltip(
            "Save the current General settings under the selected session name."
        ))
        self.save_preset_button.clicked.connect(self._save_current_preset)
        session_layout.addWidget(self.save_preset_button)
        session_layout.addStretch(1)

        general_page, general_layout = self._build_settings_page("General")
        self._add_form_row(general_layout, "Session", session_row)
        self._add_form_row(general_layout, "Accepted images", self.winner_mode_combo)
        self._add_form_row(general_layout, "Delete behavior", self.delete_mode_combo)
        self._add_checkbox_row(general_layout, "Updates", self.check_updates_on_startup_checkbox)
        self.preset_status_label = QLabel("")
        self.preset_status_label.setObjectName("mutedText")
        self.preset_status_label.setStyleSheet("font-size: 11px;")
        general_layout.addWidget(self.preset_status_label)
        general_layout.addStretch(1)
        self._add_settings_page("General", general_page)

        self.loupe_card_style_combo = QComboBox()
        self.loupe_card_style_combo.setMinimumWidth(180)
        self.loupe_card_style_combo.addItem("Detailed", "detailed")
        self.loupe_card_style_combo.addItem("Immersive", "immersive")
        self.loupe_card_style_combo.addItem("Zen", "zen")
        self.loupe_card_style_combo.addItem("Classic", "classic")
        loupe_style_index = self.loupe_card_style_combo.findData(loupe_card_style)
        self.loupe_card_style_combo.setCurrentIndex(max(0, loupe_style_index))
        self.loupe_card_style_combo.setToolTip(_settings_tooltip(
            "Card style. Detailed shows the full review card (filename, EXIF, status) up to "
            "4 columns, then collapses to the minimal card at higher column counts; the "
            "single-image view keeps the metadata strip below the photo. Immersive always "
            "uses the minimal photo-first card and paints metadata over the photo's edge. "
            "Zen strips everything away: just the photos and the selection ring. Classic is "
            "the original boxed card with the caption and metadata rows below the photo."
        ))

        self.ui_gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.ui_gamma_slider.setRange(60, 160)
        self.ui_gamma_slider.setSingleStep(5)
        self.ui_gamma_slider.setPageStep(10)
        self.ui_gamma_slider.setValue(round(max(0.60, min(1.60, float(ui_gamma))) * 100))
        self.ui_gamma_slider.setMinimumWidth(160)
        self.ui_gamma_slider.setToolTip(_settings_tooltip(
            "Brightens or darkens the whole interface to compensate for monitor "
            "differences. Values above 1.00 lift the dark tones; 1.00 is the "
            "designed appearance. Applies when you save."
        ))
        self.ui_gamma_value_label = QLabel(f"{self.ui_gamma_slider.value() / 100:.2f}")
        self.ui_gamma_value_label.setMinimumWidth(34)
        reset_gamma_button = QPushButton("Reset")
        reset_gamma_button.clicked.connect(lambda: self.ui_gamma_slider.setValue(100))
        self.ui_gamma_slider.valueChanged.connect(
            lambda value: self.ui_gamma_value_label.setText(f"{value / 100:.2f}")
        )
        self.ui_gamma_row = QWidget()
        ui_gamma_layout = QHBoxLayout(self.ui_gamma_row)
        ui_gamma_layout.setContentsMargins(0, 0, 0, 0)
        ui_gamma_layout.setSpacing(8)
        ui_gamma_layout.addWidget(self.ui_gamma_slider, 1)
        ui_gamma_layout.addWidget(self.ui_gamma_value_label)
        ui_gamma_layout.addWidget(reset_gamma_button)

        self.free_smooth_scroll_checkbox = QCheckBox("Use free smooth scrolling")
        self.free_smooth_scroll_checkbox.setChecked(free_smooth_scroll_enabled)
        self.free_smooth_scroll_checkbox.setToolTip(_settings_tooltip(
            "Allows smoother pixel-by-pixel scrolling instead of snapping by rows."
        ))

        self.preview_preload_batch_spin = QSpinBox()
        self.preview_preload_batch_spin.setRange(0, 128)
        self.preview_preload_batch_spin.setSingleStep(2)
        self.preview_preload_batch_spin.setSpecialValueText("Off")
        self.preview_preload_batch_spin.setSuffix(" images")
        self.preview_preload_batch_spin.setValue(max(0, min(128, int(preview_preload_batch_size))))
        self.preview_preload_batch_spin.setMinimumWidth(120)
        self.preview_preload_batch_spin.setToolTip(_settings_tooltip(
            "Nearby images to preload while using the popout preview. Higher values can improve rapid navigation but use more CPU and RAM."
        ))

        self.show_hidden_folders_checkbox = QCheckBox("Show hidden folders")
        self.show_hidden_folders_checkbox.setChecked(show_hidden_folders)
        self.show_hidden_folders_checkbox.setToolTip(_settings_tooltip(
            "Shows dot folders and hidden folders in the folder browser."
        ))

        self.auto_advance_checkbox = QCheckBox("Advance after Accept or Reject")
        self.auto_advance_checkbox.setChecked(auto_advance_enabled)
        self.auto_advance_checkbox.setToolTip(_settings_tooltip(
            "Moves to the next image automatically after you accept or reject the current one."
        ))

        self.burst_groups_checkbox = QCheckBox("Group burst sequences")
        self.burst_groups_checkbox.setChecked(burst_groups_enabled)
        self.burst_groups_checkbox.setToolTip(_settings_tooltip(
            "Groups likely capture bursts so related frames are easier to review together."
        ))

        self.burst_stacks_checkbox = QCheckBox("Stack similar burst frames")
        self.burst_stacks_checkbox.setChecked(burst_stacks_enabled)
        self.burst_stacks_checkbox.setToolTip(_settings_tooltip(
            "Stacks very similar burst frames behind one visible representative in the grid."
        ))

        interface_page, interface_layout = self._build_settings_page("Interface")
        self._add_form_row(interface_layout, "Toolbar", self.toolbar_style_combo)
        self._add_form_row(interface_layout, "Card style", self.loupe_card_style_combo)
        self._add_form_row(interface_layout, "UI gamma", self.ui_gamma_row)
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
        self.catalog_cache_checkbox.setToolTip(_settings_tooltip(
            "Stores lightweight folder information so previously indexed folders open faster."
        ))

        self.watch_current_folder_checkbox = QCheckBox("Refresh the open folder when files change on disk")
        self.watch_current_folder_checkbox.setChecked(watch_current_folder)
        self.watch_current_folder_checkbox.setToolTip(_settings_tooltip(
            "Automatically refreshes the current folder when files are added, removed, or renamed outside the app."
        ))

        self.catalog_summary_label = QLabel(_compact_catalog_summary(catalog_summary_text))
        self.catalog_summary_label.setWordWrap(True)
        self.catalog_summary_label.setObjectName("mutedText")
        self.catalog_summary_label.setStyleSheet("font-size: 11px;")
        self.catalog_summary_label.setToolTip(_settings_tooltip(
            "Current catalog cache status and indexed-file summary."
        ))
        folders_page, folders_layout = self._build_settings_page("Library & Folders")
        self._add_checkbox_row(folders_layout, "Catalog cache", self.catalog_cache_checkbox)
        self._add_checkbox_row(folders_layout, "Watch folder", self.watch_current_folder_checkbox)
        self._add_text_row(folders_layout, "Catalog", self.catalog_summary_label)
        folders_layout.addStretch(1)
        self._add_settings_page("Library & Folders", folders_page)

        self.ai_embed_batch_size_spin = QSpinBox()
        self.ai_embed_batch_size_spin.setRange(0, 64)
        self.ai_embed_batch_size_spin.setSingleStep(1)
        self.ai_embed_batch_size_spin.setSpecialValueText("Auto")
        self.ai_embed_batch_size_spin.setValue(max(0, int(ai_embed_batch_size)))
        self.ai_embed_batch_size_spin.setMinimumWidth(120)
        self.ai_embed_batch_size_spin.setToolTip(_settings_tooltip(
            "Concurrent workers in CLI-Culler's ingest pipeline. Both stages "
            "(preview extraction + CLIP/TOPIQ feature extraction) get this "
            "many threads each. Auto picks a balanced default for your "
            "hardware (4 on CPU, 8 on GPU). Higher values speed up ingest "
            "up to your CPU core count; very high values can oversubscribe "
            "ONNX's internal thread pool."
        ))

        self.ai_clip_model_combo = QComboBox()
        self.ai_clip_model_combo.setMinimumWidth(260)
        for variant in clip_model_variant_options():
            self.ai_clip_model_combo.addItem(variant.label, variant.key)
            index = self.ai_clip_model_combo.count() - 1
            self.ai_clip_model_combo.setItemData(
                index,
                _settings_tooltip(f"{variant.description}\n{variant.expected_delta}"),
                Qt.ItemDataRole.ToolTipRole,
            )
        selected_clip_variant = coerce_clip_model_variant(ai_clip_model_variant)
        self.ai_clip_model_combo.setCurrentIndex(
            max(0, self.ai_clip_model_combo.findData(selected_clip_variant))
        )
        self.ai_clip_model_combo.setToolTip(_settings_tooltip(
            "Select the paired CLIP vision/text ONNX export used by CLI-Culler ingest and category scoring."
        ))
        self.ai_clip_model_warning_label = QLabel("")
        self.ai_clip_model_warning_label.setWordWrap(True)
        self.ai_clip_model_warning_label.setObjectName("mutedText")
        self.ai_clip_model_warning_label.setStyleSheet("font-size: 11px; color: #d0a85c;")
        self.ai_clip_model_warning_label.setToolTip(_settings_tooltip(
            "Shows cautions for the selected CLIP model version."
        ))
        self.ai_clip_model_combo.currentIndexChanged.connect(self._update_ai_clip_model_summary)

        self.ai_review_detail_progress_checkbox = QCheckBox("Show detailed AI Review activity")
        self.ai_review_detail_progress_checkbox.setChecked(ai_review_detail_progress_enabled)
        self.ai_review_detail_progress_checkbox.setToolTip(_settings_tooltip(
            "Shows model loading, library loading, and per-stage technical activity in the AI Review progress window."
        ))

        self.ai_dispute_weight_spin = QSpinBox()
        self.ai_dispute_weight_spin.setRange(2, 5)
        self.ai_dispute_weight_spin.setSingleStep(1)
        self.ai_dispute_weight_spin.setSuffix("x")
        self.ai_dispute_weight_spin.setValue(max(2, min(5, int(ai_dispute_weight))))
        self.ai_dispute_weight_spin.setMinimumWidth(120)
        self.ai_dispute_weight_spin.setToolTip(_settings_tooltip(
            "How heavily a disputed image counts in adapter training relative "
            "to a normal label. 3x means each dispute is worth three normal "
            "labels. Higher values let disputes correct the model faster but "
            "make a few mis-clicks louder."
        ))

        # Cull aggressiveness: two spinners that together determine the bucket
        # distribution. The auto-derived label below them tells the user what
        # the rest of the folder becomes (Reject = 100 - keep_top - review_band).
        self.ai_keep_top_spin = QSpinBox()
        self.ai_keep_top_spin.setRange(1, 50)
        self.ai_keep_top_spin.setSingleStep(1)
        self.ai_keep_top_spin.setSuffix("%")
        self.ai_keep_top_spin.setValue(max(1, min(50, int(ai_keep_top_percent))))
        self.ai_keep_top_spin.setMinimumWidth(120)
        self.ai_keep_top_spin.setToolTip(_settings_tooltip(
            "Top percentile of the folder marked as Keeper. 10% means the AI "
            "passes through roughly the top 10% of images as 'Likely Keeper' "
            "after each run."
        ))

        self.ai_review_band_spin = QSpinBox()
        self.ai_review_band_spin.setRange(0, 30)
        self.ai_review_band_spin.setSingleStep(1)
        self.ai_review_band_spin.setSuffix("%")
        self.ai_review_band_spin.setValue(max(0, min(30, int(ai_review_band_percent))))
        self.ai_review_band_spin.setMinimumWidth(120)
        self.ai_review_band_spin.setToolTip(_settings_tooltip(
            "Additional band of close-to-keeper images marked as 'Needs "
            "Review' (sitting just below the Keeper cutoff). Set to 0 to "
            "disable the Review band entirely so every card is either Keeper "
            "or Reject."
        ))

        self.ai_cull_summary_label = QLabel("")
        self.ai_cull_summary_label.setObjectName("mutedText")
        self.ai_cull_summary_label.setToolTip(_settings_tooltip(
            "Shows how the Keep top and Review band settings split the folder into keep, review, and reject buckets."
        ))
        self.ai_keep_top_spin.valueChanged.connect(self._update_ai_cull_summary)
        self.ai_review_band_spin.valueChanged.connect(self._update_ai_cull_summary)

        # Blend weight between the tag-penalty-aware base score and the
        # adapter's prediction. Higher = the base score wins (tag penalties
        # for blur / blown highlights / etc. carry more weight). Lower = the
        # learned adapter dominates. 100% lets penalized images never escape
        # Reject; 0% effectively disables the penalty system.
        self.ai_base_score_weight_spin = QSpinBox()
        self.ai_base_score_weight_spin.setRange(0, 100)
        self.ai_base_score_weight_spin.setSingleStep(5)
        self.ai_base_score_weight_spin.setSuffix("%")
        self.ai_base_score_weight_spin.setValue(max(0, min(100, int(ai_base_score_weight_percent))))
        self.ai_base_score_weight_spin.setMinimumWidth(120)
        self.ai_base_score_weight_spin.setToolTip(_settings_tooltip(
            "Weight of the tag-penalty-aware base score vs. the trained "
            "adapter when blending the final ranking. 100% = base score wins "
            "outright (heavily penalized images can never pass as Keeper); "
            "0% = adapter only (tag penalties for blur / blown / harsh light "
            "have no effect). Default 65% favors the base score so the "
            "negative prompts stay authoritative, while still letting the "
            "adapter influence borderline calls."
        ))

        self.ai_label_near_duplicate_slider = QSpinBox()
        self.ai_label_near_duplicate_slider.setRange(500, 995)
        self.ai_label_near_duplicate_slider.setSingleStep(5)
        self.ai_label_near_duplicate_slider.setValue(max(500, min(995, int(round(float(ai_label_near_duplicate_threshold) * 1000)))))
        self.ai_label_near_duplicate_slider.setMinimumWidth(120)
        self.ai_label_near_duplicate_slider.setToolTip(_settings_tooltip(
            "Similarity threshold used by legacy adapter label grouping helpers. "
            "Higher values require images to be closer before they are treated as near-duplicates."
        ))

        ai_page, ai_layout = self._build_settings_page("AI")
        self._add_form_row(ai_layout, "Processing workers", self.ai_embed_batch_size_spin)
        self._add_form_row(ai_layout, "CLIP model version", self.ai_clip_model_combo)
        self._add_text_row(ai_layout, "Model notes", self.ai_clip_model_warning_label)
        self._add_checkbox_row(ai_layout, "Detailed progress log", self.ai_review_detail_progress_checkbox)
        self._add_form_row(ai_layout, "Keep top", self.ai_keep_top_spin)
        self._add_form_row(ai_layout, "Review band", self.ai_review_band_spin)
        self._add_form_row(ai_layout, "Cull breakdown", self.ai_cull_summary_label)
        self._add_form_row(ai_layout, "Base score influence", self.ai_base_score_weight_spin)
        self._add_form_row(ai_layout, "Dispute label weight", self.ai_dispute_weight_spin)
        self._add_form_row(ai_layout, "Legacy duplicate threshold", self.ai_label_near_duplicate_slider)
        ai_layout.addStretch(1)
        self._update_ai_clip_model_summary()
        self._update_ai_cull_summary()
        self._add_settings_page("AI", ai_page)

        self.dino_prefilter_enabled_checkbox = QCheckBox("Enable DINO Prefilter")
        self.dino_prefilter_enabled_checkbox.setChecked(dino_settings.enabled)
        self.dino_prefilter_enabled_checkbox.setToolTip(_settings_tooltip(
            "Runs a base-model DINO visual screen before the AI Culler. Off keeps the current AI workflow unchanged."
        ))

        self.dino_prefilter_mode_combo = QComboBox()
        self.dino_prefilter_mode_combo.setMinimumWidth(220)
        for mode in DINOPrefilterMode:
            self.dino_prefilter_mode_combo.addItem(dino_prefilter_mode_label(mode), mode)
        self.dino_prefilter_mode_combo.setCurrentIndex(
            max(0, self.dino_prefilter_mode_combo.findData(dino_settings.mode))
        )
        self.dino_prefilter_mode_combo.setToolTip(_settings_tooltip(
            "Soft Quarantine marks candidates while keeping them in the AI pool. "
            "Pool Removal excludes candidates from downstream AI scoring while keeping them visible in review."
        ))

        self.dino_prefilter_aggressiveness_spin = QSpinBox()
        self.dino_prefilter_aggressiveness_spin.setRange(1, 100)
        self.dino_prefilter_aggressiveness_spin.setSingleStep(1)
        self.dino_prefilter_aggressiveness_spin.setSuffix("%")
        self.dino_prefilter_aggressiveness_spin.setValue(dino_settings.aggressiveness_percent)
        self.dino_prefilter_aggressiveness_spin.setMinimumWidth(120)
        self.dino_prefilter_aggressiveness_spin.setToolTip(_settings_tooltip(
            "How confident DINO must be before it marks an image as trash. "
            "Higher is more conservative."
        ))

        self.dino_technical_trash_checkbox = QCheckBox("Technical trash")
        self.dino_technical_trash_checkbox.setChecked(dino_settings.technical_trash_enabled)
        self.dino_technical_trash_checkbox.setToolTip(_settings_tooltip(
            "Allows DINO to mark images with obvious technical failures such as blur or unusable exposure."
        ))
        self.dino_duplicate_trash_checkbox = QCheckBox("Duplicate trash")
        self.dino_duplicate_trash_checkbox.setChecked(dino_settings.duplicate_trash_enabled)
        self.dino_duplicate_trash_checkbox.setToolTip(_settings_tooltip(
            "Allows DINO to mark redundant images when a better representative exists."
        ))
        self.dino_low_information_checkbox = QCheckBox("Low-information filler")
        self.dino_low_information_checkbox.setChecked(dino_settings.low_information_enabled)
        self.dino_low_information_checkbox.setToolTip(_settings_tooltip(
            "Allows DINO to mark low-content filler frames that are unlikely to be useful."
        ))

        self.dino_rescue_ai_high_score_checkbox = QCheckBox("Rescue if current AI scores high")
        self.dino_rescue_ai_high_score_checkbox.setChecked(dino_settings.rescue_ai_high_score_enabled)
        self.dino_rescue_ai_high_score_checkbox.setToolTip(_settings_tooltip(
            "Keeps a DINO-marked image in the pool if the current AI score says it may be strong."
        ))
        self.dino_rescue_user_keep_checkbox = QCheckBox("Rescue if adapter or user label says keep")
        self.dino_rescue_user_keep_checkbox.setChecked(dino_settings.rescue_user_keep_enabled)
        self.dino_rescue_user_keep_checkbox.setToolTip(_settings_tooltip(
            "Keeps a DINO-marked image in the pool if your labels or adapter indicate it should be kept."
        ))
        self.dino_rescue_semantic_unique_checkbox = QCheckBox("Rescue if semantically unique")
        self.dino_rescue_semantic_unique_checkbox.setChecked(dino_settings.rescue_semantic_unique_enabled)
        self.dino_rescue_semantic_unique_checkbox.setToolTip(_settings_tooltip(
            "Keeps a DINO-marked image if it is visually or semantically different from the surrounding set."
        ))
        self.dino_rescue_best_representative_checkbox = QCheckBox("Rescue if best visual representative")
        self.dino_rescue_best_representative_checkbox.setChecked(dino_settings.rescue_best_representative_enabled)
        self.dino_rescue_best_representative_checkbox.setToolTip(_settings_tooltip(
            "Keeps the strongest representative from a similar-image group even when other frames are dumped."
        ))

        self.dino_diagnostics_checkbox = QCheckBox("Write per-run diagnostics and audit rows")
        self.dino_diagnostics_checkbox.setChecked(dino_settings.diagnostics_enabled)
        self.dino_diagnostics_checkbox.setToolTip(_settings_tooltip(
            "Writes per-run DINO decisions and reason counts for debugging and threshold tuning."
        ))

        dino_page, dino_layout = self._build_settings_page("DINO Prefilter")
        dino_hint = QLabel(
            "DINO Prefilter uses the base DINO model only. It is an optional first-pass screen for obvious trash and redundancy; it does not train, adapt, or decide final keeps."
        )
        dino_hint.setWordWrap(True)
        dino_hint.setObjectName("settingsRowLabel")
        dino_layout.addWidget(dino_hint)
        dino_layout.addSpacing(4)
        self._add_checkbox_row(dino_layout, "DINO Prefilter", self.dino_prefilter_enabled_checkbox)
        self._add_form_row(dino_layout, "Marking mode", self.dino_prefilter_mode_combo)
        self._add_form_row(dino_layout, "Trash confidence", self.dino_prefilter_aggressiveness_spin)
        reason_heading = QLabel("Allowed trash reasons")
        reason_heading.setObjectName("settingsCategoryHeading")
        dino_layout.addSpacing(8)
        dino_layout.addWidget(reason_heading)
        self._add_checkbox_row(dino_layout, "Technical", self.dino_technical_trash_checkbox)
        self._add_checkbox_row(dino_layout, "Duplicates", self.dino_duplicate_trash_checkbox)
        self._add_checkbox_row(dino_layout, "Low information", self.dino_low_information_checkbox)
        rescue_heading = QLabel("Rescue rules")
        rescue_heading.setObjectName("settingsCategoryHeading")
        dino_layout.addSpacing(8)
        dino_layout.addWidget(rescue_heading)
        self._add_checkbox_row(dino_layout, "AI score", self.dino_rescue_ai_high_score_checkbox)
        self._add_checkbox_row(dino_layout, "User labels", self.dino_rescue_user_keep_checkbox)
        self._add_checkbox_row(dino_layout, "Uniqueness", self.dino_rescue_semantic_unique_checkbox)
        self._add_checkbox_row(dino_layout, "Representative", self.dino_rescue_best_representative_checkbox)
        diagnostics_heading = QLabel("Diagnostics")
        diagnostics_heading.setObjectName("settingsCategoryHeading")
        dino_layout.addSpacing(8)
        dino_layout.addWidget(diagnostics_heading)
        self._add_checkbox_row(dino_layout, "Run diagnostics", self.dino_diagnostics_checkbox)
        dino_layout.addStretch(1)
        self._dino_dependent_controls = (
            self.dino_prefilter_mode_combo,
            self.dino_prefilter_aggressiveness_spin,
            self.dino_technical_trash_checkbox,
            self.dino_duplicate_trash_checkbox,
            self.dino_low_information_checkbox,
            self.dino_rescue_ai_high_score_checkbox,
            self.dino_rescue_user_keep_checkbox,
            self.dino_rescue_semantic_unique_checkbox,
            self.dino_rescue_best_representative_checkbox,
            self.dino_diagnostics_checkbox,
        )
        self.dino_prefilter_enabled_checkbox.toggled.connect(self._set_dino_prefilter_controls_enabled)
        self._set_dino_prefilter_controls_enabled(self.dino_prefilter_enabled_checkbox.isChecked())
        self._add_settings_page("DINO Prefilter", dino_page)

        self.phash_prefilter_enabled_checkbox = QCheckBox("Enable pHash Prefilter")
        self.phash_prefilter_enabled_checkbox.setChecked(phash_settings.enabled)
        self.phash_prefilter_enabled_checkbox.setToolTip(_settings_tooltip(
            "Runs a perceptual hash duplicate pass independent of DINO. "
            "This catches tight visual repeats and near-identical frames."
        ))
        self.phash_prefilter_mode_combo = QComboBox()
        self.phash_prefilter_mode_combo.setMinimumWidth(220)
        for mode in DINOPrefilterMode:
            self.phash_prefilter_mode_combo.addItem(dino_prefilter_mode_label(mode), mode)
        self.phash_prefilter_mode_combo.setCurrentIndex(
            max(0, self.phash_prefilter_mode_combo.findData(phash_settings.mode))
        )
        self.phash_prefilter_mode_combo.setToolTip(_settings_tooltip(
            "Soft Quarantine labels duplicates; Pool Removal keeps duplicate candidates out of the main AI scoring pool."
        ))
        self.phash_execution_mode_combo = QComboBox()
        self.phash_execution_mode_combo.setMinimumWidth(220)
        for mode in PHashExecutionMode:
            self.phash_execution_mode_combo.addItem(phash_execution_mode_label(mode), mode)
        self.phash_execution_mode_combo.setCurrentIndex(
            max(0, self.phash_execution_mode_combo.findData(phash_settings.execution_mode))
        )
        self.phash_execution_mode_combo.setToolTip(_settings_tooltip(
            "Before AI scoring can remove duplicates from the current pool. "
            "Async with DINO overlaps both prefilters. "
            "Async with main AI only annotates the current run because ingest has already started."
        ))
        self.phash_hamming_spin = QSpinBox()
        self.phash_hamming_spin.setRange(0, 64)
        self.phash_hamming_spin.setSingleStep(1)
        self.phash_hamming_spin.setValue(phash_settings.hamming_threshold)
        self.phash_hamming_spin.setMinimumWidth(120)
        self.phash_hamming_spin.setToolTip(_settings_tooltip(
            "Maximum pHash Hamming distance treated as a duplicate. "
            "Lower is stricter. 6 catches tight visual repeats while avoiding broad pose changes."
        ))
        self.phash_cache_checkbox = QCheckBox("Cache pHash metadata")
        self.phash_cache_checkbox.setChecked(phash_settings.cache_enabled)
        self.phash_cache_checkbox.setToolTip(_settings_tooltip(
            "Stores hash values only. It does not copy or cache image files."
        ))
        self.phash_diagnostics_checkbox = QCheckBox("Write per-run diagnostics and audit rows")
        self.phash_diagnostics_checkbox.setChecked(phash_settings.diagnostics_enabled)
        self.phash_diagnostics_checkbox.setToolTip(_settings_tooltip(
            "Writes per-run pHash duplicate groups and decision rows for debugging and threshold tuning."
        ))

        phash_page, phash_layout = self._build_settings_page("pHash Prefilter")
        phash_hint = QLabel(
            "pHash Prefilter is independent of DINO. It detects tight visual duplicates using hash metadata only; it does not train or copy images."
        )
        phash_hint.setWordWrap(True)
        phash_hint.setObjectName("settingsRowLabel")
        phash_layout.addWidget(phash_hint)
        phash_layout.addSpacing(4)
        self._add_checkbox_row(phash_layout, "pHash Prefilter", self.phash_prefilter_enabled_checkbox)
        self._add_form_row(phash_layout, "Marking mode", self.phash_prefilter_mode_combo)
        self._add_form_row(phash_layout, "Run timing", self.phash_execution_mode_combo)
        self._add_form_row(phash_layout, "Duplicate distance", self.phash_hamming_spin)
        self._add_checkbox_row(phash_layout, "Cache hash metadata", self.phash_cache_checkbox)
        self._add_checkbox_row(phash_layout, "Run diagnostics", self.phash_diagnostics_checkbox)
        phash_layout.addStretch(1)
        self._phash_dependent_controls = (
            self.phash_prefilter_mode_combo,
            self.phash_execution_mode_combo,
            self.phash_hamming_spin,
            self.phash_cache_checkbox,
            self.phash_diagnostics_checkbox,
        )
        self.phash_prefilter_enabled_checkbox.toggled.connect(self._set_phash_prefilter_controls_enabled)
        self._set_phash_prefilter_controls_enabled(self.phash_prefilter_enabled_checkbox.isChecked())
        self._add_settings_page("pHash Prefilter", phash_page)

        shortcuts_page = self._build_shortcuts_page(shortcut_overrides or {})
        self._add_settings_page("Shortcuts", shortcuts_page)

        footer = QFrame(self)
        footer.setObjectName("settingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 14)
        footer_layout.setSpacing(8)
        help_button = build_help_button(self, tooltip="Open settings help")
        help_button.clicked.connect(self._show_help)
        footer_layout.addWidget(help_button, 0)
        footer_layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer_layout.addWidget(buttons)
        root_layout.addWidget(footer, 0)
        self.section_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.section_list.setCurrentRow(0)
        if initial_section:
            self._select_section(initial_section)
        self._refresh_preset_dropdown()

    def _show_help(self) -> None:
        show_paged_help(
            self,
            title="Settings Help",
            pages=settings_help_pages(),
        )

    def _build_settings_page(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("settingsPageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(10)
        content.setMinimumWidth(420)
        title_label = QLabel(title)
        title_label.setObjectName("settingsPageTitle")
        layout.addWidget(title_label)
        # Separator below the title
        separator = QFrame()
        separator.setObjectName("settingsPageSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setFixedHeight(1)
        layout.addWidget(separator)
        layout.addSpacing(4)
        scroll.setWidget(content)
        return scroll, layout

    def _add_settings_page(self, title: str, page: QWidget) -> None:
        item = QListWidgetItem(title)
        self.section_list.addItem(item)
        self.pages.addWidget(page)

    def _select_section(self, title: str) -> None:
        target = title.strip().casefold()
        if not target:
            return
        for row in range(self.section_list.count()):
            item = self.section_list.item(row)
            if item is not None and item.text().strip().casefold() == target:
                self.section_list.setCurrentRow(row)
                return

    def _row_frame(self) -> tuple[QWidget, QHBoxLayout]:
        row = QWidget()
        row.setObjectName("settingsRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        return row, layout

    _ROW_LABEL_WIDTH = 132

    def _add_form_row(self, layout: QVBoxLayout, label_text: str, field: QWidget) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(self._ROW_LABEL_WIDTH)
        label.setObjectName("settingsRowLabel")
        tooltip = field.toolTip()
        if tooltip:
            label.setToolTip(tooltip)
            row.setToolTip(tooltip)
        row_layout.addWidget(label)
        row_layout.addWidget(field, 1)
        layout.addWidget(row)

    def _add_checkbox_row(self, layout: QVBoxLayout, label_text: str, checkbox: QCheckBox) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(self._ROW_LABEL_WIDTH)
        label.setObjectName("settingsRowLabel")
        tooltip = checkbox.toolTip()
        if tooltip:
            label.setToolTip(tooltip)
            row.setToolTip(tooltip)
        row_layout.addWidget(label)
        row_layout.addWidget(checkbox, 1)
        layout.addWidget(row)

    def _add_text_row(self, layout: QVBoxLayout, label_text: str, value: QLabel) -> None:
        row, row_layout = self._row_frame()
        label = QLabel(label_text)
        label.setFixedWidth(self._ROW_LABEL_WIDTH)
        label.setObjectName("settingsRowLabel")
        tooltip = value.toolTip()
        if tooltip:
            label.setToolTip(tooltip)
            row.setToolTip(tooltip)
        row_layout.addWidget(label)
        row_layout.addWidget(value, 1)
        layout.addWidget(row)

    def _build_shortcuts_page(self, current_overrides: dict[str, str]) -> QWidget:
        """Build the Shortcuts settings page from SHORTCUT_REGISTRY."""

        page, layout = self._build_settings_page("Shortcuts")
        hint = QLabel(
            "Click a row's key field and press the new chord. Use the row's "
            "Reset to revert to the default. Conflicts are reported when you "
            "click OK."
        )
        hint.setWordWrap(True)
        hint.setObjectName("settingsRowLabel")
        hint.setToolTip(_settings_tooltip(
            "Change keyboard shortcuts for common app commands."
        ))
        layout.addWidget(hint)
        layout.addSpacing(4)

        # Group registry entries by category, preserving registry order.
        grouped: OrderedDict[str, list[tuple[str, str, str]]] = OrderedDict()
        for attr_name, category, default, display in SHORTCUT_REGISTRY:
            grouped.setdefault(category, []).append((attr_name, default, display))

        self._shortcut_editors: dict[str, QKeySequenceEdit] = {}
        self._shortcut_defaults: dict[str, str] = {
            attr_name: default for attr_name, _c, default, _d in SHORTCUT_REGISTRY
        }
        self._shortcut_display_names: dict[str, str] = {
            attr_name: display for attr_name, _c, _d, display in SHORTCUT_REGISTRY
        }

        for category, entries in grouped.items():
            heading = QLabel(category)
            heading.setObjectName("settingsCategoryHeading")
            layout.addSpacing(6)
            layout.addWidget(heading)
            for attr_name, default, display in entries:
                row, row_layout = self._row_frame()
                label = QLabel(display)
                label.setFixedWidth(self._ROW_LABEL_WIDTH * 2)
                label.setObjectName("settingsRowLabel")
                tooltip = _settings_tooltip(
                    f"Keyboard shortcut for {display}. Default: {default or 'none'}."
                )
                label.setToolTip(tooltip)
                row.setToolTip(tooltip)
                row_layout.addWidget(label)

                editor = QKeySequenceEdit()
                editor.setObjectName("shortcutEditor")
                editor.setMinimumWidth(160)
                editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                editor.setToolTip(tooltip)
                effective = current_overrides.get(attr_name, default)
                if effective:
                    editor.setKeySequence(QKeySequence(effective))
                row_layout.addWidget(editor, 1)

                reset_button = QPushButton("Reset")
                reset_button.setObjectName("settingsRowReset")
                reset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                reset_button.setFixedWidth(64)
                reset_button.setToolTip(_settings_tooltip(
                    f"Restore the default shortcut for {display}."
                ))
                reset_button.clicked.connect(
                    lambda _checked=False, edit=editor, default_chord=default: edit.setKeySequence(
                        QKeySequence(default_chord)
                    )
                )
                row_layout.addWidget(reset_button)

                layout.addWidget(row)
                self._shortcut_editors[attr_name] = editor

        layout.addSpacing(8)
        reset_all = QPushButton("Reset all to defaults")
        reset_all.setObjectName("settingsResetAll")
        reset_all.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        reset_all.setToolTip(_settings_tooltip(
            "Restore every shortcut on this page to its default key binding."
        ))
        reset_all.clicked.connect(self._reset_all_shortcuts)
        layout.addWidget(reset_all, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _reset_all_shortcuts(self) -> None:
        for attr_name, editor in self._shortcut_editors.items():
            editor.setKeySequence(QKeySequence(self._shortcut_defaults.get(attr_name, "")))

    def _set_dino_prefilter_controls_enabled(self, enabled: bool) -> None:
        for control in getattr(self, "_dino_dependent_controls", ()):
            control.setEnabled(bool(enabled))

    def _set_phash_prefilter_controls_enabled(self, enabled: bool) -> None:
        for control in getattr(self, "_phash_dependent_controls", ()):
            control.setEnabled(bool(enabled))

    def _update_ai_clip_model_summary(self) -> None:
        info = clip_model_variant_info(self.ai_clip_model_combo.currentData())
        parts = [info.description, info.expected_delta]
        if info.warning:
            parts.append(f"Warning: {info.warning}")
        elif info.recommended:
            parts.append("Recommended default.")
        self.ai_clip_model_warning_label.setText("\n".join(parts))

    def _collect_shortcut_state(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Return (effective_chords_by_attr, conflicts_by_chord) from current editor state."""

        effective: dict[str, str] = {}
        for attr_name, editor in self._shortcut_editors.items():
            text = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            effective[attr_name] = text
        conflicts: dict[str, list[str]] = {}
        for attr_name, chord in effective.items():
            if not chord:
                continue
            conflicts.setdefault(chord, []).append(attr_name)
        # Only chords with more than one assignee are real conflicts.
        return effective, {chord: attrs for chord, attrs in conflicts.items() if len(attrs) > 1}

    def _shortcut_overrides_from_state(self) -> dict[str, str]:
        """Return non-default chords as overrides; defaults are dropped."""

        effective, _conflicts = self._collect_shortcut_state()
        overrides: dict[str, str] = {}
        for attr_name, chord in effective.items():
            default = self._shortcut_defaults.get(attr_name, "")
            if chord and chord != default:
                overrides[attr_name] = chord
        return overrides

    def accept(self) -> None:  # type: ignore[override]
        """Validate shortcut conflicts before accepting."""

        if getattr(self, "_shortcut_editors", None):
            _effective, conflicts = self._collect_shortcut_state()
            if conflicts:
                lines = []
                for chord, attrs in conflicts.items():
                    names = ", ".join(
                        self._shortcut_display_names.get(attr_name, attr_name) for attr_name in attrs
                    )
                    lines.append(f"  {chord} → {names}")
                response = QMessageBox.warning(
                    self,
                    "Shortcut conflicts",
                    "These shortcuts are assigned to more than one action:\n\n"
                    + "\n".join(lines)
                    + "\n\nQt will fire only one of them. Save anyway?",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if response != QMessageBox.StandardButton.Save:
                    return
        super().accept()


    def _update_ai_cull_summary(self) -> None:
        keep = int(self.ai_keep_top_spin.value())
        review = int(self.ai_review_band_spin.value())
        # Clamp combined sliders so Review never eats into Keeper or pushes
        # Reject below 0%.
        if keep + review > 100:
            review = max(0, 100 - keep)
            with QSignalBlocker(self.ai_review_band_spin):
                self.ai_review_band_spin.setValue(review)
        reject = max(0, 100 - keep - review)
        self.ai_cull_summary_label.setText(
            f"~{keep}% Keeper · ~{review}% Review · ~{reject}% Reject"
        )

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
            loupe_card_style=str(self.loupe_card_style_combo.currentData() or "detailed"),
            ui_gamma=self.ui_gamma_slider.value() / 100.0,
            free_smooth_scroll_enabled=self.free_smooth_scroll_checkbox.isChecked(),
            preview_preload_batch_size=max(0, int(self.preview_preload_batch_spin.value())),
            show_hidden_folders=self.show_hidden_folders_checkbox.isChecked(),
            auto_advance_enabled=self.auto_advance_checkbox.isChecked(),
            burst_groups_enabled=self.burst_groups_checkbox.isChecked(),
            burst_stacks_enabled=self.burst_stacks_checkbox.isChecked(),
            catalog_cache_enabled=self.catalog_cache_checkbox.isChecked(),
            watch_current_folder=self.watch_current_folder_checkbox.isChecked(),
            check_updates_on_startup=self.check_updates_on_startup_checkbox.isChecked(),
            ai_embed_batch_size=max(0, int(self.ai_embed_batch_size_spin.value())),
            ai_clip_model_variant=coerce_clip_model_variant(self.ai_clip_model_combo.currentData()),
            ai_review_detail_progress_enabled=self.ai_review_detail_progress_checkbox.isChecked(),
            ai_dispute_weight=max(2, min(5, int(self.ai_dispute_weight_spin.value()))),
            ai_keep_top_percent=max(1, min(50, int(self.ai_keep_top_spin.value()))),
            ai_review_band_percent=max(0, min(30, int(self.ai_review_band_spin.value()))),
            ai_base_score_weight_percent=max(0, min(100, int(self.ai_base_score_weight_spin.value()))),
            ai_label_near_duplicate_threshold=max(0.500, min(0.995, int(self.ai_label_near_duplicate_slider.value()) / 1000.0)),
            dino_prefilter_settings=DINOPrefilterSettings(
                enabled=self.dino_prefilter_enabled_checkbox.isChecked(),
                mode=coerce_dino_prefilter_mode(self.dino_prefilter_mode_combo.currentData()),
                aggressiveness_percent=int(self.dino_prefilter_aggressiveness_spin.value()),
                technical_trash_enabled=self.dino_technical_trash_checkbox.isChecked(),
                duplicate_trash_enabled=self.dino_duplicate_trash_checkbox.isChecked(),
                low_information_enabled=self.dino_low_information_checkbox.isChecked(),
                rescue_ai_high_score_enabled=self.dino_rescue_ai_high_score_checkbox.isChecked(),
                rescue_user_keep_enabled=self.dino_rescue_user_keep_checkbox.isChecked(),
                rescue_semantic_unique_enabled=self.dino_rescue_semantic_unique_checkbox.isChecked(),
                rescue_best_representative_enabled=self.dino_rescue_best_representative_checkbox.isChecked(),
                diagnostics_enabled=self.dino_diagnostics_checkbox.isChecked(),
            ).normalized(),
            phash_prefilter_settings=PHashPrefilterSettings(
                enabled=self.phash_prefilter_enabled_checkbox.isChecked(),
                mode=coerce_dino_prefilter_mode(self.phash_prefilter_mode_combo.currentData()),
                execution_mode=coerce_phash_execution_mode(self.phash_execution_mode_combo.currentData()),
                hamming_threshold=int(self.phash_hamming_spin.value()),
                cache_enabled=self.phash_cache_checkbox.isChecked(),
                diagnostics_enabled=self.phash_diagnostics_checkbox.isChecked(),
            ).normalized(),
            presets=tuple(self._presets) if include_presets else (),
            shortcut_overrides=self._shortcut_overrides_from_state()
            if getattr(self, "_shortcut_editors", None)
            else {},
        )
