from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
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
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ai_results import AIImageResult, build_ai_explanation_lines
from .cache import THUMBNAIL_CACHE_VERSION
from .formats import FITS_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .imaging import FITS_STF_PRESETS, FitsDisplaySettings, load_image_for_display, sanitize_display_error
from .image_resize import _pillow_from_qimage, _qimage_from_pillow
from .metadata import CaptureMetadata, EMPTY_METADATA, load_capture_metadata
from .models import ImageRecord, JPEG_SUFFIXES
from .perf import perf_logger
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
from PIL import Image as PILImage

from .ui.mask_overlay import MaskOverlay, mask_strength_qimage
from .ui.photo_editor_panel import EditRecipe, PhotoEditorPanel
from .ui import preview_studio as studio
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
    load_image: bool = True
    load_metadata: bool = True
    zoom_refresh: bool = False
    cache_only: bool = False
    fits_display_settings: FitsDisplaySettings | None = None
    queued_at_perf: float = 0.0


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
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        queue_wait_ms = (
            (start - self.request.queued_at_perf) * 1000.0
            if logger.enabled and self.request.queued_at_perf > 0.0
            else 0.0
        )
        suffix = suffix_for_path(self.request.path)
        if self.request.load_image:
            image, error = load_image_for_display(
                self.request.path,
                self.request.target_size,
                prefer_embedded=self.request.prefer_embedded,
                fits_display_settings=self.request.fits_display_settings,
            )
            if image.isNull():
                failed_at = time.perf_counter() if logger.enabled else 0.0
                display_error = sanitize_display_error(error, path=self.request.path)
                self.result_queue.put(("failed", self.request, display_error, failed_at))
                if logger.enabled:
                    logger.duration(
                        "preview.task",
                        (failed_at - start) * 1000.0,
                        state="failed",
                        path=self.request.path,
                        slot=self.request.slot,
                        request_token=self.request.token,
                        suffix=suffix,
                        width=self.request.target_size.width(),
                        height=self.request.target_size.height(),
                        prefer_embedded=self.request.prefer_embedded,
                        cache_only=self.request.cache_only,
                        load_metadata=self.request.load_metadata,
                        zoom_refresh=self.request.zoom_refresh,
                        queue_wait_ms=queue_wait_ms,
                        active_request=not self.request.cache_only,
                        error=display_error,
                    )
                return
            ready_at = time.perf_counter() if logger.enabled else 0.0
            image_ready_ms = (ready_at - start) * 1000.0 if logger.enabled else 0.0
            self.result_queue.put(("ready", self.request, image, None, ready_at))
            if logger.enabled:
                logger.duration(
                    "preview.task",
                    image_ready_ms,
                    state="ready",
                    path=self.request.path,
                    slot=self.request.slot,
                    request_token=self.request.token,
                    suffix=suffix,
                    width=self.request.target_size.width(),
                    height=self.request.target_size.height(),
                    image_width=image.width(),
                    image_height=image.height(),
                    prefer_embedded=self.request.prefer_embedded,
                    cache_only=self.request.cache_only,
                    load_metadata=self.request.load_metadata,
                    zoom_refresh=self.request.zoom_refresh,
                    queue_wait_ms=queue_wait_ms,
                    active_request=not self.request.cache_only,
                )
        if self.request.load_metadata:
            metadata_start = time.perf_counter() if logger.enabled else 0.0
            metadata_error = ""
            try:
                metadata = load_capture_metadata(self.request.path)
            except Exception as exc:  # pragma: no cover - metadata should be best-effort
                metadata = CaptureMetadata(path=self.request.path)
                metadata_error = str(exc)
            self.result_queue.put(("metadata", self.request, metadata))
            if logger.enabled:
                logger.duration(
                    "preview.task_metadata",
                    (time.perf_counter() - metadata_start) * 1000.0,
                    path=self.request.path,
                    slot=self.request.slot,
                    request_token=self.request.token,
                    cache_only=self.request.cache_only,
                    metadata_only=not self.request.load_image,
                    zoom_refresh=self.request.zoom_refresh,
                    suffix=suffix,
                    queue_wait_ms=queue_wait_ms,
                    error=metadata_error,
                )


