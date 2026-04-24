from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue

from PySide6.QtCore import QEvent, QPoint, QRect, QRunnable, QSize, QSettings, QSignalBlocker, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .ai_results import AIImageResult, build_ai_explanation_lines
from .formats import FITS_SUFFIXES, suffix_for_path
from .imaging import FITS_STF_PRESETS, FitsDisplaySettings, load_image_for_display
from .metadata import CaptureMetadata, EMPTY_METADATA, load_capture_metadata
from .models import ImageRecord, JPEG_SUFFIXES
from .review_tools import (
    DEFAULT_FOCUS_ASSIST_COLOR_ID,
    DEFAULT_FOCUS_ASSIST_STRENGTH_ID,
    EMPTY_INSPECTION_STATS,
    FOCUS_ASSIST_COLORS,
    FOCUS_ASSIST_STRENGTHS,
    FocusAssistColor,
    FocusAssistStrength,
    InspectionStats,
    build_focus_assist_image,
    build_inspection_stats,
    focus_assist_color_by_id,
    focus_assist_strength_by_id,
)
from .scanner import discover_edited_paths
from .ui.theme import ThemePalette, default_theme

COMPARE_COUNTS = (2, 3, 5, 7, 9)


@dataclass(slots=True, frozen=True)
class PreviewRequest:
    path: str
    token: int
    slot: int
    target_size: QSize
    source_signature: tuple[int, int] | None = None
    prefer_embedded: bool = False
    load_metadata: bool = True
    cache_only: bool = False
    fits_display_settings: FitsDisplaySettings | None = None


@dataclass(slots=True, frozen=True)
class PreviewEntry:
    record: ImageRecord
    source_path: str
    winner: bool = False
    reject: bool = False
    photoshop: bool = False
    edited_path: str = ""
    edited_candidates: tuple[str, ...] = ()
    label: str = ""
    ai_result: AIImageResult | None = None
    review_summary: str = ""
    workflow_summary: str = ""
    workflow_details: tuple[str, ...] = ()
    placeholder_image: QImage | None = None


class PreviewTask(QRunnable):
    def __init__(self, request: PreviewRequest, result_queue: SimpleQueue) -> None:
        super().__init__()
        self.request = request
        self.result_queue = result_queue
        self.setAutoDelete(True)

    def run(self) -> None:
        image, error = load_image_for_display(
            self.request.path,
            self.request.target_size,
            prefer_embedded=self.request.prefer_embedded,
            fits_display_settings=self.request.fits_display_settings,
        )
        if image.isNull():
            self.result_queue.put(("failed", self.request, error or "Could not decode image."))
            return
        metadata = load_capture_metadata(self.request.path) if self.request.load_metadata else None
        self.result_queue.put(("ready", self.request, image, metadata))


