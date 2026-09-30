from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
import textwrap

from PySide6.QtCore import Property, QRectF, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox as _BaseCheckBox
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
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

from .models import DeleteMode, WinnerMode
from .phash_prefilter import (
    PHashPrefilterSettings,
    default_phash_prefilter_settings,
)
from .ui.help_dialog import show_paged_help
from .ui.help_topics import settings_help_pages
from .ui.shortcuts import SHORTCUT_REGISTRY
from .ui.theme import AppearanceMode, appearance_mode_label, appearance_profile_modes, parse_appearance_mode
from .ui.display_metrics import (
    DisplayProfile,
    STANDARD_DISPLAY,
    normalize_display_profile_preference,
)


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
    loupe_card_style: str = "detailed"
    ui_gamma: float = 1.0
    interface_size: str = "automatic"
    free_smooth_scroll_enabled: bool = False
    preview_preload_batch_size: int = 10
    show_hidden_folders: bool = False
    single_drive_expansion_enabled: bool = True
    auto_advance_enabled: bool = True
    burst_groups_enabled: bool = False
    burst_stacks_enabled: bool = False
    catalog_cache_enabled: bool = True
    watch_current_folder: bool = True
    restore_folder_position: bool = True
    check_updates_on_startup: bool = True
    theme: str = "auto"
    performance_logging_enabled: bool = False
    show_ai_tags_in_grid: bool = False
    apply_edits_to_pocketdrop: bool = False
    ai_embed_batch_size: int = 0
    ai_review_detail_progress_enabled: bool = False
    ai_dispute_weight: int = 3
    ai_keep_top_percent: int = 10       # % of folder to mark as Keeper
    ai_review_band_percent: int = 10    # % below Keeper cutoff to mark as Review
    ai_base_score_weight_percent: int = 65  # blend weight (0=adapter only, 100=base only)
    phash_prefilter_settings: PHashPrefilterSettings = field(default_factory=default_phash_prefilter_settings)
    presets: tuple[WorkflowPreset, ...] = ()
    # Keybind overrides: attr_name -> chord string. Empty / missing entries
    # mean "use the registered default."
    shortcut_overrides: dict[str, str] = field(default_factory=dict)


def _color_property(attribute: str):
    def getter(self):
        return getattr(self, attribute)

    def setter(self, value) -> None:
        setattr(self, attribute, QColor(value))
        self.update()

    return Property(QColor, getter, setter)


class QCheckBox(_BaseCheckBox):
    """Switch-style check box: the label sits left of a pill toggle.

    Shadows the Qt class inside this module so every setting that was a plain
    check box becomes a switch without touching its call site. Colours come
    from the stylesheet (qproperty-*), so the theme stays in charge.
    """

    _TRACK_W = 40
    _TRACK_H = 22
    _KNOB = 16
    _GAP = 10

    trackOffColor = _color_property("_track_off")
    trackOnColor = _color_property("_track_on")
    knobColor = _color_property("_knob_off")
    knobOnColor = _color_property("_knob_on")
    labelColor = _color_property("_label")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._track_off = QColor("#2b323c")
        self._track_on = QColor("#446fd2")
        self._knob_off = QColor("#d4dbe5")
        self._knob_on = QColor("#ffffff")
        self._label = QColor("#a7b2c0")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        metrics = self.fontMetrics()
        text_width = metrics.horizontalAdvance(self.text()) if self.text() else 0
        width = text_width + (self._GAP if text_width else 0) + self._TRACK_W
        return QSize(width, max(self._TRACK_H, metrics.height()) + 4)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # type: ignore[override]
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.45)
        rect = self.rect()
        track = QRectF(
            rect.right() - self._TRACK_W + 1,
            rect.center().y() - self._TRACK_H / 2.0 + 0.5,
            self._TRACK_W,
            self._TRACK_H,
        )
        on = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track_on if on else self._track_off)
        painter.drawRoundedRect(track, self._TRACK_H / 2.0, self._TRACK_H / 2.0)
        knob_x = track.right() - 3 - self._KNOB if on else track.left() + 3
        knob = QRectF(knob_x, track.center().y() - self._KNOB / 2.0, self._KNOB, self._KNOB)
        painter.setBrush(self._knob_on if on else self._knob_off)
        painter.drawEllipse(knob)
        if self.text():
            painter.setPen(self._label)
            painter.drawText(
                QRectF(0, 0, track.left() - self._GAP, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                self.text(),
            )
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._track_on, 1.5))
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), self._TRACK_H / 2.0 + 2, self._TRACK_H / 2.0 + 2)