class PreviewPane(QWidget):
    HEART_SYMBOL = "\u2665"
    HEART_OUTLINE_SYMBOL = "\u2661"
    REJECT_SYMBOL = "\u2715"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active = False
        self._frame_visible = True
        self._studio = False
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
        self.caption_label.setWordWrap(False)
        self.caption_label.setMinimumHeight(16)

        self.metadata_label = QLabel()
        self.metadata_label.setStyleSheet("font-size: 11px; color: #9fb0c5;")
        self.metadata_label.setWordWrap(False)
        self.metadata_label.setMinimumHeight(15)
        self.metadata_label.setText(" ")
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        text_layout.addWidget(self.caption_label)
        text_layout.addWidget(self.metadata_label)

        footer_layout.addWidget(self.heart_button)
        footer_layout.addWidget(text_column, 1)
        footer_layout.addWidget(self.reject_button)

        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.footer)
        self.footer.setFixedHeight(42)
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
        # Studio has no footer at all, regardless of the caller's request.
        self.footer.setVisible(not minimal and not self._studio)

    def set_studio(self, on: bool) -> None:
        """Studio look: no footer, transparent pane on the ground — the photo
        sits flat with no frame, ring, or rounding. The pane drops its own
        margins so the stage's 12px inset is the only padding around the
        photo (matching the rail's card inset)."""
        self._studio = on
        self.footer.setVisible(not on)
        margin = 0 if on else 10
        self.layout().setContentsMargins(margin, margin, margin, margin)
        self._apply_style()

    def _apply_style(self) -> None:
        if self._studio:
            # The pane and scroll area are invisible carriers so the photo
            # floats on the ground.
            self.setStyleSheet("QWidget#previewPane { background-color: transparent; border: none; }")
            self.scroll_area.setStyleSheet(
                f"QScrollArea {{ background-color: {studio.GROUND}; border: none; }}"
            )
            self.image_label.setStyleSheet(f"background-color: {studio.GROUND}; color: {studio.TEXT_MUTE};")
            return
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
        self._studio = False
        self.setMinimumHeight(148)
        self.setMaximumHeight(168)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def set_studio(self, on: bool) -> None:
        self._studio = on
        self.update()

    def set_stats(self, stats: InspectionStats) -> None:
        self._stats = stats
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._studio:
            # Studio: no panel of its own (it sits on an inspector card) —
            # faint horizontal gridlines, soft blue luma fill with the accent
            # stroke, and dimmed RGB channel lines. Real data, prototype look.
            plot_rect = self.rect().adjusted(2, 6, -2, -8)
            if self._stats.width <= 0 or plot_rect.width() <= 0 or plot_rect.height() <= 0:
                painter.setPen(QColor(studio.TEXT_MUTE))
                painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "Load an image to inspect")
                return
            painter.setPen(QPen(QColor(28, 30, 34), 1))
            painter.drawLine(plot_rect.left(), plot_rect.center().y(), plot_rect.right(), plot_rect.center().y())
            painter.setPen(QPen(QColor(24, 26, 30), 1))
            for frac in (0.25, 0.75):
                y = plot_rect.top() + round(plot_rect.height() * frac)
                painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)

            max_value = max(
                max(self._stats.histogram_luma),
                max(self._stats.histogram_red),
                max(self._stats.histogram_green),
                max(self._stats.histogram_blue),
                1,
            )
            luma_fill = _histogram_path(self._stats.histogram_luma, plot_rect, max_value, closed=True)
            painter.fillPath(luma_fill, QColor(120, 160, 250, 52))
            painter.setPen(QPen(QColor(255, 96, 96, 110), 1.2))
            painter.drawPath(_histogram_path(self._stats.histogram_red, plot_rect, max_value))
            painter.setPen(QPen(QColor(103, 211, 137, 110), 1.2))
            painter.drawPath(_histogram_path(self._stats.histogram_green, plot_rect, max_value))
            painter.setPen(QPen(QColor(97, 177, 255, 116), 1.2))
            painter.drawPath(_histogram_path(self._stats.histogram_blue, plot_rect, max_value))
            painter.setPen(QPen(QColor(studio.ACCENT_BRIGHT), 1.6))
            painter.drawPath(_histogram_path(self._stats.histogram_luma, plot_rect, max_value))
            return

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
    DEFAULT_PRELOAD_BATCH_SIZE = 10
    MIN_PRELOAD_BATCH_SIZE = 0
    MAX_PRELOAD_BATCH_SIZE = 128
    FOCUS_ASSIST_COLOR_KEY = "preview/focus_assist_color"
    FOCUS_ASSIST_STRENGTH_KEY = "preview/focus_assist_strength"
    FOCUS_ASSIST_DIM_BACKGROUND_KEY = "preview/focus_assist_dim_background"
    FOCUS_ASSIST_ENABLED_KEY = "preview/focus_assist_enabled"
    FITS_STF_PRESET_KEY = "preview/fits_stf_preset"
    INSPECTOR_VISIBLE_KEY = "preview/inspector_visible"
    FILMSTRIP_THUMB_HEIGHT_KEY = "preview/filmstrip_thumb_height"
    FILMSTRIP_COLLAPSED_KEY = "preview/filmstrip_collapsed"
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
        self._inflight_preview_decodes: dict[str, int] = {}
        self._deferred_zoom_refreshes: set[tuple[int, str]] = set()
        self._compare_mode = False
        self._before_after_enabled = False
        self._compare_count = 3
        self._edited_variant_index = 0
        self._focused_slot = 0
        self._photoshop_available = False
        self._settings = QSettings()
        self._manual_zoom = False
        self._zoom_scale = 1.0
        self._focus_assist_enabled = self._settings.value(self.FOCUS_ASSIST_ENABLED_KEY, False, bool)
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
        self._pending_metadata_requests: set[tuple[int, int, str, bool]] = set()
        self._preload_batch_size = self.DEFAULT_PRELOAD_BATCH_SIZE
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
        self._analysis_update_timer = QTimer(self)
        self._analysis_update_timer.setSingleShot(True)
        self._analysis_update_timer.setInterval(120)
        self._analysis_update_timer.timeout.connect(self._update_analysis_panel)
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
        self._editor_recipe = EditRecipe()
        self._editor_recipe_version = 0
        self._editor_preview_cache: dict[tuple[object, ...], QImage] = {}
        self._theme = default_theme()

        self.setWindowTitle("Preview")
        self.setModal(False)
        self.setStyleSheet("background-color: #111; color: white;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(12)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("workspaceBar")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)

        self.header_identity = QWidget()
        self.header_identity.setObjectName("workspaceControls")
        header_identity_layout = QVBoxLayout(self.header_identity)
        header_identity_layout.setContentsMargins(0, 0, 0, 0)
        header_identity_layout.setSpacing(0)
        self.header_title_label = QLabel("Preview")
        self.header_title_label.setObjectName("paneTitle")
        self.header_subtitle_label = QLabel("Navigation, compare, and inspection controls")
        self.header_subtitle_label.setObjectName("panelHeaderSubtitle")
        self.header_subtitle_label.setWordWrap(True)
        header_identity_layout.addWidget(self.header_title_label)
        header_identity_layout.addWidget(self.header_subtitle_label)

        self.command_palette_button = self._build_header_tool_button("Command")
        self.command_palette_button.setToolTip("Open preview commands")
        self.command_palette_button.clicked.connect(self.command_palette_requested.emit)

        self.compare_toggle_button = self._build_header_tool_button("Compare")
        self.compare_toggle_button.setCheckable(True)
        self.compare_toggle_button.setChecked(False)
        self.compare_toggle_button.toggled.connect(self._handle_compare_button_toggled)

        self.auto_bracket_button = self._build_header_tool_button("Auto-Bracket")
        self.auto_bracket_button.setCheckable(True)
        self.auto_bracket_button.setChecked(False)
        self.auto_bracket_button.toggled.connect(self._handle_auto_bracket_button_toggled)

        self.compare_count_combo = QComboBox()
        for count in COMPARE_COUNTS:
            self.compare_count_combo.addItem(f"{count}-Up", count)
        self.compare_count_combo.setCurrentText("3-Up")
        self.compare_count_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compare_count_combo.setMinimumWidth(92)
        self.compare_count_combo.currentIndexChanged.connect(self._handle_compare_count_changed)
        self.compare_count_combo.setEnabled(False)

        self.photoshop_button = self._build_header_tool_button("Photoshop")
        self.photoshop_button.clicked.connect(self._handle_photoshop_button_clicked)

        self.before_after_button = self._build_header_tool_button("Before/After")
        self.before_after_button.setCheckable(True)
        self.before_after_button.toggled.connect(self._handle_before_after_button_toggled)

        self.focus_assist_button = QPushButton("On" if self._focus_assist_enabled else "Off")
        self.focus_assist_button.setCheckable(True)
        # Restore the persisted state before connecting so no toggle fires.
        self.focus_assist_button.setChecked(self._focus_assist_enabled)
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

        self.next_edit_button = self._build_header_tool_button("Edit 1/1")
        self.next_edit_button.clicked.connect(self._cycle_edited_variant)
        self.next_edit_button.hide()

        self.review_group = QWidget()
        self.review_group.setObjectName("workspaceControls")
        review_group_layout = QHBoxLayout(self.review_group)
        review_group_layout.setContentsMargins(0, 0, 0, 0)
        review_group_layout.setSpacing(8)
        self.review_group_label = QLabel("Review")
        self.review_group_label.setObjectName("sectionLabel")
        review_group_layout.addWidget(self.review_group_label)
        review_group_layout.addWidget(self.compare_toggle_button)
        review_group_layout.addWidget(self.auto_bracket_button)
        review_group_layout.addWidget(self.before_after_button)

        self.edit_group = QWidget()
        self.edit_group.setObjectName("workspaceControls")
        edit_group_layout = QHBoxLayout(self.edit_group)
        edit_group_layout.setContentsMargins(0, 0, 0, 0)
        edit_group_layout.setSpacing(8)
        self.edit_group_label = QLabel("Edit")
        self.edit_group_label.setObjectName("sectionLabel")
        edit_group_layout.addWidget(self.edit_group_label)
        edit_group_layout.addWidget(self.next_edit_button)
        edit_group_layout.addWidget(self.photoshop_button)

        self.layout_group = QWidget()
        self.layout_group.setObjectName("workspaceControls")
        layout_group_layout = QHBoxLayout(self.layout_group)
        layout_group_layout.setContentsMargins(0, 0, 0, 0)
        layout_group_layout.setSpacing(8)
        self.layout_group_label = QLabel("Layout")
        self.layout_group_label.setObjectName("sectionLabel")
        layout_group_layout.addWidget(self.layout_group_label)
        layout_group_layout.addWidget(self.compare_count_combo)

        self.preview_header_overflow_menu = QMenu(self)
        self.preview_header_overflow_menu.aboutToShow.connect(self._populate_header_overflow_menu)
        self.preview_header_more_button = self._build_header_tool_button("More")
        self.preview_header_more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.preview_header_more_button.setMenu(self.preview_header_overflow_menu)
        self.preview_header_more_button.hide()
        self._preview_header_group_base_visibility = {
            "review": True,
            "edit": True,
            "layout": False,
        }
        self._preview_header_overflow_hidden_groups: tuple[str, ...] = ()

        header_layout.addWidget(self.header_identity, 1)
        header_layout.addWidget(self.command_palette_button)
        header_layout.addWidget(self.review_group)
        header_layout.addWidget(self.edit_group)
        header_layout.addWidget(self.layout_group)
        header_layout.addWidget(self.preview_header_more_button)

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

        # Studio redesign: reshape the just-built widgets into the new layout
        # (build-then-restructure). Every control keeps its signals — only its
        # parent/placement/style changes. See image_triage/ui/preview_studio.py.
        self._studio_layout_active = False
        self._apply_studio_layout()

    # --- Studio layout (redesigned popout) ---------------------------------
    # Unique dialog id: every Studio rule is scoped under it so the redesign
    # out-specifies the main window's app stylesheet, which cascades into this
    # child dialog (its plain-type rules like `QComboBox {…}` would otherwise
    # tie with, and override, the Studio ones).
    STUDIO_SCOPE = "QDialog#studioPreviewDialog"

    def _studio_stylesheet_full(self) -> str:
        scope = self.STUDIO_SCOPE
        extra = f"""
            QToolButton#studioToolButton {{
                background: {studio.SURFACE_3}; border: 1px solid {studio.LINE}; color: {studio.TEXT};
                padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 500;
                min-height: 0px;
            }}
            QToolButton#studioToolButton:hover {{ background: {studio.SURFACE_HOVER}; border-color: {studio.LINE_STRONG}; }}
            QToolButton#studioToolButton:checked {{ background: {studio.ACCENT}; color: #ffffff; border-color: {studio.ACCENT}; }}
            QToolButton#studioToolButton:disabled {{ color: {studio.TEXT_MUTE}; border-color: {studio.LINE}; }}
            QComboBox:disabled {{ color: {studio.TEXT_MUTE}; border-color: {studio.LINE}; background: {studio.SURFACE_2}; }}
            QFrame#previewControlsCard {{ background: {studio.SURFACE_2}; border: 1px solid {studio.LINE}; border-radius: {studio.CARD_RADIUS}px; }}
            QLabel#previewControlsTitle {{ color: {studio.TEXT}; font-size: 13px; font-weight: 600; }}
            QLabel#previewControlsSummary {{ color: {studio.TEXT_MUTE}; font-size: 11px; }}
            QLabel#previewControlLabel {{ color: {studio.TEXT_DIM}; font-size: 12px; font-weight: 500; }}
            QLabel#previewAnalysisValue {{ color: {studio.TEXT}; font-size: 12px; }}
            QLabel#previewAnalysisHint {{ color: {studio.TEXT_MUTE}; font-size: 11px; }}
            QFrame#photoEditorPanel {{
                background: #2e2e2e; border: 1px solid #1c1c1c;
                border-radius: 6px;
            }}
            QFrame#editorTabBar {{
                background: #232323; border: none; border-bottom: 1px solid #1a1a1a;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
            QToolButton#editorTab {{
                background: transparent; border: none; color: #9a9a9a;
                padding: 7px 11px; font-size: 11px; font-weight: 600;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                min-height: 0px;
            }}
            QToolButton#editorTab:hover {{ color: #d9d9d9; }}
            QToolButton#editorTab:checked {{ background: #2e2e2e; color: #f2f2f2; }}
            QFrame#photoEditorDocBar {{ background: #2e2e2e; border-bottom: 1px solid #232323; }}
            QLabel#photoEditorSubtitle {{ color: #a0a0a0; font-size: 11px; }}
            QStackedWidget#photoEditorStack {{ background: #2e2e2e; }}
            QScrollArea#photoEditorScrollArea {{ background: #2e2e2e; border: none; }}
            QScrollArea#photoEditorScrollArea QWidget {{ background: #2e2e2e; }}
            QWidget#photoEditorBody {{ background: #2e2e2e; }}
            QScrollArea#photoEditorScrollArea QScrollBar:vertical {{
                background: transparent; width: 9px; margin: 2px 1px;
            }}
            QScrollArea#photoEditorScrollArea QScrollBar::handle:vertical {{
                background: #464646; border-radius: 4px; min-height: 24px;
            }}
            QScrollArea#photoEditorScrollArea QScrollBar::handle:vertical:hover {{ background: #5a5a5a; }}
            QScrollArea#photoEditorScrollArea QScrollBar::add-line:vertical,
            QScrollArea#photoEditorScrollArea QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollArea#photoEditorScrollArea QScrollBar::add-page:vertical,
            QScrollArea#photoEditorScrollArea QScrollBar::sub-page:vertical {{ background: transparent; }}
            QFrame#editorSection {{ background: transparent; border-bottom: 1px solid #262626; }}
            QPushButton#editorSectionHeader {{
                background: transparent; border: none; color: #d6d6d6;
                text-align: left; padding: 7px 10px; border-radius: 0px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton#editorSectionHeader:hover {{ background: #363636; color: #ffffff; }}
            QLabel#editorControlLabel {{ color: #c4c4c4; font-size: 11px; }}
            QLabel#editorNumber {{
                color: #e6e6e6; background: #232323; border: 1px solid #191919;
                border-radius: 2px; min-height: 16px; padding: 0 4px; font-size: 11px;
            }}
            QLabel#curveModeDot {{
                color: #cfcfcf; background: #232323; border: 1px solid #191919;
                border-radius: 2px; padding: 1px 6px; font-size: 10px; font-weight: 600;
            }}
            QFrame#photoEditorFooter {{
                background: #232323; border: none; border-top: 1px solid #1a1a1a;
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
            }}
            QLabel#photoEditorStatus {{ color: #8f8f8f; font-size: 10px; }}
            QFrame#photoEditorPanel QSlider::groove:horizontal {{
                height: 2px; background: #565656; border-radius: 1px;
            }}
            QFrame#photoEditorPanel QSlider::sub-page:horizontal {{
                background: #565656; border-radius: 1px;
            }}
            QFrame#photoEditorPanel QSlider::handle:horizontal {{
                width: 0px; height: 0px; margin: -5px 0;
                border-left: 5px solid transparent; border-right: 5px solid transparent;
                border-bottom: 9px solid #cfcfcf; background: transparent;
            }}
            QFrame#photoEditorPanel QSlider::handle:horizontal:hover {{ border-bottom-color: #ffffff; }}
            QFrame#photoEditorPanel QSlider#slider_temperature::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f69ff, stop:0.5 #9a9a9a, stop:1 #ffd94f);
            }}
            QFrame#photoEditorPanel QSlider#slider_tint::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #52d46b, stop:0.5 #9a9a9a, stop:1 #d64bd5);
            }}
            QFrame#photoEditorPanel QSlider#slider_vibrance::groove:horizontal,
            QFrame#photoEditorPanel QSlider#slider_saturation::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #65b7ff, stop:0.5 #d9cb60, stop:1 #ff5a5a);
            }}
            QFrame#photoEditorPanel QSlider#slider_temperature::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_tint::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_vibrance::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_saturation::sub-page:horizontal {{
                background: transparent;
            }}
            QFrame#photoEditorPanel QPushButton {{
                background: #3d3d3d; border: 1px solid #2a2a2a;
                color: #e6e6e6; padding: 5px 10px; border-radius: 3px;
                font-size: 11px; font-weight: 500;
            }}
            QFrame#photoEditorPanel QPushButton#editorActionButton {{
                min-height: 24px; padding: 5px 10px;
            }}
            QFrame#photoEditorPanel QPushButton#editorToolToggle {{
                min-height: 26px; padding: 5px 10px; font-weight: 600;
            }}
            QFrame#photoEditorPanel QPushButton#editorToolToggle:checked {{
                background: #1473e6; border-color: #1473e6; color: #ffffff;
            }}
            QLabel#editorHint {{ color: #8f8f8f; font-size: 10px; }}
            QFrame#photoEditorPanel QPushButton:hover {{
                background: #4a4a4a; border-color: #5a5a5a;
            }}
            QFrame#photoEditorPanel QPushButton:pressed {{ background: #333333; }}
            QFrame#photoEditorPanel QPushButton:disabled {{
                color: #767676; background: #333333; border-color: #282828;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton {{
                background: #1473e6; border: 1px solid #1473e6; color: #ffffff; font-weight: 600;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton:hover {{
                background: #2b84f0; border-color: #2b84f0;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton:disabled {{
                background: #2c4a6e; border-color: #2c4a6e; color: #8fa5bf;
            }}
            QFrame#photoEditorPanel QListWidget#editorList,
            QFrame#photoEditorPanel QPlainTextEdit#editorText,
            QFrame#photoEditorPanel QLineEdit,
            QFrame#photoEditorPanel QSpinBox,
            QFrame#photoEditorPanel QDoubleSpinBox,
            QFrame#photoEditorPanel QComboBox {{
                background: #1e1e1e; color: #e0e0e0;
                border: 1px solid #161616; border-radius: 3px;
                padding: 4px 6px; selection-background-color: #1473e6;
            }}
            QFrame#photoEditorPanel QLineEdit:focus,
            QFrame#photoEditorPanel QSpinBox:focus,
            QFrame#photoEditorPanel QDoubleSpinBox:focus,
            QFrame#photoEditorPanel QComboBox:focus {{ border-color: #1473e6; }}
            QFrame#photoEditorPanel QPlainTextEdit#editorText {{
                font-family: 'Consolas'; font-size: 10px; color: #c8c8c8;
            }}
            QFrame#photoEditorPanel QListWidget#editorList {{
                min-height: 74px; background: #262626; border-color: #1a1a1a;
            }}
            QFrame#photoEditorPanel QListWidget#editorList::item {{
                padding: 7px 8px; border-radius: 2px; color: #d6d6d6;
            }}
            QFrame#photoEditorPanel QListWidget#editorList::item:hover {{ background: #303030; }}
            QFrame#photoEditorPanel QListWidget#editorList::item:selected {{
                background: #264f78; color: #ffffff;
            }}
            QFrame#photoEditorPanel QCheckBox {{ color: #d6d6d6; spacing: 6px; font-size: 11px; }}
            QFrame#photoEditorPanel QCheckBox::indicator {{
                width: 13px; height: 13px; background: #1e1e1e;
                border: 1px solid #3a3a3a; border-radius: 2px;
            }}
            QFrame#photoEditorPanel QCheckBox::indicator:hover {{ border-color: #5a5a5a; }}
            QFrame#photoEditorPanel QCheckBox::indicator:checked {{
                background: #1473e6; border-color: #1473e6;
            }}
        """
        return (
            f"{scope} {{ background-color: {studio.GROUND}; color: {studio.TEXT}; }}\n"
            f"{scope} QWidget {{ font-family: 'Segoe UI'; font-size: 12px; }}\n"
            + studio.studio_stylesheet(scope=scope)
            + studio.scope_stylesheet(extra, scope)
        )

    def _studio_toggle_style(self) -> str:
        return f"""
            QPushButton {{
                background: {studio.SURFACE_3}; border: 1px solid {studio.LINE};
                color: {studio.TEXT_DIM}; padding: 5px 12px; border-radius: 6px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {studio.SURFACE_HOVER}; color: {studio.TEXT}; }}
            QPushButton:checked {{ background: {studio.ACCENT}; border-color: {studio.ACCENT}; color: #ffffff; }}
            QPushButton:disabled {{ background: {studio.SURFACE_2}; border-color: {studio.LINE}; color: {studio.TEXT_MUTE}; }}
            QPushButton:checked:disabled {{ background: {studio.SURFACE_3}; border-color: {studio.LINE_STRONG}; color: {studio.TEXT_MUTE}; }}
        """

    def _studio_card_style(self, name: str) -> str:
        return (
            f"QFrame#{name} {{ background: {studio.SURFACE_2}; border: 1px solid {studio.LINE};"
            f" border-radius: {studio.CARD_RADIUS}px; }}"
        )

    def _studio_segmented_style(self) -> str:
        return f"""
            QFrame#segmented {{ background: {studio.SURFACE_3}; border: 1px solid {studio.LINE}; border-radius: 9px; }}
            QFrame#segmented QPushButton {{
                background: transparent; border: none; color: {studio.TEXT_DIM};
                padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;
                min-height: 0px; min-width: 0px;
            }}
            QFrame#segmented QPushButton:hover {{ background: {studio.SURFACE_HOVER}; color: {studio.TEXT}; }}
            QFrame#segmented QPushButton:checked {{ background: {studio.ACCENT}; color: #ffffff; }}
            QFrame#segmented QPushButton:disabled {{ color: {studio.TEXT_MUTE}; }}
            QFrame#segmented QPushButton:checked:disabled {{ background: {studio.SURFACE_HOVER}; color: {studio.TEXT_MUTE}; }}
        """

    def _studio_combo_style(self, *, narrow: bool = False) -> str:
        return (
            f"QComboBox {{ background: {studio.SURFACE_3}; border: 1px solid {studio.LINE}; color: {studio.TEXT};"
            f" padding: 5px 9px; border-radius: 6px; font-size: 11px; min-height: 0px;"
            f" min-width: {'0' if narrow else '96'}px; }}"
            f" QComboBox:hover {{ border-color: {studio.LINE_STRONG}; }}"
            f" QComboBox:disabled {{ background: {studio.SURFACE_2}; color: {studio.TEXT_MUTE}; border-color: {studio.LINE}; }}"
            f" QComboBox::drop-down {{ border: none; width: 18px; }}"
            f" QComboBox QAbstractItemView {{ background: {studio.SURFACE_2}; border: 1px solid {studio.LINE_STRONG};"
            f" color: {studio.TEXT}; selection-background-color: {studio.ACCENT}; }}"
        )

    def _apply_studio_theme(self) -> None:
        """Studio-mode styling. Replaces the old per-widget analysis styling so
        theme changes keep the redesigned look."""
        self.setStyleSheet(self._studio_stylesheet_full())
        # Cards and combos are styled directly on the widget: the main window's
        # app stylesheet cascades into this child dialog and its plain-type
        # rules override dialog-level selectors, but never a widget's own sheet.
        for card in getattr(self, "_studio_cards", []):
            card.setStyleSheet(self._studio_card_style(card.objectName() or "card"))
        for segment in getattr(self, "_studio_segments", []):
            segment.setStyleSheet(self._studio_segmented_style())
        rail = getattr(self, "_studio_rail", None)
        if rail is not None:
            rail.setStyleSheet(
                f"QFrame#rail {{ background: {studio.SURFACE_1};"
                f" border: 1px solid {studio.LINE}; border-radius: {studio.CARD_RADIUS}px; }}"
            )
        self.compare_count_combo.setStyleSheet(self._studio_combo_style())
        self.focus_assist_color_combo.setStyleSheet(self._studio_combo_style(narrow=True))
        self.focus_assist_strength_combo.setStyleSheet(self._studio_combo_style())
        self.fits_stf_combo.setStyleSheet(self._studio_combo_style())
        # Toggle-style controls (Studio look, immune to the cascade for the
        # same reason — direct per-widget stylesheets).
        self.focus_assist_button.setStyleSheet(self._studio_toggle_style())
        self.focus_assist_background_button.setStyleSheet(self._studio_toggle_style())
        self.fits_reset_button.setStyleSheet(self._studio_toggle_style())
        self.histogram_widget.set_studio(True)
        self.histogram_widget.setMinimumHeight(84)
        self.histogram_widget.setMaximumHeight(96)
        self.histogram_widget.apply_theme(self._theme)
        for pane in self._panes:
            pane.set_studio(True)
            pane.apply_theme(self._theme)

    def _studio_group_label(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("groupLabel")
        return label

    def _studio_divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("vline")
        line.setFixedWidth(1)
        return line

    def _build_studio_nav_pill(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("navPill")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        prev = QPushButton("‹")
        prev.setObjectName("navArrow")
        prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        prev.setCursor(Qt.CursorShape.PointingHandCursor)
        prev.clicked.connect(lambda: self.navigation_requested.emit(-1))
        self._studio_nav_count = QLabel("—")
        self._studio_nav_count.setObjectName("navCount")
        self._studio_nav_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nxt = QPushButton("›")
        nxt.setObjectName("navArrow")
        nxt.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        nxt.setCursor(Qt.CursorShape.PointingHandCursor)
        nxt.clicked.connect(lambda: self.navigation_requested.emit(1))
        layout.addWidget(prev)
        layout.addWidget(self._studio_nav_count)
        layout.addWidget(nxt)
        return frame

    def _toggle_studio_inspector(self, shown: bool) -> None:
        rail = getattr(self, "_studio_rail", None)
        if rail is not None:
            rail.setVisible(shown)
        self._settings.setValue(self.INSPECTOR_VISIBLE_KEY, shown)
        self._sync_mask_overlay()

    def _sync_mask_overlay(self) -> None:
        """Push the editor panel's mask state onto the focused pane's overlay.
        The overlay goes inert (invisible, mouse-transparent) whenever the
        editor rail is hidden or the Masks tab is not active."""
        overlay = getattr(self, "_mask_overlay", None)
        panel = getattr(self, "photo_editor_panel", None)
        if overlay is None or panel is None:
            return
        state = panel.mask_overlay_state()
        rail = getattr(self, "_studio_rail", None)
        if rail is None or rail.isHidden():
            state["interactive"] = False
            state["show_overlay"] = False
            state["create_mode"] = None
        if 0 <= self._focused_slot < len(self._panes):
            overlay.attach_to(self._panes[self._focused_slot].image_label)
        overlay.set_state(**state)

    def _build_studio_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(9)

        # Rename the re-parented header buttons so the main window's app-level
        # stylesheet (which cascades into child dialogs and pins
        # workspacePresetsButton to min-height 28px) no longer matches them.
        for button in (
            self.command_palette_button,
            self.compare_toggle_button,
            self.auto_bracket_button,
            self.before_after_button,
            self.photoshop_button,
            self.next_edit_button,
        ):
            button.setObjectName("studioToolButton")

        layout.addWidget(self._studio_group_label("Review"))
        layout.addWidget(self.compare_toggle_button)
        layout.addWidget(self.auto_bracket_button)
        layout.addWidget(self.before_after_button)
        layout.addWidget(self.compare_count_combo)
        layout.addWidget(self._studio_divider())
        layout.addWidget(self._studio_group_label("Edit"))
        layout.addWidget(self.next_edit_button)
        layout.addWidget(self.photoshop_button)
        layout.addWidget(self.command_palette_button)
        layout.addStretch(1)

        self.inspector_toggle = QPushButton("Editor")
        self.inspector_toggle.setObjectName("toolBtn")
        self.inspector_toggle.setCheckable(True)
        # Restore the persisted state before connecting so no toggle fires;
        # _apply_studio_layout applies it to the rail once the rail exists.
        self.inspector_toggle.setChecked(self._settings.value(self.INSPECTOR_VISIBLE_KEY, True, bool))
        self.inspector_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.inspector_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspector_toggle.toggled.connect(self._toggle_studio_inspector)
        layout.addWidget(self.inspector_toggle)
        layout.addWidget(self._build_studio_nav_pill())

        close_btn = QPushButton("✕")
        close_btn.setObjectName("ghostBtn")
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
        return toolbar

    def _build_studio_rail(self) -> QFrame:
        """Build the popout editor rail.

        The legacy analysis panel remains hidden and keeps carrying the old
        inspection/focus/FITS state. Studio mode now presents the editor as the
        visible rail instead of mirroring those inspector controls.
        """
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(336)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.photo_editor_panel = PhotoEditorPanel(rail)
        self.photo_editor_panel.recipe_changed.connect(self._handle_editor_recipe_changed)
        self.photo_editor_panel.status_changed.connect(self._handle_editor_status_changed)
        self.photo_editor_panel.saved.connect(self._handle_editor_sidecar_saved)
        layout.addWidget(self.photo_editor_panel, 1)

        # On-canvas mask editing: the overlay lives on the focused pane's image
        # label and round-trips mask geometry with the editor panel.
        self._mask_overlay = MaskOverlay()
        self._mask_overlay.mask_created.connect(self.photo_editor_panel.handle_overlay_mask_created)
        self._mask_overlay.mask_edited.connect(self.photo_editor_panel.handle_overlay_mask_edited)
        self._mask_overlay.bitmap_edited.connect(self.photo_editor_panel.handle_overlay_bitmap_edited)
        self._mask_overlay.source_clicked.connect(self.photo_editor_panel.handle_overlay_source_clicked)
        self._mask_overlay.edit_committed.connect(self.photo_editor_panel.handle_overlay_commit)
        self.photo_editor_panel.mask_overlay_changed.connect(self._sync_mask_overlay)

        self._studio_cards = []
        self._studio_segments = []
        return rail

    def _apply_studio_layout(self) -> None:
        self._studio_layout_active = True
        self.setObjectName("studioPreviewDialog")

        toolbar = self._build_studio_toolbar()
        self._studio_toolbar = toolbar

        rail = self._build_studio_rail()
        self._studio_rail = rail
        # Every studio surface (toolbar, image stage, rail, filmstrip) floats as
        # a rounded panel on a uniform 8px gutter (matching the main window's
        # central container inset). Below the toolbar the body is two columns:
        # the image stage stacked over the filmstrip on the left, and the
        # full-height editor rail on the right — so the filmstrip ends at the
        # rail's left edge instead of running across the whole bottom.
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self.panes_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_panel.hide()
        for widget in (self.analysis_title_label, self.analysis_subtitle_label, self.inspection_hint_label):
            widget.hide()

        layout = self.layout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.replaceWidget(self.header_widget, toolbar)
        self.header_widget.hide()
        self.info_label.hide()

        self._filmstrip_current = 0
        self._filmstrip = studio.Filmstrip(focus="middle")
        self._filmstrip.restore_layout(
            self._settings.value(self.FILMSTRIP_THUMB_HEIGHT_KEY, studio.Filmstrip.DEFAULT_THUMB_H, int),
            self._settings.value(self.FILMSTRIP_COLLAPSED_KEY, False, bool),
        )
        self._filmstrip.layout_changed.connect(self._save_filmstrip_layout)
        self._filmstrip.frame_selected.connect(self._handle_studio_filmstrip_selected)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        left_column = QWidget(body)
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        layout.replaceWidget(self.content_widget, body)
        layout.setStretchFactor(body, 1)
        left_layout.addWidget(self.content_widget, 1)
        left_layout.addWidget(self._filmstrip)
        body_layout.addWidget(left_column, 1)
        body_layout.addWidget(rail)
        # Apply the persisted editor rail visibility now that the rail exists.
        rail.setVisible(self.inspector_toggle.isChecked())
        # Debounce for async thumbnail arrivals so a burst of thumbnail_ready
        # signals repopulates the strip once, not once per thumb.
        self._filmstrip_refresh_timer = QTimer(self)
        self._filmstrip_refresh_timer.setSingleShot(True)
        self._filmstrip_refresh_timer.setInterval(120)
        self._filmstrip_refresh_timer.timeout.connect(self._refresh_studio_filmstrip)

        self._apply_studio_theme()
        # Reflect initial state into the freshly-built Studio controls
        # (gray-outs, segmented selections, card summaries).
        self._sync_preview_controls()

    def set_browse_context(
        self,
        total: int,
        current: int,
        thumb_provider=None,
        tag_provider=None,
    ) -> None:
        """Give the filmstrip and nav pill their place in the full image list.

        Called by the owner whenever the popout opens or navigates. ``current``
        is the 0-based index into the owner's ordered records; providers map an
        index to a thumbnail pixmap / status tag color.
        """
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._filmstrip_current = max(0, int(current))
        if not getattr(self, "_studio_layout_active", False):
            if logger.enabled:
                logger.duration(
                    "preview.filmstrip_set_source",
                    (time.perf_counter() - start) * 1000.0,
                    state="studio_inactive",
                    total=total,
                    current=current,
                )
            return
        self._filmstrip.set_source(total, current, thumb_provider, tag_provider)
        self._studio_nav_count.setText(f"{current + 1} / {total}" if total > 0 else "—")
        if logger.enabled:
            logger.duration(
                "preview.filmstrip_set_source",
                (time.perf_counter() - start) * 1000.0,
                state="populated",
                total=total,
                current=current,
            )

    def refresh_filmstrip(self) -> None:
        """Repopulate filmstrip thumbs soon (debounced); used by the owner when
        async thumbnails finish loading."""
        if getattr(self, "_studio_layout_active", False) and self.isVisible():
            self._filmstrip_refresh_timer.start()

    def _refresh_studio_filmstrip(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._filmstrip.refresh()
        if logger.enabled:
            logger.duration(
                "preview.filmstrip_refresh",
                (time.perf_counter() - start) * 1000.0,
                current=getattr(self, "_filmstrip_current", 0),
            )

    def _save_filmstrip_layout(self) -> None:
        self._settings.setValue(self.FILMSTRIP_THUMB_HEIGHT_KEY, self._filmstrip.thumb_height())
        self._settings.setValue(self.FILMSTRIP_COLLAPSED_KEY, self._filmstrip.is_collapsed())

    def _handle_studio_filmstrip_selected(self, index: int) -> None:
        delta = index - getattr(self, "_filmstrip_current", 0)
        logger = perf_logger()
        if logger.enabled:
            logger.log(
                "preview.filmstrip_selected",
                index=index,
                previous_index=getattr(self, "_filmstrip_current", 0),
                delta=delta,
            )
        if delta:
            self.navigation_requested.emit(delta)

    def _build_header_tool_button(self, text: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("workspacePresetsButton")
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

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

    def _update_header_summary(self) -> None:
        subtitle = "Navigation, compare, and inspection controls"
        if self._winner_ladder_mode:
            subtitle = "Winner Ladder"
        elif self._before_after_enabled and self._entries:
            subtitle = f"Before/After | {self._entries[0].record.name}"
        elif self._compare_mode and self._entries:
            subtitle = f"Compare {len(self._entries)}-Up"
        elif self._entries:
            label = self._entries[0].label.strip()
            subtitle = f"{label} | {self._entries[0].record.name}" if label else self._entries[0].record.name
        self.header_subtitle_label.setText(subtitle)

    def _visible_header_groups(self) -> list[tuple[str, QWidget]]:
        return [
            ("review", self.review_group),
            ("edit", self.edit_group),
            ("layout", self.layout_group),
        ]

    def _header_group_required_width(self, widget: QWidget) -> int:
        return max(widget.minimumWidth(), widget.minimumSizeHint().width(), widget.sizeHint().width())

    def _apply_header_overflow(self) -> None:
        if getattr(self, "_studio_layout_active", False):
            # The Studio toolbar owns its own layout; the old responsive
            # overflow machinery is retired under the redesign.
            return
        available_width = self.header_widget.width()
        if available_width <= 0:
            return

        layout = self.header_widget.layout()
        if not isinstance(layout, QHBoxLayout):
            return
        margins = layout.contentsMargins()
        spacing = layout.spacing()

        active_groups = [
            (group_id, widget)
            for group_id, widget in self._visible_header_groups()
            if self._preview_header_group_base_visibility.get(group_id, False)
        ]
        hidden_groups: list[str] = []
        overflow_order = ("layout", "edit", "review")

        def required_width(group_ids_hidden: set[str]) -> int:
            width = margins.left() + margins.right()
            visible_widgets = [self.header_identity, self.command_palette_button]
            for group_id, widget in active_groups:
                if group_id not in group_ids_hidden:
                    visible_widgets.append(widget)
            if group_ids_hidden:
                visible_widgets.append(self.preview_header_more_button)
            if len(visible_widgets) > 1:
                width += (len(visible_widgets) - 1) * spacing
            width += max(180, self.header_title_label.minimumSizeHint().width())
            width += max(
                self.command_palette_button.minimumSizeHint().width(),
                self.command_palette_button.sizeHint().width(),
            )
            for group_id, widget in active_groups:
                if group_id in group_ids_hidden:
                    continue
                width += self._header_group_required_width(widget)
            if group_ids_hidden:
                width += max(
                    self.preview_header_more_button.minimumSizeHint().width(),
                    self.preview_header_more_button.sizeHint().width(),
                )
            return width

        hidden_set: set[str] = set()
        current_required_width = required_width(hidden_set)
        for group_id in overflow_order:
            if current_required_width <= available_width:
                break
            if group_id not in {active_id for active_id, _widget in active_groups}:
                continue
            hidden_set.add(group_id)
            hidden_groups.append(group_id)
            current_required_width = required_width(hidden_set)

        self._preview_header_overflow_hidden_groups = tuple(hidden_groups)
        for group_id, widget in self._visible_header_groups():
            widget.setVisible(self._preview_header_group_base_visibility.get(group_id, False) and group_id not in hidden_set)
        self.preview_header_more_button.setVisible(bool(hidden_groups))

    def _populate_header_overflow_menu(self) -> None:
        self.preview_header_overflow_menu.clear()
        if not self._preview_header_overflow_hidden_groups:
            empty_action = self.preview_header_overflow_menu.addAction("No hidden controls")
            empty_action.setEnabled(False)
            return

        if "review" in self._preview_header_overflow_hidden_groups:
            review_menu = self.preview_header_overflow_menu.addMenu("Review")
            compare_action = review_menu.addAction("Compare")
            compare_action.setCheckable(True)
            compare_action.setChecked(self._compare_mode)
            compare_action.triggered.connect(lambda checked: self.compare_toggle_button.setChecked(checked))
            auto_bracket_action = review_menu.addAction("Auto-Bracket")
            auto_bracket_action.setCheckable(True)
            auto_bracket_action.setChecked(self._auto_bracket_enabled)
            auto_bracket_action.triggered.connect(lambda checked: self.auto_bracket_button.setChecked(checked))
            before_after_action = review_menu.addAction("Before/After")
            before_after_action.setCheckable(True)
            before_after_action.setChecked(self._before_after_enabled)
            before_after_action.setEnabled(not self.before_after_button.isHidden())
            before_after_action.triggered.connect(lambda checked: self.before_after_button.setChecked(checked))

        if "edit" in self._preview_header_overflow_hidden_groups:
            edit_menu = self.preview_header_overflow_menu.addMenu("Edit")
            next_edit_action = edit_menu.addAction(self.next_edit_button.text())
            next_edit_action.setEnabled((not self.next_edit_button.isHidden()) and self.next_edit_button.isEnabled())
            next_edit_action.triggered.connect(self._cycle_edited_variant)
            photoshop_action = edit_menu.addAction("Photoshop")
            photoshop_action.setEnabled(self.photoshop_button.isEnabled())
            photoshop_action.triggered.connect(self._handle_photoshop_button_clicked)

        if "layout" in self._preview_header_overflow_hidden_groups:
            layout_menu = self.preview_header_overflow_menu.addMenu("Layout")
            if not self._compare_mode:
                idle_action = layout_menu.addAction("Compare count is available in Compare mode")
                idle_action.setEnabled(False)
            else:
                for count in COMPARE_COUNTS:
                    action = layout_menu.addAction(f"{count}-Up")
                    action.setCheckable(True)
                    action.setChecked(count == self._compare_count)
                    action.triggered.connect(lambda _checked=False, target=count: self.set_compare_count(target))

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
        self.compare_count_combo.setVisible(self._compare_mode)
        self.focus_assist_button.setText("On" if self._focus_assist_enabled else "Off")
        self.focus_assist_background_button.setText("Dimmed" if self._focus_assist_dim_background else "Original")
        advanced_focus_visible = self._focus_assist_enabled
        if getattr(self, "_studio_layout_active", False) and hasattr(self, "_studio_focus_enable"):
            # Studio: the segmented controls mirror the hidden legacy widgets
            # that carry the state; everything except the Enabled toggle grays
            # out when focus peaking is off instead of vanishing.
            self._studio_focus_enable.set_current(1 if self._focus_assist_enabled else 0)
            self._studio_focus_strength.set_current(max(0, self.focus_assist_strength_combo.currentIndex()))
            self._studio_focus_background.set_current(0 if self._focus_assist_dim_background else 1)
            self.focus_assist_color_combo.setEnabled(self._focus_assist_enabled)
            self._studio_focus_strength.setEnabled(self._focus_assist_enabled)
            self._studio_focus_background.setEnabled(self._focus_assist_enabled)
        else:
            self.focus_color_row.setVisible(advanced_focus_visible)
            self.focus_strength_row.setVisible(advanced_focus_visible)
            self.focus_background_row.setVisible(advanced_focus_visible)

        if hasattr(self, "focus_controls_summary_label"):
            self.focus_controls_summary_label.setText("On" if self._focus_assist_enabled else "Off")
        fits_controls_visible = self._focused_entry_supports_fits_stf()
        fits_reset_enabled = self._fits_display_settings.preset.id != FitsDisplaySettings().preset.id
        if getattr(self, "_studio_layout_active", False) and hasattr(self, "fits_controls_card"):
            # Studio: the FITS card keeps its place in the rail (prototype
            # layout); its controls gray out for non-FITS images.
            self.fits_controls_card.setVisible(True)
            self.fits_stf_combo.setEnabled(fits_controls_visible)
            self.fits_reset_button.setEnabled(fits_controls_visible and fits_reset_enabled)
        else:
            self.fits_controls_card.setVisible(fits_controls_visible)
            self.fits_reset_button.setEnabled(fits_reset_enabled)
        if hasattr(self, "fits_controls_summary_label"):
            self.fits_controls_summary_label.setText(self._fits_display_settings.preset.label)
        with QSignalBlocker(self.fits_stf_combo):
            fits_preset_index = self.fits_stf_combo.findData(self._fits_display_settings.preset.id)
            if fits_preset_index >= 0:
                self.fits_stf_combo.setCurrentIndex(fits_preset_index)
        if not getattr(self, "_studio_layout_active", False):
            # Legacy layout only — under Studio these are detached husks whose
            # contents were re-parented into the toolbar/rail; re-showing them
            # would float them over the dialog as orphans.
            self.header_widget.setVisible(True)
            self.analysis_panel.setVisible(True)
        hint_text = "Histogram follows the focused pane. Focus Peaking settings live in the inspection card."
        if fits_controls_visible:
            hint_text = "Histogram follows the focused pane. FITS display stretch changes the preview only."
        self.inspection_hint_label.setText(hint_text)
        self._preview_header_group_base_visibility["review"] = True
        self._preview_header_group_base_visibility["edit"] = True
        self._preview_header_group_base_visibility["layout"] = self._compare_mode
        self._update_header_summary()
        self._apply_header_overflow()
        for pane in self._panes[: len(self._entries)]:
            pane.set_minimal(False)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        if getattr(self, "_studio_layout_active", False):
            self._apply_studio_theme()
            return
        self.setStyleSheet(f"background-color: {theme.image_bg.css}; color: {theme.text_primary.css};")
        self.content_widget.setStyleSheet("background-color: transparent;")
        self.info_label.setStyleSheet(f"font-size: 14px; color: {theme.text_secondary.css};")
        self.focus_assist_button.setStyleSheet(self._toggle_button_style())
        self.focus_assist_background_button.setStyleSheet(self._toggle_button_style())
        combo_style = self._combo_box_style()
        self.focus_assist_color_combo.setStyleSheet(combo_style)
        self.focus_assist_strength_combo.setStyleSheet(combo_style)
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

    def set_preload_batch_size(self, value: int) -> None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = self.DEFAULT_PRELOAD_BATCH_SIZE
        self._preload_batch_size = max(
            self.MIN_PRELOAD_BATCH_SIZE,
            min(self.MAX_PRELOAD_BATCH_SIZE, parsed),
        )

    def preload_batch_size(self) -> int:
        return self._preload_batch_size

    def set_photoshop_available(self, available: bool) -> None:
        self._photoshop_available = available
        self.photoshop_button.setEnabled(available)
        if available:
            self.photoshop_button.setText("Photoshop")
        else:
            self.photoshop_button.setText("Photoshop Not Found")

    def show_entries(self, entries: list[PreviewEntry]) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        was_visible = self.isVisible()
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
        self._pending_zoom_refresh_slots = []
        self._deferred_zoom_refreshes.clear()
        self._zoom_request_timer.stop()
        self._dragging = False
        self._pending_right_close = False
        self._edited_variant_index = 0
        self._rebuild_entries()
        self._sync_editor_to_focused_entry()
        self._sync_preview_controls()
        fullscreen_reapplied = False
        window_activated = False
        if not was_visible:
            # Open as a normal maximized window: fills the available desktop
            # but respects the taskbar, so nothing clips off-screen. (True
            # fullscreen fought the Windows taskbar and clipped the edges;
            # revisit as an explicit toggle later.)
            self.showMaximized()
            self.raise_()
            self.activateWindow()
            window_activated = True
        elif self.isMinimized():
            self.showMaximized()
            fullscreen_reapplied = True
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._refresh_timer.start()
        QTimer.singleShot(0, self._request_preview_loads)
        if logger.enabled:
            logger.duration(
                "preview.show_entries",
                (time.perf_counter() - start) * 1000.0,
                entry_count=len(entries),
                compare_mode=self._compare_mode,
                before_after=self._before_after_enabled,
                was_visible=was_visible,
                window_activated=window_activated,
                fullscreen_reapplied=fullscreen_reapplied,
                paths=[entry.source_path for entry in entries[:8]],
            )

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
        self._pending_zoom_refresh_slots = []
        self._deferred_zoom_refreshes.clear()
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
        self._apply_header_overflow()
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
        self._settings.setValue(self.FOCUS_ASSIST_ENABLED_KEY, checked)
        self._hide_all_loupes()
        self._sync_preview_controls()
        self._render_all()

    def _handle_focus_assist_background_toggled(self, checked: bool) -> None:
        self._focus_assist_dim_background = checked
        self._settings.setValue(self.FOCUS_ASSIST_DIM_BACKGROUND_KEY, checked)
        self._focus_assist_cache.clear()
        self._sync_preview_controls()
        self._schedule_analysis_panel_update()
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
        self._schedule_analysis_panel_update()
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
        self._schedule_analysis_panel_update()
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
            self._schedule_analysis_panel_update()

    def _ensure_panes(self, count: int) -> None:
        while len(self._panes) < count:
            pane = PreviewPane()
            if getattr(self, "_studio_layout_active", False):
                pane.set_studio(True)
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
        self._source_versions = [self._entry_source_signature(entry) for entry in self._entries]
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
        if len(self._entries) == 1:
            return False
        if len(self._entries) != 1:
            return True
        if not 0 <= slot < len(self._panes):
            return False
        target = self._fit_target_size(self._panes[slot])
        minimum_width = max(720, int(target.width() * 0.66))
        minimum_height = max(420, int(target.height() * 0.66))
        return image.width() >= minimum_width and image.height() >= minimum_height

    def _should_preserve_raw_placeholder_visual(self, slot: int, path: str, *, prefer_embedded: bool) -> bool:
        if not prefer_embedded or self._manual_zoom:
            return False
        if suffix_for_path(path) not in RAW_SUFFIXES:
            return False
        if not 0 <= slot < len(self._current_placeholder_flags):
            return False
        if not self._current_placeholder_flags[slot]:
            return False
        if not 0 <= slot < len(self._current_images):
            return False
        return not self._current_images[slot].isNull()

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

    def _request_preview_loads(
        self,
        slots: list[int] | None = None,
        *,
        force_metadata: bool = False,
        zoom_refresh: bool = False,
    ) -> None:
        if not self._entries:
            return

        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        target_slots = slots if slots is not None else list(range(len(self._entries)))
        if not target_slots:
            return
        self._load_token += 1
        token = self._load_token
        self._pending_requests = 0
        self._pending_metadata_requests = {
            key for key in self._pending_metadata_requests if key[3]
        }
        cache_hits = 0
        metadata_cache_hits = 0
        queued_requests = 0
        queued_metadata_only = 0
        for slot in target_slots:
            slot_start = time.perf_counter() if logger.enabled else 0.0
            if not 0 <= slot < len(self._entries):
                continue
            entry = self._entries[slot]
            source_signature = self._entry_source_signature(entry)
            target_size = self._decode_target_size(slot)
            # Keep RAW previews on the camera-rendered embedded JPEG at every
            # zoom level. Switching to rawpy for manual zoom changes white
            # balance and tone rendering, making the photo appear to change.
            prefer_embedded = True
            fits_display_settings = self._fits_display_settings_for_entry(entry)
            cached_image, cache_key = self._cached_preview_image_with_fallback(
                entry.source_path,
                source_signature,
                target_size,
                prefer_embedded=prefer_embedded,
                fits_display_settings=fits_display_settings,
            )
            should_load_metadata = force_metadata or entry.source_path not in self._metadata_cache
            preserve_placeholder = self._should_preserve_raw_placeholder_visual(
                slot,
                entry.source_path,
                prefer_embedded=prefer_embedded,
            )
            has_cached_image = cached_image is not None and not cached_image.isNull()
            if has_cached_image:
                cache_hits += 1
                if not preserve_placeholder:
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
                if not preserve_placeholder:
                    self._render_pane(slot)
                    if slot == self._focused_slot:
                        self._schedule_analysis_panel_update()
                elif slot == self._focused_slot and metadata is not None:
                    self._schedule_analysis_panel_update()
                if not should_load_metadata:
                    metadata_cache_hits += 1
                    if logger.enabled:
                        logger.duration(
                            "preview.request_slot",
                            (time.perf_counter() - slot_start) * 1000.0,
                            path=entry.source_path,
                            slot=slot,
                            request_token=token,
                            state="cache_hit",
                            metadata_cached=True,
                            preserve_placeholder=preserve_placeholder,
                            width=target_size.width(),
                            height=target_size.height(),
                        )
                    continue
            load_image = not has_cached_image
            if load_image:
                self._pending_requests += 1
            else:
                queued_metadata_only += 1
            queued_requests += 1
            if should_load_metadata:
                self._pending_metadata_requests.add((token, slot, entry.source_path, False))
            task = PreviewTask(
                PreviewRequest(
                    path=entry.source_path,
                    token=token,
                    slot=slot,
                    target_size=target_size,
                    source_signature=source_signature,
                    prefer_embedded=prefer_embedded,
                    load_image=load_image,
                    load_metadata=should_load_metadata,
                    zoom_refresh=zoom_refresh,
                    fits_display_settings=fits_display_settings,
                    queued_at_perf=time.perf_counter() if logger.enabled else 0.0,
                ),
                self._result_queue,
            )
            if load_image:
                self._inflight_preview_decodes[entry.source_path] = (
                    self._inflight_preview_decodes.get(entry.source_path, 0) + 1
                )
            self._pool.start(task, self._visible_request_priority(slot))
            if logger.enabled:
                logger.duration(
                    "preview.request_slot",
                    (time.perf_counter() - slot_start) * 1000.0,
                    path=entry.source_path,
                    slot=slot,
                    request_token=token,
                    state="queued_metadata_only" if has_cached_image else "queued_decode",
                    zoom_refresh=zoom_refresh,
                    metadata_cached=not should_load_metadata,
                    preserve_placeholder=preserve_placeholder,
                    width=target_size.width(),
                    height=target_size.height(),
                )
        if not self._drain_timer.isActive():
            self._drain_timer.start()
        if logger.enabled:
            logger.duration(
                "preview.request_loads",
                (time.perf_counter() - start) * 1000.0,
                requested_slots=len(target_slots),
                pending_requests=self._pending_requests,
                request_token=token,
                cache_hits=cache_hits,
                metadata_cache_hits=metadata_cache_hits,
                queued_requests=queued_requests,
                queued_metadata_only=queued_metadata_only,
                force_metadata=force_metadata,
                manual_zoom=self._manual_zoom,
                zoom_refresh=zoom_refresh,
            )

    def _drain_results(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        processed = 0
        resumable_zoom_slots: set[int] = set()
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
            metadata_request_key = (int(request.token), int(request.slot), request.path, bool(request.cache_only))

            if state in {"ready", "failed"} and not request.cache_only and request.token == self._load_token and self._pending_requests > 0:
                self._pending_requests -= 1
            if state in {"ready", "failed"} and request.load_image and not request.cache_only:
                inflight_count = self._inflight_preview_decodes.get(request.path, 0)
                if inflight_count <= 1:
                    self._inflight_preview_decodes.pop(request.path, None)
                    deferred_for_path = [
                        item for item in self._deferred_zoom_refreshes if item[1] == request.path
                    ]
                    for slot, path in deferred_for_path:
                        self._deferred_zoom_refreshes.discard((slot, path))
                        if (
                            self.isVisible()
                            and self._manual_zoom
                            and 0 <= slot < len(self._entries)
                            and self._entries[slot].source_path == path
                        ):
                            resumable_zoom_slots.add(slot)
                else:
                    self._inflight_preview_decodes[request.path] = inflight_count - 1
            if state == "failed":
                self._pending_metadata_requests.discard(metadata_request_key)
            if state == "metadata":
                self._pending_metadata_requests.discard(metadata_request_key)
                metadata = payload[0] if payload else None
                if metadata is not None:
                    self._metadata_cache[request.path] = metadata
                    if (
                        not request.cache_only
                        and request.token == self._load_token
                        and 0 <= request.slot < len(self._entries)
                        and request.path == self._entries[request.slot].source_path
                    ):
                        self._current_metadata[request.slot] = metadata
                        self._render_pane(request.slot)
                        if request.slot == self._focused_slot:
                            self._schedule_analysis_panel_update()
                processed += 1
                continue
            if state == "ready":
                image = payload[0]
                metadata = payload[1] if len(payload) > 1 else None
                self._cache_preview_image(cache_key, image)
                if metadata is not None:
                    self._metadata_cache[request.path] = metadata
            if request.cache_only:
                processed += 1
                continue

            active_process_start = time.perf_counter() if logger.enabled else 0.0
            result_ready_at = 0.0
            if state == "ready" and len(payload) > 2 and isinstance(payload[2], (int, float)):
                result_ready_at = float(payload[2])
            elif state == "failed" and len(payload) > 1 and isinstance(payload[1], (int, float)):
                result_ready_at = float(payload[1])
            is_current_result = (
                request.token == self._load_token
                and 0 <= request.slot < len(self._entries)
                and request.path == self._entries[request.slot].source_path
            )

            if is_current_result:
                if state == "ready":
                    image = payload[0]
                    metadata = payload[1] if len(payload) > 1 else None
                    preserve_placeholder = self._should_preserve_raw_placeholder_visual(
                        request.slot,
                        request.path,
                        prefer_embedded=request.prefer_embedded,
                    )
                    if not preserve_placeholder:
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
                    if not preserve_placeholder:
                        self._render_pane(request.slot)
                        if request.slot == self._focused_slot:
                            self._schedule_analysis_panel_update()
                    elif request.slot == self._focused_slot and metadata is not None:
                        self._schedule_analysis_panel_update()
                else:
                    if self._current_images[request.slot].isNull():
                        self._show_failed(request.slot, payload[0])
            if logger.enabled and state in {"ready", "failed"}:
                drain_at = time.perf_counter()
                logger.duration(
                    "preview.active_result",
                    (drain_at - active_process_start) * 1000.0,
                    state=state,
                    path=request.path,
                    slot=request.slot,
                    request_token=request.token,
                    current_token=self._load_token,
                    focused_slot=self._focused_slot,
                    active_slot=request.slot == self._focused_slot,
                    stale=not is_current_result,
                    result_to_drain_ms=((active_process_start - result_ready_at) * 1000.0 if result_ready_at > 0.0 else 0.0),
                    queue_to_drain_ms=((drain_at - request.queued_at_perf) * 1000.0 if request.queued_at_perf > 0.0 else 0.0),
                )
            processed += 1

        if resumable_zoom_slots:
            self._request_preview_loads(sorted(resumable_zoom_slots), zoom_refresh=True)
        if processed == 0 and self._pending_requests == 0 and not self._pending_metadata_requests:
            self._drain_timer.stop()
        if logger.enabled and processed:
            logger.duration(
                "preview.drain_results",
                (time.perf_counter() - start) * 1000.0,
                processed=processed,
                pending_requests=self._pending_requests,
            )

    def preload_paths(self, paths: list[str], *, load_metadata: bool = True) -> None:
        if not paths:
            return
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        target_size = self._preload_target_size()
        max_paths = self._preload_batch_size
        if max_paths <= 0:
            if logger.enabled:
                logger.duration(
                    "preview.preload_paths",
                    (time.perf_counter() - start) * 1000.0,
                    candidate_count=len(paths),
                    queued=0,
                    load_metadata=load_metadata,
                    max_paths=max_paths,
                )
            return
        queued = 0
        for index, path in enumerate(paths[:max_paths]):
            if not path:
                continue
            source_signature = None
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
            queued += 1
            should_load_metadata = load_metadata and index < 2 and path not in self._metadata_cache
            if should_load_metadata:
                self._pending_metadata_requests.add((0, -1, path, True))
            task = PreviewTask(
                PreviewRequest(
                    path=path,
                    token=0,
                    slot=-1,
                    target_size=target_size,
                    source_signature=source_signature,
                    prefer_embedded=True,
                    load_metadata=should_load_metadata,
                    cache_only=True,
                    fits_display_settings=fits_display_settings,
                    queued_at_perf=time.perf_counter() if logger.enabled else 0.0,
                ),
                self._result_queue,
            )
            self._pool.start(task, -20 - index)
        if not self._drain_timer.isActive():
            self._drain_timer.start()
        if logger.enabled:
            logger.duration(
                "preview.preload_paths",
                (time.perf_counter() - start) * 1000.0,
                candidate_count=len(paths),
                queued=queued,
                load_metadata=load_metadata,
                max_paths=max_paths,
            )

    def _render_all(self) -> None:
        for slot in range(len(self._entries)):
            self._render_pane(slot)
        if self._manual_zoom:
            self._apply_zoom_to_all()
        self._update_focus_styles()
        self._update_cursor()
        self._schedule_analysis_panel_update()
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
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
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
        pane.caption_label.setToolTip(caption_text)

        metadata_lines: list[str] = []
        if metadata.display_text:
            metadata_lines.append(metadata.display_text)
        ai_text = _format_ai_metadata(entry.ai_result)
        if ai_text:
            metadata_lines.append(ai_text)
        metadata_text = "  |  ".join(metadata_lines)
        pane.metadata_label.setText(metadata_text or " ")
        pane.metadata_label.setToolTip(metadata_text)
        pane.metadata_label.setVisible(True)
        pane.heart_button.setChecked(entry.winner)
        pane.heart_button.setText(pane.HEART_SYMBOL if entry.winner else pane.HEART_OUTLINE_SYMBOL)
        pane.reject_button.setChecked(entry.reject)
        pane.reject_button.setText(pane.REJECT_SYMBOL)

        if display_image.isNull():
            pane.image_label.setScaledContents(False)
            pane.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            pane.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            current_pixmap = pane.image_label.pixmap()
            target = self._fit_target_size(pane)
            has_stable_pixmap = (
                current_pixmap is not None
                and not current_pixmap.isNull()
                and pane.image_label.width() <= target.width() + 4
                and pane.image_label.height() <= target.height() + 4
            )
            if not has_stable_pixmap:
                pane.image_label.resize(target)
                pane.image_label.clear()
                pane.image_label.setText("Loading full preview...")
            if slot < len(self._rendered_display_keys):
                self._rendered_display_keys[slot] = None
            if logger.enabled:
                logger.duration(
                    "preview.render_pane",
                    (time.perf_counter() - start) * 1000.0,
                    state="loading_placeholder",
                    path=entry.source_path,
                    slot=slot,
                    request_token=self._load_token,
                )
            return

        if self._manual_zoom:
            pane.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            pane.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
            pane.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            pane.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            pane.image_label.setScaledContents(False)
            target = self._fit_target_size(pane)
            display_key = (
                *self._display_render_key(slot, display_image),
                "fit",
                target.width(),
                target.height(),
            )
            if (
                slot < len(self._rendered_display_keys)
                and self._rendered_display_keys[slot] == display_key
                and pane.image_label.pixmap() is not None
                and not pane.image_label.pixmap().isNull()
            ):
                if logger.enabled:
                    logger.duration(
                        "preview.render_pane",
                        (time.perf_counter() - start) * 1000.0,
                        state="render_cache_hit",
                        path=entry.source_path,
                        slot=slot,
                        request_token=self._load_token,
                        image_width=display_image.width(),
                        image_height=display_image.height(),
                    )
                return
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
                self._rendered_display_keys[slot] = display_key
            pane.scroll_area.horizontalScrollBar().setValue(0)
            pane.scroll_area.verticalScrollBar().setValue(0)
        if logger.enabled:
            logger.duration(
                "preview.render_pane",
                (time.perf_counter() - start) * 1000.0,
                state="rendered",
                path=entry.source_path,
                slot=slot,
                request_token=self._load_token,
                image_width=display_image.width(),
                image_height=display_image.height(),
                manual_zoom=self._manual_zoom,
                focus_assist=self._focus_assist_enabled,
            )

    def _sync_editor_to_focused_entry(self) -> None:
        panel = getattr(self, "photo_editor_panel", None)
        if panel is None:
            return
        if not self._entries or not 0 <= self._focused_slot < len(self._entries):
            panel.set_image(None)
            return
        panel.set_image(self._entries[self._focused_slot].source_path)

    def _handle_editor_recipe_changed(self, recipe: EditRecipe) -> None:
        self._editor_recipe = recipe
        self._editor_recipe_version += 1
        self._editor_preview_cache.clear()
        self._focus_assist_cache.clear()
        if (
            0 <= self._focused_slot < len(self._rendered_display_keys)
            and self._focused_slot < len(self._panes)
        ):
            self._rendered_display_keys[self._focused_slot] = None
            self._render_pane(self._focused_slot)

    def _handle_editor_status_changed(self, message: str) -> None:
        if message and getattr(self, "_studio_layout_active", False):
            self.info_label.setText(message)

    def _handle_editor_sidecar_saved(self, path: str) -> None:
        if getattr(self, "_studio_layout_active", False):
            self.info_label.setText(f"Saved edit sidecar: {Path(path).name}")

    def _editor_recipe_is_default(self) -> bool:
        for value in asdict(self._editor_recipe).values():
            if value not in (0, 0.0, None):
                return False
        return True

    def _editor_masked_adjustments(self) -> list:
        panel = getattr(self, "photo_editor_panel", None)
        if panel is None:
            return []
        return panel.masked_adjustments()

    def _editor_edits_active(self) -> bool:
        return not self._editor_recipe_is_default() or bool(self._editor_masked_adjustments())

    def _editor_image_for_slot(self, slot: int, image: QImage) -> QImage:
        masked = self._editor_masked_adjustments()
        if (
            not 0 <= slot < len(self._entries)
            or image.isNull()
            or (self._editor_recipe_is_default() and not masked)
        ):
            return image
        cache_key = (*self._image_cache_key(slot, image), "editor", self._editor_recipe_version)
        cached = self._editor_preview_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            source = _pillow_from_qimage(image)
            adjusted = self._editor_recipe.apply(source)
            for components, source_size, mask_recipe in masked:
                # Union strength field for the mask group, from the same
                # linear-falloff math as the renderer; local adjustments
                # composite on top of the global result, weighted per-pixel
                # by group strength.
                strength_q = mask_strength_qimage(
                    components, adjusted.width, adjusted.height, source_size
                )
                if strength_q is None:
                    continue
                strength = PILImage.frombuffer(
                    "L",
                    (strength_q.width(), strength_q.height()),
                    bytes(strength_q.constBits()),
                    "raw",
                    "L",
                    strength_q.bytesPerLine(),
                    1,
                )
                local = mask_recipe.apply(adjusted)
                adjusted = PILImage.composite(local, adjusted, strength)
            rendered = _qimage_from_pillow(adjusted, target_size=QSize())
        except Exception as exc:
            self._handle_editor_status_changed(f"Preview edit failed: {exc}")
            return image
        self._editor_preview_cache[cache_key] = rendered
        return rendered

    def _display_image_for_slot(self, slot: int) -> QImage:
        if not 0 <= slot < len(self._current_images):
            return QImage()
        image = self._current_images[slot]
        if slot == self._focused_slot:
            image = self._editor_image_for_slot(slot, image)
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
        stats_image = self._inspection_source_image(image)
        stats = build_inspection_stats(stats_image)
        if not stats_image.isNull() and stats_image.size() != image.size():
            stats = replace(stats, width=image.width(), height=image.height())
        self._inspection_stats_cache[cache_key] = stats
        return stats

    def _inspection_source_image(self, image: QImage) -> QImage:
        max_edge = 1280
        if image.width() <= max_edge and image.height() <= max_edge:
            return image
        return image.scaled(
            QSize(max_edge, max_edge),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

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
        if slot == self._focused_slot and self._editor_edits_active():
            base_key = (*base_key, "editor", self._editor_recipe_version)
        if not self._focus_assist_enabled:
            return (*base_key, "display")
        return (*self._focus_assist_cache_key(slot, image), "focus-assist")

    def _focus_assist_cache_key(
        self, slot: int, image: QImage
    ) -> tuple[object, ...]:
        base_key = self._image_cache_key(slot, image)
        if slot == self._focused_slot and self._editor_edits_active():
            base_key = (*base_key, "editor", self._editor_recipe_version)
        return (
            *base_key,
            self._focus_assist_color.id,
            self._focus_assist_strength.id,
            self._focus_assist_dim_background,
        )

    def _schedule_analysis_panel_update(self, delay_ms: int = 120) -> None:
        self._analysis_update_timer.start(max(0, delay_ms))

    def _set_ai_reasons(self, lines, fallback: str = "") -> None:
        """Prototype look: each rationale line rendered as '›  reason'."""
        if isinstance(lines, str):
            lines = [lines]
        items = [line for line in lines if line]
        if not items and fallback:
            items = [fallback]
        self.ai_explanation_label.setText("\n".join(f"›  {line}" for line in items))

    def _update_studio_confidence(self, result) -> None:
        """Feed the gold confidence bar + number from the AI result's folder
        percentile (the same metric the grid badge shows)."""
        if not hasattr(self, "_studio_confidence_bar") or not hasattr(self, "_studio_confidence_pct"):
            return
        if result is None:
            self._studio_confidence_bar.set_pct(0)
            self._studio_confidence_pct.setText("--")
            return
        value = result.folder_percentile if result.folder_percentile is not None else result.normalized_score
        self._studio_confidence_bar.set_pct(int(round(value)) if value is not None else 0)
        self._studio_confidence_pct.setText(result.display_score_text or "--")

    def _update_analysis_panel(self) -> None:
        if not self._entries or not 0 <= self._focused_slot < len(self._entries):
            self.analysis_subtitle_label.setText("Focused image analysis")
            self.histogram_widget.set_stats(EMPTY_INSPECTION_STATS)
            self.inspection_dimensions_label.setText("--")
            self.inspection_exposure_label.setText("--")
            self.inspection_clipping_label.setText("--")
            self.inspection_detail_label.setText("--")
            self.ai_confidence_label.setText("Confidence: --")
            self._update_studio_confidence(None)
            self._set_ai_reasons([], fallback="Load an AI-scored image to see ranking rationale.")
            return

        entry = self._entries[self._focused_slot]
        stats = self._inspection_stats_for_slot(self._focused_slot)
        title = Path(entry.source_path).name
        if entry.label:
            title = f"{entry.label} | {title}"
        self.analysis_subtitle_label.setText(title)
        self.histogram_widget.set_stats(stats)

        if stats.width <= 0 or stats.height <= 0:
            self.inspection_dimensions_label.setText("Loading...")
            self.inspection_exposure_label.setText("Loading...")
            self.inspection_clipping_label.setText("Loading...")
            self.inspection_detail_label.setText("Loading...")
            self.ai_confidence_label.setText(
                f"Confidence: {entry.ai_result.confidence_bucket_label}" if entry.ai_result is not None else "Confidence: --"
            )
            self._update_studio_confidence(entry.ai_result)
            explanation_lines = list(build_ai_explanation_lines(entry.ai_result, review_summary=entry.review_summary))
            explanation_lines.extend(entry.workflow_details[:3])
            self._set_ai_reasons(explanation_lines, fallback="Load an AI-scored image to see ranking rationale.")
            return

        self.inspection_dimensions_label.setText(f"{stats.width:,} x {stats.height:,}")
        self.inspection_exposure_label.setText(
            f"mean {stats.mean_luminance:.0f} · median {stats.median_luminance:.0f}"
        )
        self.inspection_clipping_label.setText(
            f"{stats.highlight_clip_pct:.1f}% hi · {stats.shadow_clip_pct:.1f}% lo"
        )
        self.inspection_detail_label.setText(
            f"{stats.detail_score:.0f}/100 · {_detail_label(stats.detail_score)}"
        )
        if entry.ai_result is None:
            self.ai_confidence_label.setText("Confidence: --")
            self._update_studio_confidence(None)
            self._set_ai_reasons(list(entry.workflow_details), fallback="AI is not loaded for this image.")
            return
        self.ai_confidence_label.setText(f"Confidence: {entry.ai_result.confidence_bucket_label}")
        self._update_studio_confidence(entry.ai_result)
        explanation = build_ai_explanation_lines(
            entry.ai_result,
            review_summary=entry.review_summary,
            detail_score=stats.detail_score,
        )
        explanation_lines = list(explanation)
        explanation_lines.extend(entry.workflow_details[:3])
        self._set_ai_reasons(
            explanation_lines,
            fallback="AI scoring is loaded, but no explanation signals are available.",
        )

    def _show_failed(self, slot: int, message: str) -> None:
        if not 0 <= slot < len(self._entries):
            return
        pane = self._panes[slot]
        pane.image_label.setScaledContents(False)
        pane.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane.image_label.resize(self._fit_target_size(pane))
        pane.image_label.clear()
        if slot < len(self._rendered_display_keys):
            self._rendered_display_keys[slot] = None
        pane.image_label.setText(f"Failed\n{message}")

    def _fit_target_size(self, pane: PreviewPane) -> QSize:
        viewport = pane.scroll_area.viewport().size()
        # Studio panes are flush with the stage's 12px inset, so the photo
        # fits edge-to-edge; the legacy frame keeps a little slack so the
        # centered image never touches its border.
        slack = 0 if getattr(self, "_studio_layout_active", False) else 8
        width = max(1, viewport.width() - slack)
        height = max(1, viewport.height() - slack)
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
        overscan = 1.25 if len(self._entries) <= 1 else 1.12
        max_edge = 3840 if len(self._entries) <= 1 else 2560
        return QSize(
            min(max_edge, max(1, int(round(fit_target.width() * overscan)))),
            min(max_edge, max(1, int(round(fit_target.height() * overscan)))),
        )

    def _prime_entry_images_from_cache(self) -> None:
        if not self._entries:
            return
        for slot, entry in enumerate(self._entries):
            source_signature = self._source_versions[slot] if slot < len(self._source_versions) else self._entry_source_signature(entry)
            target_size = self._decode_target_size(slot)
            cached_image, cache_key = self._cached_preview_image_with_fallback(
                entry.source_path,
                source_signature,
                target_size,
                prefer_embedded=True,
                fits_display_settings=self._fits_display_settings_for_entry(entry),
            )
            if cached_image is None or cached_image.isNull():
                continue
            preserve_placeholder = self._should_preserve_raw_placeholder_visual(
                slot,
                entry.source_path,
                prefer_embedded=True,
            )
            if not preserve_placeholder:
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
            THUMBNAIL_CACHE_VERSION,
            path,
            source_signature,
            max(0, target_size.width()),
            max(0, target_size.height()),
            prefer_embedded,
            self._fits_display_cache_key_for_path(path, fits_display_settings),
        )

    def _entry_source_signature(self, entry: PreviewEntry) -> tuple[int, int] | None:
        signature = _record_path_signature(entry.record, entry.source_path)
        if signature is not None:
            return signature
        return _file_signature(entry.source_path)

    def _cached_preview_image(
        self,
        cache_key: tuple[object, ...],
    ) -> QImage | None:
        cached = self._preview_cache.get(cache_key)
        if cached is None:
            return None
        self._preview_cache.move_to_end(cache_key)
        return cached[0]

    def _cached_preview_image_with_fallback(
        self,
        path: str,
        source_signature: tuple[int, int] | None,
        target_size: QSize,
        *,
        prefer_embedded: bool,
        fits_display_settings: FitsDisplaySettings | None = None,
    ) -> tuple[QImage | None, tuple[object, ...]]:
        cache_key = self._preview_cache_key(
            path,
            source_signature,
            target_size,
            prefer_embedded=prefer_embedded,
            fits_display_settings=fits_display_settings,
        )
        cached_image = self._cached_preview_image(cache_key)
        if cached_image is not None or source_signature is None:
            return cached_image, cache_key
        preload_key = self._preview_cache_key(
            path,
            None,
            target_size,
            prefer_embedded=prefer_embedded,
            fits_display_settings=fits_display_settings,
        )
        cached_image = self._cached_preview_image(preload_key)
        if cached_image is not None:
            self._cache_preview_image(cache_key, cached_image)
        return cached_image, cache_key

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
        matching_keys = [key for key in self._preview_cache if len(key) > 1 and key[1] == path]
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
        self._deferred_zoom_refreshes.clear()
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
        ready_slots: list[int] = []
        deferred_slots: list[int] = []
        for slot in slots:
            if not 0 <= slot < len(self._entries):
                continue
            path = self._entries[slot].source_path
            key = (slot, path)
            self._deferred_zoom_refreshes = {
                item for item in self._deferred_zoom_refreshes if item[0] != slot
            }
            if self._inflight_preview_decodes.get(path, 0) > 0:
                self._deferred_zoom_refreshes.add(key)
                deferred_slots.append(slot)
            else:
                ready_slots.append(slot)
        logger = perf_logger()
        if logger.enabled and deferred_slots:
            logger.log(
                "preview.zoom_refresh_coalesced",
                slots=deferred_slots,
                paths=[self._entries[slot].source_path for slot in deferred_slots],
            )
        if ready_slots:
            self._request_preview_loads(ready_slots, zoom_refresh=True)

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
        self._sync_editor_to_focused_entry()
        self._sync_mask_overlay()
        self._update_focus_styles()
        if getattr(self, "_studio_layout_active", False):
            # The Studio ring is painted on the photo pixmap, so moving focus
            # needs a re-render; the display keys carry the active flag, so
            # only the two affected panes actually redraw.
            self._render_all()
        self._schedule_analysis_panel_update()
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
            self._schedule_analysis_panel_update()
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
            self._schedule_analysis_panel_update()
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
            self._update_header_summary()
            self.info_label.clear()
            return
        self._update_header_summary()
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


def _record_path_signature(record: ImageRecord, path: str) -> tuple[int, int] | None:
    normalized_path = _normalized_path_key(path)
    if normalized_path == _normalized_path_key(record.path):
        return (record.modified_ns, record.size)
    for variant in record.display_variants:
        if normalized_path == _normalized_path_key(variant.path):
            return (variant.modified_ns, variant.size)
    return None


def _normalized_path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


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