class PreviewPane(QWidget):
    HEART_SYMBOL = "\u2665"
    HEART_OUTLINE_SYMBOL = "\u2661"
    REJECT_SYMBOL = "\u2715"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active = False
        self._frame_visible = True
        self.setObjectName("previewPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = default_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._apply_style()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background-color: #111;")
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setMouseTracking(True)
        self.scroll_area.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.viewport().setMouseTracking(True)
        self.scroll_area.horizontalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.image_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background-color: #111;")
        self.image_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.image_label.setMouseTracking(True)
        self.scroll_area.setWidget(self.image_label)
        self.loupe_overlay = LoupeOverlay(self)

        self.footer = QWidget()
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        self.heart_button = QPushButton(self.HEART_OUTLINE_SYMBOL)
        self.heart_button.setCheckable(True)
        self.heart_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.heart_button.setFixedSize(36, 24)
        self.heart_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 8px;
                color: #f2f5f8;
                font-size: 14px;
                padding-bottom: 1px;
            }
            QPushButton:checked {
                background-color: rgba(255, 111, 125, 0.16);
                border-color: #ff6f7d;
                color: #ff6f7d;
            }
            QPushButton:hover {
                border-color: rgba(255,255,255,0.45);
            }
            QPushButton:checked:hover {
                border-color: #ff6f7d;
            }
            """
        )

        self.reject_button = QPushButton(self.REJECT_SYMBOL)
        self.reject_button.setCheckable(True)
        self.reject_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reject_button.setFixedSize(36, 24)
        self.reject_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 8px;
                color: #f2f5f8;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:checked {
                background-color: rgba(255, 102, 102, 0.18);
                border-color: #ff6666;
                color: #ff7b7b;
            }
            QPushButton:hover {
                border-color: rgba(255,255,255,0.45);
            }
            QPushButton:checked:hover {
                border-color: #ff6666;
            }
            """
        )

        text_column = QWidget()
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.caption_label = QLabel()
        self.caption_label.setStyleSheet("font-size: 12px; color: #d9e3ee;")
        self.caption_label.setWordWrap(True)

        self.metadata_label = QLabel()
        self.metadata_label.setStyleSheet("font-size: 11px; color: #9fb0c5;")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        text_layout.addWidget(self.caption_label)
        text_layout.addWidget(self.metadata_label)

        footer_layout.addWidget(self.heart_button)
        footer_layout.addWidget(text_column, 1)
        footer_layout.addWidget(self.reject_button)

        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.footer)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.scroll_area.setStyleSheet(f"background-color: {theme.image_bg.css};")
        self.image_label.setStyleSheet(f"background-color: {theme.image_bg.css};")
        self.caption_label.setStyleSheet(f"font-size: 12px; color: {theme.text_primary.css};")
        self.metadata_label.setStyleSheet(f"font-size: 11px; color: {theme.text_secondary.css};")
        self.heart_button.setStyleSheet(self._badge_button_style(theme, theme.danger))
        self.reject_button.setStyleSheet(self._badge_button_style(theme, theme.danger))
        self._apply_style()

    @staticmethod
    def _badge_button_style(theme: ThemePalette, accent) -> str:
        return f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {theme.border.with_alpha(120).css};
                border-radius: 8px;
                color: {theme.text_primary.css};
                font-size: 13px;
                font-weight: 700;
                padding-bottom: 1px;
            }}
            QPushButton:checked {{
                background-color: {accent.with_alpha(44).css};
                border-color: {accent.css};
                color: {accent.css};
            }}
            QPushButton:hover {{
                border-color: {theme.border.with_alpha(180).css};
            }}
            QPushButton:checked:hover {{
                border-color: {accent.css};
            }}
        """

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def set_frame_visible(self, visible: bool) -> None:
        self._frame_visible = visible
        self._apply_style()

    def set_minimal(self, minimal: bool) -> None:
        self.footer.setVisible(not minimal)

    def _apply_style(self) -> None:
        if not self._frame_visible:
            self.setStyleSheet(
                """
                QWidget#previewPane {
                    background-color: transparent;
                    border: none;
                    border-radius: 0px;
                }
                """
            )
            return
        if self._active:
            self.setStyleSheet(
                f"""
                QWidget#previewPane {{
                    background-color: {self._theme.raised_bg.css};
                    border: 3px solid {self._theme.accent.css};
                    border-radius: 14px;
                }}
                """
            )
            return
        self.setStyleSheet(
            f"""
            QWidget#previewPane {{
                background-color: {self._theme.panel_bg.css};
                border: 2px solid {self._theme.border.css};
                border-radius: 14px;
            }}
            """
        )


class LoupeOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._label = "150%"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(244, 244)
        self.hide()

    def set_content(self, pixmap: QPixmap, label: str) -> None:
        self._pixmap = pixmap
        self._label = label
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        panel_rect = self.rect().adjusted(4, 4, -4, -4)
        badge_rect = QRect(panel_rect.center().x() - 34, panel_rect.bottom() - 36, 68, 24)
        image_rect = QRect(panel_rect.left(), panel_rect.top(), panel_rect.width(), panel_rect.height() - 20)

        path = QPainterPath()
        path.addEllipse(image_rect)
        painter.fillPath(path, QColor(12, 16, 22, 238))
        painter.setClipPath(path)
        if not self._pixmap.isNull():
            painter.drawPixmap(image_rect, self._pixmap)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#8ab4ff"), 2))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 16, 22, 225))
        painter.drawRoundedRect(badge_rect, 12, 12)
        painter.setPen(QColor("#dbe7ff"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self._label)


class HistogramWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._theme = default_theme()
        self._stats = EMPTY_INSPECTION_STATS
        self.setMinimumHeight(148)
        self.setMaximumHeight(168)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def set_stats(self, stats: InspectionStats) -> None:
        self._stats = stats
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        panel_rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.image_bg.qcolor())
        painter.drawRoundedRect(panel_rect, 12, 12)

        plot_rect = panel_rect.adjusted(12, 12, -12, -14)
        if self._stats.width <= 0 or plot_rect.width() <= 0 or plot_rect.height() <= 0:
            painter.setPen(self._theme.text_secondary.qcolor())
            painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "Load an image to inspect")
            return

        painter.setPen(QPen(self._theme.border_muted.qcolor(), 1))
        for step in range(1, 4):
            x = plot_rect.left() + (plot_rect.width() * step // 4)
            painter.drawLine(x, plot_rect.top(), x, plot_rect.bottom())
        for step in range(1, 3):
            y = plot_rect.bottom() - (plot_rect.height() * step // 3)
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)

        max_value = max(
            max(self._stats.histogram_luma),
            max(self._stats.histogram_red),
            max(self._stats.histogram_green),
            max(self._stats.histogram_blue),
            1,
        )

        luma_path = _histogram_path(self._stats.histogram_luma, plot_rect, max_value, closed=True)
        painter.fillPath(luma_path, self._theme.accent_soft.qcolor())

        painter.setPen(QPen(QColor(255, 96, 96, 176), 1.4))
        painter.drawPath(_histogram_path(self._stats.histogram_red, plot_rect, max_value))
        painter.setPen(QPen(QColor(103, 211, 137, 176), 1.4))
        painter.drawPath(_histogram_path(self._stats.histogram_green, plot_rect, max_value))
        painter.setPen(QPen(QColor(97, 177, 255, 184), 1.4))
        painter.drawPath(_histogram_path(self._stats.histogram_blue, plot_rect, max_value))
        painter.setPen(QPen(self._theme.text_primary.qcolor(), 1.5))
        painter.drawPath(_histogram_path(self._stats.histogram_luma, plot_rect, max_value))


class FullScreenPreview(QDialog):
    FOCUS_ASSIST_COLOR_KEY = "preview/focus_assist_color"
    FOCUS_ASSIST_STRENGTH_KEY = "preview/focus_assist_strength"
    FOCUS_ASSIST_DIM_BACKGROUND_KEY = "preview/focus_assist_dim_background"
    FITS_STF_PRESET_KEY = "preview/fits_stf_preset"
    navigation_requested = Signal(int)
    compare_mode_changed = Signal(bool)
    auto_bracket_mode_changed = Signal(bool)
    compare_count_changed = Signal(int)
    command_palette_requested = Signal()
    photoshop_requested = Signal(str)
    winner_requested = Signal(str)
    reject_requested = Signal(str)
    keep_requested = Signal(str)
    delete_requested = Signal(str)
    move_requested = Signal(str)
    rate_requested = Signal(str, int)
    tag_requested = Signal(str)
    winner_ladder_choice_requested = Signal(str)
    winner_ladder_skip_requested = Signal()
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[PreviewEntry] = []
        self._source_entries: list[PreviewEntry] = []
        self._current_images: list[QImage] = []
        self._current_metadata: list[CaptureMetadata] = []
        self._source_versions: list[tuple[int, int] | None] = []
        self._metadata_cache: dict[str, CaptureMetadata] = {}
        self._load_token = 0
        self._pending_requests = 0
        self._pending_zoom_refresh_slots: list[int] = []
        self._compare_mode = False
        self._before_after_enabled = False
        self._compare_count = 3
        self._edited_variant_index = 0
        self._focused_slot = 0
        self._photoshop_available = False
        self._settings = QSettings()
        self._manual_zoom = False
        self._zoom_scale = 1.0
        self._focus_assist_enabled = False
        self._focus_assist_color = focus_assist_color_by_id(
            self._settings.value(self.FOCUS_ASSIST_COLOR_KEY, DEFAULT_FOCUS_ASSIST_COLOR_ID, str)
        )
        self._focus_assist_strength = focus_assist_strength_by_id(
            self._settings.value(self.FOCUS_ASSIST_STRENGTH_KEY, DEFAULT_FOCUS_ASSIST_STRENGTH_ID, str)
        )
        self._focus_assist_dim_background = self._settings.value(
            self.FOCUS_ASSIST_DIM_BACKGROUND_KEY,
            True,
            bool,
        )
        self._fits_display_settings = FitsDisplaySettings(
            stf_preset_id=self._settings.value(
                self.FITS_STF_PRESET_KEY,
                FitsDisplaySettings().stf_preset_id,
                str,
            )
        )
        self._loupe_enabled = False
        self._loupe_zoom_levels = (1.25, 1.5, 2.0, 3.0)
        self._loupe_zoom_index = 1
        self._loupe_zoom = self._loupe_zoom_levels[self._loupe_zoom_index]
        self._loupe_slot = -1
        self._loupe_global_pos = QPoint()
        self._dragging = False
        self._drag_start_global_pos = QPoint()
        self._drag_start_scrolls: list[QPoint] = []
        self._pending_right_close = False
        self._auto_advance_enabled = True
        self._winner_ladder_mode = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._result_queue: SimpleQueue = SimpleQueue()
        self._preview_cache: OrderedDict[tuple[object, ...], tuple[QImage, int]] = OrderedDict()
        self._preview_cache_bytes = 0
        self._preview_cache_limit = 320 * 1024 * 1024
        self._pending_cache_keys: set[tuple[object, ...]] = set()
        self._current_placeholder_flags: list[bool] = []
        self._current_image_display_tokens: list[tuple[object, ...]] = []
        self._rendered_display_keys: list[tuple[object, ...] | None] = []
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(12)
        self._drain_timer.timeout.connect(self._drain_results)
        self._zoom_request_timer = QTimer(self)
        self._zoom_request_timer.setSingleShot(True)
        self._zoom_request_timer.setInterval(90)
        self._zoom_request_timer.timeout.connect(self._request_zoom_resolution_refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1200)
        self._refresh_timer.timeout.connect(self._poll_source_updates)
        self._refresh_interval_active_ms = 1200
        self._refresh_interval_idle_ms = 3200
        self._refresh_interval_background_ms = 8400
        self._stable_poll_cycles = 0
        self._poll_round_robin_slot = 0
        self._next_edited_discovery_at = 0.0
        self._edited_discovery_requested = False
        self._edited_discovery_interval_found_s = 8.5
        self._edited_discovery_interval_missing_s = 17.0
        self._edited_discovery_interval_with_candidates_s = 12.0
        self._panes: list[PreviewPane] = []
        self._watched_widgets: dict[object, int] = {}
        self._inspection_stats_cache: dict[tuple[object, ...], InspectionStats] = {}
        self._focus_assist_cache: dict[tuple[object, ...], QImage] = {}
        self._theme = default_theme()

        self.setWindowTitle("Preview")
        self.setModal(False)
        self.setStyleSheet("background-color: #111; color: white;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(12)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("previewHeaderBar")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)

        self.compare_toggle_button = QPushButton("Compare")
        self.compare_toggle_button.setCheckable(True)
        self.compare_toggle_button.setChecked(False)
        self.compare_toggle_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compare_toggle_button.toggled.connect(self._handle_compare_button_toggled)
        self.compare_toggle_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            QPushButton:checked:hover {
                background-color: #1d4ed8;
                border-color: #1d4ed8;
            }
            """
        )

        self.auto_bracket_button = QPushButton("Auto-Bracket")
        self.auto_bracket_button.setCheckable(True)
        self.auto_bracket_button.setChecked(False)
        self.auto_bracket_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_bracket_button.toggled.connect(self._handle_auto_bracket_button_toggled)
        self.auto_bracket_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            QPushButton:checked:hover {
                background-color: #1d4ed8;
                border-color: #1d4ed8;
            }
            """
        )

        self.compare_count_combo = QComboBox()
        for count in COMPARE_COUNTS:
            self.compare_count_combo.addItem(f"{count}-Up", count)
        self.compare_count_combo.setCurrentText("3-Up")
        self.compare_count_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compare_count_combo.setMinimumWidth(92)
        self.compare_count_combo.currentIndexChanged.connect(self._handle_compare_count_changed)
        self.compare_count_combo.setEnabled(False)

        self.photoshop_button = QPushButton("Photoshop")
        self.photoshop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.photoshop_button.clicked.connect(self._handle_photoshop_button_clicked)
        self.photoshop_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            QPushButton:disabled {
                color: #7d8897;
                border-color: #313a47;
            }
            """
        )

        self.before_after_button = QPushButton("Before/After")
        self.before_after_button.setCheckable(True)
        self.before_after_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.before_after_button.toggled.connect(self._handle_before_after_button_toggled)
        self.before_after_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            """
        )

        self.focus_assist_button = QPushButton("Off")
        self.focus_assist_button.setCheckable(True)
        self.focus_assist_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_assist_button.setMinimumWidth(68)
        self.focus_assist_button.toggled.connect(self._handle_focus_assist_button_toggled)
        self.focus_assist_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            """
        )

        self.focus_assist_background_button = QPushButton("Dimmed" if self._focus_assist_dim_background else "Original")
        self.focus_assist_background_button.setCheckable(True)
        self.focus_assist_background_button.setChecked(self._focus_assist_dim_background)
        self.focus_assist_background_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_assist_background_button.setMinimumWidth(88)
        self.focus_assist_background_button.toggled.connect(self._handle_focus_assist_background_toggled)
        self.focus_assist_background_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            """
        )

        self.focus_assist_color_combo = QComboBox()
        self.focus_assist_color_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_assist_color_combo.setMinimumWidth(96)
        for color in FOCUS_ASSIST_COLORS:
            self.focus_assist_color_combo.addItem(color.label, color.id)
        color_index = self.focus_assist_color_combo.findData(self._focus_assist_color.id)
        if color_index >= 0:
            self.focus_assist_color_combo.setCurrentIndex(color_index)
        self.focus_assist_color_combo.currentIndexChanged.connect(self._handle_focus_assist_color_changed)

        self.focus_assist_strength_combo = QComboBox()
        self.focus_assist_strength_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_assist_strength_combo.setMinimumWidth(104)
        for strength in FOCUS_ASSIST_STRENGTHS:
            self.focus_assist_strength_combo.addItem(strength.label, strength.id)
        strength_index = self.focus_assist_strength_combo.findData(self._focus_assist_strength.id)
        if strength_index >= 0:
            self.focus_assist_strength_combo.setCurrentIndex(strength_index)
        self.focus_assist_strength_combo.currentIndexChanged.connect(self._handle_focus_assist_strength_changed)

        self.next_edit_button = QPushButton("Edit 1/1")
        self.next_edit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.next_edit_button.clicked.connect(self._cycle_edited_variant)
        self.next_edit_button.hide()
        self.next_edit_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a5568;
                border-radius: 6px;
                color: #f2f5f8;
                padding: 4px 12px;
            }
            QPushButton:hover {
                border-color: #6f7f95;
            }
            QPushButton:disabled {
                color: #7d8897;
                border-color: #313a47;
            }
            """
        )

        self.review_group = QWidget()
        self.review_group.setObjectName("previewToolbarGroup")
        review_group_layout = QHBoxLayout(self.review_group)
        review_group_layout.setContentsMargins(0, 0, 0, 0)
        review_group_layout.setSpacing(8)
        self.review_group_label = QLabel("Review")
        self.review_group_label.setObjectName("previewHeaderLabel")
        review_group_layout.addWidget(self.review_group_label)
        review_group_layout.addWidget(self.compare_toggle_button)
        review_group_layout.addWidget(self.auto_bracket_button)
        review_group_layout.addWidget(self.before_after_button)

        self.edit_group = QWidget()
        self.edit_group.setObjectName("previewToolbarGroup")
        edit_group_layout = QHBoxLayout(self.edit_group)
        edit_group_layout.setContentsMargins(0, 0, 0, 0)
        edit_group_layout.setSpacing(8)
        self.edit_group_label = QLabel("Edit")
        self.edit_group_label.setObjectName("previewHeaderLabel")
        edit_group_layout.addWidget(self.edit_group_label)
        edit_group_layout.addWidget(self.next_edit_button)
        edit_group_layout.addWidget(self.photoshop_button)

        self.layout_group = QWidget()
        self.layout_group.setObjectName("previewToolbarGroup")
        layout_group_layout = QHBoxLayout(self.layout_group)
        layout_group_layout.setContentsMargins(0, 0, 0, 0)
        layout_group_layout.setSpacing(8)
        self.layout_group_label = QLabel("Layout")
        self.layout_group_label.setObjectName("previewHeaderLabel")
        layout_group_layout.addWidget(self.layout_group_label)
        layout_group_layout.addWidget(self.compare_count_combo)

        header_layout.addStretch(1)
        header_layout.addWidget(self.review_group)
        header_layout.addWidget(self.edit_group)
        header_layout.addWidget(self.layout_group)

        self.content_widget = QWidget()
        self._content_layout = QHBoxLayout(self.content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(14)

        self.panes_widget = QWidget()
        self.panes_layout = QGridLayout(self.panes_widget)
        self.panes_layout.setContentsMargins(0, 0, 0, 0)
        self.panes_layout.setHorizontalSpacing(12)
        self.panes_layout.setVerticalSpacing(12)
        self._content_layout.addWidget(self.panes_widget, 1)

        self.analysis_panel = QFrame()
        self.analysis_panel.setObjectName("previewAnalysisPanel")
        self.analysis_panel.setMinimumWidth(260)
        self.analysis_panel.setMaximumWidth(304)
        analysis_layout = QVBoxLayout(self.analysis_panel)
        analysis_layout.setContentsMargins(14, 14, 14, 14)
        analysis_layout.setSpacing(8)

        self.analysis_title_label = QLabel("Inspection")
        self.analysis_title_label.setObjectName("previewAnalysisTitle")
        self.analysis_subtitle_label = QLabel("Focused image analysis")
        self.analysis_subtitle_label.setObjectName("previewAnalysisSubtitle")
        self.analysis_subtitle_label.setWordWrap(True)

        self.focus_controls_card = QFrame()
        self.focus_controls_card.setObjectName("previewControlsCard")
        focus_controls_layout = QVBoxLayout(self.focus_controls_card)
        focus_controls_layout.setContentsMargins(12, 12, 12, 12)
        focus_controls_layout.setSpacing(8)

        self.focus_controls_title_label = QLabel("Focus Peaking")
        self.focus_controls_title_label.setObjectName("previewControlsTitle")
        self.focus_controls_summary_label = QLabel("Off")
        self.focus_controls_summary_label.setObjectName("previewControlsSummary")

        self.focus_enable_row = QWidget()
        focus_enable_layout = QHBoxLayout(self.focus_enable_row)
        focus_enable_layout.setContentsMargins(0, 0, 0, 0)
        focus_enable_layout.setSpacing(10)
        self.focus_enable_label = QLabel("Enabled")
        self.focus_enable_label.setObjectName("previewControlLabel")
        focus_enable_layout.addWidget(self.focus_enable_label)
        focus_enable_layout.addStretch(1)
        focus_enable_layout.addWidget(self.focus_assist_button)

        self.focus_color_row = QWidget()
        focus_color_layout = QHBoxLayout(self.focus_color_row)
        focus_color_layout.setContentsMargins(0, 0, 0, 0)
        focus_color_layout.setSpacing(10)
        self.focus_color_label = QLabel("Color")
        self.focus_color_label.setObjectName("previewControlLabel")
        focus_color_layout.addWidget(self.focus_color_label)
        focus_color_layout.addStretch(1)
        focus_color_layout.addWidget(self.focus_assist_color_combo)

        self.focus_strength_row = QWidget()
        focus_strength_layout = QHBoxLayout(self.focus_strength_row)
        focus_strength_layout.setContentsMargins(0, 0, 0, 0)
        focus_strength_layout.setSpacing(10)
        self.focus_strength_label = QLabel("Sensitivity")
        self.focus_strength_label.setObjectName("previewControlLabel")
        focus_strength_layout.addWidget(self.focus_strength_label)
        focus_strength_layout.addStretch(1)
        focus_strength_layout.addWidget(self.focus_assist_strength_combo)

        self.focus_background_row = QWidget()
        focus_background_layout = QHBoxLayout(self.focus_background_row)
        focus_background_layout.setContentsMargins(0, 0, 0, 0)
        focus_background_layout.setSpacing(10)
        self.focus_background_label = QLabel("Background")
        self.focus_background_label.setObjectName("previewControlLabel")
        focus_background_layout.addWidget(self.focus_background_label)
        focus_background_layout.addStretch(1)
        focus_background_layout.addWidget(self.focus_assist_background_button)

        focus_controls_layout.addWidget(self.focus_controls_title_label)
        focus_controls_layout.addWidget(self.focus_controls_summary_label)
        focus_controls_layout.addWidget(self.focus_enable_row)
        focus_controls_layout.addWidget(self.focus_color_row)
        focus_controls_layout.addWidget(self.focus_strength_row)
        focus_controls_layout.addWidget(self.focus_background_row)

        self.fits_controls_card = QFrame()
        self.fits_controls_card.setObjectName("previewControlsCard")
        fits_controls_layout = QVBoxLayout(self.fits_controls_card)
        fits_controls_layout.setContentsMargins(12, 12, 12, 12)
        fits_controls_layout.setSpacing(8)

        self.fits_controls_title_label = QLabel("FITS Display")
        self.fits_controls_title_label.setObjectName("previewControlsTitle")
        self.fits_controls_summary_label = QLabel("Auto STF")
        self.fits_controls_summary_label.setObjectName("previewControlsSummary")

        self.fits_stf_row = QWidget()
        fits_stf_layout = QHBoxLayout(self.fits_stf_row)
        fits_stf_layout.setContentsMargins(0, 0, 0, 0)
        fits_stf_layout.setSpacing(10)
        self.fits_stf_label = QLabel("Stretch")
        self.fits_stf_label.setObjectName("previewControlLabel")
        self.fits_stf_combo = QComboBox()
        self.fits_stf_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fits_stf_combo.setMinimumWidth(132)
        for preset in FITS_STF_PRESETS:
            self.fits_stf_combo.addItem(preset.label, preset.id)
        fits_preset_index = self.fits_stf_combo.findData(self._fits_display_settings.preset.id)
        if fits_preset_index >= 0:
            self.fits_stf_combo.setCurrentIndex(fits_preset_index)
        self.fits_stf_combo.currentIndexChanged.connect(self._handle_fits_stf_changed)
        fits_stf_layout.addWidget(self.fits_stf_label)
        fits_stf_layout.addStretch(1)
        fits_stf_layout.addWidget(self.fits_stf_combo)

        self.fits_reset_button = QPushButton("Reset")
        self.fits_reset_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fits_reset_button.clicked.connect(self._handle_fits_stf_reset)

        fits_controls_layout.addWidget(self.fits_controls_title_label)
        fits_controls_layout.addWidget(self.fits_controls_summary_label)
        fits_controls_layout.addWidget(self.fits_stf_row)
        fits_controls_layout.addWidget(self.fits_reset_button, 0, Qt.AlignmentFlag.AlignRight)

        self.histogram_widget = HistogramWidget(self.analysis_panel)
        self.inspection_dimensions_label = QLabel("Size: --")
        self.inspection_dimensions_label.setObjectName("previewAnalysisValue")
        self.inspection_exposure_label = QLabel("Exposure: --")
        self.inspection_exposure_label.setObjectName("previewAnalysisValue")
        self.inspection_clipping_label = QLabel("Clipping: --")
        self.inspection_clipping_label.setObjectName("previewAnalysisValue")
        self.inspection_detail_label = QLabel("Detail: --")
        self.inspection_detail_label.setObjectName("previewAnalysisValue")
        self.ai_explanation_card = QFrame()
        self.ai_explanation_card.setObjectName("previewControlsCard")
        explanation_layout = QVBoxLayout(self.ai_explanation_card)
        explanation_layout.setContentsMargins(12, 12, 12, 12)
        explanation_layout.setSpacing(6)
        self.ai_explanation_title_label = QLabel("Why AI Picked This")
        self.ai_explanation_title_label.setObjectName("previewControlsTitle")
        self.ai_confidence_label = QLabel("Confidence: --")
        self.ai_confidence_label.setObjectName("previewControlsSummary")
        self.ai_explanation_label = QLabel("Load an AI-scored image to see ranking rationale.")
        self.ai_explanation_label.setObjectName("previewAnalysisHint")
        self.ai_explanation_label.setWordWrap(True)
        explanation_layout.addWidget(self.ai_explanation_title_label)
        explanation_layout.addWidget(self.ai_confidence_label)
        explanation_layout.addWidget(self.ai_explanation_label)
        self.inspection_hint_label = QLabel(
            "Histogram follows the focused pane. Focus Peaking settings live in the inspection card."
        )
        self.inspection_hint_label.setObjectName("previewAnalysisHint")
        self.inspection_hint_label.setWordWrap(True)

        analysis_layout.addWidget(self.analysis_title_label)
        analysis_layout.addWidget(self.analysis_subtitle_label)
        analysis_layout.addWidget(self.focus_controls_card)
        analysis_layout.addWidget(self.fits_controls_card)
        analysis_layout.addWidget(self.histogram_widget)
        analysis_layout.addWidget(self.inspection_dimensions_label)
        analysis_layout.addWidget(self.inspection_exposure_label)
        analysis_layout.addWidget(self.inspection_clipping_label)
        analysis_layout.addWidget(self.inspection_detail_label)
        analysis_layout.addWidget(self.ai_explanation_card)
        analysis_layout.addStretch(1)
        analysis_layout.addWidget(self.inspection_hint_label)

        self._content_layout.addWidget(self.analysis_panel, 0)

        self.info_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet("font-size: 14px; color: #ddd;")
        self.info_label.setWordWrap(True)

        root_layout.addWidget(self.header_widget)
        root_layout.addWidget(self.content_widget, 1)
        root_layout.addWidget(self.info_label)
        self._sync_preview_controls()
        self.apply_theme(self._theme)

    def _toggle_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {self._theme.border.css};
                border-radius: 8px;
                color: {self._theme.text_primary.css};
                padding: 4px 12px;
            }}
            QPushButton:checked {{
                background-color: {self._theme.accent_soft.css};
                border-color: {self._theme.accent.css};
                color: {self._theme.text_primary.css};
            }}
            QPushButton:hover {{
                border-color: {self._theme.selection_outline.css};
            }}
            QPushButton:checked:hover {{
                border-color: {self._theme.accent_hover.css};
            }}
            QPushButton:disabled {{
                color: {self._theme.text_disabled.css};
                border-color: {self._theme.border_muted.css};
            }}
        """

    def _action_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {self._theme.border.css};
                border-radius: 8px;
                color: {self._theme.text_primary.css};
                padding: 4px 12px;
            }}
            QPushButton:hover {{
                border-color: {self._theme.selection_outline.css};
            }}
            QPushButton:disabled {{
                color: {self._theme.text_disabled.css};
                border-color: {self._theme.border_muted.css};
            }}
        """

    def _combo_box_style(self) -> str:
        return f"""
            QComboBox {{
                background-color: {self._theme.input_bg.css};
                border: 1px solid {self._theme.border.css};
                border-radius: 8px;
                color: {self._theme.text_primary.css};
                padding: 4px 8px;
            }}
            QComboBox:hover {{
                border-color: {self._theme.selection_outline.css};
            }}
            QComboBox:disabled {{
                color: {self._theme.text_disabled.css};
                border-color: {self._theme.border_muted.css};
            }}
            QComboBox QAbstractItemView {{
                background-color: {self._theme.panel_bg.css};
                border: 1px solid {self._theme.border.css};
                color: {self._theme.text_primary.css};
                selection-background-color: {self._theme.selection_fill.css};
                selection-color: {self._theme.text_primary.css};
            }}
        """

    def _entry_supports_fits_stf(self, entry: PreviewEntry | None) -> bool:
        if entry is None or not entry.source_path:
            return False
        return suffix_for_path(entry.source_path) in FITS_SUFFIXES

    def _fits_display_settings_for_path(self, path: str) -> FitsDisplaySettings | None:
        if suffix_for_path(path) not in FITS_SUFFIXES:
            return None
        return self._fits_display_settings

    def _fits_display_settings_for_entry(self, entry: PreviewEntry | None) -> FitsDisplaySettings | None:
        if entry is None:
            return None
        return self._fits_display_settings_for_path(entry.source_path)

    def _focused_entry_supports_fits_stf(self) -> bool:
        if not self._entries or not 0 <= self._focused_slot < len(self._entries):
            return False
        return self._entry_supports_fits_stf(self._entries[self._focused_slot])

    def _fits_entry_slots(self) -> list[int]:
        return [slot for slot, entry in enumerate(self._entries) if self._entry_supports_fits_stf(entry)]

    def _fits_display_cache_key_for_path(
        self,
        path: str,
        fits_display_settings: FitsDisplaySettings | None = None,
    ) -> tuple[object, ...]:
        settings = fits_display_settings if fits_display_settings is not None else self._fits_display_settings_for_path(path)
        return settings.cache_key() if settings is not None else ()

    def _sync_preview_controls(self) -> None:
        edited_candidates = self._edited_candidates_for_entry(self._source_entries[0]) if self._source_entries else ()
        before_after_visible = (not self._compare_mode) and len(self._source_entries) == 1 and bool(edited_candidates)
        self.before_after_button.setVisible(before_after_visible)
        self.layout_group.setVisible(self._compare_mode)
        self.compare_count_combo.setVisible(self._compare_mode)
        self.focus_assist_button.setText("On" if self._focus_assist_enabled else "Off")
        self.focus_assist_background_button.setText("Dimmed" if self._focus_assist_dim_background else "Original")
        advanced_focus_visible = self._focus_assist_enabled
        self.focus_color_row.setVisible(advanced_focus_visible)
        self.focus_strength_row.setVisible(advanced_focus_visible)
        self.focus_background_row.setVisible(advanced_focus_visible)

        if not self._focus_assist_enabled:
            summary = "Off"
        else:
            summary = (
                f"{self._focus_assist_color.label} | "
                f"{self._focus_assist_strength.label} | "
                f"{'Dimmed' if self._focus_assist_dim_background else 'Original'} background"
            )
        self.focus_controls_summary_label.setText(summary)
        fits_controls_visible = self._focused_entry_supports_fits_stf()
        self.fits_controls_card.setVisible(fits_controls_visible)
        self.fits_controls_summary_label.setText(self._fits_display_settings.preset.label)
        with QSignalBlocker(self.fits_stf_combo):
            fits_preset_index = self.fits_stf_combo.findData(self._fits_display_settings.preset.id)
            if fits_preset_index >= 0:
                self.fits_stf_combo.setCurrentIndex(fits_preset_index)
        self.fits_reset_button.setEnabled(self._fits_display_settings.preset.id != FitsDisplaySettings().preset.id)
        self.header_widget.setVisible(True)
        self.analysis_panel.setVisible(True)
        hint_text = "Histogram follows the focused pane. Focus Peaking settings live in the inspection card."
        if fits_controls_visible:
            hint_text = "Histogram follows the focused pane. FITS display stretch changes the preview only."
        self.inspection_hint_label.setText(hint_text)
        for pane in self._panes[: len(self._entries)]:
            pane.set_minimal(False)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"background-color: {theme.image_bg.css}; color: {theme.text_primary.css};")
        self.header_widget.setStyleSheet(
            f"""
            QWidget#previewHeaderBar {{
                background-color: {theme.toolbar_bg.css};
                border: 1px solid {theme.border.css};
                border-radius: 12px;
            }}
            QWidget#previewToolbarGroup {{
                background-color: transparent;
                border: none;
            }}
            QLabel#previewHeaderLabel {{
                color: {theme.text_muted.css};
                font-size: 11px;
                font-weight: 600;
                background-color: transparent;
                padding: 0 2px 0 0;
            }}
            """
        )
        self.content_widget.setStyleSheet("background-color: transparent;")
        self.info_label.setStyleSheet(f"font-size: 14px; color: {theme.text_secondary.css};")
        self.compare_toggle_button.setStyleSheet(self._toggle_button_style())
        self.auto_bracket_button.setStyleSheet(self._toggle_button_style())
        self.before_after_button.setStyleSheet(self._toggle_button_style())
        self.focus_assist_button.setStyleSheet(self._toggle_button_style())
        self.focus_assist_background_button.setStyleSheet(self._toggle_button_style())
        self.photoshop_button.setStyleSheet(self._action_button_style())
        self.next_edit_button.setStyleSheet(self._action_button_style())
        combo_style = self._combo_box_style()
        self.focus_assist_color_combo.setStyleSheet(combo_style)
        self.focus_assist_strength_combo.setStyleSheet(combo_style)
        self.compare_count_combo.setStyleSheet(combo_style)
        self.fits_stf_combo.setStyleSheet(combo_style)
        self.analysis_panel.setStyleSheet(
            f"""
            QFrame#previewAnalysisPanel {{
                background-color: {theme.panel_bg.css};
                border: 1px solid {theme.border.css};
                border-radius: 14px;
            }}
            QLabel#previewAnalysisTitle {{
                color: {theme.text_primary.css};
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#previewAnalysisSubtitle {{
                color: {theme.text_secondary.css};
                font-size: 12px;
            }}
            QFrame#previewControlsCard {{
                background-color: {theme.panel_alt_bg.css};
                border: 1px solid {theme.border.css};
                border-radius: 12px;
            }}
            QLabel#previewControlsTitle {{
                color: {theme.text_primary.css};
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#previewControlsSummary {{
                color: {theme.text_secondary.css};
                font-size: 11px;
                padding-bottom: 2px;
            }}
            QLabel#previewControlLabel {{
                color: {theme.text_muted.css};
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#previewAnalysisValue {{
                color: {theme.text_primary.css};
                font-size: 12px;
            }}
            QLabel#previewAnalysisHint {{
                color: {theme.text_secondary.css};
                font-size: 11px;
            }}
            """
        )
        self.histogram_widget.apply_theme(theme)
        self.fits_reset_button.setStyleSheet(self._action_button_style())
        for pane in self._panes:
            pane.apply_theme(theme)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Tab and self._compare_mode and len(self._entries) > 1:
                self.keyPressEvent(event)
                return event.isAccepted()
        return super().event(event)

    def compare_mode(self) -> bool:
        return self._compare_mode

    def compare_count(self) -> int:
        return self._compare_count

    def set_compare_mode(self, enabled: bool) -> None:
        if self._compare_mode == enabled:
            return
        self._compare_mode = enabled
        if not enabled:
            self._winner_ladder_mode = False
        if enabled and self._before_after_enabled:
            self._before_after_enabled = False
            with QSignalBlocker(self.before_after_button):
                self.before_after_button.setChecked(False)
        self.compare_count_combo.setEnabled(enabled)
        with QSignalBlocker(self.compare_toggle_button):
            self.compare_toggle_button.setChecked(enabled)
        self._rebuild_entries()
        self._sync_preview_controls()
        self._update_layout()
        self._update_info_label()

    def set_compare_count(self, count: int) -> None:
        if self._compare_count == count:
            return
        self._compare_count = count
        combo_index = self.compare_count_combo.findData(count)
        if combo_index >= 0 and combo_index != self.compare_count_combo.currentIndex():
            self.compare_count_combo.setCurrentIndex(combo_index)
        self._update_layout()
        self._update_info_label()

    def set_auto_bracket_mode(self, enabled: bool) -> None:
        with QSignalBlocker(self.auto_bracket_button):
            self.auto_bracket_button.setChecked(enabled)

    def set_auto_advance_enabled(self, enabled: bool) -> None:
        self._auto_advance_enabled = bool(enabled)

    def set_photoshop_available(self, available: bool) -> None:
        self._photoshop_available = available
        self.photoshop_button.setEnabled(available)
        if available:
            self.photoshop_button.setText("Photoshop")
        else:
            self.photoshop_button.setText("Photoshop Not Found")

    def show_entries(self, entries: list[PreviewEntry]) -> None:
        self._source_entries = list(entries)
        if len(entries) < 2:
            self._winner_ladder_mode = False
        self._stable_poll_cycles = 0
        self._poll_round_robin_slot = 0
        self._edited_discovery_requested = True
        self._next_edited_discovery_at = time.monotonic() + 1.8
        self._refresh_timer.setInterval(self._refresh_interval_active_ms)
        self._focused_slot = 0
        self._manual_zoom = False
        self._zoom_scale = 1.0
        self._dragging = False
        self._pending_right_close = False
        self._edited_variant_index = 0
        self._rebuild_entries()
        self._sync_preview_controls()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._refresh_timer.start()
        QTimer.singleShot(0, self._request_preview_loads)

    def eventFilter(self, watched, event) -> bool:
        pane_index = self._watched_widgets.get(watched)
        if pane_index is None:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            self.keyPressEvent(event)
            return event.isAccepted()

        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            self._handle_wheel_zoom(event.angleDelta().y())
            event.accept()
            return True

        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.RightButton:
                self._pending_right_close = True
                event.accept()
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                self._set_focused_slot(pane_index)
                return self._handle_mouse_press(event)

        if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            self._update_loupe(pane_index, event.globalPosition().toPoint())
            return self._handle_mouse_move(event)

        if event.type() == QEvent.Type.Leave:
            self._hide_loupe(pane_index)

        if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.RightButton and self._pending_right_close:
                self._pending_right_close = False
                self.close()
                event.accept()
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                return self._handle_mouse_release()

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._pending_right_close = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._pending_right_close:
            self._pending_right_close = False
            self.close()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._refresh_timer.stop()
        self._stable_poll_cycles = 0
        self._poll_round_robin_slot = 0
        self._edited_discovery_requested = False
        self._zoom_request_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        review_shortcut_allowed = not bool(
            modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.close()
            event.accept()
            return
        if key == Qt.Key.Key_Tab and self._compare_mode and len(self._entries) > 1:
            step = -1 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1
            self._set_focused_slot((self._focused_slot + step) % len(self._entries))
            event.accept()
            return
        if self._winner_ladder_mode:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_A) and self._entries:
                self.winner_ladder_choice_requested.emit(self._entries[0].record.path)
                event.accept()
                return
            if key in (Qt.Key.Key_Right, Qt.Key.Key_D) and len(self._entries) > 1:
                self.winner_ladder_choice_requested.emit(self._entries[1].record.path)
                event.accept()
                return
            if key == Qt.Key.Key_W and review_shortcut_allowed:
                path = self._focused_path()
                if path:
                    self.winner_ladder_choice_requested.emit(path)
                    event.accept()
                    return
            if key == Qt.Key.Key_N and review_shortcut_allowed:
                self.winner_ladder_skip_requested.emit()
                event.accept()
                return
        if self._compare_mode:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
                self.navigation_requested.emit(-max(1, self._compare_count))
                event.accept()
                return
            if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
                self.navigation_requested.emit(max(1, self._compare_count))
                event.accept()
                return
            if key == Qt.Key.Key_Up:
                self.navigation_requested.emit(-1)
                event.accept()
                return
            if key == Qt.Key.Key_Down:
                self.navigation_requested.emit(1)
                event.accept()
                return
        else:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
                self.navigation_requested.emit(-1)
                event.accept()
                return
            if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
                self.navigation_requested.emit(1)
                event.accept()
                return
        if key in (Qt.Key.Key_Z, Qt.Key.Key_1):
            self._toggle_zoom()
            event.accept()
            return
        if key == Qt.Key.Key_L and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._cycle_loupe_zoom()
            event.accept()
            return
        if key == Qt.Key.Key_L:
            self._toggle_loupe()
            event.accept()
            return
        if key == Qt.Key.Key_F and not bool(
            modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        ):
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                self.cycle_focus_assist_color()
            else:
                self.toggle_focus_assist_command()
            event.accept()
            return
        if key == Qt.Key.Key_0:
            self._set_fit_mode()
            event.accept()
            return
        if key == Qt.Key.Key_W and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.winner_requested.emit(path)
                if self._should_auto_advance_after_review():
                    self.navigation_requested.emit(1)
                event.accept()
                return
        if key == Qt.Key.Key_X and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.reject_requested.emit(path)
                if self._should_auto_advance_after_review():
                    self.navigation_requested.emit(1)
                event.accept()
                return
        if key == Qt.Key.Key_K and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.keep_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_Delete and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.delete_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_M and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.move_requested.emit(path)
                event.accept()
                return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_5 and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.rate_requested.emit(path, key - Qt.Key.Key_0)
                if self._should_auto_advance_after_review():
                    self.navigation_requested.emit(1)
                event.accept()
                return
        if key == Qt.Key.Key_T and review_shortcut_allowed:
            path = self._focused_path()
            if path:
                self.tag_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_C and review_shortcut_allowed:
            self.compare_mode_changed.emit(not self._compare_mode)
            event.accept()
            return
        super().keyPressEvent(event)

    def _should_auto_advance_after_review(self) -> bool:
        return (
            self._auto_advance_enabled
            and not self._compare_mode
            and not self._before_after_enabled
            and not self._winner_ladder_mode
            and len(self._entries) == 1
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_all()

    def _handle_compare_count_changed(self) -> None:
        selected = self.compare_count_combo.currentData()
        if isinstance(selected, int) and selected != self._compare_count:
            self._compare_count = selected
            self.compare_count_changed.emit(selected)
            self._update_info_label()

    def _handle_compare_button_toggled(self, checked: bool) -> None:
        self.compare_mode_changed.emit(checked)

    def _handle_auto_bracket_button_toggled(self, checked: bool) -> None:
        self.auto_bracket_mode_changed.emit(checked)

    def _handle_photoshop_button_clicked(self) -> None:
        path = self._focused_photoshop_path()
        if path and self._photoshop_available:
            self.photoshop_requested.emit(path)

    def _handle_before_after_button_toggled(self, checked: bool) -> None:
        self._before_after_enabled = checked
        if checked:
            self._edited_variant_index = 0
        self._rebuild_entries()
        self._sync_preview_controls()
        self._request_preview_loads()

    def _handle_focus_assist_button_toggled(self, checked: bool) -> None:
        self._focus_assist_enabled = checked
        self._hide_all_loupes()
        self._sync_preview_controls()
        self._render_all()

    def _handle_focus_assist_background_toggled(self, checked: bool) -> None:
        self._focus_assist_dim_background = checked
        self._settings.setValue(self.FOCUS_ASSIST_DIM_BACKGROUND_KEY, checked)
        self._focus_assist_cache.clear()
        self._sync_preview_controls()
        self._update_analysis_panel()
        if self._focus_assist_enabled:
            self._render_all()

    def _handle_focus_assist_color_changed(self) -> None:
        selected = self.focus_assist_color_combo.currentData()
        if not isinstance(selected, str):
            return
        color = focus_assist_color_by_id(selected)
        if color.id == self._focus_assist_color.id:
            return
        self._focus_assist_color = color
        self._settings.setValue(self.FOCUS_ASSIST_COLOR_KEY, color.id)
        self._focus_assist_cache.clear()
        self._sync_preview_controls()
        self._update_analysis_panel()
        if self._focus_assist_enabled:
            self._render_all()

    def _handle_focus_assist_strength_changed(self) -> None:
        selected = self.focus_assist_strength_combo.currentData()
        if not isinstance(selected, str):
            return
        strength = focus_assist_strength_by_id(selected)
        if strength.id == self._focus_assist_strength.id:
            return
        self._focus_assist_strength = strength
        self._settings.setValue(self.FOCUS_ASSIST_STRENGTH_KEY, strength.id)
        self._focus_assist_cache.clear()
        self._sync_preview_controls()
        self._update_analysis_panel()
        if self._focus_assist_enabled:
            self._render_all()

    def _handle_fits_stf_changed(self) -> None:
        selected = self.fits_stf_combo.currentData()
        if not isinstance(selected, str):
            return
        settings = FitsDisplaySettings(stf_preset_id=selected)
        if settings.cache_key() == self._fits_display_settings.cache_key():
            return
        self._apply_fits_display_settings(settings)

    def _handle_fits_stf_reset(self) -> None:
        self._apply_fits_display_settings(FitsDisplaySettings())

    def _apply_fits_display_settings(self, settings: FitsDisplaySettings) -> None:
        normalized = FitsDisplaySettings(stf_preset_id=settings.preset.id)
        if normalized.cache_key() == self._fits_display_settings.cache_key():
            self._sync_preview_controls()
            return
        self._fits_display_settings = normalized
        self._settings.setValue(self.FITS_STF_PRESET_KEY, normalized.preset.id)
        self._inspection_stats_cache.clear()
        self._focus_assist_cache.clear()
        target_slots = self._fits_entry_slots()
        target_paths = {self._entries[slot].source_path for slot in target_slots if 0 <= slot < len(self._entries)}
        for path in target_paths:
            self._invalidate_preview_cache(path)
        self._sync_preview_controls()
        self._update_info_label()
        if target_slots:
            self._request_preview_loads(target_slots)
        else:
            self._update_analysis_panel()

    def _ensure_panes(self, count: int) -> None:
        while len(self._panes) < count:
            pane = PreviewPane()
            pane.apply_theme(self._theme)
            pane.image_label.installEventFilter(self)
            pane.scroll_area.viewport().installEventFilter(self)
            pane.heart_button.clicked.connect(lambda _checked=False, slot=len(self._panes): self._handle_heart_clicked(slot))
            pane.reject_button.clicked.connect(lambda _checked=False, slot=len(self._panes): self._handle_reject_clicked(slot))
            self._watched_widgets[pane.image_label] = len(self._panes)
            self._watched_widgets[pane.scroll_area.viewport()] = len(self._panes)
            self._panes.append(pane)

    def _rebuild_entries(self) -> None:
        before_after_entries = self._before_after_entries()
        if before_after_entries is not None:
            self._entries = before_after_entries
        else:
            self._entries = list(self._source_entries)
        current_paths = {entry.source_path for entry in self._entries}
        self._inspection_stats_cache = {
            key: value for key, value in self._inspection_stats_cache.items() if key[0] in current_paths
        }
        self._focus_assist_cache = {
            key: value for key, value in self._focus_assist_cache.items() if key[0] in current_paths
        }
        self._current_images = [QImage() for _ in self._entries]
        self._current_metadata = [self._metadata_cache.get(entry.source_path, EMPTY_METADATA) for entry in self._entries]
        self._source_versions = [_file_signature(entry.source_path) for entry in self._entries]
        self._current_placeholder_flags = [False for _ in self._entries]
        self._current_image_display_tokens = [() for _ in self._entries]
        self._rendered_display_keys = [None for _ in self._entries]
        self._pending_requests = 0
        self._focused_slot = min(self._focused_slot, max(0, len(self._entries) - 1))
        self._update_before_after_controls()
        self._update_layout()
        self._seed_entry_images_from_placeholders()
        self._prime_entry_images_from_cache()
        self._render_all()

    def _before_after_entries(self) -> list[PreviewEntry] | None:
        if self._compare_mode or not self._before_after_enabled or len(self._source_entries) != 1:
            return None
        entry = self._source_entries[0]
        edited_candidates = self._edited_candidates_for_entry(entry)
        if not edited_candidates:
            return None
        edited_index = self._edited_variant_index % len(edited_candidates)
        edited_path = edited_candidates[edited_index]
        before_path = self._before_source_path(entry.record)
        return [
            PreviewEntry(
                record=entry.record,
                source_path=before_path,
                winner=entry.winner,
                reject=entry.reject,
                photoshop=entry.photoshop,
                edited_path=edited_path,
                edited_candidates=edited_candidates,
                label="Before",
                ai_result=entry.ai_result,
                review_summary=entry.review_summary,
                workflow_summary=entry.workflow_summary,
                workflow_details=entry.workflow_details,
                placeholder_image=entry.placeholder_image if before_path == entry.source_path else None,
            ),
            PreviewEntry(
                record=entry.record,
                source_path=edited_path,
                winner=entry.winner,
                reject=entry.reject,
                photoshop=entry.photoshop,
                edited_path=edited_path,
                edited_candidates=edited_candidates,
                label="After",
                ai_result=entry.ai_result,
                review_summary=entry.review_summary,
                workflow_summary=entry.workflow_summary,
                workflow_details=entry.workflow_details,
            ),
        ]

    def _seed_entry_images_from_placeholders(self) -> None:
        for slot, entry in enumerate(self._entries):
            placeholder = entry.placeholder_image
            if placeholder is None or placeholder.isNull():
                continue
            if not self._should_use_placeholder_image(slot, placeholder):
                continue
            self._current_images[slot] = placeholder.copy()
            if slot < len(self._current_placeholder_flags):
                self._current_placeholder_flags[slot] = True
            if slot < len(self._current_image_display_tokens):
                self._current_image_display_tokens[slot] = ()

    def _should_use_placeholder_image(self, slot: int, image: QImage) -> bool:
        if image.isNull():
            return False
        if len(self._entries) != 1:
            return True
        if not 0 <= slot < len(self._panes):
            return False
        target = self._fit_target_size(self._panes[slot])
        minimum_width = max(720, int(target.width() * 0.66))
        minimum_height = max(420, int(target.height() * 0.66))
        return image.width() >= minimum_width and image.height() >= minimum_height

    def _before_source_path(self, record: ImageRecord) -> str:
        for path in record.companion_paths:
            if Path(path).suffix.lower() in JPEG_SUFFIXES:
                return path
        return record.path

    def _update_before_after_controls(self) -> None:
        edited_candidates = self._edited_candidates_for_entry(self._source_entries[0]) if self._source_entries else ()
        eligible = (not self._compare_mode) and len(self._source_entries) == 1 and bool(edited_candidates)
        self.before_after_button.setEnabled(eligible)
        if not eligible and self._before_after_enabled:
            self._before_after_enabled = False
            with QSignalBlocker(self.before_after_button):
                self.before_after_button.setChecked(False)
        multiple_candidates = len(edited_candidates) > 1
        self.next_edit_button.setVisible(self._before_after_enabled and multiple_candidates)
        self.next_edit_button.setEnabled(multiple_candidates)
        if multiple_candidates:
            current = (self._edited_variant_index % len(edited_candidates)) + 1
            self.next_edit_button.setText(f"Edit {current}/{len(edited_candidates)}")
        self._sync_preview_controls()

    def _update_layout(self) -> None:
        visible_count = max(1, len(self._entries))
        self._ensure_panes(visible_count)

        while self.panes_layout.count():
            item = self.panes_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()

        columns = self._column_count_for(visible_count)
        show_focus_frame = visible_count > 1
        for index, pane in enumerate(self._panes[:visible_count]):
            row = index // columns
            column = index % columns
            self.panes_layout.addWidget(pane, row, column)
            pane.caption_label.setVisible((self._compare_mode and visible_count > 1) or self._before_after_enabled)
            pane.set_frame_visible(show_focus_frame)
            pane.set_active(index == self._focused_slot)
            pane.set_minimal(False)
            pane.show()

        for pane in self._panes[visible_count:]:
            pane.hide()
        if self._before_after_enabled and visible_count == 2:
            self.panes_layout.setColumnStretch(0, 1)
            self.panes_layout.setColumnStretch(1, 1)
            for column in range(2, 6):
                self.panes_layout.setColumnStretch(column, 0)
        else:
            for column in range(6):
                self.panes_layout.setColumnStretch(column, 1 if column < columns else 0)

    def _column_count_for(self, count: int) -> int:
        if count <= 3:
            return count
        if count in (4, 5, 6):
            return 3
        if count in (7, 8):
            return 4
        return 3

    def _request_preview_loads(self, slots: list[int] | None = None, *, force_metadata: bool = False) -> None:
        if not self._entries:
            return

        target_slots = slots if slots is not None else list(range(len(self._entries)))
        if not target_slots:
            return
        self._load_token += 1
        token = self._load_token
        self._pending_requests = 0
        for slot in target_slots:
            if not 0 <= slot < len(self._entries):
                continue
            entry = self._entries[slot]
            source_signature = _file_signature(entry.source_path)
            target_size = self._decode_target_size(slot)
            prefer_embedded = not self._manual_zoom
            fits_display_settings = self._fits_display_settings_for_entry(entry)
            cache_key = self._preview_cache_key(
                entry.source_path,
                source_signature,
                target_size,
                prefer_embedded=prefer_embedded,
                fits_display_settings=fits_display_settings,
            )
            cached_image = self._cached_preview_image(cache_key)
            should_load_metadata = force_metadata or entry.source_path not in self._metadata_cache
            if cached_image is not None and not cached_image.isNull():
                self._current_images[slot] = cached_image
                if slot < len(self._current_image_display_tokens):
                    self._current_image_display_tokens[slot] = self._fits_display_cache_key_for_path(
                        entry.source_path,
                        fits_display_settings,
                    )
                if slot < len(self._current_placeholder_flags):
                    self._current_placeholder_flags[slot] = False
                if slot < len(self._source_versions):
                    self._source_versions[slot] = source_signature
                metadata = self._metadata_cache.get(entry.source_path)
                if metadata is not None:
                    self._current_metadata[slot] = metadata
                self._render_pane(slot)
                if slot == self._focused_slot:
                    self._update_analysis_panel()
                if not should_load_metadata:
                    continue
            self._pending_requests += 1
            task = PreviewTask(
                PreviewRequest(
                    path=entry.source_path,
                    token=token,
                    slot=slot,
                    target_size=target_size,
                    source_signature=source_signature,
                    prefer_embedded=prefer_embedded,
                    load_metadata=should_load_metadata,
                    fits_display_settings=fits_display_settings,
                ),
                self._result_queue,
            )
            self._pool.start(task, self._visible_request_priority(slot))
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def _drain_results(self) -> None:
        processed = 0
        while processed < 16:
            try:
                item = self._result_queue.get_nowait()
            except Empty:
                break

            state, request, *payload = item
            cache_key = self._preview_cache_key(
                request.path,
                request.source_signature,
                request.target_size,
                prefer_embedded=request.prefer_embedded,
                fits_display_settings=request.fits_display_settings,
            )
            self._pending_cache_keys.discard(cache_key)

            if not request.cache_only and request.token == self._load_token and self._pending_requests > 0:
                self._pending_requests -= 1
            if state == "ready":
                image = payload[0]
                metadata = payload[1] if len(payload) > 1 else None
                self._cache_preview_image(cache_key, image)
                if metadata is not None:
                    self._metadata_cache[request.path] = metadata
            if request.cache_only:
                processed += 1
                continue

            if request.token == self._load_token and 0 <= request.slot < len(self._entries) and request.path == self._entries[request.slot].source_path:
                if state == "ready":
                    image = payload[0]
                    metadata = payload[1] if len(payload) > 1 else None
                    self._current_images[request.slot] = image
                    if request.slot < len(self._current_image_display_tokens):
                        self._current_image_display_tokens[request.slot] = self._fits_display_cache_key_for_path(
                            request.path,
                            request.fits_display_settings,
                        )
                    if request.slot < len(self._current_placeholder_flags):
                        self._current_placeholder_flags[request.slot] = False
                    if request.slot < len(self._source_versions):
                        self._source_versions[request.slot] = request.source_signature
                    if metadata is not None:
                        self._current_metadata[request.slot] = metadata
                    self._render_pane(request.slot)
                    if request.slot == self._focused_slot:
                        self._update_analysis_panel()
                else:
                    if self._current_images[request.slot].isNull():
                        self._show_failed(request.slot, payload[0])
            processed += 1

        if processed == 0 and self._pending_requests == 0:
            self._drain_timer.stop()

    def preload_paths(self, paths: list[str], *, load_metadata: bool = True) -> None:
        if not paths:
            return
        target_size = self._preload_target_size()
        max_paths = 10
        for index, path in enumerate(paths[:max_paths]):
            if not path or not os.path.exists(path):
                continue
            source_signature = _file_signature(path)
            fits_display_settings = self._fits_display_settings_for_path(path)
            cache_key = self._preview_cache_key(
                path,
                source_signature,
                target_size,
                prefer_embedded=True,
                fits_display_settings=fits_display_settings,
            )
            if self._cached_preview_image(cache_key) is not None or cache_key in self._pending_cache_keys:
                continue
            self._pending_cache_keys.add(cache_key)
            task = PreviewTask(
                PreviewRequest(
                    path=path,
                    token=0,
                    slot=-1,
                    target_size=target_size,
                    source_signature=source_signature,
                    prefer_embedded=True,
                    load_metadata=load_metadata and index < 2 and path not in self._metadata_cache,
                    cache_only=True,
                    fits_display_settings=fits_display_settings,
                ),
                self._result_queue,
            )
            self._pool.start(task, -20 - index)
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def _render_all(self) -> None:
        for slot in range(len(self._entries)):
            self._render_pane(slot)
        if self._manual_zoom:
            self._apply_zoom_to_all()
        self._update_focus_styles()
        self._update_cursor()
        self._update_analysis_panel()
        self._update_info_label()

    def _poll_source_updates(self) -> None:
        if not self.isVisible() or not self._entries or self._pending_requests > 0:
            return
        if not self.isActiveWindow():
            if self._refresh_timer.interval() != self._refresh_interval_background_ms:
                self._refresh_timer.setInterval(self._refresh_interval_background_ms)
            return

        now = time.monotonic()
        if (
            not self._compare_mode
            and len(self._source_entries) == 1
            and now >= self._next_edited_discovery_at
        ):
            source_entry = self._source_entries[0]
            has_candidates = bool(self._edited_candidates_for_entry(source_entry))
            if self._edited_discovery_requested or not has_candidates:
                discovered = discover_edited_paths(source_entry.record)
                if discovered:
                    self.set_edited_candidates(source_entry.record.path, tuple(discovered))
                    self._next_edited_discovery_at = now + self._edited_discovery_interval_found_s
                else:
                    self._next_edited_discovery_at = now + self._edited_discovery_interval_missing_s
                self._edited_discovery_requested = False
            else:
                self._next_edited_discovery_at = now + self._edited_discovery_interval_with_candidates_s

        changed_slots: list[int] = []
        total_slots = len(self._entries)
        slots_to_check: list[int] = list(range(total_slots))
        if total_slots > 2 and self._stable_poll_cycles >= 4:
            focused = max(0, min(self._focused_slot, total_slots - 1))
            self._poll_round_robin_slot = (self._poll_round_robin_slot + 1) % total_slots
            secondary = self._poll_round_robin_slot
            slots_to_check = [focused]
            if secondary != focused:
                slots_to_check.append(secondary)

        for slot in slots_to_check:
            entry = self._entries[slot]
            current_signature = self._source_versions[slot] if slot < len(self._source_versions) else None
            latest_signature = _file_signature(entry.source_path)
            if latest_signature is None or latest_signature == current_signature:
                continue
            self._metadata_cache.pop(entry.source_path, None)
            self._invalidate_preview_cache(entry.source_path)
            changed_slots.append(slot)

        if changed_slots:
            self._stable_poll_cycles = 0
            self._poll_round_robin_slot = 0
            self._refresh_timer.setInterval(900)
            self._request_preview_loads(changed_slots, force_metadata=True)
            return

        self._stable_poll_cycles += 1
        if self._stable_poll_cycles >= 12:
            next_interval = 5200
        elif self._stable_poll_cycles >= 5:
            next_interval = self._refresh_interval_idle_ms
        else:
            next_interval = self._refresh_interval_active_ms
        if self._refresh_timer.interval() != next_interval:
            self._refresh_timer.setInterval(next_interval)

    def _render_pane(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        pane = self._panes[slot]
        entry = self._entries[slot]
        display_image = self._display_image_for_slot(slot)
        metadata = self._current_metadata[slot] if slot < len(self._current_metadata) else EMPTY_METADATA
        if entry.label:
            caption_text = f"{entry.label} | {Path(entry.source_path).name}"
        else:
            caption_text = entry.record.name
        if entry.ai_result is not None and entry.ai_result.is_top_pick:
            caption_text = f"{caption_text} | AI Top Pick"
        pane.caption_label.setText(caption_text)

        metadata_lines: list[str] = []
        if metadata.display_text:
            metadata_lines.append(metadata.display_text)
        ai_text = _format_ai_metadata(entry.ai_result)
        if ai_text:
            metadata_lines.append(ai_text)
        pane.metadata_label.setText("\n".join(metadata_lines))
        pane.metadata_label.setVisible(bool(metadata_lines))
        pane.heart_button.setChecked(entry.winner)
        pane.heart_button.setText(pane.HEART_SYMBOL if entry.winner else pane.HEART_OUTLINE_SYMBOL)
        pane.reject_button.setChecked(entry.reject)
        pane.reject_button.setText(pane.REJECT_SYMBOL)

        if display_image.isNull():
            pane.image_label.setScaledContents(False)
            pane.image_label.resize(1, 1)
            pane.image_label.clear()
            if slot < len(self._rendered_display_keys):
                self._rendered_display_keys[slot] = None
            pane.image_label.setText("Loading full preview...")
            return

        if self._manual_zoom:
            scaled_size = QSize(
                max(1, int(round(display_image.width() * self._zoom_scale))),
                max(1, int(round(display_image.height() * self._zoom_scale))),
            )
            pane.image_label.setText("")
            pane.image_label.setScaledContents(True)
            display_key = self._display_render_key(slot, display_image)
            if (
                slot >= len(self._rendered_display_keys)
                or self._rendered_display_keys[slot] != display_key
                or pane.image_label.pixmap() is None
                or pane.image_label.pixmap().isNull()
            ):
                pane.image_label.setPixmap(QPixmap.fromImage(display_image))
                if slot < len(self._rendered_display_keys):
                    self._rendered_display_keys[slot] = display_key
            pane.image_label.resize(scaled_size)
        else:
            pane.image_label.setScaledContents(False)
            target = self._fit_target_size(pane)
            transform_mode = Qt.TransformationMode.SmoothTransformation
            fitted_size = display_image.size().scaled(target, Qt.AspectRatioMode.KeepAspectRatio)
            if fitted_size == display_image.size():
                pixmap = QPixmap.fromImage(display_image)
            else:
                pixmap = QPixmap.fromImage(
                    display_image.scaled(
                        target,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        transform_mode,
                    )
                )
            pane.image_label.setText("")
            pane.image_label.setPixmap(pixmap)
            pane.image_label.resize(pixmap.size())
            if slot < len(self._rendered_display_keys):
                self._rendered_display_keys[slot] = None
            pane.scroll_area.horizontalScrollBar().setValue(0)
            pane.scroll_area.verticalScrollBar().setValue(0)

    def _display_image_for_slot(self, slot: int) -> QImage:
        if not 0 <= slot < len(self._current_images):
            return QImage()
        image = self._current_images[slot]
        if image.isNull() or not self._focus_assist_enabled:
            return image
        cache_key = self._focus_assist_cache_key(slot, image)
        cached = self._focus_assist_cache.get(cache_key)
        if cached is not None:
            return cached
        assisted = build_focus_assist_image(
            image,
            self._focus_assist_color,
            self._focus_assist_strength,
            dim_background=self._focus_assist_dim_background,
        )
        self._focus_assist_cache[cache_key] = assisted
        return assisted

    def _inspection_stats_for_slot(self, slot: int) -> InspectionStats:
        if not 0 <= slot < len(self._current_images):
            return EMPTY_INSPECTION_STATS
        image = self._current_images[slot]
        if image.isNull():
            return EMPTY_INSPECTION_STATS
        cache_key = self._image_cache_key(slot, image)
        cached = self._inspection_stats_cache.get(cache_key)
        if cached is not None:
            return cached
        stats = build_inspection_stats(image)
        self._inspection_stats_cache[cache_key] = stats
        return stats

    def _image_cache_key(self, slot: int, image: QImage) -> tuple[object, ...]:
        path = self._entries[slot].source_path if 0 <= slot < len(self._entries) else ""
        signature = self._source_versions[slot] if 0 <= slot < len(self._source_versions) else None
        display_token = self._current_image_display_tokens[slot] if 0 <= slot < len(self._current_image_display_tokens) else ()
        return (
            path,
            signature,
            image.width(),
            image.height(),
            display_token,
        )

    def _display_render_key(self, slot: int, image: QImage) -> tuple[object, ...]:
        base_key = self._image_cache_key(slot, image)
        if not self._focus_assist_enabled:
            return (*base_key, "display")
        return (*self._focus_assist_cache_key(slot, image), "focus-assist")

    def _focus_assist_cache_key(
        self, slot: int, image: QImage
    ) -> tuple[object, ...]:
        return (
            *self._image_cache_key(slot, image),
            self._focus_assist_color.id,
            self._focus_assist_strength.id,
            self._focus_assist_dim_background,
        )

    def _update_analysis_panel(self) -> None:
        if not self._entries or not 0 <= self._focused_slot < len(self._entries):
            self.analysis_subtitle_label.setText("Focused image analysis")
            self.histogram_widget.set_stats(EMPTY_INSPECTION_STATS)
            self.inspection_dimensions_label.setText("Size: --")
            self.inspection_exposure_label.setText("Exposure: --")
            self.inspection_clipping_label.setText("Clipping: --")
            self.inspection_detail_label.setText("Detail: --")
            self.ai_confidence_label.setText("Confidence: --")
            self.ai_explanation_label.setText("Load an AI-scored image to see ranking rationale.")
            return

        entry = self._entries[self._focused_slot]
        stats = self._inspection_stats_for_slot(self._focused_slot)
        title = Path(entry.source_path).name
        if entry.label:
            title = f"{entry.label} | {title}"
        self.analysis_subtitle_label.setText(title)
        self.histogram_widget.set_stats(stats)

        if stats.width <= 0 or stats.height <= 0:
            self.inspection_dimensions_label.setText("Size: Loading...")
            self.inspection_exposure_label.setText("Exposure: Loading...")
            self.inspection_clipping_label.setText("Clipping: Loading...")
            self.inspection_detail_label.setText("Detail: Loading...")
            self.ai_confidence_label.setText(
                f"Confidence: {entry.ai_result.confidence_bucket_label}" if entry.ai_result is not None else "Confidence: --"
            )
            explanation_lines = list(build_ai_explanation_lines(entry.ai_result, review_summary=entry.review_summary))
            explanation_lines.extend(entry.workflow_details[:3])
            self.ai_explanation_label.setText("\n".join(explanation_lines) or "Load an AI-scored image to see ranking rationale.")
            return

        self.inspection_dimensions_label.setText(f"Size: {stats.width:,} x {stats.height:,}")
        self.inspection_exposure_label.setText(
            f"Exposure: mean {stats.mean_luminance:.0f} | median {stats.median_luminance:.0f}"
        )
        self.inspection_clipping_label.setText(
            f"Clipping: {stats.shadow_clip_pct:.1f}% shadows | {stats.highlight_clip_pct:.1f}% highlights"
        )
        self.inspection_detail_label.setText(
            f"Detail: {stats.detail_score:.0f}/100 | {_detail_label(stats.detail_score)}"
        )
        if entry.ai_result is None:
            self.ai_confidence_label.setText("Confidence: --")
            self.ai_explanation_label.setText("\n".join(entry.workflow_details) or "AI is not loaded for this image.")
            return
        self.ai_confidence_label.setText(f"Confidence: {entry.ai_result.confidence_bucket_label}")
        explanation = build_ai_explanation_lines(
            entry.ai_result,
            review_summary=entry.review_summary,
            detail_score=stats.detail_score,
        )
        explanation_lines = list(explanation)
        explanation_lines.extend(entry.workflow_details[:3])
        self.ai_explanation_label.setText(
            "\n".join(explanation_lines) if explanation_lines else "AI scoring is loaded, but no explanation signals are available."
        )

    def _show_failed(self, slot: int, message: str) -> None:
        if not 0 <= slot < len(self._entries):
            return
        pane = self._panes[slot]
        pane.image_label.setScaledContents(False)
        pane.image_label.resize(1, 1)
        pane.image_label.clear()
        if slot < len(self._rendered_display_keys):
            self._rendered_display_keys[slot] = None
        pane.image_label.setText(f"Failed\n{message}")

    def _fit_target_size(self, pane: PreviewPane) -> QSize:
        viewport = pane.scroll_area.viewport().size()
        width = max(1, viewport.width() - 8)
        height = max(1, viewport.height() - 8)
        return QSize(width, height)

    def _decode_target_size(self, slot: int) -> QSize:
        if 0 <= slot < len(self._panes):
            fit_target = self._fit_target_size(self._panes[slot])
        else:
            fit_target = QSize(max(1, self.width()), max(1, self.height()))
        if (not self.isVisible()) or fit_target.width() < 800 or fit_target.height() < 600:
            window_size = self.size()
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                window_size = screen.availableGeometry().size()
            columns = 2 if self._before_after_enabled and len(self._entries) == 2 else max(1, self._column_count_for(max(1, len(self._entries))))
            sidebar_width = 360 if len(self._entries) <= 1 and not self._compare_mode else 72
            fit_target = QSize(
                max(1, int((window_size.width() - sidebar_width) / columns) - 48),
                max(1, window_size.height() - 188),
            )
        if self._manual_zoom and slot == self._focused_slot:
            zoom_scale = max(1.0, self._zoom_scale)
            overscan = 1.25 if len(self._entries) <= 1 else 1.15
            max_edge = 8192 if len(self._entries) <= 1 else 6144
            return QSize(
                min(max_edge, max(1, int(round(fit_target.width() * zoom_scale * overscan)))),
                min(max_edge, max(1, int(round(fit_target.height() * zoom_scale * overscan)))),
            )
        overscan = 2.0
        max_edge = 5120 if len(self._entries) <= 1 else 4096
        return QSize(
            min(max_edge, max(1, int(round(fit_target.width() * overscan)))),
            min(max_edge, max(1, int(round(fit_target.height() * overscan)))),
        )

    def _prime_entry_images_from_cache(self) -> None:
        if not self._entries:
            return
        for slot, entry in enumerate(self._entries):
            source_signature = self._source_versions[slot] if slot < len(self._source_versions) else _file_signature(entry.source_path)
            target_size = self._decode_target_size(slot)
            cache_key = self._preview_cache_key(
                entry.source_path,
                source_signature,
                target_size,
                prefer_embedded=not self._manual_zoom,
                fits_display_settings=self._fits_display_settings_for_entry(entry),
            )
            cached_image = self._cached_preview_image(cache_key)
            if cached_image is None or cached_image.isNull():
                continue
            self._current_images[slot] = cached_image
            if slot < len(self._current_image_display_tokens):
                self._current_image_display_tokens[slot] = self._fits_display_cache_key_for_path(
                    entry.source_path,
                    self._fits_display_settings_for_entry(entry),
                )
            if slot < len(self._current_placeholder_flags):
                self._current_placeholder_flags[slot] = False
            metadata = self._metadata_cache.get(entry.source_path)
            if metadata is not None and slot < len(self._current_metadata):
                self._current_metadata[slot] = metadata

    def _visible_request_priority(self, slot: int) -> int:
        distance = abs(slot - self._focused_slot)
        return max(20, 80 - (distance * 8))

    def _preload_target_size(self) -> QSize:
        if self._panes:
            return self._decode_target_size(min(max(self._focused_slot, 0), len(self._panes) - 1))
        width = max(960, int(self.width() * 0.7))
        height = max(720, int(self.height() * 0.7))
        return QSize(width, height)

    def _preview_cache_key(
        self,
        path: str,
        source_signature: tuple[int, int] | None,
        target_size: QSize,
        *,
        prefer_embedded: bool,
        fits_display_settings: FitsDisplaySettings | None = None,
    ) -> tuple[object, ...]:
        return (
            path,
            source_signature,
            max(0, target_size.width()),
            max(0, target_size.height()),
            prefer_embedded,
            self._fits_display_cache_key_for_path(path, fits_display_settings),
        )

    def _cached_preview_image(
        self,
        cache_key: tuple[object, ...],
    ) -> QImage | None:
        cached = self._preview_cache.get(cache_key)
        if cached is None:
            return None
        self._preview_cache.move_to_end(cache_key)
        return cached[0]

    def _cache_preview_image(
        self,
        cache_key: tuple[object, ...],
        image: QImage,
    ) -> None:
        if image.isNull():
            return
        cost = max(1, image.sizeInBytes())
        existing = self._preview_cache.pop(cache_key, None)
        if existing is not None:
            self._preview_cache_bytes -= existing[1]
        self._preview_cache[cache_key] = (image, cost)
        self._preview_cache.move_to_end(cache_key)
        self._preview_cache_bytes += cost
        while self._preview_cache_bytes > self._preview_cache_limit and self._preview_cache:
            _, (_, removed_cost) = self._preview_cache.popitem(last=False)
            self._preview_cache_bytes -= removed_cost

    def _invalidate_preview_cache(self, path: str) -> None:
        matching_keys = [key for key in self._preview_cache if key[0] == path]
        for key in matching_keys:
            _image, cost = self._preview_cache.pop(key)
            self._preview_cache_bytes -= cost

    def _fit_scale_threshold(self) -> float:
        scales: list[float] = []
        for slot, image in enumerate(self._current_images):
            if image.isNull():
                continue
            pane = self._panes[slot]
            target = self._fit_target_size(pane)
            scales.append(
                min(
                    1.0,
                    target.width() / max(1, image.width()),
                    target.height() / max(1, image.height()),
                )
            )
        return max(scales, default=1.0)

    def _toggle_zoom(self) -> None:
        if self._manual_zoom:
            self._set_fit_mode()
            return
        self._set_manual_zoom(1.0)

    def _set_fit_mode(self) -> None:
        self._manual_zoom = False
        self._zoom_scale = self._fit_scale_threshold()
        self._dragging = False
        self._pending_zoom_refresh_slots = []
        self._zoom_request_timer.stop()
        self._hide_all_loupes()
        self._render_all()

    def _set_manual_zoom(self, scale: float) -> None:
        fit_scale = self._fit_scale_threshold()
        clamped = max(fit_scale, min(8.0, scale))
        if clamped <= fit_scale * 1.02:
            self._set_fit_mode()
            return
        entered_manual = not self._manual_zoom
        self._manual_zoom = True
        self._zoom_scale = clamped
        self._hide_all_loupes()
        self._render_all()
        if self._entries:
            target_slots = list(range(len(self._entries))) if (self._before_after_enabled or self._compare_mode) else [self._focused_slot]
            self._schedule_zoom_resolution_refresh(target_slots, delay_ms=0 if entered_manual else 90)

    def _handle_wheel_zoom(self, delta: int) -> None:
        if delta == 0 or not self._entries:
            return
        current_scale = self._zoom_scale if self._manual_zoom else self._fit_scale_threshold()
        step = 1.15 if delta > 0 else 1 / 1.15
        self._set_manual_zoom(current_scale * step)

    def _schedule_zoom_resolution_refresh(self, slots: list[int], *, delay_ms: int = 90) -> None:
        normalized_slots = [slot for slot in sorted(set(slots)) if 0 <= slot < len(self._entries)]
        if not normalized_slots:
            return
        self._pending_zoom_refresh_slots = normalized_slots
        if delay_ms <= 0:
            self._zoom_request_timer.stop()
            self._request_zoom_resolution_refresh()
            return
        self._zoom_request_timer.start(delay_ms)

    def _request_zoom_resolution_refresh(self) -> None:
        if not self._pending_zoom_refresh_slots:
            return
        slots = list(self._pending_zoom_refresh_slots)
        self._pending_zoom_refresh_slots = []
        self._request_preview_loads(slots)

    def _handle_mouse_press(self, event: QMouseEvent) -> bool:
        if not self._manual_zoom:
            return False
        self._dragging = True
        self._hide_all_loupes()
        self._drag_start_global_pos = event.globalPosition().toPoint()
        self._drag_start_scrolls = [
            QPoint(pane.scroll_area.horizontalScrollBar().value(), pane.scroll_area.verticalScrollBar().value())
            for pane in self._panes[: len(self._entries)]
        ]
        self._update_cursor()
        return True

    def _handle_mouse_move(self, event: QMouseEvent) -> bool:
        if not self._dragging:
            return False
        delta = event.globalPosition().toPoint() - self._drag_start_global_pos
        for pane, start in zip(self._panes[: len(self._entries)], self._drag_start_scrolls):
            pane.scroll_area.horizontalScrollBar().setValue(start.x() - delta.x())
            pane.scroll_area.verticalScrollBar().setValue(start.y() - delta.y())
        return True

    def _handle_mouse_release(self) -> bool:
        if not self._dragging:
            return False
        self._dragging = False
        self._update_cursor()
        return True

    def _toggle_loupe(self) -> None:
        self._loupe_enabled = not self._loupe_enabled
        if not self._loupe_enabled:
            self._hide_all_loupes()
        elif self._loupe_slot >= 0:
            self._update_loupe(self._loupe_slot, self._loupe_global_pos)
        self._update_info_label()

    def _cycle_loupe_zoom(self) -> None:
        self._loupe_zoom_index = (self._loupe_zoom_index + 1) % len(self._loupe_zoom_levels)
        self._loupe_zoom = self._loupe_zoom_levels[self._loupe_zoom_index]
        self._loupe_enabled = True
        if self._loupe_slot >= 0:
            self._update_loupe(self._loupe_slot, self._loupe_global_pos)
        self._update_info_label()

    def _update_loupe(self, slot: int, global_pos: QPoint) -> None:
        self._loupe_slot = slot
        self._loupe_global_pos = global_pos
        if not self._loupe_enabled or self._dragging or not 0 <= slot < len(self._entries):
            self._hide_loupe(slot)
            return
        if not 0 <= slot < len(self._current_images):
            self._hide_loupe(slot)
            return

        pane = self._panes[slot]
        image = self._current_images[slot]
        if image.isNull() or pane.image_label.pixmap() is None or pane.image_label.pixmap().isNull():
            self._hide_loupe(slot)
            return

        label_pos = pane.image_label.mapFromGlobal(global_pos)
        if not pane.image_label.rect().contains(label_pos):
            self._hide_loupe(slot)
            return

        display_size = pane.image_label.size()
        if display_size.width() <= 0 or display_size.height() <= 0:
            self._hide_loupe(slot)
            return

        x_ratio = image.width() / max(1, display_size.width())
        y_ratio = image.height() / max(1, display_size.height())
        source_x = int(round(label_pos.x() * x_ratio))
        source_y = int(round(label_pos.y() * y_ratio))
        overlay = pane.loupe_overlay
        sample_width = max(24, int(round(overlay.width() / self._loupe_zoom)))
        sample_height = max(24, int(round((overlay.height() - 28) / self._loupe_zoom)))
        source_rect = QRect(
            source_x - sample_width // 2,
            source_y - sample_height // 2,
            sample_width,
            sample_height,
        )
        max_left = max(0, image.width() - sample_width)
        max_top = max(0, image.height() - sample_height)
        source_rect.moveLeft(max(0, min(source_rect.left(), max_left)))
        source_rect.moveTop(max(0, min(source_rect.top(), max_top)))
        crop = image.copy(source_rect)
        loupe_pixmap = QPixmap.fromImage(
            crop.scaled(
                overlay.width() - 4,
                overlay.height() - 24,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )
        overlay.set_content(loupe_pixmap, f"{int(round(self._loupe_zoom * 100))}%")

        pane_point = pane.mapFromGlobal(global_pos)
        target_x = pane_point.x() + 24
        target_y = pane_point.y() - overlay.height() - 12
        bounds = pane.rect().adjusted(10, 10, -10, -10)
        if target_x + overlay.width() > bounds.right():
            target_x = pane_point.x() - overlay.width() - 24
        if target_x < bounds.left():
            target_x = bounds.left()
        if target_y < bounds.top():
            target_y = min(bounds.bottom() - overlay.height(), pane_point.y() + 24)
        overlay.move(target_x, target_y)
        overlay.show()
        overlay.raise_()

    def _hide_loupe(self, slot: int) -> None:
        if 0 <= slot < len(self._panes):
            self._panes[slot].loupe_overlay.hide()
        if self._loupe_slot == slot:
            self._loupe_slot = -1

    def _hide_all_loupes(self) -> None:
        for pane in self._panes:
            pane.loupe_overlay.hide()
        self._loupe_slot = -1

    def _apply_zoom_to_all(self) -> None:
        for pane in self._panes[: len(self._entries)]:
            viewport = pane.scroll_area.viewport().size()
            image_size = pane.image_label.size()
            max_x = max(0, image_size.width() - viewport.width())
            max_y = max(0, image_size.height() - viewport.height())
            pane.scroll_area.horizontalScrollBar().setValue(max_x // 2)
            pane.scroll_area.verticalScrollBar().setValue(max_y // 2)

    def _update_cursor(self) -> None:
        cursor = None
        if self._manual_zoom:
            cursor = Qt.CursorShape.ClosedHandCursor if self._dragging else Qt.CursorShape.OpenHandCursor
        for pane in self._panes[: len(self._entries)]:
            if cursor is None:
                pane.image_label.unsetCursor()
                pane.scroll_area.viewport().unsetCursor()
            else:
                pane.image_label.setCursor(cursor)
                pane.scroll_area.viewport().setCursor(cursor)

    def _set_focused_slot(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        if self._focused_slot == slot:
            return
        self._focused_slot = slot
        self._update_focus_styles()
        self._update_analysis_panel()
        self._update_info_label()

    def _update_focus_styles(self) -> None:
        for index, pane in enumerate(self._panes[: len(self._entries)]):
            pane.set_active(index == self._focused_slot)

    def _handle_heart_clicked(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        self._set_focused_slot(slot)
        self.winner_requested.emit(self._entries[slot].record.path)
        if self._should_auto_advance_after_review():
            self.navigation_requested.emit(1)

    def _handle_reject_clicked(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        self._set_focused_slot(slot)
        self.reject_requested.emit(self._entries[slot].record.path)
        if self._should_auto_advance_after_review():
            self.navigation_requested.emit(1)

    def _focused_path(self) -> str:
        if not 0 <= self._focused_slot < len(self._entries):
            return ""
        return self._entries[self._focused_slot].record.path

    def focused_path(self) -> str:
        return self._focused_path()

    def _focused_photoshop_path(self) -> str:
        if not 0 <= self._focused_slot < len(self._entries):
            return ""
        entry = self._entries[self._focused_slot]
        if entry.source_path:
            return entry.source_path
        return entry.record.path

    def focused_photoshop_path(self) -> str:
        return self._focused_photoshop_path()

    def _edited_candidates_for_entry(self, entry: PreviewEntry) -> tuple[str, ...]:
        if entry.edited_candidates:
            return entry.edited_candidates
        if entry.edited_path:
            return (entry.edited_path,)
        if entry.record.edited_paths:
            return entry.record.edited_paths
        if entry.record.preferred_edit_path:
            return (entry.record.preferred_edit_path,)
        return ()

    def _cycle_edited_variant(self) -> None:
        if not self._source_entries:
            return
        candidates = self._edited_candidates_for_entry(self._source_entries[0])
        if len(candidates) <= 1:
            return
        self._edited_variant_index = (self._edited_variant_index + 1) % len(candidates)
        self._rebuild_entries()
        self._request_preview_loads()

    def anchor_path(self) -> str:
        if not self._entries:
            return ""
        return self._entries[0].record.path

    def compare_mode_enabled(self) -> bool:
        return self._compare_mode

    def toggle_compare_mode(self) -> None:
        self.compare_mode_changed.emit(not self._compare_mode)

    def navigate_relative(self, delta: int) -> None:
        self.navigation_requested.emit(delta)

    def toggle_zoom_command(self) -> None:
        self._toggle_zoom()

    def fit_to_screen(self) -> None:
        self._set_fit_mode()

    def toggle_loupe_command(self) -> None:
        self._toggle_loupe()

    def toggle_focus_assist_command(self) -> None:
        with QSignalBlocker(self.focus_assist_button):
            self.focus_assist_button.setChecked(not self._focus_assist_enabled)
        self._handle_focus_assist_button_toggled(not self._focus_assist_enabled)

    def focus_assist_enabled(self) -> bool:
        return self._focus_assist_enabled

    def focus_assist_color(self) -> FocusAssistColor:
        return self._focus_assist_color

    def set_focus_assist_color_by_id(self, color_id: str) -> None:
        color = focus_assist_color_by_id(color_id)
        if color.id == self._focus_assist_color.id:
            return
        combo_index = self.focus_assist_color_combo.findData(color.id)
        if combo_index >= 0:
            self.focus_assist_color_combo.setCurrentIndex(combo_index)
        else:
            self._focus_assist_color = color
            self._settings.setValue(self.FOCUS_ASSIST_COLOR_KEY, color.id)
            self._focus_assist_cache.clear()
            self._sync_preview_controls()
            self._update_analysis_panel()
            if self._focus_assist_enabled:
                self._render_all()

    def cycle_focus_assist_color(self) -> None:
        current_index = self.focus_assist_color_combo.currentIndex()
        next_index = (current_index + 1) % max(1, self.focus_assist_color_combo.count())
        self.focus_assist_color_combo.setCurrentIndex(next_index)

    def focus_assist_strength(self) -> FocusAssistStrength:
        return self._focus_assist_strength

    def set_focus_assist_strength_by_id(self, strength_id: str) -> None:
        strength = focus_assist_strength_by_id(strength_id)
        if strength.id == self._focus_assist_strength.id:
            return
        combo_index = self.focus_assist_strength_combo.findData(strength.id)
        if combo_index >= 0:
            self.focus_assist_strength_combo.setCurrentIndex(combo_index)
        else:
            self._focus_assist_strength = strength
            self._settings.setValue(self.FOCUS_ASSIST_STRENGTH_KEY, strength.id)
            self._focus_assist_cache.clear()
            self._sync_preview_controls()
            self._update_analysis_panel()
            if self._focus_assist_enabled:
                self._render_all()

    def cycle_focus_assist_strength(self) -> None:
        current_index = self.focus_assist_strength_combo.currentIndex()
        next_index = (current_index + 1) % max(1, self.focus_assist_strength_combo.count())
        self.focus_assist_strength_combo.setCurrentIndex(next_index)

    def focus_assist_dim_background(self) -> bool:
        return self._focus_assist_dim_background

    def set_focus_assist_dim_background(self, enabled: bool) -> None:
        with QSignalBlocker(self.focus_assist_background_button):
            self.focus_assist_background_button.setChecked(enabled)
        self._handle_focus_assist_background_toggled(enabled)

    def toggle_focus_assist_background_command(self) -> None:
        self.set_focus_assist_dim_background(not self._focus_assist_dim_background)

    def set_annotation_state(self, path: str, winner: bool, reject: bool) -> None:
        updated = False
        for collection_name in ("_source_entries", "_entries"):
            collection = getattr(self, collection_name)
            for index, entry in enumerate(collection):
                if entry.record.path != path:
                    continue
                collection[index] = PreviewEntry(
                    record=entry.record,
                    source_path=entry.source_path,
                    winner=winner,
                    reject=reject,
                    photoshop=entry.photoshop,
                    edited_path=entry.edited_path,
                    edited_candidates=entry.edited_candidates,
                    label=entry.label,
                    ai_result=entry.ai_result,
                    review_summary=entry.review_summary,
                    workflow_summary=entry.workflow_summary,
                    workflow_details=entry.workflow_details,
                    placeholder_image=entry.placeholder_image,
                )
                updated = True
        if updated:
            self._render_all()

    def set_edited_candidates(self, path: str, edited_candidates: tuple[str, ...]) -> None:
        updated = False
        for collection_name in ("_source_entries", "_entries"):
            collection = getattr(self, collection_name)
            for index, entry in enumerate(collection):
                if entry.record.path != path:
                    continue
                collection[index] = PreviewEntry(
                    record=entry.record,
                    source_path=entry.source_path,
                    winner=entry.winner,
                    reject=entry.reject,
                    photoshop=entry.photoshop,
                    edited_path=edited_candidates[0] if edited_candidates else "",
                    edited_candidates=edited_candidates,
                    label=entry.label,
                    ai_result=entry.ai_result,
                    review_summary=entry.review_summary,
                    workflow_summary=entry.workflow_summary,
                    workflow_details=entry.workflow_details,
                    placeholder_image=entry.placeholder_image,
                )
                updated = True
        if updated:
            self._edited_variant_index = min(self._edited_variant_index, max(0, len(edited_candidates) - 1))
            if self._before_after_enabled:
                self._rebuild_entries()
                self._request_preview_loads()
            else:
                self._update_before_after_controls()
                self._update_info_label()

    def _update_info_label(self) -> None:
        if not self._entries:
            self.info_label.clear()
            return
        if self._before_after_enabled:
            prefix = f"Before/After | {self._entries[0].record.name}"
        elif self._compare_mode:
            prefix = f"Compare {len(self._entries)}-Up"
        else:
            prefix = self._entries[0].record.name
        mode = f"{int(round(self._zoom_scale * 100))}%" if self._manual_zoom else "Fit"
        focused_entry = self._entries[min(self._focused_slot, len(self._entries) - 1)]
        confidence_hint = (
            f" | {focused_entry.ai_result.confidence_bucket_label}"
            if focused_entry.ai_result is not None
            else ""
        )
        review_hint = f" | {focused_entry.review_summary}" if focused_entry.review_summary else ""
        workflow_hint = f" | {focused_entry.workflow_summary}" if focused_entry.workflow_summary else ""
        if self._winner_ladder_mode:
            self.info_label.setText(
                f"Winner Ladder  |  {prefix}{confidence_hint}{review_hint}{workflow_hint}  |  Left/A choose winner, Right/D choose challenger, W choose focus, N skip, Tab focus, Esc exit"
            )
            return
        if self._compare_mode:
            nav_hint = "Left/Right jump group, Up/Down step one"
        elif self._before_after_enabled:
            nav_hint = "Left/Right step images, cycle edit variants with the Edit button"
        else:
            nav_hint = "Left/Right step images"
        focus_hint = ""
        if self._compare_mode and self._entries:
            focus_hint = f" | Focus: {self._focused_slot + 1}/{len(self._entries)}"
        loupe_hint = f" | Loupe {int(round(self._loupe_zoom * 100))}%" if self._loupe_enabled else ""
        focus_assist_hint = (
            f" | Focus Assist {self._focus_assist_color.label}/{self._focus_assist_strength.label}"
            f"/{'Dim' if self._focus_assist_dim_background else 'Original'}"
            if self._focus_assist_enabled
            else ""
        )
        fits_hint = f" | FITS {self._fits_display_settings.preset.label}" if self._focused_entry_supports_fits_stf() else ""
        auto_advance_hint = " | Auto-Advance" if self._should_auto_advance_after_review() else ""
        self.info_label.setText(
            f"{prefix}{confidence_hint}{review_hint}{workflow_hint}  |  {mode}{focus_hint}{loupe_hint}{focus_assist_hint}{fits_hint}{auto_advance_hint}  |  {nav_hint}, wheel/Z zoom, L loupe, Alt+L cycle loupe, F focus assist, Shift+F cycles colors, use the inspection card for peaking controls, drag to pan, Tab focus, W/X/K/Delete/M/0-5/T actions, C compare, 0 to fit"
        )

    def winner_ladder_mode_enabled(self) -> bool:
        return self._winner_ladder_mode

    def set_winner_ladder_mode(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._winner_ladder_mode == normalized:
            return
        self._winner_ladder_mode = normalized
        if normalized:
            if not self._compare_mode:
                self.set_compare_mode(True)
        self._sync_preview_controls()
        self._update_layout()
        self._update_info_label()


def _file_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _format_ai_metadata(ai_result: AIImageResult | None) -> str:
    if ai_result is None:
        return ""

    parts = [f"AI score {ai_result.display_score_with_scale_text}"]
    if ai_result.confidence_bucket_label:
        parts.append(ai_result.confidence_bucket_label)
    if ai_result.group_id:
        parts.append(ai_result.group_id)
    if ai_result.group_size > 1:
        parts.append(ai_result.rank_text)
        if ai_result.is_top_pick:
            parts.append("recommended keeper")
    return "  |  ".join(part for part in parts if part)


def _detail_label(score: float) -> str:
    if score >= 72.0:
        return "high detail"
    if score >= 44.0:
        return "moderate detail"
    return "soft detail"


def _histogram_path(values: tuple[int, ...], rect: QRect, max_value: int, *, closed: bool = False) -> QPainterPath:
    path = QPainterPath()
    if not values or rect.width() <= 0 or rect.height() <= 0 or max_value <= 0:
        return path

    left = float(rect.left())
    bottom = float(rect.bottom())
    x_step = rect.width() / max(1, len(values) - 1)
    if closed:
        path.moveTo(left, bottom)
    for index, value in enumerate(values):
        x = left + (x_step * index)
        y = bottom - ((float(value) / float(max_value)) * rect.height())
        if index == 0:
            if closed:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
            continue
        path.lineTo(x, y)
    if closed:
        path.lineTo(float(rect.right()), bottom)
        path.closeSubpath()
    return path