def _paint_nav_glyph(painter: QPainter, kind: str) -> None:
    """Sidebar icons, drawn on a 24x24 grid (matches the settings mockup)."""
    if kind == "general":
        for x1, y1, x2, y2 in ((4, 7, 20, 7), (4, 17, 20, 17), (8, 4, 8, 10), (16, 14, 16, 20)):
            painter.drawLine(x1, y1, x2, y2)
    elif kind == "interface":
        painter.drawRoundedRect(QRectF(4, 5, 16, 14), 2, 2)
        painter.drawLine(9, 5, 9, 19)
    elif kind == "library":
        painter.drawRoundedRect(QRectF(2.5, 9.5, 19, 10), 2.5, 2.5)
        path = QPainterPath()
        path.moveTo(3.5, 9.5)
        path.lineTo(3.5, 7.5)
        path.lineTo(9.5, 7.5)
        path.lineTo(11.5, 9.5)
        painter.drawPath(path)
    elif kind == "ai":
        path = QPainterPath()
        for index, (x, y) in enumerate(((12, 3), (14, 8), (19, 10), (14, 12), (12, 17), (10, 12), (5, 10), (10, 8))):
            path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(18, 16, 18, 20)
        painter.drawLine(16, 18, 20, 18)
    elif kind == "duplicates":
        painter.drawRoundedRect(QRectF(7, 7, 11, 11), 2, 2)
        painter.drawRoundedRect(QRectF(4, 4, 11, 12), 2, 2)
    elif kind == "shortcuts":
        painter.drawRoundedRect(QRectF(3.5, 6, 17, 12), 2, 2)
        for x1, y1, x2, y2 in ((7, 10, 8, 10), (11, 10, 12, 10), (15, 10, 17, 10), (7, 14, 14, 14)):
            painter.drawLine(x1, y1, x2, y2)


class _NavButton(QPushButton):
    iconColor = _color_property("_icon")
    iconActiveColor = _color_property("_icon_on")

    def __init__(self, text: str, kind: str, parent=None) -> None:
        super().__init__(text, parent)
        self._kind = kind
        self._icon = QColor("#8e9aab")
        self._icon_on = QColor("#79a4ff")
        self.setObjectName("settingsNavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = 17.0
        painter.translate(14.0, (self.height() - size) / 2.0)
        painter.scale(size / 24.0, size / 24.0)
        pen = QPen(self._icon_on if self.isChecked() else self._icon, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _paint_nav_glyph(painter, self._kind)


_NAV_GROUPS = (
    ("Application", "Application settings", (("General", "general"), ("Interface", "interface"), ("Library & Folders", "library"))),
    ("Workflow", "Workflow settings", (("AI Culling", "ai"), ("Duplicates", "duplicates"), ("Shortcuts", "shortcuts"))),
)
_SECTION_HINTS = {
    "Review behavior": "Defaults used when a new review starts",
    "App updates": "Keep Image Triage current automatically",
    "Appearance": "Scale and presentation",
    "Navigation and preview": "How browsing feels",
    "Review flow": "How review moves between images",
    "Folder browsing": "Remember and refresh locations",
    "Catalog": "Local browsing acceleration",
    "Processing": "Performance and visibility",
    "Result ranges": "Tune how the finished ranking is divided",
    "Detection": "How similar images are grouped",
    "Storage and diagnostics": "What is kept between runs",
}
_PAGE_DESCRIPTIONS_FALLBACK = {
    "Shortcuts": "Customize keyboard commands used throughout Image Triage.",
}
_CARD_CONTROL_WIDTH = 345


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
        loupe_card_style: str = "detailed",
        allowed_card_styles: "tuple[str, ...] | None" = None,
        ui_gamma: float = 1.0,
        interface_size: str = "automatic",
        free_smooth_scroll_enabled: bool = False,
        preview_preload_batch_size: int = 10,
        show_hidden_folders: bool = False,
        single_drive_expansion_enabled: bool = True,
        auto_advance_enabled: bool = True,
        burst_groups_enabled: bool = False,
        burst_stacks_enabled: bool = False,
        catalog_cache_enabled: bool = True,
        watch_current_folder: bool = True,
        restore_folder_position: bool = True,
        check_updates_on_startup: bool = True,
        theme: str = "auto",
        performance_logging_enabled: bool = False,
        show_ai_tags_in_grid: bool = False,
        apply_edits_to_pocketdrop: bool = False,
        ai_embed_batch_size: int = 0,
        ai_review_detail_progress_enabled: bool = False,
        ai_dispute_weight: int = 3,
        ai_keep_top_percent: int = 10,
        ai_review_band_percent: int = 10,
        ai_base_score_weight_percent: int = 65,
        phash_prefilter_settings: PHashPrefilterSettings | None = None,
        catalog_summary_text: str = "",
        presets: list[WorkflowPreset] | None = None,
        preset_save_callback: Callable[[tuple[WorkflowPreset, ...]], None] | None = None,
        shortcut_overrides: dict[str, str] | None = None,
        initial_section: str | None = None,
        display_profile: DisplayProfile | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._display_profile = display_profile or STANDARD_DISPLAY
        profile = self._display_profile
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(profile.settings_min_width, profile.settings_min_height)
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width, height = profile.settings_width, profile.settings_height
        if available is not None:
            width = min(width, int(available.width() * 0.94))
            height = min(height, int(available.height() * 0.94))
            self.setMinimumSize(min(profile.settings_min_width, width), min(profile.settings_min_height, height))
        self.resize(width, height)
        self._presets = list(presets or [])
        self._preset_save_callback = preset_save_callback
        self._updating_session = False
        phash_settings = (phash_prefilter_settings or default_phash_prefilter_settings()).normalized()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._page_titles: list[str] = []
        self._page_descriptions: dict[str, str] = dict(_PAGE_DESCRIPTIONS_FALLBACK)
        self._nav_buttons: list[_NavButton] = []
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_seen_groups: set[str] = set()
        self._cards: dict[int, tuple[QVBoxLayout, list[int]]] = {}
        self._initial_control_state: dict[QWidget, object] = {}

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        sidebar = QFrame(body)
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(profile.settings_nav_width)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 16)
        sidebar_layout.setSpacing(0)

        search_box = QFrame(sidebar)
        search_box.setObjectName("settingsSearchBox")
        search_layout = QHBoxLayout(search_box)
        search_layout.setContentsMargins(12, 0, 12, 0)
        search_layout.setSpacing(9)
        search_glyph = QLabel("\uE721", search_box)
        search_glyph.setObjectName("settingsSearchGlyph")
        search_layout.addWidget(search_glyph)
        self.search_field = QLineEdit(search_box)
        self.search_field.setObjectName("settingsSearchField")
        self.search_field.setPlaceholderText("Search settings")
        self.search_field.setToolTip("Type a word and press Enter to jump to the first page that mentions it.")
        self.search_field.returnPressed.connect(self._search_settings)
        search_layout.addWidget(self.search_field, 1)
        sidebar_layout.addWidget(search_box)
        sidebar_layout.addSpacing(18)

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(4)
        sidebar_layout.addLayout(self.nav_layout)
        sidebar_layout.addStretch(1)

        help_card = QFrame(sidebar)
        help_card.setObjectName("settingsHelpCard")
        help_layout = QVBoxLayout(help_card)
        help_layout.setContentsMargins(12, 12, 12, 12)
        help_layout.setSpacing(4)
        help_title = QLabel("Need a hand?", help_card)
        help_title.setObjectName("settingsHelpTitle")
        help_text = QLabel("See what each setting changes and when to use it.", help_card)
        help_text.setObjectName("settingsHelpText")
        help_text.setWordWrap(True)
        self.help_button = QPushButton("Open settings guide", help_card)
        self.help_button.setObjectName("settingsHelpButton")
        self.help_button.setToolTip("Open a plain-language guide to these settings")
        self.help_button.clicked.connect(self._show_help)
        help_layout.addWidget(help_title)
        help_layout.addWidget(help_text)
        help_layout.addSpacing(6)
        help_layout.addWidget(self.help_button)
        sidebar_layout.addWidget(help_card)

        main = QWidget(body)
        main.setObjectName("settingsMain")
        self._main_layout = QVBoxLayout(main)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        header = QFrame(main)
        header.setObjectName("settingsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(30, 25, 30, 18)
        header_layout.setSpacing(18)
        header_copy = QVBoxLayout()
        header_copy.setSpacing(0)
        self.header_eyebrow = QLabel("Application settings", header)
        self.header_eyebrow.setObjectName("settingsEyebrow")
        self.header_title = QLabel("General", header)
        self.header_title.setObjectName("settingsTitle")
        self.header_subtitle = QLabel("", header)
        self.header_subtitle.setObjectName("settingsSubtitle")
        self.header_subtitle.setWordWrap(True)
        header_copy.addWidget(self.header_eyebrow)
        header_copy.addSpacing(7)
        header_copy.addWidget(self.header_title)
        header_copy.addSpacing(7)
        header_copy.addWidget(self.header_subtitle)
        header_layout.addLayout(header_copy, 1)
        self._main_layout.addWidget(header)

        self.pages = QStackedWidget(main)
        self.pages.setObjectName("settingsPages")
        self.pages.setMinimumWidth(profile.settings_pages_min_width)
        self._main_layout.addWidget(self.pages, 1)

        body_layout.addWidget(sidebar)
        body_layout.addWidget(main, 1)
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

        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(200)
        self.theme_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for mode in appearance_profile_modes():
            self.theme_combo.addItem(appearance_mode_label(mode), mode.value)
        current_theme_index = self.theme_combo.findData(parse_appearance_mode(theme).value)
        self.theme_combo.setCurrentIndex(max(0, current_theme_index))
        self.theme_combo.setToolTip(_settings_tooltip(
            "The app's colour theme. Also available from the View menu; both change the same setting."
        ))

        self.performance_logging_checkbox = QCheckBox("Log detailed performance timings")
        self.performance_logging_checkbox.setChecked(performance_logging_enabled)
        self.performance_logging_checkbox.setToolTip(_settings_tooltip(
            "Writes step timings to a JSONL log for diagnosing slowness. Also available from the "
            "Tools menu; both change the same setting."
        ))

        self.show_ai_tags_in_grid_checkbox = QCheckBox("Show AI tags on cards in the grid")
        self.show_ai_tags_in_grid_checkbox.setChecked(show_ai_tags_in_grid)
        self.show_ai_tags_in_grid_checkbox.setToolTip(_settings_tooltip(
            "Off by default. When on, AI badges (top pick, confidence, etc.) show on grid cards "
            "during manual review, not only in the inspector."
        ))

        self.apply_edits_to_pocketdrop_checkbox = QCheckBox("Apply edits before sending to PocketDrop")
        self.apply_edits_to_pocketdrop_checkbox.setChecked(apply_edits_to_pocketdrop)
        self.apply_edits_to_pocketdrop_checkbox.setToolTip(_settings_tooltip(
            "Off by default. When on, sending a photo with a real built-in editor session to "
            "PocketDrop sends a rendered copy with its edits applied instead of the original file."
        ))

        session_row = QWidget()
        session_row.setToolTip(_settings_tooltip(
            "Choose or name the settings preset used for this review profile."
        ))
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(8)
        session_layout.addWidget(self.session_combo)
        self.save_preset_button = QPushButton("Save Preset")
        self.save_preset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.save_preset_button.setToolTip(_settings_tooltip(
            "Save the current General settings under the selected profile name."
        ))
        self.save_preset_button.clicked.connect(self._save_current_preset)
        session_layout.addWidget(self.save_preset_button)
        session_layout.addStretch(1)

        general_page, general_layout = self._build_settings_page(
            "General",
            "Choose what happens to accepted and deleted images, and manage reusable review presets.",
        )
        self._add_category_heading(general_layout, "Review behavior")
        self._add_form_row(general_layout, "Profile preset", session_row)
        self._add_form_row(general_layout, "Accepted images", self.winner_mode_combo)
        self._add_form_row(general_layout, "Delete behavior", self.delete_mode_combo)
        self._add_category_heading(general_layout, "Appearance & diagnostics")
        self._add_form_row(general_layout, "Theme", self.theme_combo)
        self._add_checkbox_row(general_layout, "Performance logging", self.performance_logging_checkbox)
        self._add_checkbox_row(general_layout, "AI tags in grid", self.show_ai_tags_in_grid_checkbox)
        self._add_checkbox_row(general_layout, "PocketDrop", self.apply_edits_to_pocketdrop_checkbox)
        self._add_category_heading(general_layout, "App updates")
        self._add_checkbox_row(general_layout, "Automatic check", self.check_updates_on_startup_checkbox)
        self.preset_status_label = QLabel("")
        self.preset_status_label.setObjectName("mutedText")
        self.preset_status_label.setStyleSheet("font-size: 11px;")
        general_layout.addWidget(self.preset_status_label)
        general_layout.addStretch(1)
        self._add_settings_page("General", general_page)

        self.loupe_card_style_combo = QComboBox()
        self.loupe_card_style_combo.setMinimumWidth(180)
        # The host can restrict selectable styles on smaller displays, so honor
        # the allowed list when one is supplied.
        _card_style_options = (
            ("Detailed", "detailed"),
            ("Zen", "zen"),
            ("Gallery", "gallery"),
        )
        _allowed = set(allowed_card_styles) if allowed_card_styles else {key for _label, key in _card_style_options}
        for _label, _key in _card_style_options:
            if _key in _allowed:
                self.loupe_card_style_combo.addItem(_label, _key)
        loupe_style_index = self.loupe_card_style_combo.findData(loupe_card_style)
        self.loupe_card_style_combo.setCurrentIndex(max(0, loupe_style_index))
        self.loupe_card_style_combo.setToolTip(_settings_tooltip(
            "Detailed shows filenames, image details, and review status. Zen removes most "
            "labels so the photos take priority. Gallery keeps a compact caption while "
            "leaving more room for each image."
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

        self.interface_size_combo = QComboBox()
        self.interface_size_combo.setMinimumWidth(180)
        self.interface_size_combo.addItem("Automatic (recommended)", "automatic")
        self.interface_size_combo.addItem("Compact", "compact")
        self.interface_size_combo.addItem("Comfortable", "standard")
        self.interface_size_combo.addItem("Large", "spacious")
        interface_size_index = self.interface_size_combo.findData(
            normalize_display_profile_preference(interface_size)
        )
        self.interface_size_combo.setCurrentIndex(max(0, interface_size_index))
        self.interface_size_combo.setToolTip(_settings_tooltip(
            "Automatic adapts the interface to the app window's usable logical size. "
            "Choose another size to keep the same control density on every display."
        ))

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

        self.single_drive_expansion_checkbox = QCheckBox(
            "Keep only one branch expanded per level"
        )
        self.single_drive_expansion_checkbox.setChecked(single_drive_expansion_enabled)
        self.single_drive_expansion_checkbox.setToolTip(_settings_tooltip(
            "Opening a drive or folder collapses its expanded siblings at the same level. "
            "The active path remains open while you browse deeper."
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

        interface_page, interface_layout = self._build_settings_page(
            "Interface",
            "Adjust how the image grid looks, how previews load, and how review moves from one image to the next.",
        )
        self._add_category_heading(interface_layout, "Appearance")
        self._add_form_row(interface_layout, "Interface size", self.interface_size_combo)
        self._add_form_row(interface_layout, "Card style", self.loupe_card_style_combo)
        self._add_form_row(interface_layout, "UI gamma", self.ui_gamma_row)
        self._add_category_heading(interface_layout, "Navigation and preview")
        self._add_checkbox_row(interface_layout, "Scrolling", self.free_smooth_scroll_checkbox)
        self._add_form_row(interface_layout, "Preview preload", self.preview_preload_batch_spin)
        self._add_checkbox_row(interface_layout, "Folders", self.show_hidden_folders_checkbox)
        self._add_category_heading(interface_layout, "Review flow")
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

        self.restore_folder_position_checkbox = QCheckBox("Reopen folders where I left off")
        self.restore_folder_position_checkbox.setChecked(restore_folder_position)
        self.restore_folder_position_checkbox.setToolTip(_settings_tooltip(
            "Remembers the photo you were on in each folder, across restarts, and brings it back to the top "
            "of the view when the folder opens. When off, folders always open at their start."
        ))

        self.catalog_summary_label = QLabel(_compact_catalog_summary(catalog_summary_text))
        self.catalog_summary_label.setWordWrap(True)
        self.catalog_summary_label.setObjectName("mutedText")
        self.catalog_summary_label.setStyleSheet("font-size: 11px;")
        self.catalog_summary_label.setToolTip(_settings_tooltip(
            "Current catalog cache status and indexed-file summary."
        ))
        folders_page, folders_layout = self._build_settings_page(
            "Library & Folders",
            "Control folder navigation, automatic refresh, and the lightweight catalog data used for faster browsing.",
        )
        self._add_category_heading(folders_layout, "Folder browsing")
        self._add_checkbox_row(
            folders_layout, "Folder tree", self.single_drive_expansion_checkbox
        )
        self._add_checkbox_row(folders_layout, "Watch folder", self.watch_current_folder_checkbox)
        self._add_checkbox_row(folders_layout, "Position", self.restore_folder_position_checkbox)
        self._add_category_heading(folders_layout, "Catalog")
        self._add_checkbox_row(folders_layout, "Catalog cache", self.catalog_cache_checkbox)
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
            "How many images the AI prepares at the same time. Auto chooses a balanced "
            "value for your computer. A higher value may finish sooner, but it can use "
            "more memory and make the app or computer less responsive."
        ))

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
            "Top percentile of the folder marked as Winner. 10% means the AI "
            "passes through roughly the top 10% of images as 'Likely Winner' "
            "after each run."
        ))

        self.ai_review_band_spin = QSpinBox()
        self.ai_review_band_spin.setRange(0, 30)
        self.ai_review_band_spin.setSingleStep(1)
        self.ai_review_band_spin.setSuffix("%")
        self.ai_review_band_spin.setValue(max(0, min(30, int(ai_review_band_percent))))
        self.ai_review_band_spin.setMinimumWidth(120)
        self.ai_review_band_spin.setToolTip(_settings_tooltip(
            "Additional band of close-to-winner images marked as 'Needs "
            "Review' (sitting just below the Winner cutoff). Set to 0 to "
            "disable the Review band entirely so every card is either Winner "
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
            "outright (heavily penalized images can never pass as Winner); "
            "0% = adapter only (tag penalties for blur / blown / harsh light "
            "have no effect). Default 65% favors the base score so the "
            "negative prompts stay authoritative, while still letting the "
            "adapter influence borderline calls."
        ))

        ai_page, ai_layout = self._build_settings_page(
            "AI Culling",
            "Tune processing and decide how much of a finished ranking is treated as likely winners or needs review.",
        )
        self._add_category_heading(ai_layout, "Processing")
        self._add_form_row(ai_layout, "AI batch size", self.ai_embed_batch_size_spin)
        self._add_checkbox_row(ai_layout, "Detailed progress log", self.ai_review_detail_progress_checkbox)
        self._add_category_heading(ai_layout, "Result ranges")
        self._add_form_row(ai_layout, "Likely winners", self.ai_keep_top_spin)
        self._add_form_row(ai_layout, "Review band", self.ai_review_band_spin)
        self._add_form_row(ai_layout, "Cull breakdown", self.ai_cull_summary_label)
        self._add_category_heading(ai_layout, "Adapter")
        self._add_form_row(ai_layout, "Dispute weight", self.ai_dispute_weight_spin)
        self._add_form_row(ai_layout, "Base score weight", self.ai_base_score_weight_spin)
        ai_layout.addStretch(1)
        self._update_ai_cull_summary()
        self._add_settings_page("AI Culling", ai_page)

        self.phash_prefilter_enabled_checkbox = QCheckBox("Enable pHash Prefilter")
        self.phash_prefilter_enabled_checkbox.setChecked(phash_settings.enabled)
        self.phash_prefilter_enabled_checkbox.setToolTip(_settings_tooltip(
            "Runs a perceptual hash duplicate pass before AI scoring. "
            "This catches tight visual repeats and near-identical frames. "
            "Manual winners are always preserved and preferred as group representatives."
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

        phash_page, phash_layout = self._build_settings_page(
            "Duplicates",
            "Find nearly identical frames before the slower AI scoring pass. This uses a visual fingerprint called pHash; it never deletes an image.",
        )
        self._add_category_heading(phash_layout, "Detection")
        self._add_checkbox_row(phash_layout, "Duplicate check", self.phash_prefilter_enabled_checkbox)
        self._add_form_row(phash_layout, "Duplicate distance", self.phash_hamming_spin)
        self._add_category_heading(phash_layout, "Storage and diagnostics")
        self._add_checkbox_row(phash_layout, "Cache hash metadata", self.phash_cache_checkbox)
        self._add_checkbox_row(phash_layout, "Run diagnostics", self.phash_diagnostics_checkbox)
        phash_layout.addStretch(1)
        self._phash_dependent_controls = (
            self.phash_hamming_spin,
            self.phash_cache_checkbox,
            self.phash_diagnostics_checkbox,
        )
        self.phash_prefilter_enabled_checkbox.toggled.connect(self._set_phash_prefilter_controls_enabled)
        self._set_phash_prefilter_controls_enabled(self.phash_prefilter_enabled_checkbox.isChecked())
        self._add_settings_page("Duplicates", phash_page)

        shortcuts_page = self._build_shortcuts_page(shortcut_overrides or {})
        self._add_settings_page("Shortcuts", shortcuts_page)

        footer = QFrame(self)
        footer.setObjectName("settingsFooter")
        footer.setFixedHeight(62)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(30, 0, 20, 0)
        footer_layout.setSpacing(10)
        footer_note = QLabel("Changes are applied after you click Save changes.", footer)
        footer_note.setObjectName("settingsFooterNote")
        footer_layout.addWidget(footer_note, 1)
        self.reset_page_button = QPushButton("Reset page", footer)
        self.reset_page_button.setObjectName("settingsFooterButton")
        self.reset_page_button.setToolTip("Put this page's settings back to how they were when you opened Settings.")
        self.reset_page_button.clicked.connect(self._reset_current_page)
        self.cancel_button = QPushButton("Cancel", footer)
        self.cancel_button.setObjectName("settingsFooterButton")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("Save changes", footer)
        self.save_button.setObjectName("settingsPrimaryButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.reset_page_button)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.save_button)
        self._main_layout.addWidget(footer, 0)
        self._show_page(0)
        if initial_section:
            self._select_section(initial_section)
        self._refresh_preset_dropdown()
        self._snapshot_controls()

    def _show_help(self) -> None:
        show_paged_help(
            self,
            title="Settings Help",
            pages=settings_help_pages(),
        )

    def _build_settings_page(self, title: str, description: str = "") -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("settingsPageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 22, 30, 28)
        layout.setSpacing(0)
        if description:
            self._page_descriptions[title] = description
        scroll.setWidget(content)
        return scroll, layout

    def _add_settings_page(self, title: str, page: QWidget) -> None:
        group_title, group_kind_icon = "", "general"
        for name, _eyebrow, entries in _NAV_GROUPS:
            for entry_title, icon_kind in entries:
                if entry_title == title:
                    group_title, group_kind_icon = name, icon_kind
        if group_title and group_title not in self._nav_seen_groups:
            self._nav_seen_groups.add(group_title)
            label = QLabel(group_title.upper())
            label.setObjectName("settingsNavLabel")
            if self.nav_layout.count():
                self.nav_layout.addSpacing(14)
            self.nav_layout.addWidget(label)
        index = self.pages.count()
        button = _NavButton(title.replace("&", "&&"), group_kind_icon)
        button.clicked.connect(lambda _checked=False, i=index: self._show_page(i))
        self._nav_group.addButton(button)
        self.nav_layout.addWidget(button)
        self._nav_buttons.append(button)
        self._page_titles.append(title)
        self.pages.addWidget(page)

    def _show_page(self, index: int) -> None:
        if not 0 <= index < len(self._page_titles):
            return
        title = self._page_titles[index]
        self.pages.setCurrentIndex(index)
        if index < len(self._nav_buttons):
            self._nav_buttons[index].setChecked(True)
        eyebrow = "Application settings"
        for _name, group_eyebrow, entries in _NAV_GROUPS:
            if any(entry_title == title for entry_title, _kind in entries):
                eyebrow = group_eyebrow
        self.header_eyebrow.setText(eyebrow.upper())
        self.header_title.setText(title)
        self.header_subtitle.setText(self._page_descriptions.get(title, ""))

    def _select_section(self, title: str) -> None:
        target = title.strip().casefold()
        target = {
            "ai": "ai culling",
            "phash prefilter": "duplicates",
        }.get(target, target)
        if not target:
            return
        for index, page_title in enumerate(self._page_titles):
            if page_title.strip().casefold() == target:
                self._show_page(index)
                return

    def _search_settings(self) -> None:
        query = self.search_field.text().strip().casefold()
        if not query:
            return
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            haystack = [self._page_titles[index], self._page_descriptions.get(self._page_titles[index], "")]
            haystack.extend(label.text() for label in page.findChildren(QLabel))
            haystack.extend(box.text() for box in page.findChildren(QCheckBox))
            if query in " ".join(haystack).casefold():
                self._show_page(index)
                return
        self.search_field.selectAll()
        self.search_field.setToolTip("No settings match that search.")

    # -- reset page ------------------------------------------------------
    _CONTROL_TYPES = (QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QSlider, QKeySequenceEdit)

    @staticmethod
    def _read_control(widget: QWidget) -> object:
        if isinstance(widget, _BaseCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentIndex()
        if isinstance(widget, QKeySequenceEdit):
            return widget.keySequence()
        return widget.value()  # spin boxes and sliders

    @staticmethod
    def _write_control(widget: QWidget, value: object) -> None:
        if isinstance(widget, _BaseCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            widget.setCurrentIndex(int(value))
        elif isinstance(widget, QKeySequenceEdit):
            widget.setKeySequence(value)
        else:
            widget.setValue(value)

    def _snapshot_controls(self) -> None:
        for index in range(self.pages.count()):
            for widget in self.pages.widget(index).findChildren(QWidget):
                if isinstance(widget, self._CONTROL_TYPES):
                    self._initial_control_state[widget] = self._read_control(widget)

    def _reset_current_page(self) -> None:
        page = self.pages.currentWidget()
        if page is None:
            return
        for widget in page.findChildren(QWidget):
            if widget in self._initial_control_state and isinstance(widget, self._CONTROL_TYPES):
                self._write_control(widget, self._initial_control_state[widget])

    # -- cards and rows --------------------------------------------------
    def _card_layout(self, layout: QVBoxLayout) -> tuple[QVBoxLayout, list[int]]:
        entry = self._cards.get(id(layout))
        if entry is None:
            card = QFrame()
            card.setObjectName("settingsCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)
            layout.addWidget(card)
            entry = (card_layout, [0])
            self._cards[id(layout)] = entry
        return entry

    def _append_to_card(self, layout: QVBoxLayout, row: QWidget) -> None:
        card_layout, count = self._card_layout(layout)
        if count[0]:
            divider = QFrame()
            divider.setObjectName("settingsRowDivider")
            divider.setFixedHeight(1)
            card_layout.addWidget(divider)
        card_layout.addWidget(row)
        count[0] += 1

    def _add_category_heading(self, layout: QVBoxLayout, title: str) -> None:
        self._cards.pop(id(layout), None)
        if layout.count() > 0:
            layout.addSpacing(22)
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(15)
        name = QLabel(title)
        name.setObjectName("settingsSectionTitle")
        head_layout.addWidget(name)
        head_layout.addStretch(1)
        hint_text = _SECTION_HINTS.get(title, "")
        if hint_text:
            hint = QLabel(hint_text)
            hint.setObjectName("settingsSectionHint")
            head_layout.addWidget(hint)
        layout.addWidget(head)
        layout.addSpacing(10)

    def _add_card_row(self, layout: QVBoxLayout, label_text: str, control: QWidget) -> None:
        row = QWidget()
        row.setObjectName("settingsCardRow")
        row.setMinimumHeight(70)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(16, 14, 16, 14)
        row_layout.setSpacing(24)
        tooltip = control.toolTip()
        if tooltip:
            row.setToolTip(tooltip)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        title = QLabel(label_text)
        title.setObjectName("settingsRowTitle")
        copy.addWidget(title)
        description = " ".join(tooltip.split())
        if description:
            desc = QLabel(description)
            desc.setObjectName("settingsRowDesc")
            desc.setWordWrap(True)
            copy.addWidget(desc)
        row_layout.addLayout(copy, 1)
        if isinstance(control, _BaseCheckBox):
            row_layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            control.setMinimumWidth(0)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
            if isinstance(control, QComboBox) and not control.isEditable():
                chevron_layout = QHBoxLayout(control)
                chevron_layout.setContentsMargins(0, 0, 13, 0)
                chevron_layout.addStretch(1)
                chevron = QLabel("", control)
                chevron.setObjectName("settingsComboChevron")
                chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                chevron_layout.addWidget(chevron)
            holder = QWidget()
            holder.setFixedWidth(_CARD_CONTROL_WIDTH)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(control, 1)
            row_layout.addWidget(holder, 0, Qt.AlignmentFlag.AlignVCenter)
        self._append_to_card(layout, row)

    def _add_form_row(self, layout: QVBoxLayout, label_text: str, field: QWidget) -> None:
        self._add_card_row(layout, label_text, field)

    def _add_checkbox_row(self, layout: QVBoxLayout, label_text: str, checkbox: QCheckBox) -> None:
        self._add_card_row(layout, label_text, checkbox)

    def _add_text_row(self, layout: QVBoxLayout, label_text: str, value: QLabel) -> None:
        self._add_card_row(layout, label_text, value)

    def _row_frame(self) -> tuple[QWidget, QHBoxLayout]:
        row = QWidget()
        row.setObjectName("settingsRow")
        layout = QHBoxLayout(row)
        profile = self._display_profile
        layout.setContentsMargins(
            profile.settings_row_margin_x,
            profile.settings_row_margin_y,
            profile.settings_row_margin_x,
            profile.settings_row_margin_y,
        )
        layout.setSpacing(profile.settings_row_spacing)
        return row, layout

    def _build_shortcuts_page(self, current_overrides: dict[str, str]) -> QWidget:
        """Build the Shortcuts settings page from SHORTCUT_REGISTRY."""

        page, layout = self._build_settings_page("Shortcuts")
        hint = QLabel(
            "Click a row's key field and press the new chord. Use the row's "
            "Reset to revert to the default. Conflicts are reported when you "
            "click Save changes."
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
            self._add_category_heading(layout, category)
            for attr_name, default, display in entries:
                row, row_layout = self._row_frame()
                row.setObjectName("settingsCardRow")
                row.setMinimumHeight(56)
                label = QLabel(display)
                label.setFixedWidth(self._display_profile.settings_shortcut_label_width)
                label.setObjectName("settingsRowTitle")
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
                reset_button.setFixedWidth(self._display_profile.settings_shortcut_reset_width)
                reset_button.setToolTip(_settings_tooltip(
                    f"Restore the default shortcut for {display}."
                ))
                reset_button.clicked.connect(
                    lambda _checked=False, edit=editor, default_chord=default: edit.setKeySequence(
                        QKeySequence(default_chord)
                    )
                )
                row_layout.addWidget(reset_button)

                self._append_to_card(layout, row)
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

    def _set_phash_prefilter_controls_enabled(self, enabled: bool) -> None:
        for control in getattr(self, "_phash_dependent_controls", ()):
            control.setEnabled(bool(enabled))

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
            f"~{keep}% Winner · ~{review}% Review · ~{reject}% Reject"
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
            loupe_card_style=str(self.loupe_card_style_combo.currentData() or "detailed"),
            ui_gamma=self.ui_gamma_slider.value() / 100.0,
            interface_size=normalize_display_profile_preference(
                self.interface_size_combo.currentData()
            ),
            free_smooth_scroll_enabled=self.free_smooth_scroll_checkbox.isChecked(),
            preview_preload_batch_size=max(0, int(self.preview_preload_batch_spin.value())),
            show_hidden_folders=self.show_hidden_folders_checkbox.isChecked(),
            single_drive_expansion_enabled=self.single_drive_expansion_checkbox.isChecked(),
            auto_advance_enabled=self.auto_advance_checkbox.isChecked(),
            burst_groups_enabled=self.burst_groups_checkbox.isChecked(),
            burst_stacks_enabled=self.burst_stacks_checkbox.isChecked(),
            catalog_cache_enabled=self.catalog_cache_checkbox.isChecked(),
            watch_current_folder=self.watch_current_folder_checkbox.isChecked(),
            restore_folder_position=self.restore_folder_position_checkbox.isChecked(),
            check_updates_on_startup=self.check_updates_on_startup_checkbox.isChecked(),
            theme=str(self.theme_combo.currentData()),
            performance_logging_enabled=self.performance_logging_checkbox.isChecked(),
            show_ai_tags_in_grid=self.show_ai_tags_in_grid_checkbox.isChecked(),
            apply_edits_to_pocketdrop=self.apply_edits_to_pocketdrop_checkbox.isChecked(),
            ai_embed_batch_size=max(0, int(self.ai_embed_batch_size_spin.value())),
            ai_review_detail_progress_enabled=self.ai_review_detail_progress_checkbox.isChecked(),
            ai_dispute_weight=max(2, min(5, int(self.ai_dispute_weight_spin.value()))),
            ai_keep_top_percent=max(1, min(50, int(self.ai_keep_top_spin.value()))),
            ai_review_band_percent=max(0, min(30, int(self.ai_review_band_spin.value()))),
            ai_base_score_weight_percent=max(0, min(100, int(self.ai_base_score_weight_spin.value()))),
            phash_prefilter_settings=PHashPrefilterSettings(
                enabled=self.phash_prefilter_enabled_checkbox.isChecked(),
                hamming_threshold=int(self.phash_hamming_spin.value()),
                cache_enabled=self.phash_cache_checkbox.isChecked(),
                diagnostics_enabled=self.phash_diagnostics_checkbox.isChecked(),
            ).normalized(),
            presets=tuple(self._presets) if include_presets else (),
            shortcut_overrides=self._shortcut_overrides_from_state()
            if getattr(self, "_shortcut_editors", None)
            else {},
        )
