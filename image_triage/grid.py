from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QMimeData, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QBrush, QColor, QContextMenuEvent, QCursor, QDrag, QFont, QImage, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPaintEvent, QPainterPath, QPalette, QPen, QPixmap, QTextOption, QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QMenu, QToolButton

from .ai_results import AIConfidenceBucket, AIImageResult, refine_ai_result_with_review_insight
from .cache import ThumbnailKey
from .metadata import CaptureMetadata, MetadataKey, MetadataManager
from .models import ImageRecord, ImageVariant, SessionAnnotation
from .perf import perf_logger
from .review_workflows import review_round_short_label
from .scanner import normalized_path_key
from .thumbnails import ThumbnailManager
from .ui.grid_card_renderer import (
    COMPACT_COLUMN_THRESHOLD,
    IMAGE_CORNER_RADIUS,
    GridCardData,
    grid_card_action_rects,
    grid_card_height_for_width,
    load_action_icon,
    paint_grid_card,
    _paint_duplicate_icon,
    _paint_heart_icon,
    _paint_reject_icon,
    _paint_spark_icon,
)
from .ui.prototype_style import folder_icon_pixmap
from .ui.theme import ThemePalette, default_theme


_AI_RESULT_MISSING = object()


def _fast_path_key(path: str) -> str:
    return os.path.normpath(path).casefold()


@dataclass(slots=True, frozen=True)
class BurstVisualInfo:
    group_number: int
    index_in_group: int
    group_size: int
    label: str = "Group"
    kind: str = "similar"


@dataclass(slots=True, frozen=True)
class GridDeltaUpdate:
    changed_paths: tuple[str, ...] = ()
    selection_anchor: int | None = None
    preserve_pixmap_cache: bool = True


class ThumbnailGridView(QAbstractScrollArea):
    INTERNAL_RECORD_MIME = "application/x-image-triage-record-paths"
    HEART_SYMBOL = "\u2665"
    HEART_OUTLINE_SYMBOL = "\u2661"
    REJECT_SYMBOL = "\u2715"
    UNDO_SYMBOL = "\u21b6"
    LEFT_ARROW_SYMBOL = "\u276e"
    RIGHT_ARROW_SYMBOL = "\u276f"

    # Bounds for the continuous (smooth) zoom tile width, in logical px.
    MIN_TILE_WIDTH = 120
    MAX_TILE_WIDTH = 620

    current_changed = Signal(int)
    preview_requested = Signal(int)
    delete_requested = Signal(int)
    move_requested = Signal(int)
    keep_requested = Signal(int)
    rate_requested = Signal(int, int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)
    adapter_label_requested = Signal(str, str)
    adapter_reasons_requested = Signal(str, tuple)
    adapter_review_mode_cleared = Signal()
    dispute_label_requested = Signal(str, str)  # (record_path, user_corrective_label)
    dispute_chord_started = Signal()  # user pressed D, awaiting 1-5
    dispute_chord_cancelled = Signal()  # chord timed out or unrelated key pressed
    context_menu_requested = Signal(int, object)
    selection_changed = Signal()

    def __init__(self, thumbnail_manager: ThumbnailManager, parent=None) -> None:
        super().__init__(parent)
        self.thumbnail_manager = thumbnail_manager
        self.thumbnail_manager.thumbnail_ready.connect(self._handle_thumbnail_ready)
        self.thumbnail_manager.thumbnail_failed.connect(self._handle_thumbnail_failed)
        self.metadata_manager = MetadataManager(parent=self)
        self.metadata_manager.metadata_ready.connect(self._handle_metadata_ready)

        self._items: list[ImageRecord] = []
        self._path_to_index: dict[str, int] = {}
        self._fast_path_to_index: dict[str, int] = {}
        self._variant_path_to_index: dict[str, int] = {}
        self._variant_indexes: dict[str, int] = {}
        self._annotations: dict[str, SessionAnnotation] = {}
        self._burst_groups_by_path: dict[str, BurstVisualInfo] = {}
        self._burst_groups: list[tuple[int, ...]] = []
        self._burst_group_members_by_index: dict[int, tuple[int, ...]] = {}
        self._burst_group_anchor_by_index: dict[int, int] = {}
        self._burst_group_members_by_anchor: dict[int, tuple[int, ...]] = {}
        self._burst_display_member_by_anchor: dict[int, int] = {}
        self._burst_stack_mode = False
        self._visible_item_indexes: list[int] = []
        self._visible_slot_by_item_index: dict[int, int] = {}
        self._ai_results_by_path: dict[str, AIImageResult] = {}
        self._ai_results_by_fast_path: dict[str, AIImageResult] = {}
        self._ai_result_cache: dict[str, AIImageResult | None] = {}
        self._review_insights_by_path: dict[str, object] = {}
        self._workflow_insights_by_path: dict[str, object] = {}
        self._dino_prefilter_decisions_by_path: dict[str, object] = {}
        self._normalized_path_cache: dict[str, str] = {}
        self._failed_paths: set[str] = set()
        self._failed_messages: dict[str, str] = {}
        self._empty_message = "Choose a folder to start triaging images."
        self._meta_cache: dict[str, str] = {}
        self._meta_with_ai_cache: dict[str, str] = {}
        self._capture_cache: dict[str, str] = {}
        self._display_aspect_ratio_by_path: dict[str, float] = {}
        self._pixmap_cache: OrderedDict[ThumbnailKey, tuple[QPixmap, int]] = OrderedDict()
        self._pixmap_cache_bytes = 0
        self._pixmap_cache_limit = 192 * 1024 * 1024
        self._current_index = -1
        self._selected_indexes: set[int] = set()
        self._selection_anchor = -1
        self._tool_checkbox_mode = False
        self._tool_tile_toggle_mode = False
        self._free_smooth_scroll_enabled = False
        self._action_mode = "normal"
        self._show_ai_annotations = False
        self._compact_card_mode = False
        # AI dispute chord state: when the user presses D in AI Review mode we
        # set this to time.time() + 2.0, so the next 1-5 keypress within the
        # window counts as a dispute (with the chosen corrective label).
        self._awaiting_dispute_until: float = 0.0
        self._disputed_paths: set[str] = set()
        self._adapter_review_mode = False
        self._adapter_review_paths: set[str] = set()
        self._adapter_review_label_controls_enabled = True
        self._adapter_review_reason_controls_enabled = False
        self._adapter_labels_by_path: dict[str, str] = {}
        self._adapter_reason_tags_by_path: dict[str, tuple[str, ...]] = {}
        self._adapter_reason_options: tuple[tuple[str, str], ...] = ()
        self._adapter_label_combos: dict[int, QComboBox] = {}
        self._adapter_reason_buttons: dict[int, QToolButton] = {}
        self._columns = 3
        # Zoom can be driven two ways: discrete "column" mode (combo/presets,
        # fills the row width) or continuous "tile" mode (the zoom slider, fixed
        # tile width that reflows columns to fit — this is what feels smooth).
        self._zoom_mode = "column"
        self._zoom_columns = 3
        self._zoom_tile_width = 240
        self._row_x_offset = 0
        self._margin = 18
        self._spacing = 18
        self._caption_height = 22
        self._action_height = 24
        self._capture_height = 16
        self._meta_height = 16
        self._image_padding = 10
        self._buffer_rows = 1
        self._tile_width_value = 220
        self._image_height_value = 180
        self._tile_height_value = 0
        self._row_height_value = 0
        self._thumbnail_target_size_value = QSize(64, 64)
        self._thumbnail_request_timer = QTimer(self)
        self._thumbnail_request_timer.setSingleShot(True)
        self._thumbnail_request_timer.setInterval(20)
        self._thumbnail_request_timer.timeout.connect(self._request_visible_thumbnails)
        self._loupe_card_style = "detailed"
        self._title_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._meta_font = QFont("Segoe UI", 9)
        self._review_title_font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        self._review_capture_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._review_meta_font = QFont("Segoe UI", 9)
        self._review_badge_font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        self._placeholder_font = QFont("Segoe UI", 11)
        self._empty_font = QFont("Segoe UI", 14)
        self._border_active = QColor("#2ed58e")
        self._border_selected = QColor("#39454a")
        self._border_idle = QColor("#252a31")
        self._background_active = QColor("#1f2926")
        self._background_selected = QColor("#1a211f")
        self._background_idle = QColor("#121417")
        self._title_color = QColor("#f4f7fb")
        self._capture_color = QColor("#c6d2e0")
        self._meta_color = QColor("#9aa9bd")
        self._placeholder_color = QColor("#2a3441")
        self._placeholder_text_color = QColor("#afbdcf")
        self._failed_text_color = QColor("#d7a6a6")
        self._badge_background = QColor(10, 15, 20, 190)
        self._badge_text_color = QColor("#f8f9fb")
        self._winner_color = QColor("#ff6f7d")
        self._winner_button_fill = QColor(255, 111, 125, 38)
        self._winner_button_border = QColor(255, 255, 255, 55)
        self._winner_button_hover = QColor(255, 255, 255, 80)
        self._accepted_color = QColor("#46c37b")
        self._accepted_badge_fill = QColor(28, 92, 56, 215)
        self._accepted_badge_text = QColor("#e8fff1")
        self._edited_badge_fill = QColor(28, 64, 120, 215)
        self._edited_badge_text = QColor("#e8f1ff")
        self._burst_badge_fill = QColor(22, 90, 146, 220)
        self._burst_badge_text = QColor("#edf7ff")
        self._burst_accent = QColor("#57b1ff")
        self._ai_pick_badge_fill = QColor(180, 138, 26, 220)
        self._ai_pick_badge_text = QColor("#fff6d8")
        self._review_badge_border = QColor(255, 212, 112, 80)
        self._review_scrim_color = QColor("#07090d")
        self._review_duplicate_badge_fill = QColor(23, 25, 27, 190)
        self._review_duplicate_badge_text = QColor("#ead9a8")
        self._review_ai_badge_fill = QColor(124, 84, 20, 168)
        self._review_ai_badge_color = QColor("#ffd36c")
        self._review_keeper_color = QColor("#8ef7a8")
        self._review_index_text = QColor("#8fc1ff")
        self._ai_score_badge_fill = QColor(14, 19, 29, 210)
        self._ai_score_badge_text = QColor("#dce8ff")
        self._workflow_best_badge_fill = QColor(34, 96, 64, 220)
        self._workflow_best_badge_text = QColor("#ebfff2")
        self._workflow_round_badge_fill = QColor(28, 82, 120, 220)
        self._workflow_round_badge_text = QColor("#e8f4ff")
        self._workflow_miss_badge_fill = QColor(120, 28, 36, 220)
        self._workflow_miss_badge_text = QColor("#ffe8ea")
        self._workflow_review_badge_fill = QColor(117, 82, 18, 220)
        self._workflow_review_badge_text = QColor("#fff4d6")
        self._reject_color = QColor("#ff7777")
        self._reject_button_fill = QColor(255, 119, 119, 40)
        self._reject_button_border = QColor(255, 255, 255, 55)
        self._reject_button_hover = QColor(255, 255, 255, 80)
        self._reject_badge_fill = QColor(120, 28, 36, 215)
        self._reject_badge_text = QColor("#ffe8ea")
        self._winner_button_size = QSize(34, 22)
        self._winner_button_font = QFont("Segoe UI Symbol", 12)
        self._checkbox_size = QSize(22, 22)
        self._hovered_winner_index = -1
        self._hovered_reject_index = -1
        self._hovered_left_arrow_index = -1
        self._hovered_right_arrow_index = -1
        self._hovered_burst_left_index = -1
        self._hovered_burst_right_index = -1
        self._hovered_checkbox_index = -1
        self._press_pos: QPoint | None = None
        self._press_index = -1
        self._press_on_interactive_control = False
        self._pending_single_selection_index = -1
        self._pending_clear_selection = False
        self._marquee_origin: QPoint | None = None
        self._marquee_rect = QRect()
        self._marquee_base_selection: set[int] = set()
        self._marquee_active = False
        self._wheel_angle_remainder = 0
        self._wheel_pixel_remainder = 0
        self._zoom_wheel_angle_remainder = 0
        self._zoom_wheel_pixel_remainder = 0
        self._smooth_scroll_target: int | None = None
        self._smooth_scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._smooth_scroll_animation.setDuration(150)
        self._smooth_scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._smooth_scroll_animation.finished.connect(self._handle_smooth_scroll_finished)
        self._zoom_index = -1
        self._zoom_factor = 1.0
        self._zoom_focus = (0.5, 0.5)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().valueChanged.connect(self._handle_scroll_value_changed)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.apply_theme(default_theme())
        self._recalculate_metrics()

    def apply_theme(self, theme: ThemePalette) -> None:
        self._border_active = QColor("#6090ff")
        self._border_selected = QColor("#78a0fa")
        self._border_idle = theme.border.qcolor()
        self._background_active = theme.raised_bg.qcolor()
        self._background_selected = theme.panel_bg.qcolor()
        self._background_idle = theme.panel_alt_bg.qcolor()
        self._viewport_bg = theme.image_bg.qcolor()           # #070707 viewport
        self._footer_bg = theme.panel_alt_bg.qcolor().darker(110)  # rating strip under each image
        self._title_color = theme.text_primary.qcolor()
        self._capture_color = theme.text_secondary.qcolor()
        self._meta_color = theme.text_muted.qcolor()
        self._placeholder_color = theme.input_hover_bg.qcolor()
        self._placeholder_text_color = theme.text_secondary.qcolor()
        self._failed_text_color = theme.danger.qcolor()
        self._badge_background = theme.badge_bg.qcolor()
        self._badge_text_color = theme.badge_text.qcolor()
        self._winner_color = theme.success.qcolor()
        self._winner_button_fill = theme.success_soft.with_alpha(58).qcolor()
        self._winner_button_border = theme.border.with_alpha(105).qcolor()
        self._winner_button_hover = theme.border.with_alpha(150).qcolor()
        self._accepted_color = theme.success.qcolor()
        self._accepted_badge_fill = theme.success_soft.qcolor()
        self._accepted_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.success.qcolor()
        self._edited_badge_fill = theme.accent_soft.with_alpha(180).qcolor()
        self._edited_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.accent.qcolor()
        self._burst_badge_fill = theme.accent_soft.qcolor()
        self._burst_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.accent.qcolor()
        self._burst_accent = theme.accent.qcolor()
        self._ai_pick_badge_fill = theme.warning_soft.qcolor()
        self._ai_pick_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.warning.qcolor()
        self._ai_score_badge_fill = theme.badge_bg.qcolor()
        self._ai_score_badge_text = theme.badge_text.qcolor()
        self._workflow_best_badge_fill = theme.success_soft.qcolor()
        self._workflow_best_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.success.qcolor()
        self._workflow_round_badge_fill = theme.accent_soft.qcolor()
        self._workflow_round_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.accent.qcolor()
        self._workflow_miss_badge_fill = theme.danger_soft.qcolor()
        self._workflow_miss_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.danger.qcolor()
        self._workflow_review_badge_fill = theme.warning_soft.qcolor()
        self._workflow_review_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.warning.qcolor()
        self._reject_color = theme.danger.qcolor()
        self._reject_button_fill = theme.danger_soft.with_alpha(50).qcolor()
        self._reject_button_border = theme.border.with_alpha(105).qcolor()
        self._reject_button_hover = theme.border.with_alpha(150).qcolor()
        self._reject_badge_fill = theme.danger_soft.qcolor()
        self._reject_badge_text = theme.badge_text.qcolor() if theme.is_dark else theme.danger.qcolor()
        self._checkbox_border = theme.border.with_alpha(190).qcolor()
        self._checkbox_fill = theme.raised_bg.with_alpha(225).qcolor()
        self._checkbox_selected_fill = theme.accent.qcolor()
        self._checkbox_check = theme.badge_text.qcolor() if theme.is_dark else QColor("#ffffff")

        palette = self.palette()
        # Darker viewport so the thumbnail cards float over it (prototype look).
        palette.setColor(QPalette.ColorRole.Base, theme.window_bg.qcolor())
        palette.setColor(QPalette.ColorRole.Window, theme.panel_bg.qcolor())
        palette.setColor(QPalette.ColorRole.Text, theme.text_primary.qcolor())
        palette.setColor(QPalette.ColorRole.Mid, theme.border.qcolor())
        self.setPalette(palette)
        self.viewport().update()

    def set_items(
        self,
        items: list[ImageRecord],
        *,
        emit_state_signals: bool = True,
        request_thumbnails: bool = True,
    ) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        previous_variant_indexes = dict(self._variant_indexes)
        self._reset_image_zoom()
        was_in_adapter_review = self._adapter_review_mode
        self._adapter_review_mode = False
        self._adapter_review_paths.clear()
        self._adapter_labels_by_path.clear()
        self._delete_adapter_label_controls()
        if was_in_adapter_review:
            self.adapter_review_mode_cleared.emit()
        self._items = items
        self._burst_groups_by_path = {}
        self._burst_groups = []
        self._burst_group_members_by_index = {}
        self._burst_group_anchor_by_index = {}
        self._burst_group_members_by_anchor = {}
        self._burst_display_member_by_anchor = {}
        self._path_to_index = {item.path: index for index, item in enumerate(items)}
        self._variant_path_to_index = {
            variant.path: index
            for index, item in enumerate(items)
            for variant in item.display_variants
        }
        self._fast_path_to_index = {
            _fast_path_key(path): index
            for path, index in self._variant_path_to_index.items()
        }
        self._fast_path_to_index.update(
            {
                _fast_path_key(path): index
                for path, index in self._path_to_index.items()
            }
        )
        self._variant_indexes = {
            item.path: min(previous_variant_indexes.get(item.path, 0), max(0, item.stack_count - 1))
            for item in items
        }
        self._failed_paths.clear()
        self._failed_messages.clear()
        # Keep folder-open fast for large lists by priming only visible rows.
        self._meta_cache.clear()
        self._capture_cache.clear()
        self._ai_result_cache.clear()
        self._normalized_path_cache.clear()
        self._meta_with_ai_cache.clear()
        self._display_aspect_ratio_by_path.clear()
        self._clear_pixmap_cache()
        self._current_index = 0 if items else -1
        self._selected_indexes = {0} if items else set()
        self._selection_anchor = self._current_index
        self._reset_pointer_interaction(clear_marquee=True)
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        self._prime_visible_text_caches()
        self.viewport().update()
        if request_thumbnails:
            self._schedule_visible_thumbnail_requests(immediate=True)
        if emit_state_signals:
            self.current_changed.emit(self._current_index)
            self.selection_changed.emit()
        if logger.enabled:
            logger.duration(
                "grid.set_items",
                (time.perf_counter() - start) * 1000.0,
                count=len(items),
                columns=self._columns,
                visible_count=len(self._visible_item_indexes),
            )

    def append_items(self, items: list[ImageRecord], *, request_thumbnails: bool = True) -> None:
        if not items:
            return
        start_index = len(self._items)
        self._items.extend(items)
        for offset, item in enumerate(items):
            index = start_index + offset
            self._path_to_index[item.path] = index
            self._fast_path_to_index[_fast_path_key(item.path)] = index
            self._variant_indexes[item.path] = min(
                self._variant_indexes.get(item.path, 0),
                max(0, item.stack_count - 1),
            )
            for variant in item.display_variants:
                self._variant_path_to_index[variant.path] = index
                self._fast_path_to_index[_fast_path_key(variant.path)] = index
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        self._prime_visible_text_caches(limit=120)
        self.viewport().update()
        if request_thumbnails:
            self._schedule_visible_thumbnail_requests()

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._annotations = annotations
        self.viewport().update()

    def set_adapter_review_mode(
        self,
        paths: list[str] | tuple[str, ...],
        labels_by_path: dict[str, str] | None = None,
        *,
        label_controls_enabled: bool = True,
        reason_controls_enabled: bool = False,
        reason_tags_by_path: dict[str, tuple[str, ...]] | None = None,
        reason_options: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self._adapter_review_mode = True
        self._adapter_review_paths = {_fast_path_key(path) for path in paths if path}
        self._adapter_review_label_controls_enabled = bool(label_controls_enabled)
        self._adapter_review_reason_controls_enabled = bool(reason_controls_enabled)
        self._adapter_labels_by_path = dict(labels_by_path or {})
        self._adapter_reason_tags_by_path = {
            str(path): tuple(values)
            for path, values in (reason_tags_by_path or {}).items()
            if values
        }
        self._adapter_reason_options = tuple(reason_options or ())
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        if self._visible_item_indexes:
            self._current_index = self._visible_item_indexes[0]
            self._selected_indexes = {self._current_index}
            self._selection_anchor = self._current_index
            self.current_changed.emit(self._current_index)
            self.selection_changed.emit()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def clear_adapter_review_mode(self) -> None:
        if not self._adapter_review_mode:
            return
        self._adapter_review_mode = False
        self._adapter_review_paths.clear()
        self._adapter_review_label_controls_enabled = True
        self._adapter_review_reason_controls_enabled = False
        self._adapter_labels_by_path.clear()
        self._adapter_reason_tags_by_path.clear()
        self._hide_adapter_label_controls()
        self._hide_adapter_reason_controls()
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.adapter_review_mode_cleared.emit()

    def update_adapter_review_labels(self, labels_by_path: dict[str, str]) -> None:
        self._adapter_labels_by_path = dict(labels_by_path)
        self._sync_adapter_label_controls()
        self.viewport().update()

    def update_adapter_review_reason_tags(self, reason_tags_by_path: dict[str, tuple[str, ...]]) -> None:
        self._adapter_reason_tags_by_path = {
            str(path): tuple(values)
            for path, values in reason_tags_by_path.items()
            if values
        }
        self._sync_adapter_reason_controls()
        self.viewport().update()

    def update_annotations(self, changed_paths: list[str] | tuple[str, ...] | set[str]) -> None:
        if not changed_paths:
            return
        dirty_indexes = self._indexes_for_paths(changed_paths)
        if not dirty_indexes:
            return
        self._update_selection_tiles(dirty_indexes)

    def update_items(self, delta: GridDeltaUpdate | None = None) -> None:
        if not self._items:
            return
        patch = delta or GridDeltaUpdate()
        changed_paths = tuple(path for path in patch.changed_paths if path)
        if not changed_paths:
            self.viewport().update()
            if patch.selection_anchor is not None and 0 <= patch.selection_anchor < len(self._items):
                self._selection_anchor = patch.selection_anchor
            self._schedule_visible_thumbnail_requests(immediate=True)
            return

        dirty_indexes = self._indexes_for_paths(changed_paths)
        for index in dirty_indexes:
            if not 0 <= index < len(self._items):
                continue
            record = self._items[index]
            for variant in record.display_variants:
                self._meta_cache[variant.path] = self._format_meta_line(record, variant)
                self._capture_cache[variant.path] = self._format_capture_line(self.metadata_manager.get_cached(variant))
                self._meta_with_ai_cache.pop(variant.path, None)
                self._failed_paths.discard(variant.path)
                self._display_aspect_ratio_by_path.pop(variant.path, None)
            self._ai_result_cache.pop(record.path, None)
            self._display_aspect_ratio_by_path.pop(record.path, None)

        if patch.selection_anchor is not None and 0 <= patch.selection_anchor < len(self._items):
            self._selection_anchor = patch.selection_anchor
        if not patch.preserve_pixmap_cache:
            self._clear_pixmap_cache(paths=changed_paths)

        if dirty_indexes:
            self._update_selection_tiles(dirty_indexes)
            self._schedule_visible_thumbnail_requests(immediate=True)

    def update_review_workflow_insights(
        self,
        insights_by_path: dict[str, object],
        changed_paths: list[str] | tuple[str, ...] | set[str],
    ) -> None:
        if not changed_paths:
            return
        dirty_indexes = self._indexes_for_paths(changed_paths)
        for path in changed_paths:
            if not path:
                continue
            normalized = _fast_path_key(path)
            insight = insights_by_path.get(path) or insights_by_path.get(normalized)
            if insight is None:
                self._workflow_insights_by_path.pop(path, None)
                self._workflow_insights_by_path.pop(normalized, None)
                continue
            self._workflow_insights_by_path[path] = insight
            self._workflow_insights_by_path[normalized] = insight
        for index in dirty_indexes:
            if not 0 <= index < len(self._items):
                continue
            for variant in self._items[index].display_variants:
                self._meta_with_ai_cache.pop(variant.path, None)
        if dirty_indexes:
            self._update_selection_tiles(dirty_indexes)

    def _indexes_for_paths(self, paths: list[str] | tuple[str, ...] | set[str]) -> set[int]:
        indexes: set[int] = set()
        for path in paths:
            if not path:
                continue
            index = self._path_to_index.get(path)
            if index is None:
                index = self._fast_path_to_index.get(_fast_path_key(path))
            if index is not None:
                indexes.add(index)
        return indexes

    def visible_item_paths(self, *, include_prefetch: bool = True, limit: int | None = None) -> list[str]:
        indexes = self._visible_indexes()
        if not indexes:
            return []
        selected: list[int]
        if include_prefetch:
            max_prefetch = max(1, self._columns * max(1, self._buffer_rows + 1))
            min_visible = min(indexes)
            max_visible = max(indexes)
            prefetch_indexes: set[int] = set(indexes)
            for offset in range(1, max_prefetch + 1):
                left = min_visible - offset
                right = max_visible + offset
                if 0 <= left < len(self._items):
                    prefetch_indexes.add(left)
                if 0 <= right < len(self._items):
                    prefetch_indexes.add(right)
            selected = sorted(prefetch_indexes)
        else:
            selected = list(indexes)

        paths = [self._items[index].path for index in selected if 0 <= index < len(self._items)]
        if limit is not None and limit >= 0:
            return paths[:limit]
        return paths

    def _prime_visible_text_caches(self, *, limit: int = 240) -> None:
        if not self._items:
            return
        for path in self.visible_item_paths(limit=limit):
            index = self._path_to_index.get(path)
            if index is None or not 0 <= index < len(self._items):
                continue
            record = self._items[index]
            variant = self._current_variant(record)
            if variant.path not in self._meta_cache:
                self._meta_cache[variant.path] = self._format_meta_line(record, variant)
            if variant.path not in self._capture_cache:
                self._capture_cache[variant.path] = self._format_capture_line(self.metadata_manager.get_cached(variant))
            if variant.path != record.path and record.path not in self._capture_cache:
                self._capture_cache[record.path] = self._format_capture_line(self.metadata_manager.get_cached(record))

    def set_burst_groups(
        self,
        burst_groups_by_path: dict[str, BurstVisualInfo],
        burst_groups: list[tuple[int, ...]] | None = None,
        *,
        request_thumbnails: bool = True,
    ) -> None:
        previous_display = dict(self._burst_display_member_by_anchor)
        self._burst_groups_by_path = dict(burst_groups_by_path)
        self._burst_groups = list(burst_groups or [])
        self._burst_group_members_by_index = {}
        self._burst_group_anchor_by_index = {}
        self._burst_group_members_by_anchor = {}
        self._burst_display_member_by_anchor = {}
        for group in self._burst_groups:
            members = tuple(index for index in group if 0 <= index < len(self._items))
            if not members:
                continue
            anchor = members[0]
            self._burst_group_members_by_anchor[anchor] = members
            display_member = previous_display.get(anchor, members[0])
            if display_member not in members:
                display_member = members[0]
            self._burst_display_member_by_anchor[anchor] = display_member
            for index in members:
                self._burst_group_members_by_index[index] = members
                self._burst_group_anchor_by_index[index] = anchor
        self._normalize_burst_stack_selection()
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        if request_thumbnails:
            self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update()

    def set_burst_stack_mode(self, enabled: bool, *, request_thumbnails: bool = True) -> None:
        normalized = bool(enabled)
        if self._burst_stack_mode == normalized:
            return
        self._burst_stack_mode = normalized
        self._normalize_burst_stack_selection()
        self._rebuild_visible_items()
        self._refresh_layout_after_visible_items_changed()
        if request_thumbnails:
            self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update()

    def schedule_visible_thumbnail_requests(self, *, immediate: bool = False) -> None:
        self._schedule_visible_thumbnail_requests(immediate=immediate)

    def set_ai_results(self, ai_results_by_path: dict[str, AIImageResult]) -> None:
        self._ai_results_by_path = dict(ai_results_by_path)
        self._ai_results_by_fast_path = {
            _fast_path_key(result.file_path): result
            for result in self._ai_results_by_path.values()
        }
        self._ai_result_cache.clear()
        self._normalized_path_cache.clear()
        self._meta_with_ai_cache.clear()
        self.viewport().update()

    def set_disputed_paths(self, paths: set[str] | frozenset[str] | None) -> None:
        """Push the set of disputed paths from the window. Drives the
        'Disputed' badge that paints in AI Review mode."""

        self._disputed_paths = {_fast_path_key(p) for p in (paths or set()) if p}
        self.viewport().update()

    def set_dino_prefilter_decisions(self, decisions_by_path: dict[str, object]) -> None:
        self._dino_prefilter_decisions_by_path = {
            _fast_path_key(path): decision
            for path, decision in decisions_by_path.items()
            if path
        }
        self.viewport().update()

    def set_review_insights(self, insights_by_path: dict[str, object]) -> None:
        self._review_insights_by_path = dict(insights_by_path)
        self._ai_result_cache.clear()
        self._meta_with_ai_cache.clear()
        self.viewport().update()

    def set_review_workflow_insights(self, insights_by_path: dict[str, object]) -> None:
        self._workflow_insights_by_path = dict(insights_by_path)
        self._meta_with_ai_cache.clear()
        self.viewport().update()

    def set_empty_message(self, message: str) -> None:
        self._empty_message = message.strip() or "Choose a folder to start triaging images."
        if not self._items:
            self.viewport().update()

    def set_column_count(self, columns: int) -> None:
        self._zoom_mode = "column"
        self._zoom_columns = max(1, min(8, columns))
        self._columns = self._zoom_columns
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def set_zoom_tile_width(self, width: int) -> None:
        """Continuous zoom: render tiles at a fixed width, reflowing columns.

        This is what makes the zoom slider feel smooth — the tile size tracks
        the slider 1:1 instead of snapping to whole-column steps.
        """
        self._zoom_mode = "tile"
        self._zoom_tile_width = max(self.MIN_TILE_WIDTH, min(self.MAX_TILE_WIDTH, int(width)))
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def current_columns(self) -> int:
        return self._columns

    def current_tile_width(self) -> int:
        return int(self._tile_width_value)

    def zoom_mode(self) -> str:
        return self._zoom_mode

    def set_compact_card_mode(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._compact_card_mode == normalized:
            return
        self._compact_card_mode = normalized
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def compact_card_mode(self) -> bool:
        return self._compact_card_mode

    def _use_loupe_card_style(self) -> bool:
        return not self._compact_card_mode and self._columns == 1

    def _use_new_grid_card(self) -> bool:
        """Multi-column tiles use the shared grid_card_renderer design.

        Non-"normal" action modes keep the legacy card because its buttons
        carry the undo affordances those filtered views rely on.
        """
        return (
            not self._compact_card_mode
            and self._columns > 1
            and self._action_mode == "normal"
        )

    def _use_compact_grid_card(self) -> bool:
        """Whether multi-column tiles use the barebones compact card (3:2
        photo, paired heart/reject buttons bottom-right, filename bottom-left,
        badge chips in the top corners).

        Detailed style keeps the full review card up to the column threshold
        and collapses past it; immersive style is always barebones."""
        if self._loupe_card_style == "detailed":
            return self._columns > COMPACT_COLUMN_THRESHOLD
        return True

    def _grid_card_badge_text(self) -> bool:
        """Badges show icon + text up to the column threshold; past it they
        collapse to icon-only chips (the renderer also falls back on its own
        whenever the text pills would not fit the card)."""
        return self._columns <= COMPACT_COLUMN_THRESHOLD

    def set_loupe_card_style(self, style: str) -> None:
        """Select the card style.

        "detailed": the full review card (filename, EXIF, meta, position,
        status) up to the column threshold; past it the card collapses to the
        barebones compact layout. In the single-column loupe the photo is
        fitted above the metadata strip so the overlay never covers it.
        "immersive": the photo fills the cell at every size — the grid always
        uses the barebones card, and the loupe paints its metadata over the
        photo's bottom edge on a lighter (65% alpha) scrim.
        """
        normalized = "immersive" if str(style).strip().casefold() == "immersive" else "detailed"
        if self._loupe_card_style == normalized:
            return
        self._loupe_card_style = normalized
        # The styles change tile metrics in the grid too (full cards at low
        # column counts in detailed mode), so always reflow.
        self._refresh_layout_after_visible_items_changed()
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update()

    def loupe_card_style(self) -> str:
        return self._loupe_card_style

    def set_free_smooth_scroll_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._free_smooth_scroll_enabled == normalized:
            return
        self._free_smooth_scroll_enabled = normalized
        self._wheel_angle_remainder = 0
        self._wheel_pixel_remainder = 0
        self._stop_smooth_scroll()

    def current_scroll_value(self) -> int:
        return self.verticalScrollBar().value()

    def restore_scroll_value(self, value: int) -> None:
        self._stop_smooth_scroll()
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(max(scrollbar.minimum(), min(scrollbar.maximum(), int(value))))

    def set_action_mode(self, mode: str) -> None:
        normalized = mode if mode in {"normal", "accepted_only", "rejected_only", "recycle_only"} else "normal"
        if self._action_mode == normalized:
            return
        self._action_mode = normalized
        self.viewport().update()

    def set_show_ai_annotations(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._show_ai_annotations == normalized:
            return
        self._show_ai_annotations = normalized
        self._meta_with_ai_cache.clear()
        self.viewport().update()

    def set_tool_checkbox_mode(
        self,
        enabled: bool,
        *,
        clear_selection: bool = False,
        toggle_on_image_click: bool = False,
    ) -> None:
        normalized = bool(enabled)
        changed = self._tool_checkbox_mode != normalized
        self._tool_checkbox_mode = normalized
        self._tool_tile_toggle_mode = normalized and bool(toggle_on_image_click)
        if clear_selection:
            self.clear_selection(keep_current=True)
        if changed:
            self._hovered_burst_left_index = -1
            self._hovered_burst_right_index = -1
            self._hovered_checkbox_index = -1
            self.viewport().unsetCursor()
            self.viewport().update()

    def tool_checkbox_mode(self) -> bool:
        return self._tool_checkbox_mode

    def clear_selection(self, *, keep_current: bool = True) -> None:
        previous_selection = set(self._selected_indexes)
        self._selected_indexes = set()
        if not keep_current:
            self._current_index = -1
            self._selection_anchor = -1
        self._update_selection_tiles(previous_selection)
        if previous_selection:
            self.selection_changed.emit()

    def current_index(self) -> int:
        return self._current_index

    def selected_indexes(self) -> list[int]:
        valid = [index for index in self._selected_indexes if 0 <= index < len(self._items)]
        if not self._burst_stack_mode:
            return sorted(valid)
        return sorted(valid, key=lambda index: self._visible_slot_by_item_index.get(index, index))

    def set_selected_indexes(self, indexes: list[int], *, current_index: int | None = None) -> None:
        valid = sorted({index for index in indexes if 0 <= index < len(self._items)})
        previous_selection = set(self._selected_indexes)
        if not valid:
            self._selected_indexes = set()
            if current_index is not None and 0 <= current_index < len(self._items):
                self._set_current_index(current_index)
                self._selection_anchor = current_index
            self._update_selection_tiles(previous_selection)
            if previous_selection:
                self.selection_changed.emit()
            return

        focus_index = current_index if current_index in valid else valid[0]
        self._selected_indexes = set(valid)
        self._selection_anchor = focus_index
        self._set_current_index(focus_index)
        self._update_selection_tiles(previous_selection | self._selected_indexes)
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def set_logical_selection(self, indexes: list[int], *, current_index: int | None = None) -> None:
        valid = sorted({index for index in indexes if 0 <= index < len(self._items)})
        if current_index is not None and 0 <= current_index < len(self._items):
            focus_index = current_index
        elif valid:
            focus_index = valid[0]
        else:
            focus_index = -1
        self._selected_indexes = set(valid)
        self._current_index = focus_index
        self._selection_anchor = focus_index

    def selected_count(self) -> int:
        return len(self.selected_indexes())

    @classmethod
    def dragged_record_paths_from_mime(cls, mime_data: QMimeData | None) -> list[str]:
        if mime_data is None or not mime_data.hasFormat(cls.INTERNAL_RECORD_MIME):
            return []
        payload = bytes(mime_data.data(cls.INTERNAL_RECORD_MIME)).decode("utf-8", errors="ignore")
        paths: list[str] = []
        seen: set[str] = set()
        for line in payload.splitlines():
            path = line.strip()
            if not path:
                continue
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    def set_current_index(self, index: int) -> None:
        if self._tool_checkbox_mode:
            self._set_current_index(index)
            self._selection_anchor = index
            return
        self._set_single_selection(index)

    def current_record(self) -> ImageRecord | None:
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index]
        return None

    def thumbnail_for(self, index: int) -> QImage | None:
        if not 0 <= index < len(self._items):
            return None
        if self._items[index].is_folder:
            return None
        target = self._thumbnail_target_size()
        return self.thumbnail_manager.get_cached(self._current_variant(self._items[index]), target)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        super().scrollContentsBy(dx, dy)
        self.viewport().update()
        self._schedule_visible_thumbnail_requests()
        if logger.enabled:
            logger.duration(
                "grid.scroll_contents",
                (time.perf_counter() - start) * 1000.0,
                dx=dx,
                dy=dy,
                value=self.verticalScrollBar().value(),
            )

    def wheelEvent(self, event: QWheelEvent) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._handle_zoom_wheel(event):
            event.accept()
            if logger.enabled:
                logger.duration("grid.wheel", (time.perf_counter() - start) * 1000.0, mode="zoom")
            return

        row_height = self._row_height()
        if not self._items or row_height <= 0:
            super().wheelEvent(event)
            if logger.enabled:
                logger.duration("grid.wheel", (time.perf_counter() - start) * 1000.0, mode="default")
            return

        if self._free_smooth_scroll_enabled:
            scroll_delta = self._wheel_scroll_delta_pixels(event, row_height)
            if scroll_delta:
                self._scroll_by_pixels(scroll_delta)
            event.accept()
            if logger.enabled:
                logger.duration("grid.wheel", (time.perf_counter() - start) * 1000.0, mode="free_smooth", delta=scroll_delta)
            return

        steps = self._wheel_row_steps(event, row_height)
        if steps == 0:
            event.accept()
            if logger.enabled:
                logger.duration("grid.wheel", (time.perf_counter() - start) * 1000.0, mode="aligned", steps=0)
            return

        self._scroll_by_aligned_rows(steps)
        event.accept()
        if logger.enabled:
            logger.duration("grid.wheel", (time.perf_counter() - start) * 1000.0, mode="aligned", steps=steps)

    def paintEvent(self, event: QPaintEvent) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), getattr(self, "_viewport_bg", None) or self.palette().color(QPalette.ColorRole.Base))

        if not self._items:
            self._paint_empty_state(painter)
            self._hide_adapter_label_controls()
            if logger.enabled:
                logger.duration("grid.paint", (time.perf_counter() - start) * 1000.0, visible=0, event_width=event.rect().width(), event_height=event.rect().height())
            return

        target_size = self._thumbnail_target_size()
        painted = 0
        pixmap_hits = 0
        for index in self._visible_indexes():
            rect = self._item_rect(index)
            if not rect.intersects(event.rect()):
                continue
            record = self._items[index]
            variant = self._current_variant(record)
            pixmap = None
            if not record.is_folder:
                key = self.thumbnail_manager.make_key(variant, target_size)
                pixmap = self._cached_pixmap_for_key(key)
                if pixmap is None:
                    image = self.thumbnail_manager.get_cached(variant, target_size)
                    pixmap = self._pixmap_for(key, image)
                else:
                    pixmap_hits += 1
            self._paint_tile(painter, index, rect, record, pixmap)
            painted += 1

        if self._marquee_active and not self._marquee_rect.isNull():
            overlay_fill = QColor(self._border_active)
            overlay_fill.setAlpha(46)
            overlay_border = QColor(self._border_active)
            overlay_border.setAlpha(185)
            painter.setPen(QPen(overlay_border, 1, Qt.PenStyle.DashLine))
            painter.setBrush(overlay_fill)
            painter.drawRect(self._marquee_rect)
        self._sync_adapter_label_controls()
        if logger.enabled:
            logger.duration(
                "grid.paint",
                (time.perf_counter() - start) * 1000.0,
                visible=painted,
                pixmap_hits=pixmap_hits,
                event_width=event.rect().width(),
                event_height=event.rect().height(),
                scroll_value=self.verticalScrollBar().value(),
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        self._reset_pointer_interaction(clear_marquee=False)
        point = event.position().toPoint()
        index = self._index_at(point.x(), point.y())
        self._press_pos = point
        self._press_index = index
        if index >= 0:
            rect = self._item_rect(index)
            if self._tool_checkbox_mode and self._checkbox_rect(rect).contains(point):
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._select_range(index)
                else:
                    self._toggle_selection(index)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if not self._tool_checkbox_mode and self._burst_left_arrow_rect(rect, index).contains(point):
                self._cycle_burst(index, -1)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if not self._tool_checkbox_mode and self._burst_right_arrow_rect(rect, index).contains(point):
                self._cycle_burst(index, 1)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if self._left_arrow_rect(rect, self._items[index]).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self._cycle_variant(index, -1)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if self._right_arrow_rect(rect, self._items[index]).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self._cycle_variant(index, 1)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if not self._items[index].is_folder and self._winner_button_rect(rect).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self.winner_requested.emit(index)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if not self._items[index].is_folder and self._reject_button_rect(rect).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self.reject_requested.emit(index)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if self._tool_checkbox_mode:
                if (
                    self._tool_tile_toggle_mode
                    and not self._items[index].is_folder
                    and self._image_rect(rect).contains(point)
                ):
                    modifiers = event.modifiers()
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self._select_range(index)
                    else:
                        self._toggle_selection(index)
                    self._press_on_interactive_control = True
                else:
                    self._set_current_index(index)
                    self._selection_anchor = index
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            modifiers = event.modifiers()
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                self._select_range(index)
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                self._toggle_selection(index)
            else:
                if index in self._selected_indexes and len(self._selected_indexes) > 1:
                    self._pending_single_selection_index = index
                    self._set_current_index(index)
                    self._selection_anchor = index
                else:
                    self._set_single_selection(index)
        else:
            modifiers = event.modifiers()
            additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            self._marquee_origin = point
            self._marquee_base_selection = set(self._selected_indexes) if additive else set()
            self._pending_clear_selection = not additive
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            index = self._index_at(point.x(), point.y())
            if index >= 0:
                rect = self._item_rect(index)
                record = self._items[index]
                if (
                    self._left_arrow_rect(rect, record).contains(point)
                    or self._right_arrow_rect(rect, record).contains(point)
                    or self._burst_left_arrow_rect(rect, index).contains(point)
                    or self._burst_right_arrow_rect(rect, index).contains(point)
                ):
                    event.accept()
                    return
                self._set_current_index(index)
                self.preview_requested.emit(index)
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        point = event.pos()
        index = self._index_at(point.x(), point.y())
        if index < 0:
            super().contextMenuEvent(event)
            return
        if self._zoom_index == index and self._zoom_factor > 1.0:
            self._reset_image_zoom()
            event.accept()
            return
        if self._tool_checkbox_mode:
            self._set_current_index(index)
        elif index in self._selected_indexes:
            self._set_current_index(index)
        else:
            self._set_single_selection(index)
        self.context_menu_requested.emit(index, event.globalPos())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._marquee_active:
                self._clear_marquee_selection()
                self._reset_pointer_interaction(clear_marquee=False)
                event.accept()
                return
            pending_single_selection_index = self._pending_single_selection_index
            pending_clear_selection = self._pending_clear_selection
            self._reset_pointer_interaction(clear_marquee=True)
            if pending_single_selection_index >= 0:
                self._set_single_selection(pending_single_selection_index)
                event.accept()
                return
            if pending_clear_selection:
                self.clear_selection(keep_current=True)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if self._marquee_origin is not None and (self._marquee_active or (point - self._marquee_origin).manhattanLength() >= QApplication.startDragDistance()):
                self._marquee_active = True
                self._set_marquee_rect(QRect(self._marquee_origin, point).normalized())
                self._apply_marquee_selection()
                event.accept()
                return
            if (
                not self._tool_checkbox_mode
                and not self._press_on_interactive_control
                and self._press_pos is not None
                and self._press_index >= 0
                and (point - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
            ):
                if self._start_internal_drag():
                    event.accept()
                    return

        point = event.position().toPoint()
        index = self._index_at(point.x(), point.y())
        hovered_winner = -1
        hovered_reject = -1
        hovered_left_arrow = -1
        hovered_right_arrow = -1
        hovered_burst_left = -1
        hovered_burst_right = -1
        hovered_checkbox = -1
        if index >= 0:
            rect = self._item_rect(index)
            if self._tool_checkbox_mode and self._checkbox_rect(rect).contains(point):
                hovered_checkbox = index
            if self._winner_button_rect(rect).contains(point):
                hovered_winner = index
            if self._reject_button_rect(rect).contains(point):
                hovered_reject = index
            if self._left_arrow_rect(rect, self._items[index]).contains(point):
                hovered_left_arrow = index
            if self._right_arrow_rect(rect, self._items[index]).contains(point):
                hovered_right_arrow = index
            if not self._tool_checkbox_mode and self._burst_left_arrow_rect(rect, index).contains(point):
                hovered_burst_left = index
            if not self._tool_checkbox_mode and self._burst_right_arrow_rect(rect, index).contains(point):
                hovered_burst_right = index

        if (
            hovered_winner != self._hovered_winner_index
            or hovered_reject != self._hovered_reject_index
            or hovered_left_arrow != self._hovered_left_arrow_index
            or hovered_right_arrow != self._hovered_right_arrow_index
            or hovered_burst_left != self._hovered_burst_left_index
            or hovered_burst_right != self._hovered_burst_right_index
            or hovered_checkbox != self._hovered_checkbox_index
        ):
            previous_winner = self._hovered_winner_index
            previous_reject = self._hovered_reject_index
            previous_left_arrow = self._hovered_left_arrow_index
            previous_right_arrow = self._hovered_right_arrow_index
            previous_burst_left = self._hovered_burst_left_index
            previous_burst_right = self._hovered_burst_right_index
            previous_checkbox = self._hovered_checkbox_index
            self._hovered_winner_index = hovered_winner
            self._hovered_reject_index = hovered_reject
            self._hovered_left_arrow_index = hovered_left_arrow
            self._hovered_right_arrow_index = hovered_right_arrow
            self._hovered_burst_left_index = hovered_burst_left
            self._hovered_burst_right_index = hovered_burst_right
            self._hovered_checkbox_index = hovered_checkbox
            pointer = (
                hovered_winner >= 0
                or hovered_reject >= 0
                or hovered_left_arrow >= 0
                or hovered_right_arrow >= 0
                or hovered_burst_left >= 0
                or hovered_burst_right >= 0
                or hovered_checkbox >= 0
            )
            self.viewport().setCursor(QCursor(Qt.CursorShape.PointingHandCursor) if pointer else QCursor(Qt.CursorShape.ArrowCursor))
            for tile_index in {
                previous_winner,
                previous_reject,
                previous_left_arrow,
                previous_right_arrow,
                previous_burst_left,
                previous_burst_right,
                previous_checkbox,
                hovered_winner,
                hovered_reject,
                hovered_left_arrow,
                hovered_right_arrow,
                hovered_burst_left,
                hovered_burst_right,
                hovered_checkbox,
            }:
                if tile_index >= 0:
                    self.viewport().update(self._item_rect(tile_index))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        previous_winner = self._hovered_winner_index
        previous_reject = self._hovered_reject_index
        previous_left_arrow = self._hovered_left_arrow_index
        previous_right_arrow = self._hovered_right_arrow_index
        previous_burst_left = self._hovered_burst_left_index
        previous_burst_right = self._hovered_burst_right_index
        previous_checkbox = self._hovered_checkbox_index
        self._hovered_winner_index = -1
        self._hovered_reject_index = -1
        self._hovered_left_arrow_index = -1
        self._hovered_right_arrow_index = -1
        self._hovered_burst_left_index = -1
        self._hovered_burst_right_index = -1
        self._hovered_checkbox_index = -1
        self.viewport().unsetCursor()
        for tile_index in {
            previous_winner,
            previous_reject,
            previous_left_arrow,
            previous_right_arrow,
            previous_burst_left,
            previous_burst_right,
            previous_checkbox,
        }:
            if tile_index >= 0:
                self.viewport().update(self._item_rect(tile_index))
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._items:
            super().keyPressEvent(event)
            return

        if not self._visible_item_indexes:
            super().keyPressEvent(event)
            return
        index = self._current_index if self._current_index >= 0 else self._visible_item_indexes[0]
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
        visible_rows = max(1, self.viewport().height() // max(1, self._row_height()))
        page_step = visible_rows * self._columns

        if key == Qt.Key.Key_A and modifiers & Qt.KeyboardModifier.ControlModifier:
            self._select_all()
            return
        if not self._tool_checkbox_mode and review_shortcut_allowed and key == Qt.Key.Key_BracketLeft and self._can_cycle_burst(index):
            self._cycle_burst(index, -1)
            return
        if not self._tool_checkbox_mode and review_shortcut_allowed and key == Qt.Key.Key_BracketRight and self._can_cycle_burst(index):
            self._cycle_burst(index, 1)
            return
        current_slot = self._current_visible_slot()
        if key == Qt.Key.Key_Left:
            next_slot = max(0, current_slot - 1)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_Right:
            next_slot = min(len(self._visible_item_indexes) - 1, current_slot + 1)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_Up:
            next_slot = max(0, current_slot - self._columns)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_Down:
            next_slot = min(len(self._visible_item_indexes) - 1, current_slot + self._columns)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_Home:
            next_index = self._visible_item_indexes[0]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_End:
            next_index = self._visible_item_indexes[-1]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_PageUp:
            next_slot = max(0, current_slot - page_step)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_PageDown:
            next_slot = min(len(self._visible_item_indexes) - 1, current_slot + page_step)
            next_index = self._visible_item_indexes[next_slot]
            if self._tool_checkbox_mode:
                self._set_current_index(next_index)
            else:
                self._set_single_selection(next_index)
            return
        if key == Qt.Key.Key_Space and review_shortcut_allowed and self._tool_checkbox_mode:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                self._select_range(index)
            else:
                self._toggle_selection(index)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and review_shortcut_allowed:
            self.preview_requested.emit(index)
            return
        if key == Qt.Key.Key_Space and review_shortcut_allowed:
            self.preview_requested.emit(index)
            return
        if key == Qt.Key.Key_Delete and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.delete_requested.emit(index)
            return
        if key == Qt.Key.Key_K and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.keep_requested.emit(index)
            return
        if key == Qt.Key.Key_M and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.move_requested.emit(index)
            return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_5 and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            label_map = {
                Qt.Key.Key_1: "hero",
                Qt.Key.Key_2: "strong",
                Qt.Key.Key_3: "maybe",
                Qt.Key.Key_4: "weak",
                Qt.Key.Key_5: "reject",
            }
            # Dispute chord: D was pressed within the last 2s and we're in AI
            # Review mode. Consume the 1-5 as a dispute label rather than a
            # regular rating.
            if (
                self._show_ai_annotations
                and Qt.Key.Key_1 <= key <= Qt.Key.Key_5
                and self._awaiting_dispute_until > 0.0
                and time.time() <= self._awaiting_dispute_until
            ):
                self._awaiting_dispute_until = 0.0
                self.dispute_label_requested.emit(self._items[index].path, label_map[key])
                return
            if self._adapter_review_mode and Qt.Key.Key_1 <= key <= Qt.Key.Key_5:
                if not self._adapter_review_label_controls_enabled:
                    return
                self._set_adapter_label_for_index(index, label_map[key], emit=True)
                return
            self.rate_requested.emit(index, key - Qt.Key.Key_0)
            return
        if key == Qt.Key.Key_D and self._show_ai_annotations and review_shortcut_allowed:
            # Start the dispute chord. The next 1-5 key within 2s will be
            # treated as a dispute label for the current card.
            if self._items[index].is_folder:
                return
            self._awaiting_dispute_until = time.time() + 2.0
            self.dispute_chord_started.emit()
            return
        if key == Qt.Key.Key_T and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.tag_requested.emit(index)
            return
        if key == Qt.Key.Key_W and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.winner_requested.emit(index)
            return
        if key == Qt.Key.Key_X and review_shortcut_allowed:
            if self._items[index].is_folder:
                return
            self.reject_requested.emit(index)
            return
        if key == Qt.Key.Key_Escape and self._zoom_factor > 1.0:
            self._reset_image_zoom()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Control and self._zoom_factor > 1.0:
            self._reset_image_zoom()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _handle_zoom_wheel(self, event: QWheelEvent) -> bool:
        if not self._items:
            return False
        point = event.position().toPoint()
        index = self._index_at(point.x(), point.y())
        if index < 0:
            return False
        rect = self._item_rect(index)
        image_rect = self._image_rect(rect)
        if not image_rect.contains(point):
            return False
        record = self._items[index]
        variant = self._current_variant(record)
        target = self._thumbnail_target_size()
        image = self.thumbnail_manager.get_cached(variant, target)
        if image is None or image.isNull():
            return False
        key = self.thumbnail_manager.make_key(variant, target)
        pixmap = self._pixmap_for(key, image)
        if pixmap is None or pixmap.isNull():
            return False
        draw_rect = self._image_draw_rect(image_rect, pixmap)
        if not draw_rect.contains(point):
            return False
        steps = self._zoom_wheel_steps(event)
        if steps == 0:
            return True
        previous_index = self._zoom_index
        changed = self._apply_image_zoom(index, draw_rect, pixmap, point, steps)
        if self._zoom_index == index and self._zoom_factor > 1.0:
            self.thumbnail_manager.request_thumbnail(
                variant,
                self._zoom_thumbnail_target_size(image_rect),
                priority=20_000,
                drop_if_not_wanted=False,
            )
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if changed:
            dirty_indexes = {index}
            if previous_index >= 0:
                dirty_indexes.add(previous_index)
            self._update_selection_tiles(dirty_indexes)
        return True

    def _zoom_wheel_steps(self, event: QWheelEvent) -> int:
        angle_y = event.angleDelta().y()
        if angle_y:
            if self._zoom_wheel_angle_remainder and (self._zoom_wheel_angle_remainder > 0) != (angle_y > 0):
                self._zoom_wheel_angle_remainder = 0
            self._zoom_wheel_angle_remainder += angle_y
            steps = int(abs(self._zoom_wheel_angle_remainder) // 120)
            if steps <= 0:
                return 0
            direction = 1 if self._zoom_wheel_angle_remainder > 0 else -1
            self._zoom_wheel_angle_remainder -= (120 * steps) if self._zoom_wheel_angle_remainder > 0 else (-120 * steps)
            self._zoom_wheel_pixel_remainder = 0
            return direction * steps

        pixel_y = event.pixelDelta().y()
        if not pixel_y:
            return 0
        if self._zoom_wheel_pixel_remainder and (self._zoom_wheel_pixel_remainder > 0) != (pixel_y > 0):
            self._zoom_wheel_pixel_remainder = 0
        self._zoom_wheel_pixel_remainder += pixel_y
        threshold = 48
        steps = int(abs(self._zoom_wheel_pixel_remainder) // threshold)
        if steps <= 0:
            return 0
        direction = 1 if self._zoom_wheel_pixel_remainder > 0 else -1
        self._zoom_wheel_pixel_remainder -= threshold * steps if self._zoom_wheel_pixel_remainder > 0 else -threshold * steps
        self._zoom_wheel_angle_remainder = 0
        return direction * steps

    def _apply_image_zoom(
        self,
        index: int,
        draw_rect: QRect,
        pixmap: QPixmap,
        point: QPoint,
        steps: int,
    ) -> bool:
        if steps == 0 or pixmap.isNull() or draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return False
        previous_state = (self._zoom_index, round(self._zoom_factor, 4), self._zoom_focus)
        base_factor = self._zoom_factor if index == self._zoom_index else 1.0
        new_factor = max(1.0, min(4.0, base_factor + (0.25 * steps)))
        if new_factor <= 1.0:
            self._reset_image_zoom()
            return previous_state != (self._zoom_index, round(self._zoom_factor, 4), self._zoom_focus)
        width = max(1, draw_rect.width() - 1)
        height = max(1, draw_rect.height() - 1)
        focus_x = max(0.0, min(1.0, (point.x() - draw_rect.left()) / width))
        focus_y = max(0.0, min(1.0, (point.y() - draw_rect.top()) / height))
        self._zoom_index = index
        self._zoom_factor = new_factor
        self._zoom_focus = (focus_x, focus_y)
        return previous_state != (self._zoom_index, round(self._zoom_factor, 4), self._zoom_focus)

    def _reset_image_zoom(self) -> None:
        previous_index = self._zoom_index
        had_zoom = previous_index >= 0 or self._zoom_factor != 1.0
        self._zoom_index = -1
        self._zoom_factor = 1.0
        self._zoom_focus = (0.5, 0.5)
        self._zoom_wheel_angle_remainder = 0
        self._zoom_wheel_pixel_remainder = 0
        if had_zoom and previous_index >= 0:
            self.viewport().update(self._item_rect(previous_index))

    def _handle_scroll_value_changed(self) -> None:
        self._schedule_visible_thumbnail_requests()

    def _handle_thumbnail_ready(self, key: ThumbnailKey, _image: QImage) -> None:
        self._failed_paths.discard(key.path)
        self._failed_messages.pop(key.path, None)
        if _image.width() > 0 and _image.height() > 0:
            self._display_aspect_ratio_by_path[key.path] = _image.width() / _image.height()
        target = self._thumbnail_target_size()
        if key.width != target.width() or key.height != target.height():
            return

        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return

        rect = self._item_rect(index)
        if (
            self._should_fit_single_visible_tile() and self._single_visible_item_matches_path(key.path)
        ) or (self._current_item_matches_path(key.path) and self._loupe_tile_height_stale()):
            self._refresh_layout_after_visible_items_changed()
            self._schedule_visible_thumbnail_requests(immediate=True)
            rect = self._item_rect(index)
        if rect.intersects(self.viewport().rect()):
            self._cache_pixmap(key, _image)
            self.viewport().update(rect)

    def _handle_thumbnail_failed(self, key: ThumbnailKey, _message: str) -> None:
        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return
        self._failed_paths.add(key.path)
        self._failed_messages[key.path] = _message
        rect = self._item_rect(index)
        if rect.intersects(self.viewport().rect()):
            self.viewport().update(rect)

    def _handle_metadata_ready(self, key: MetadataKey, metadata: CaptureMetadata) -> None:
        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return
        self._capture_cache[key.path] = self._format_capture_line(metadata)
        if metadata.width > 0 and metadata.height > 0:
            self._display_aspect_ratio_by_path[key.path] = metadata.width / metadata.height
        if (
            self._should_fit_single_visible_tile() and self._single_visible_item_matches_path(key.path)
        ) or (self._current_item_matches_path(key.path) and self._loupe_tile_height_stale()):
            self._refresh_layout_after_visible_items_changed()
            self._schedule_visible_thumbnail_requests(immediate=True)
        rect = self._item_rect(index)
        if rect.intersects(self.viewport().rect()):
            self.viewport().update(rect)

    def _paint_empty_state(self, painter: QPainter) -> None:
        painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
        painter.setFont(self._empty_font)
        painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self._empty_message)

    def _paint_tile(self, painter: QPainter, index: int, rect: QRect, record: ImageRecord, pixmap: QPixmap | None) -> None:
        is_current = index == self._current_index
        is_selected = index in self._selected_indexes
        annotation = self._annotations.get(record.path)
        is_winner = bool(annotation and annotation.winner)
        is_rejected = bool(annotation and annotation.reject)
        variant = self._current_variant(record)
        burst_info = self._burst_groups_by_path.get(record.path)
        ai_result = self._ai_result_for(record, variant)
        review_insight = self._review_insight_for(record)
        use_loupe_card = self._use_loupe_card_style()
        # Zoomed tiles fall back to the legacy painter, which owns the
        # zoom-crop drawing path.
        use_new_grid_card = (
            self._use_new_grid_card()
            and not record.is_folder
            and not self._adapter_review_mode
            and not (index == self._zoom_index and self._zoom_factor > 1.0)
        )
        painter.save()
        image_rect = self._image_rect(rect)
        photo_bottom: int | None = None
        loupe_photo_rect: QRect | None = None
        if use_new_grid_card:
            if burst_info is not None and self._burst_stack_mode:
                self._paint_burst_stack_layers(painter, image_rect, highlighted=is_current or is_selected)
            status_text = self._review_keeper_label(ai_result, self._workflow_insight_for(record))
            duplicate_text = self._review_group_badge_text(
                burst_info, self._dino_prefilter_decision_for(record)
            )
            ai_text = self._review_ai_badge_label(ai_result)
            card_data = GridCardData(
                tags=self._review_workflow_tags(record),
                filename=variant.name if record.has_variant_stack else record.name,
                exif_text=self._review_capture_text(record),
                meta_text=self._review_passive_meta_text(record, variant),
                duplicate_text=duplicate_text,
                ai_text=ai_text,
                position_text=self._review_position_text(index),
                status_text=status_text,
                status_kind=status_text.casefold(),
                duplicate_visible=bool(duplicate_text),
                ai_visible=bool(ai_text),
                selected=is_current or is_selected,
                favorite=is_winner,
                rejected=is_rejected,
                hover_favorite=index == self._hovered_winner_index,
                hover_reject=index == self._hovered_reject_index,
                immersive=self._loupe_card_style == "immersive",
            )
            paint_grid_card(
                painter,
                rect,
                pixmap if pixmap is not None and not pixmap.isNull() else None,
                card_data,
                compact=self._use_compact_grid_card(),
                compact_actions="right",
                compact_filename=True,
                compact_badge_text=self._grid_card_badge_text(),
            )
            if variant.path in self._failed_paths:
                painter.setPen(self._failed_text_color)
                painter.setFont(self._placeholder_font)
                failed_message = self._failed_messages.get(variant.path, "").strip()
                failure_text = f"Failed\n{failed_message}" if failed_message else "Failed"
                painter.drawText(
                    image_rect.adjusted(12, 12, -12, -12),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    failure_text,
                )
        else:
            if not use_loupe_card:
                # Boxed cards paint a frame; the loupe photo sits directly on
                # the viewport.
                if is_rejected:
                    border_color = self._reject_color
                    background_color = QColor("#171113")
                elif is_winner:
                    border_color = self._accepted_color
                    background_color = QColor("#111c16")
                else:
                    if is_current:
                        border_color = self._border_active
                        background_color = self._background_active
                    elif is_selected:
                        border_color = self._border_selected
                        background_color = self._background_selected
                    else:
                        border_color = self._border_idle
                        background_color = self._background_idle
                painter.setPen(QPen(border_color, 1.4 if is_current or is_selected else 1.0))
                painter.setBrush(background_color)
                painter.drawRoundedRect(QRectF(rect), 7, 7)

            if burst_info is not None:
                if self._burst_stack_mode:
                    self._paint_burst_stack_layers(painter, image_rect, highlighted=is_current or is_selected)
            if not (use_loupe_card and pixmap is not None and not pixmap.isNull()):
                # Frameless loupe photos sit directly on the viewport, so the
                # placeholder backdrop only paints while there is no photo yet
                # (otherwise it shows as a gray pane in the dead area around
                # the photo).
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._placeholder_color)
                painter.drawRoundedRect(QRectF(image_rect), 5, 5)

            corner_radius = IMAGE_CORNER_RADIUS if use_loupe_card else 5.0
            if pixmap is not None and not pixmap.isNull():
                draw_rect = self._image_draw_rect(image_rect, pixmap)
                photo_bottom = draw_rect.bottom()
                loupe_photo_rect = QRect(draw_rect)
                if use_loupe_card:
                    # Learn the aspect ratio from the pixmap itself so the
                    # height-fitted tile settles even when no metadata or
                    # thumbnail-ready event supplied the dimensions.
                    self._display_aspect_ratio_by_path.setdefault(
                        variant.path, pixmap.width() / max(1, pixmap.height())
                    )
                zoom_pixmap = self._zoom_pixmap_for_tile(index, record, variant, image_rect)
                clip_path = QPainterPath()
                clip_path.addRoundedRect(QRectF(draw_rect if use_loupe_card else image_rect), corner_radius, corner_radius)
                painter.save()
                painter.setClipPath(clip_path)
                if index == self._zoom_index and self._zoom_factor > 1.0:
                    zoom_source = zoom_pixmap if zoom_pixmap is not None and not zoom_pixmap.isNull() else pixmap
                    source_rect = self._zoom_source_rect(zoom_source, self._zoom_factor, self._zoom_focus)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    painter.drawPixmap(draw_rect, zoom_source, source_rect)
                else:
                    painter.drawPixmap(draw_rect, pixmap)
                painter.restore()
            elif record.is_folder:
                self._paint_folder_thumbnail(painter, image_rect)
            elif variant.path in self._failed_paths:
                painter.setPen(self._failed_text_color)
                painter.setFont(self._placeholder_font)
                failed_message = self._failed_messages.get(variant.path, "").strip()
                failure_text = "Failed"
                if failed_message:
                    failure_text = f"Failed\n{failed_message}"
                painter.drawText(
                    image_rect.adjusted(12, 12, -12, -12),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    failure_text,
                )
            else:
                painter.setPen(self._placeholder_text_color)
                painter.setFont(self._placeholder_font)
                painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "Loading...")

            if use_loupe_card:
                # Selection ring on the photo itself (there is no card frame).
                # Current uses the accent blue with a thin dark contrast line
                # inside it so the ring stays visible over bright photo edges;
                # other selected tiles get the lighter blue; idle keeps the
                # subtle hairline.
                ring_rect = QRectF(loupe_photo_rect if loupe_photo_rect is not None else image_rect)
                painter.save()
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if is_current or is_selected:
                    accent = QColor(self._border_active if is_current else self._border_selected)
                    painter.setPen(QPen(QColor(0, 0, 0, 140), 1.0))
                    painter.drawRoundedRect(
                        ring_rect.adjusted(2.0, 2.0, -2.0, -2.0), IMAGE_CORNER_RADIUS - 1, IMAGE_CORNER_RADIUS - 1
                    )
                    painter.setPen(QPen(accent, 2.0 if is_current else 1.4))
                    painter.drawRoundedRect(
                        ring_rect.adjusted(0.5, 0.5, -0.5, -0.5), IMAGE_CORNER_RADIUS, IMAGE_CORNER_RADIUS
                    )
                else:
                    painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
                    painter.drawRoundedRect(
                        ring_rect.adjusted(0.5, 0.5, -0.5, -0.5), IMAGE_CORNER_RADIUS, IMAGE_CORNER_RADIUS
                    )
                painter.restore()

        # Rating/meta footer strip under compact cards. The normal review card
        # now paints metadata over a bottom image scrim instead.
        footer_top = image_rect.bottom() + 1
        if not use_loupe_card and not use_new_grid_card and footer_top < rect.bottom() - 1:
            footer_clip = QPainterPath()
            footer_clip.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 6, 6)
            painter.save()
            painter.setClipPath(footer_clip)
            painter.fillRect(
                QRectF(rect.left() + 1, footer_top, rect.width() - 2, rect.bottom() - footer_top - 1),
                getattr(self, "_footer_bg", self._background_idle),
            )
            painter.restore()

        if not use_loupe_card and not use_new_grid_card and annotation and (annotation.rating or annotation.tags):
            badge = self._annotation_badge(annotation)
            badge_rect = QRect(image_rect.right() - 160, image_rect.bottom() - 30, 150, 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._badge_background)
            painter.drawRoundedRect(QRectF(badge_rect), 8, 8)
            painter.setPen(self._badge_text_color)
            painter.setFont(self._meta_font)
            painter.drawText(badge_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, badge)

        workflow_insight = self._workflow_insight_for(record)
        dino_decision = self._dino_prefilter_decision_for(record)

        if use_loupe_card:
            self._paint_review_top_badges(
                painter,
                image_rect,
                burst_info=burst_info,
                ai_result=ai_result,
                dino_decision=dino_decision,
            )
        elif not use_new_grid_card:
            # Legacy card only: the shared renderer draws these as its own
            # tag rail (GridCardData.tags), uniform with the card design.
            left_badge_x = image_rect.left() + 10 + (32 if self._tool_checkbox_mode else 0)
            left_badge_y = image_rect.top() + 10
            if is_rejected:
                self._paint_state_badge(
                    painter,
                    QRect(left_badge_x, left_badge_y, 88, 24),
                    "Rejected",
                    self._reject_badge_fill,
                    self._reject_badge_text,
                )
                left_badge_y += 30
            elif is_winner:
                self._paint_state_badge(
                    painter,
                    QRect(left_badge_x, left_badge_y, 88, 24),
                    "Accepted",
                    self._accepted_badge_fill,
                    self._accepted_badge_text,
                )
                left_badge_y += 30

            if workflow_insight is not None and getattr(workflow_insight, "has_round", False):
                short_label = review_round_short_label(getattr(workflow_insight, "review_round", ""))
                if short_label:
                    self._paint_state_badge(
                        painter,
                        QRect(left_badge_x, left_badge_y, 88, 24),
                        short_label,
                        self._workflow_round_badge_fill,
                        self._workflow_round_badge_text,
                    )
                    left_badge_y += 30
            if self._show_ai_annotations and workflow_insight is not None and getattr(workflow_insight, "best_in_group", False):
                self._paint_state_badge(
                    painter,
                    QRect(left_badge_x, left_badge_y, 94, 24),
                    "Best Frame",
                    self._workflow_best_badge_fill,
                    self._workflow_best_badge_text,
                )
                left_badge_y += 30
            if self._show_ai_annotations and _fast_path_key(record.path) in self._disputed_paths:
                self._paint_state_badge(
                    painter,
                    QRect(left_badge_x, left_badge_y, 88, 24),
                    "Disputed",
                    QColor(140, 78, 16, 220),
                    QColor("#fff1d6"),
                )
                left_badge_y += 30
            if self._show_ai_annotations and dino_decision is not None:
                dino_badge = self._dino_prefilter_badge(dino_decision)
                if dino_badge is not None:
                    badge_text, fill, text, badge_width = dino_badge
                    self._paint_state_badge(
                        painter,
                        QRect(left_badge_x, left_badge_y, badge_width, 24),
                        badge_text,
                        fill,
                        text,
                    )
                    left_badge_y += 30
            if self._show_ai_annotations and workflow_insight is not None and getattr(workflow_insight, "disagreement_badge", ""):
                fill, text = self._workflow_disagreement_palette(getattr(workflow_insight, "disagreement_level", ""))
                self._paint_state_badge(
                    painter,
                    QRect(left_badge_x, left_badge_y, 88, 24),
                    getattr(workflow_insight, "disagreement_badge", ""),
                    fill,
                    text,
                )
                left_badge_y += 30

        if self._tool_checkbox_mode:
            self._paint_tool_checkbox(
                painter,
                self._checkbox_rect(rect),
                checked=is_selected,
                hovered=index == self._hovered_checkbox_index,
            )

        badge_y = image_rect.top() + 10
        if not use_loupe_card and not use_new_grid_card and record.has_edits:
            self._paint_state_badge(
                painter,
                QRect(image_rect.right() - 94, badge_y, 84, 24),
                "Edited",
                self._edited_badge_fill,
                self._edited_badge_text,
            )
            badge_y += 30

        ai_badge = self._primary_ai_badge(ai_result)
        if not use_loupe_card and not use_new_grid_card and ai_badge is not None:
            badge_text, fill, text, badge_width = ai_badge
            self._paint_state_badge(
                painter,
                QRect(image_rect.right() - (badge_width + 10), badge_y, badge_width, 24),
                badge_text,
                fill,
                text,
            )

        if not use_loupe_card and not use_new_grid_card and self._show_ai_annotations and ai_result is not None:
            score_badge_rect = QRect(image_rect.right() - 92, image_rect.bottom() - 30, 82, 24)
            self._paint_state_badge(
                painter,
                score_badge_rect,
                f"AI {ai_result.display_score_text}",
                self._ai_score_badge_fill,
                self._ai_score_badge_text,
            )

        if use_loupe_card:
            self._paint_review_overlay(
                painter,
                index,
                rect,
                record,
                variant,
                annotation,
                ai_result,
                workflow_insight,
                photo_bottom=photo_bottom,
            )
        elif not use_new_grid_card:
            title_rect = self._title_rect(rect)
            capture_rect = self._capture_rect(rect)
            meta_rect = self._meta_rect(rect)
            title_text_rect = QRect(title_rect)
            title_badge_width = 0
            if burst_info is not None:
                title_badge_width = 116
                badge_fill, badge_text = self._group_badge_palette(burst_info.kind)
                self._paint_state_badge(
                    painter,
                    QRect(title_rect.right() - title_badge_width, title_rect.top(), title_badge_width, 22),
                    f"{burst_info.label} {burst_info.index_in_group}/{burst_info.group_size}",
                    badge_fill,
                    badge_text,
                )
                title_text_rect.setRight(title_text_rect.right() - (title_badge_width + 8))
            if record.has_variant_stack:
                title_text_rect.setRight(title_text_rect.right() - 56)
                self._paint_state_badge(
                    painter,
                    QRect(title_rect.right() - 48, title_rect.top(), 48, 22),
                    f"{self._variant_index(record) + 1}/{record.stack_count}",
                    QColor(18, 27, 40, 220),
                    QColor("#dce5f2"),
                )
            if self._adapter_review_mode and self._record_in_adapter_review(index):
                adapter_rect = self._adapter_label_rect(rect)
                # Only clip the filename when the combo is actually in the title row
                # (right-aligned mode). When it falls back to the action row below,
                # the title row is unaffected.
                if adapter_rect.top() <= title_rect.bottom():
                    title_text_rect.setRight(min(title_text_rect.right(), adapter_rect.left() - 8))

            painter.setPen(self._title_color)
            painter.setFont(self._title_font)
            title_option = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            title_option.setWrapMode(QTextOption.WrapMode.WordWrap)
            painter.drawText(QRectF(title_text_rect), variant.name if record.has_variant_stack else record.name, title_option)

            if not record.is_folder and not self._adapter_review_mode:
                self._paint_winner_button(
                    painter,
                    self._winner_button_rect(rect),
                    annotation.winner if annotation else False,
                    index == self._hovered_winner_index,
                )
                self._paint_reject_button(
                    painter,
                    self._reject_button_rect(rect),
                    annotation.reject if annotation else False,
                    index == self._hovered_reject_index,
                )

            if not self._compact_card_mode:
                painter.setPen(self._capture_color)
                painter.setFont(self._meta_font)
                capture_text = painter.fontMetrics().elidedText(self._capture_line(record), Qt.TextElideMode.ElideRight, capture_rect.width())
                painter.drawText(capture_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, capture_text)

                painter.setPen(self._meta_color)
                painter.setFont(self._meta_font)
                meta_text = painter.fontMetrics().elidedText(self._meta_line(record), Qt.TextElideMode.ElideRight, meta_rect.width())
                painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta_text)
        if record.has_variant_stack:
            self._paint_variant_arrow(
                painter,
                self._left_arrow_rect(rect, record),
                self.LEFT_ARROW_SYMBOL,
                index == self._hovered_left_arrow_index,
            )
            self._paint_variant_arrow(
                painter,
                self._right_arrow_rect(rect, record),
                self.RIGHT_ARROW_SYMBOL,
                index == self._hovered_right_arrow_index,
            )
        if self._burst_stack_mode and burst_info is not None:
            self._paint_burst_nav_bubble(
                painter,
                self._burst_left_arrow_rect(rect, index),
                self.LEFT_ARROW_SYMBOL,
                index == self._hovered_burst_left_index,
            )
            self._paint_burst_nav_bubble(
                painter,
                self._burst_right_arrow_rect(rect, index),
                self.RIGHT_ARROW_SYMBOL,
                index == self._hovered_burst_right_index,
            )
        painter.restore()

    def _annotation_badge(self, annotation: SessionAnnotation) -> str:
        parts: list[str] = []
        if annotation.rating:
            rating = max(0, min(5, int(annotation.rating)))
            parts.append("\u2605" * rating + "\u2606" * (5 - rating))
        if annotation.tags:
            parts.append(", ".join(annotation.tags[:2]))
        return " | ".join(parts)

    def _meta_line(self, record: ImageRecord) -> str:
        variant = self._current_variant(record)
        cached = self._meta_with_ai_cache.get(variant.path)
        if cached is not None:
            return cached
        rendered = self._format_meta_with_ai(record, variant)
        self._meta_with_ai_cache[variant.path] = rendered
        return rendered

    def _capture_line(self, record: ImageRecord) -> str:
        variant = self._current_variant(record)
        capture = self._capture_cache.get(variant.path)
        if capture is None:
            capture = self._format_capture_line(self.metadata_manager.get_cached(variant))
            self._capture_cache[variant.path] = capture
        if capture or variant.path == record.path:
            return capture
        base_capture = self._capture_cache.get(record.path)
        if base_capture is None:
            base_capture = self._format_capture_line(self.metadata_manager.get_cached(record))
            self._capture_cache[record.path] = base_capture
        return base_capture

    def _format_capture_line(self, metadata: CaptureMetadata | None) -> str:
        if metadata is None:
            return ""
        return metadata.summary

    def _format_meta_line(self, record: ImageRecord, variant: ImageVariant | None = None) -> str:
        if record.is_folder:
            if record.modified_ns > 0:
                return "Folder  |  " + datetime.fromtimestamp(record.modified_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")
            return "Folder"
        if record.has_variant_stack:
            item = variant or self._current_variant(record)
            size_bytes = item.size
            modified_ns = item.modified_ns
        else:
            size_bytes = record.size
            modified_ns = record.modified_ns
        parts: list[str] = []
        if record.bundle_label:
            parts.append(record.bundle_label)
        if size_bytes > 0:
            parts.append(f"{size_bytes / (1024 * 1024):.1f} MB")
        if modified_ns > 0:
            parts.append(datetime.fromtimestamp(modified_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M"))
        return "  |  ".join(parts)

    def _format_meta_with_ai(self, record: ImageRecord, variant: ImageVariant | None = None) -> str:
        item = variant or self._current_variant(record)
        base = self._meta_cache.get(item.path, self._format_meta_line(record, item))
        ai_result = self._ai_result_for(record, item)
        review_insight = self._review_insight_for(record)
        workflow_insight = self._workflow_insight_for(record)
        parts = [base]
        if review_insight is not None and getattr(review_insight, "has_group", False):
            parts.append(getattr(review_insight, "summary_text", ""))
        workflow_summary = self._visible_workflow_summary(workflow_insight)
        if workflow_summary:
            parts.append(workflow_summary)
        if self._show_ai_annotations and ai_result is not None:
            ai_parts = [f"AI {ai_result.display_score_text}", ai_result.confidence_bucket_label]
            if ai_result.group_id:
                ai_parts.append(ai_result.group_id)
            if ai_result.group_size > 1:
                ai_parts.append(ai_result.rank_text)
            parts.extend(part for part in ai_parts if part)
        return "  |  ".join(part for part in parts if part)

    def _review_insight_for(self, record: ImageRecord):
        return self._review_insights_by_path.get(record.path) or self._review_insights_by_path.get(_fast_path_key(record.path))

    def _workflow_insight_for(self, record: ImageRecord):
        return self._workflow_insights_by_path.get(record.path) or self._workflow_insights_by_path.get(_fast_path_key(record.path))

    def _dino_prefilter_decision_for(self, record: ImageRecord):
        for candidate in record.stack_paths:
            decision = self._dino_prefilter_decisions_by_path.get(_fast_path_key(candidate))
            if decision is not None:
                return decision
        return None

    def _visible_workflow_summary(self, workflow_insight) -> str:
        if workflow_insight is None:
            return ""
        summary = str(getattr(workflow_insight, "summary_text", "") or "").strip()
        if not summary:
            return ""
        if self._show_ai_annotations:
            return summary
        parts = [part.strip() for part in summary.split("|")]
        visible_parts = [part for part in parts if part and part not in {"AI Disagreement", "Best Frame"}]
        return " | ".join(visible_parts)

    def _group_badge_palette(self, kind: str) -> tuple[QColor, QColor]:
        if kind == "exact_duplicate":
            return QColor(122, 46, 53, 220), QColor("#ffe9ec")
        if kind == "likely_duplicate":
            return QColor(117, 82, 18, 220), QColor("#fff4d6")
        if kind == "burst":
            return QColor(35, 104, 109, 220), QColor("#e8ffff")
        return self._burst_badge_fill, self._burst_badge_text

    def _confidence_badge_palette(self, short_label: str) -> tuple[QColor, QColor]:
        if short_label == "Winner":
            return QColor(34, 96, 64, 220), QColor("#ebfff2")
        if short_label == "Keeper":
            return QColor(28, 82, 120, 220), QColor("#e8f4ff")
        if short_label in {"Needs Review", "Review"}:
            return self._workflow_review_badge_fill, self._workflow_review_badge_text
        return QColor(118, 54, 48, 220), QColor("#fff0ee")

    def _primary_ai_badge(self, ai_result: AIImageResult | None) -> tuple[str, QColor, QColor, int] | None:
        if not self._show_ai_annotations:
            return None
        if ai_result is None:
            return None
        if ai_result.is_top_pick:
            return ("AI Pick", self._ai_pick_badge_fill, self._ai_pick_badge_text, 94)
        if ai_result.confidence_bucket == AIConfidenceBucket.NEEDS_REVIEW:
            fill, text = self._confidence_badge_palette("Needs Review")
            return ("Needs Review", fill, text, 116)
        label = ai_result.confidence_bucket_short_label
        fill, text = self._confidence_badge_palette(label)
        return (label, fill, text, 106)

    def _dino_prefilter_badge(self, decision) -> tuple[str, QColor, QColor, int] | None:
        action = str(getattr(decision, "action", "") or "")
        reason = str(getattr(decision, "reason", "") or "")
        prefix = "pHash" if reason == "phash_duplicate_trash" else "DINO"
        if action == "quarantine":
            label = "pHash Duplicate" if prefix == "pHash" else "DINO Quarantine"
            return (label, self._workflow_review_badge_fill, self._workflow_review_badge_text, 126)
        if action == "remove_from_pool":
            return (f"{prefix} Removed", self._workflow_miss_badge_fill, self._workflow_miss_badge_text, 112)
        if action == "rescued":
            return (f"{prefix} Rescued", self._workflow_best_badge_fill, self._workflow_best_badge_text, 112)
        return None

    def _workflow_disagreement_palette(self, level: str) -> tuple[QColor, QColor]:
        if level == "strong":
            return self._workflow_miss_badge_fill, self._workflow_miss_badge_text
        return self._workflow_review_badge_fill, self._workflow_review_badge_text

    def _paint_review_top_badges(
        self,
        painter: QPainter,
        image_rect: QRect,
        *,
        burst_info: BurstVisualInfo | None,
        ai_result: AIImageResult | None,
        dino_decision,
    ) -> None:
        scale = self._review_scale(image_rect)
        margin = self._review_overlay_margin(image_rect)
        badge_y = image_rect.top() + margin
        left_text = self._review_group_badge_text(burst_info, dino_decision)
        if left_text:
            left_rect = self._review_badge_rect(
                painter, left_text, image_rect.left() + margin, badge_y, scale, icon="duplicate"
            )
            self._paint_review_pill(
                painter,
                left_rect,
                left_text,
                self._review_duplicate_badge_fill,
                self._review_duplicate_badge_text,
                border=self._review_badge_border,
                scale=scale,
                icon="duplicate",
            )

        right_text = self._review_ai_badge_label(ai_result)
        if right_text:
            right_rect = self._review_badge_rect(painter, right_text, 0, badge_y, scale, icon="spark")
            right_rect.moveRight(image_rect.right() - margin)
            self._paint_review_pill(
                painter,
                right_rect,
                right_text,
                self._review_ai_badge_fill,
                self._review_ai_badge_color,
                border=self._review_badge_border,
                scale=scale,
                icon="spark",
            )

    def _paint_review_overlay(
        self,
        painter: QPainter,
        index: int,
        rect: QRect,
        record: ImageRecord,
        variant: ImageVariant,
        annotation: SessionAnnotation | None,
        ai_result: AIImageResult | None,
        workflow_insight,
        photo_bottom: int | None = None,
    ) -> None:
        image_rect = self._image_rect(rect)
        scale = self._review_scale(image_rect)
        margin = self._review_overlay_margin(image_rect)
        button_size = self._review_action_button_size(image_rect)
        button_gap = self._review_action_button_gap(image_rect)
        title_height, capture_height, meta_height, line_gap = self._review_text_metrics(image_rect)

        # Anchor the overlay to the photo's bottom edge in immersive mode (the
        # photo-fit strip anchors to the tile bottom by design). This keeps
        # the controls on the photo even when the tile is taller than the
        # frame — e.g. before the aspect ratio is learned.
        photo_edge = image_rect.bottom() if photo_bottom is None else min(photo_bottom, image_rect.bottom())
        anchor_base = photo_edge if self._loupe_card_style == "immersive" else image_rect.bottom()
        # Text rows anchor to the classic button position (button centered on
        # the meta row); the buttons themselves may drop below that anchor so
        # they never clip the status text (see _review_button_top).
        anchor_center_y = anchor_base - self._review_overlay_bottom_margin(image_rect) - button_size // 2
        meta_y = anchor_center_y - meta_height // 2
        capture_y = meta_y - capture_height - line_gap
        title_y = capture_y - title_height - line_gap

        reject_rect = QRect(0, 0, button_size, button_size)
        reject_rect.moveRight(image_rect.right() - margin)
        reject_rect.moveTop(self._review_button_top(image_rect, photo_bottom=anchor_base))
        winner_rect = QRect(0, 0, button_size, button_size)
        winner_rect.moveRight(reject_rect.left() - button_gap)
        winner_rect.moveTop(reject_rect.top())

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(image_rect), IMAGE_CORNER_RADIUS, IMAGE_CORNER_RADIUS)

        painter.save()
        painter.setClipPath(clip)
        # Scrim anchored to the pane bottom. Its job is to hide the dead space
        # between the photo's bottom edge and the pane, so it stays fully
        # opaque up to the photo's bottom edge (or the top of the text block,
        # whichever is higher) and feathers out shortly above that. The
        # immersive style lightens every stop to 65% so the photo reads
        # through the metadata strip.
        if self._loupe_card_style == "immersive":
            text_top = title_y - line_gap
            overlap = max(6, int(round(8 * scale)))
            solid_top = min(text_top, photo_edge - overlap)
            solid_h = max(1, anchor_base - solid_top + 1)
            feather_h = max(20, int(round(30 * scale)))
            total = max(1, min(solid_h + feather_h, image_rect.height()))
            scrim_top = anchor_base - total + 1
            feather_frac = feather_h / float(solid_h + feather_h)
            scrim_rect = QRect(
                image_rect.left(),
                scrim_top,
                image_rect.width(),
                anchor_base - scrim_top + 1,
            )

            def scrim_stop(alpha: int) -> QColor:
                color = QColor(self._review_scrim_color)
                # The fade stays translucent (65%) so the photo reads through,
                # but the solid band behind the text ramps to 80% so the
                # labels keep a legibility floor (matches the shared grid
                # card renderer).
                color.setAlpha(round(alpha * (0.65 + 0.15 * (alpha / 255.0))))
                return color

            gradient = QLinearGradient(scrim_rect.topLeft(), scrim_rect.bottomLeft())
            gradient.setColorAt(0.0, scrim_stop(0))
            gradient.setColorAt(feather_frac * 0.50, scrim_stop(40))
            gradient.setColorAt(feather_frac * 0.80, scrim_stop(125))
            gradient.setColorAt(feather_frac, scrim_stop(235))
            gradient.setColorAt(min(1.0, feather_frac + (1.0 - feather_frac) * 0.12), scrim_stop(255))
            gradient.setColorAt(1.0, scrim_stop(255))
            painter.fillRect(scrim_rect, QBrush(gradient))
        elif photo_edge < image_rect.bottom():
            # Photo-fit: nothing draws over the image — the metadata strip
            # below the photo gets a solid backdrop instead of a feathered
            # scrim.
            painter.fillRect(
                QRect(
                    image_rect.left(),
                    photo_edge + 1,
                    image_rect.width(),
                    image_rect.bottom() - photo_edge,
                ),
                self._review_scrim_color,
            )
        painter.restore()
        right_width = max(96, int(round(64 * scale)))
        left_width = max(80, winner_rect.left() - margin - image_rect.left() - margin - int(round(18 * scale)))

        title_rect = QRect(image_rect.left() + margin, title_y, left_width, title_height)
        capture_rect = QRect(title_rect.left(), capture_y, left_width, capture_height)
        meta_rect = QRect(title_rect.left(), meta_y, left_width, meta_height)
        right_rect = QRect(image_rect.right() - margin - right_width, title_y, right_width, capture_height + title_height + line_gap)

        painter.save()
        painter.setPen(QColor("#f4f7fb"))
        painter.setFont(self._review_font(14, scale, QFont.Weight.DemiBold))
        title_text = painter.fontMetrics().elidedText(
            variant.name if record.has_variant_stack else record.name,
            Qt.TextElideMode.ElideRight,
            title_rect.width(),
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)

        painter.setPen(QColor("#d9e5f2"))
        painter.setFont(self._review_font(10, scale, QFont.Weight.DemiBold))
        capture_text = self._review_capture_text(record)
        capture_text = painter.fontMetrics().elidedText(capture_text, Qt.TextElideMode.ElideRight, capture_rect.width())
        painter.drawText(capture_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, capture_text)

        painter.setPen(self._meta_color)
        painter.setFont(self._review_font(9, scale))
        meta_text = self._review_passive_meta_text(record, variant)
        meta_text = painter.fontMetrics().elidedText(meta_text, Qt.TextElideMode.ElideRight, meta_rect.width())
        painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta_text)

        # Image count: same row as the filename (top), right-aligned.
        painter.setPen(self._review_index_text)
        painter.setFont(self._review_font(10, scale, QFont.Weight.Bold))
        painter.drawText(
            QRect(right_rect.left(), title_rect.top(), right_rect.width(), title_height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self._review_position_text(index),
        )

        keeper_text = self._review_keeper_label(ai_result, workflow_insight)
        if keeper_text:
            # Status just under the count row, right-aligned. The rect is sized
            # to the real font height plus TextDontClip so descenders never get
            # cut off, and the dropped buttons below leave it clear air.
            keeper_y = title_rect.bottom() + max(2, int(round(2 * scale)))
            painter.setPen(self._review_keeper_color)
            painter.setFont(self._review_font(10, scale, QFont.Weight.DemiBold))
            keeper_height = max(capture_height, painter.fontMetrics().height())
            painter.drawText(
                QRect(right_rect.left(), keeper_y, right_rect.width(), keeper_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextDontClip,
                keeper_text,
            )
        painter.restore()

        if not record.is_folder and not self._adapter_review_mode:
            self._paint_review_action_button(
                painter,
                winner_rect,
                "heart",
                active=bool(annotation and annotation.winner),
                hovered=index == self._hovered_winner_index,
                accent=self._winner_color,
            )
            self._paint_review_action_button(
                painter,
                reject_rect,
                "reject",
                active=bool(annotation and annotation.reject),
                hovered=index == self._hovered_reject_index,
                accent=self._reject_color,
            )

    def _paint_review_action_button(
        self,
        painter: QPainter,
        rect: QRect,
        icon: str,
        *,
        active: bool,
        hovered: bool,
        accent: QColor,
    ) -> None:
        if rect.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(0, 0, 0, 128 if hovered or active else 88)
        border = QColor(accent) if active else QColor(255, 255, 255)
        border.setAlpha(190 if active else (105 if hovered else 72))
        # Muted idle white so the buttons sit quietly on the scrim; hover
        # brightens back up and active takes the accent.
        color = QColor(accent) if active else (QColor("#e9eef3") if hovered else QColor("#c3ccd6"))
        painter.setPen(QPen(border, 1.1))
        painter.setBrush(fill)
        painter.drawEllipse(rect)

        image = load_action_icon(icon, (color.red(), color.green(), color.blue()))
        if image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # Draw only the central glyph of the artwork; the asset's own
            # thick ring stays outside this crop and the thin ellipse above
            # replaces it.
            crop_frac = 0.62
            source_inset_x = image.width() * (1 - crop_frac) / 2
            source_inset_y = image.height() * (1 - crop_frac) / 2
            source = QRectF(image.rect()).adjusted(
                source_inset_x, source_inset_y, -source_inset_x, -source_inset_y
            )
            target_inset_x = rect.width() * (1 - crop_frac) / 2
            target_inset_y = rect.height() * (1 - crop_frac) / 2
            target = QRectF(rect).adjusted(target_inset_x, target_inset_y, -target_inset_x, -target_inset_y)
            painter.drawImage(target, image, source)
        elif icon == "heart":
            # Fallback: vector icons when the PNG assets are missing.
            _paint_heart_icon(painter, rect, color, filled=active)
        else:
            _paint_reject_icon(painter, rect, color)
        painter.restore()

    @staticmethod
    def _review_badge_icon_metrics(scale: float) -> tuple[int, int]:
        icon_width = max(10, int(round(13 * scale)))
        icon_gap = max(6, int(round(7 * scale)))
        return icon_width, icon_gap

    def _paint_review_pill(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        fill: QColor,
        foreground: QColor,
        *,
        border: QColor | None = None,
        scale: float = 1.0,
        icon: str = "",
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(border, 1.0) if border is not None else Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        radius = min(rect.height() / 2, max(6.0, 6.0 * scale))
        painter.drawRoundedRect(QRectF(rect), radius, radius)

        inset = max(10, int(round(10 * scale)))
        text_rect = rect.adjusted(inset, 0, -inset, 0)
        if icon:
            icon_width, icon_gap = self._review_badge_icon_metrics(scale)
            icon_rect = QRect(
                rect.left() + inset,
                rect.top() + (rect.height() - icon_width) // 2,
                icon_width,
                icon_width,
            )
            if icon == "duplicate":
                _paint_duplicate_icon(painter, icon_rect, foreground, scale)
            elif icon == "spark":
                _paint_spark_icon(painter, icon_rect, foreground)
            text_rect = rect.adjusted(inset + icon_width + icon_gap, 0, -inset, 0)

        painter.setPen(foreground)
        painter.setFont(self._review_font(9, scale, QFont.Weight.DemiBold))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def _review_badge_rect(
        self, painter: QPainter, text: str, x: int, y: int, scale: float = 1.0, *, icon: str = ""
    ) -> QRect:
        painter.save()
        painter.setFont(self._review_font(9, scale, QFont.Weight.DemiBold))
        width = max(int(round(82 * scale)), painter.fontMetrics().horizontalAdvance(text) + int(round(22 * scale)))
        painter.restore()
        if icon:
            icon_width, icon_gap = self._review_badge_icon_metrics(scale)
            width += icon_width + icon_gap
        return QRect(x, y, width, max(26, int(round(26 * scale))))

    def _review_group_badge_text(self, burst_info: BurstVisualInfo | None, dino_decision) -> str:
        if burst_info is not None and burst_info.group_size > 1:
            label = str(burst_info.label or "Group")
            if burst_info.kind in {"exact_duplicate", "likely_duplicate"} or "dup" in label.casefold():
                label = "Near Duplicate"
            elif burst_info.kind == "burst":
                label = "Burst"
            elif burst_info.kind == "similar":
                label = "Similar"
            return f"{label} \u00b7 {burst_info.index_in_group}/{burst_info.group_size}"
        if dino_decision is not None:
            action = str(getattr(dino_decision, "action", "") or "")
            reason = str(getattr(dino_decision, "reason", "") or "")
            if action == "quarantine" and reason == "phash_duplicate_trash":
                return "Near Duplicate"
        return ""

    def _review_ai_badge_label(self, ai_result: AIImageResult | None) -> str:
        if not self._show_ai_annotations or ai_result is None:
            return ""
        score = ai_result.display_score_text
        if ai_result.is_top_pick:
            return f"AI Pick \u00b7 {score}" if score else "AI Pick"
        return f"AI {score}" if score else ai_result.confidence_bucket_short_label

    def _review_keeper_label(self, ai_result: AIImageResult | None, workflow_insight) -> str:
        if ai_result is not None:
            if ai_result.is_top_pick:
                return "Keeper"
            label = ai_result.confidence_bucket_short_label
            return "Review" if label in {"Needs Review", "Review"} else label
        if workflow_insight is not None and getattr(workflow_insight, "best_in_group", False):
            return "Keeper"
        return ""

    def _review_workflow_tags(self, record: ImageRecord) -> tuple[tuple[str, str], ...]:
        """AI workflow tags for the shared card's tag rail.

        Mirrors the conditions the legacy card uses for its stacked state
        badges; the renderer draws them as accent pills (or dots past the
        column threshold) so they stay uniform with the card design.
        Accepted/Rejected are omitted — the card's heart/reject buttons
        already carry that state.
        """
        tags: list[tuple[str, str]] = []
        workflow_insight = self._workflow_insight_for(record)
        if workflow_insight is not None and getattr(workflow_insight, "has_round", False):
            short_label = review_round_short_label(getattr(workflow_insight, "review_round", ""))
            if short_label:
                tags.append((short_label, "round"))
        if self._show_ai_annotations:
            if workflow_insight is not None and getattr(workflow_insight, "best_in_group", False):
                tags.append(("Best Frame", "best_frame"))
            if _fast_path_key(record.path) in self._disputed_paths:
                tags.append(("Disputed", "disputed"))
            dino_tag = self._dino_prefilter_tag(self._dino_prefilter_decision_for(record))
            if dino_tag is not None:
                tags.append(dino_tag)
            if workflow_insight is not None and getattr(workflow_insight, "disagreement_badge", ""):
                level = getattr(workflow_insight, "disagreement_level", "")
                tags.append(
                    (
                        getattr(workflow_insight, "disagreement_badge", ""),
                        "ai_miss" if level == "strong" else "needs_review",
                    )
                )
        if record.has_edits:
            tags.append(("Edited", "edited"))
        return tuple(tags)

    def _dino_prefilter_tag(self, decision) -> tuple[str, str] | None:
        """(text, tag kind) form of _dino_prefilter_badge for the card rail."""
        if decision is None:
            return None
        action = str(getattr(decision, "action", "") or "")
        reason = str(getattr(decision, "reason", "") or "")
        prefix = "pHash" if reason == "phash_duplicate_trash" else "DINO"
        if action == "quarantine":
            return ("pHash Duplicate" if prefix == "pHash" else "DINO Quarantine", "needs_review")
        if action == "remove_from_pool":
            return (f"{prefix} Removed", "ai_miss")
        if action == "rescued":
            return (f"{prefix} Rescued", "best_frame")
        return None

    def _review_position_text(self, index: int) -> str:
        slot = self._visible_slot_for_index(index)
        total = len(self._visible_item_indexes)
        if slot is None or total <= 0:
            return ""
        return f"{slot + 1} / {total}"

    def _review_capture_text(self, record: ImageRecord) -> str:
        return self._capture_line(record).replace("  |  ", " · ").replace(" | ", " · ")

    def _review_passive_meta_text(self, record: ImageRecord, variant: ImageVariant) -> str:
        parts: list[str] = []
        size_bytes = variant.size if record.has_variant_stack else record.size
        modified_ns = variant.modified_ns if record.has_variant_stack else record.modified_ns
        if size_bytes > 0:
            parts.append(f"{size_bytes / (1024 * 1024):.1f} MB")
        if modified_ns > 0:
            parts.append(datetime.fromtimestamp(modified_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M"))
        folder = Path(variant.path if record.has_variant_stack else record.path).parent.name
        if folder:
            parts.append(folder)
        return " · ".join(parts)

    def _ai_result_for(self, record: ImageRecord, variant: ImageVariant | None = None) -> AIImageResult | None:
        if not self._ai_results_by_path:
            return None
        item = variant or self._current_variant(record)
        cached = self._ai_result_cache.get(item.path, _AI_RESULT_MISSING)
        if cached is not _AI_RESULT_MISSING:
            return cached

        for candidate in (item.path, *record.stack_paths):
            result = self._ai_results_by_fast_path.get(_fast_path_key(candidate))
            if result is None:
                # The canonical path normalization is comparatively expensive, so we only do it on cache misses.
                normalized = self._normalized_path_cache.get(candidate)
                if normalized is None:
                    normalized = normalized_path_key(candidate)
                    self._normalized_path_cache[candidate] = normalized
                result = self._ai_results_by_path.get(normalized)
            if result is not None:
                refined = refine_ai_result_with_review_insight(result, self._review_insight_for(record))
                self._ai_result_cache[item.path] = refined
                return refined
        self._ai_result_cache[item.path] = None
        return None

    def _paint_variant_arrow(self, painter: QPainter, rect: QRect, symbol: str, hovered: bool) -> None:
        if rect.isEmpty():
            return
        painter.save()
        border = QColor(255, 255, 255, 95 if hovered else 55)
        background = QColor(18, 27, 40, 210 if hovered else 170)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(rect), 10, 10)
        painter.setPen(QColor("#f2f5f8"))
        painter.setFont(self._winner_button_font)
        painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
        painter.restore()

    def _paint_burst_stack_layers(self, painter: QPainter, image_rect: QRect, *, highlighted: bool) -> None:
        layer_specs = (
            (16, 14, 48 if highlighted else 34),
            (8, 7, 78 if highlighted else 58),
        )
        for x_offset, y_offset, alpha in layer_specs:
            shadow_rect = QRect(image_rect)
            shadow_rect.adjust(0, 0, -x_offset, -y_offset)
            shadow_rect.translate(x_offset, y_offset)
            if shadow_rect.width() < 24 or shadow_rect.height() < 24:
                continue
            fill = QColor(self._background_idle if not highlighted else self._background_selected)
            fill.setAlpha(alpha)
            border = QColor(self._border_idle if not highlighted else self._border_selected)
            border.setAlpha(min(150, alpha + 40))
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(shadow_rect), 10, 10)

    def _paint_burst_nav_bubble(self, painter: QPainter, rect: QRect, symbol: str, hovered: bool) -> None:
        if rect.isEmpty():
            return
        painter.save()
        border = QColor(self._burst_accent)
        border.setAlpha(215 if hovered else 155)
        background = QColor(12, 18, 28, 225 if hovered else 165)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(background)
        painter.drawEllipse(rect)
        painter.setPen(QColor("#f5f9ff"))
        painter.setFont(self._winner_button_font)
        painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
        painter.restore()

    def _paint_winner_button(self, painter: QPainter, rect: QRect, active: bool, hovered: bool) -> None:
        painter.save()
        undo_mode = self._action_mode in {"accepted_only", "recycle_only"}
        background = self._winner_button_fill if active or undo_mode else QColor(255, 255, 255, 0)
        border = self._accepted_color if undo_mode else (self._winner_color if active else (self._winner_button_hover if hovered else self._winner_button_border))
        symbol = self.UNDO_SYMBOL if undo_mode else (self.HEART_SYMBOL if active else self.HEART_OUTLINE_SYMBOL)
        text_color = self._accepted_color if undo_mode else (self._winner_color if active else QColor("#f2f5f8"))

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(rect), 8, 8)
        painter.setPen(text_color)
        painter.setFont(self._winner_button_font)
        painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
        painter.restore()

    def _paint_reject_button(self, painter: QPainter, rect: QRect, active: bool, hovered: bool) -> None:
        painter.save()
        undo_mode = self._action_mode == "rejected_only"
        background = self._reject_button_fill if active or undo_mode else QColor(255, 255, 255, 0)
        border = self._reject_color if active or undo_mode else (self._reject_button_hover if hovered else self._reject_button_border)
        text_color = self._reject_color if active or undo_mode else QColor("#f2f5f8")
        symbol = self.UNDO_SYMBOL if undo_mode else self.REJECT_SYMBOL

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(rect), 8, 8)
        painter.setPen(text_color)
        painter.setFont(self._winner_button_font)
        painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, symbol)
        painter.restore()

    def _paint_state_badge(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        background: QColor,
        foreground: QColor,
    ) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(rect), 8, 8)
        painter.setPen(foreground)
        painter.setFont(self._meta_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_folder_thumbnail(self, painter: QPainter, image_rect: QRect) -> None:
        painter.save()
        bounds = image_rect.adjusted(
            max(12, image_rect.width() // 12),
            max(12, image_rect.height() // 8),
            -max(12, image_rect.width() // 12),
            -max(12, image_rect.height() // 8),
        )
        if bounds.width() <= 0 or bounds.height() <= 0:
            painter.restore()
            return

        icon_size = max(24, int(min(bounds.width() * 0.84, bounds.height() * 0.854)))
        icon = folder_icon_pixmap(icon_size)
        target = QRect(0, 0, icon_size, icon_size)
        target.moveCenter(bounds.center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target, icon, icon.rect())
        painter.restore()

    def _paint_tool_checkbox(self, painter: QPainter, rect: QRect, *, checked: bool, hovered: bool) -> None:
        painter.save()
        border = self._checkbox_selected_fill if checked else (self._checkbox_selected_fill if hovered else self._checkbox_border)
        fill = self._checkbox_selected_fill if checked else self._checkbox_fill
        painter.setPen(QPen(border, 1.3))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(rect), 6, 6)
        if checked:
            painter.setPen(QPen(self._checkbox_check, 2.0))
            left = rect.left() + 5
            mid_x = rect.left() + 9
            right = rect.right() - 5
            top = rect.top() + 11
            mid_y = rect.bottom() - 7
            bottom = rect.top() + 6
            painter.drawLine(left, top, mid_x, mid_y)
            painter.drawLine(mid_x, mid_y, right, bottom)
        painter.restore()

    def _title_rect(self, tile_rect: QRect) -> QRect:
        if self._use_loupe_card_style():
            image_rect = self._image_rect(tile_rect)
            margin = self._review_overlay_margin(image_rect)
            overlay_height = self._review_overlay_height(image_rect)
            right_reserved = max(96, min(140, image_rect.width() // 4))
            return QRect(
                image_rect.left() + margin,
                image_rect.bottom() - overlay_height + max(16, margin),
                max(40, image_rect.width() - (margin * 2) - right_reserved),
                24,
            )
        return QRect(
            tile_rect.x() + 12,
            tile_rect.y() + self._image_padding + self._image_height() + 8,
            tile_rect.width() - 24,
            self._caption_height,
        )

    def _image_rect(self, tile_rect: QRect) -> QRect:
        if self._use_loupe_card_style():
            # The loupe photo sits directly on the viewport — no parent card
            # box around it, so the image owns the whole tile.
            return QRect(tile_rect)
        if self._use_new_grid_card():
            if self._use_compact_grid_card():
                # Barebones compact card: the 3:2 photo owns the whole tile.
                return QRect(tile_rect)
            # Detailed card: the full-width 3:2 photo pane at the top.
            return QRect(tile_rect.x(), tile_rect.y(), tile_rect.width(), self._image_height())
        return QRect(
            tile_rect.x() + self._image_padding,
            tile_rect.y() + self._image_padding,
            tile_rect.width() - (self._image_padding * 2),
            self._image_height(),
        )

    def _image_draw_rect(self, image_rect: QRect, pixmap: QPixmap) -> QRect:
        draw_size = pixmap.size()
        if self._columns == 1 or pixmap.height() > pixmap.width():
            fit_rect = image_rect
            force_scale = False
            if self._use_loupe_card_style() and self._loupe_card_style == "detailed":
                # Detailed loupe: the photo pane is full-width 3:2 above the
                # metadata strip. Let the tile grow vertically when needed
                # instead of height-limiting the photo and narrowing it.
                photo_height = int(round(image_rect.width() * 2 / 3))
                if photo_height >= 96:
                    fit_rect = QRect(image_rect.x(), image_rect.y(), image_rect.width(), photo_height)
                    force_scale = True
            if force_scale or draw_size.width() > fit_rect.width() or draw_size.height() > fit_rect.height():
                draw_size.scale(fit_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
            draw_rect = QRect(QPoint(0, 0), draw_size)
            if self._columns == 1:
                draw_rect.moveTop(image_rect.top())
                draw_rect.moveLeft(image_rect.left() + max(0, (image_rect.width() - draw_rect.width()) // 2))
            else:
                draw_rect.moveCenter(image_rect.center())
        else:
            draw_size.scale(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            draw_rect = QRect(QPoint(0, 0), draw_size)
            draw_rect.moveCenter(image_rect.center())
        return draw_rect

    def _zoom_source_rect(self, pixmap: QPixmap, factor: float, focus: tuple[float, float]) -> QRect:
        if factor <= 1.0 or pixmap.isNull():
            return pixmap.rect()
        crop_width = max(1, min(pixmap.width(), int(round(pixmap.width() / factor))))
        crop_height = max(1, min(pixmap.height(), int(round(pixmap.height() / factor))))
        focus_x = max(0.0, min(1.0, float(focus[0])))
        focus_y = max(0.0, min(1.0, float(focus[1])))
        center_x = int(round((pixmap.width() - 1) * focus_x))
        center_y = int(round((pixmap.height() - 1) * focus_y))
        left = max(0, min(pixmap.width() - crop_width, center_x - crop_width // 2))
        top = max(0, min(pixmap.height() - crop_height, center_y - crop_height // 2))
        return QRect(left, top, crop_width, crop_height)

    def _zoom_pixmap_for_tile(
        self,
        index: int,
        record: ImageRecord,
        variant: ImageVariant,
        image_rect: QRect,
    ) -> QPixmap | None:
        if index != self._zoom_index or self._zoom_factor <= 1.0:
            return None
        zoom_target = self._zoom_thumbnail_target_size(image_rect)
        if not zoom_target.isValid():
            return None
        key = self.thumbnail_manager.make_key(variant, zoom_target)
        image = self.thumbnail_manager.get_cached(variant, zoom_target)
        if image is None:
            self.thumbnail_manager.request_thumbnail(variant, zoom_target, priority=20_000)
            return None
        return self._pixmap_for(key, image)

    def _zoom_thumbnail_target_size(self, image_rect: QRect) -> QSize:
        if image_rect.width() <= 0 or image_rect.height() <= 0:
            return QSize()
        scale = max(2.0, min(4.0, self._zoom_factor))
        width = min(2048, max(image_rect.width(), int(round(image_rect.width() * scale))))
        height = min(2048, max(image_rect.height(), int(round(image_rect.height() * scale))))
        return QSize(width, height)

    def _capture_rect(self, tile_rect: QRect) -> QRect:
        if self._compact_card_mode:
            return QRect()
        if not self._use_loupe_card_style():
            title_rect = self._title_rect(tile_rect)
            return QRect(
                tile_rect.x() + 12,
                title_rect.bottom() + 2,
                tile_rect.width() - 24,
                self._capture_height,
            )
        title_rect = self._title_rect(tile_rect)
        image_rect = self._image_rect(tile_rect)
        margin = self._review_overlay_margin(image_rect)
        right_reserved = max(96, min(140, image_rect.width() // 4))
        return QRect(
            image_rect.left() + margin,
            title_rect.bottom() + 1,
            max(40, image_rect.width() - (margin * 2) - right_reserved),
            20,
        )

    def _meta_rect(self, tile_rect: QRect) -> QRect:
        if self._compact_card_mode:
            return QRect()
        if not self._use_loupe_card_style():
            capture_rect = self._capture_rect(tile_rect)
            return QRect(
                tile_rect.x() + 12,
                capture_rect.bottom() + 2,
                tile_rect.width() - 24,
                self._meta_height,
            )
        action_rect = self._action_rect(tile_rect)
        winner_rect = self._winner_button_rect(tile_rect)
        left = action_rect.left()
        right = winner_rect.left() - 12 if not winner_rect.isEmpty() else action_rect.right()
        return QRect(
            left,
            action_rect.top(),
            max(0, right - left),
            action_rect.height(),
        )

    def _adapter_label_rect(self, tile_rect: QRect) -> QRect:
        title_rect = self._title_rect(tile_rect)
        title_height = max(20, title_rect.height())
        # Burst/dup badges are forced off in adapter review mode (window-side
        # pHash dedup takes their place), so the right side of the title row is
        # free. Plant the combo in the badge slot: right-aligned, fixed width,
        # with a filename-min slot reserved on the left. If the tile is so
        # narrow that even a compact combo would crowd the filename, fall back
        # to the full-width action row below.
        combo_width = 120
        filename_min = 60
        if title_rect.width() - combo_width >= filename_min:
            x = title_rect.right() - combo_width
            y = title_rect.y() + max(0, (title_rect.height() - title_height) // 2)
            return QRect(x, y, combo_width, title_height)
        action_rect = self._action_rect(tile_rect)
        return QRect(
            action_rect.x(),
            action_rect.y(),
            action_rect.width(),
            max(22, action_rect.height()),
        )

    def _action_rect(self, tile_rect: QRect) -> QRect:
        if self._compact_card_mode:
            title_rect = self._title_rect(tile_rect)
            return QRect(
                tile_rect.x() + 12,
                title_rect.bottom() + 5,
                tile_rect.width() - 24,
                self._action_height,
            )
        if not self._use_loupe_card_style():
            meta_rect = self._meta_rect(tile_rect)
            return QRect(
                tile_rect.x() + 12,
                meta_rect.bottom() + 6,
                tile_rect.width() - 24,
                self._action_height,
            )
        image_rect = self._image_rect(tile_rect)
        margin = self._review_overlay_margin(image_rect)
        button_size = self._review_action_button_size(image_rect)
        return QRect(
            image_rect.left() + margin,
            self._review_button_top(image_rect),
            image_rect.width() - (margin * 2),
            button_size,
        )

    def _winner_button_rect(self, tile_rect: QRect) -> QRect:
        if self._action_mode in {"rejected_only", "recycle_only"}:
            return QRect()
        if self._use_new_grid_card():
            return grid_card_action_rects(
                tile_rect, compact=self._use_compact_grid_card(), compact_actions="right"
            ).favorite
        action_rect = self._action_rect(tile_rect)
        if self._use_loupe_card_style():
            image_rect = self._image_rect(tile_rect)
            size = self._review_action_button_size(image_rect)
            gap = self._review_action_button_gap(image_rect)
            rect = QRect(0, 0, size, size)
            rect.moveRight(action_rect.right() - size - gap)
        else:
            rect = QRect(0, 0, self._winner_button_size.width(), self._winner_button_size.height())
            rect.moveLeft(action_rect.left())
        rect.moveTop(action_rect.top() + max(0, (action_rect.height() - rect.height()) // 2))
        return rect

    def _reject_button_rect(self, tile_rect: QRect) -> QRect:
        if self._action_mode in {"accepted_only", "recycle_only"}:
            return QRect()
        if self._use_new_grid_card():
            return grid_card_action_rects(
                tile_rect, compact=self._use_compact_grid_card(), compact_actions="right"
            ).reject
        action_rect = self._action_rect(tile_rect)
        if self._use_loupe_card_style():
            image_rect = self._image_rect(tile_rect)
            size = self._review_action_button_size(image_rect)
            rect = QRect(0, 0, size, size)
        else:
            rect = QRect(0, 0, self._winner_button_size.width(), self._winner_button_size.height())
        if self._action_mode == "rejected_only":
            rect.moveLeft(action_rect.left())
        else:
            rect.moveRight(action_rect.right())
        rect.moveTop(action_rect.top() + max(0, (action_rect.height() - rect.height()) // 2))
        return rect

    def _review_overlay_margin(self, image_rect: QRect) -> int:
        return max(18, min(56, int(round(image_rect.width() * 0.035))))

    def _review_overlay_bottom_margin(self, image_rect: QRect) -> int:
        """Vertical inset of the overlay's bottom anchor. Strictly proportional
        (no upper clamp) so the strip under the metadata reads the same at any
        window size — the side margin's 56px cap made it balloon relative to
        the card in smaller windows."""
        return max(12, int(round(image_rect.width() * 0.022)))

    def _review_overlay_height(self, image_rect: QRect) -> int:
        return max(170, min(360, int(round(image_rect.height() * 0.34))))

    def _review_scale(self, image_rect: QRect) -> float:
        return max(1.0, min(2.6, image_rect.width() / 580.0))

    def _review_font(
        self,
        point_size: int,
        scale: float,
        weight: QFont.Weight = QFont.Weight.Normal,
        *,
        family: str = "Segoe UI",
    ) -> QFont:
        return QFont(family, max(1, int(round(point_size * scale))), weight)

    def _review_action_button_size(self, image_rect: QRect) -> int:
        return max(46, min(80, image_rect.width() // 19))

    def _review_action_button_gap(self, image_rect: QRect) -> int:
        return max(12, min(24, image_rect.width() // 64))

    def _review_text_metrics(self, image_rect: QRect) -> tuple[int, int, int, int]:
        """(title_height, capture_height, meta_height, line_gap) for the overlay."""
        scale = self._review_scale(image_rect)
        title_height = max(24, int(round(17 * scale)))
        capture_height = max(20, int(round(13 * scale)))
        meta_height = max(18, int(round(11 * scale)))
        line_gap = max(5, int(round(7 * scale)))
        return title_height, capture_height, meta_height, line_gap

    def _loupe_photo_bottom(self, image_rect: QRect) -> int:
        """Bottom edge of the current photo inside the loupe tile. The
        immersive overlay anchors here rather than at the tile bottom, so the
        controls stay on the photo even when the tile is taller than the
        frame (aspect ratio not learned yet, portrait clamp, etc.)."""
        if self._loupe_card_style != "immersive":
            return image_rect.bottom()
        aspect = self._current_loupe_aspect_ratio()
        if not aspect or aspect <= 0:
            return image_rect.bottom()
        photo_height = int(round(image_rect.width() / aspect))
        if photo_height >= image_rect.height():
            return image_rect.bottom()
        return image_rect.top() + photo_height - 1

    def _review_button_top(self, image_rect: QRect, photo_bottom: int | None = None) -> int:
        """Top edge of the action buttons: anchored so the buttons center on
        the meta row, then dropped just far enough that they clear the status
        text on the capture row (keeps Keeper/Review from clipping)."""
        scale = self._review_scale(image_rect)
        bottom_margin = self._review_overlay_bottom_margin(image_rect)
        button_size = self._review_action_button_size(image_rect)
        _title_height, capture_height, meta_height, line_gap = self._review_text_metrics(image_rect)
        if photo_bottom is None:
            photo_bottom = self._loupe_photo_bottom(image_rect)
        anchor_base = min(photo_bottom, image_rect.bottom())
        anchor_center_y = anchor_base - bottom_margin - button_size // 2
        meta_y = anchor_center_y - meta_height // 2
        capture_y = meta_y - capture_height - line_gap
        button_top = anchor_center_y - button_size // 2
        min_button_top = capture_y + capture_height + max(4, int(round(4 * scale)))
        return max(button_top, min_button_top)

    def _review_text_block_height(self, image_rect: QRect) -> int:
        """Footer height from the title row down to the pane bottom; the
        photo-fit style reserves this strip below the photo."""
        bottom_margin = self._review_overlay_bottom_margin(image_rect)
        button_size = self._review_action_button_size(image_rect)
        title_height, capture_height, meta_height, line_gap = self._review_text_metrics(image_rect)
        return (
            bottom_margin
            + button_size // 2
            + meta_height // 2
            + line_gap
            + capture_height
            + line_gap
            + title_height
            + line_gap
        )

    def _left_arrow_rect(self, tile_rect: QRect, record: ImageRecord) -> QRect:
        if not record.has_variant_stack:
            return QRect()
        image_rect = self._image_rect(tile_rect)
        rect = QRect(0, 0, 30, 42)
        rect.moveLeft(image_rect.left() + 10)
        rect.moveTop(image_rect.center().y() - rect.height() // 2)
        return rect

    def _checkbox_rect(self, tile_rect: QRect) -> QRect:
        image_rect = self._image_rect(tile_rect)
        rect = QRect(0, 0, self._checkbox_size.width(), self._checkbox_size.height())
        rect.moveLeft(image_rect.left() + 10)
        rect.moveTop(image_rect.top() + 10)
        return rect

    def _right_arrow_rect(self, tile_rect: QRect, record: ImageRecord) -> QRect:
        if not record.has_variant_stack:
            return QRect()
        image_rect = self._image_rect(tile_rect)
        rect = QRect(0, 0, 30, 42)
        rect.moveRight(image_rect.right() - 10)
        rect.moveTop(image_rect.center().y() - rect.height() // 2)
        return rect

    def _burst_left_arrow_rect(self, tile_rect: QRect, index: int) -> QRect:
        if not self._burst_stack_mode or not self._can_cycle_burst(index):
            return QRect()
        image_rect = self._image_rect(tile_rect)
        rect = QRect(0, 0, 34, 34)
        rect.moveLeft(image_rect.left() + 12)
        rect.moveTop(image_rect.center().y() - rect.height() // 2)
        return rect

    def _burst_right_arrow_rect(self, tile_rect: QRect, index: int) -> QRect:
        if not self._burst_stack_mode or not self._can_cycle_burst(index):
            return QRect()
        image_rect = self._image_rect(tile_rect)
        rect = QRect(0, 0, 34, 34)
        rect.moveRight(image_rect.right() - 12)
        rect.moveTop(image_rect.center().y() - rect.height() // 2)
        return rect

    def displayed_variant_path(self, index: int) -> str:
        if not 0 <= index < len(self._items):
            return ""
        return self._current_variant(self._items[index]).path

    def _variant_index(self, record: ImageRecord) -> int:
        return min(self._variant_indexes.get(record.path, 0), max(0, record.stack_count - 1))

    def _current_variant(self, record: ImageRecord) -> ImageVariant:
        variants = record.display_variants
        return variants[self._variant_index(record)]

    def _cycle_variant(self, index: int, step: int) -> None:
        if not 0 <= index < len(self._items):
            return
        record = self._items[index]
        if record.stack_count <= 1:
            return
        current = self._variant_index(record)
        next_index = (current + step) % record.stack_count
        self._variant_indexes[record.path] = next_index
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update(self._item_rect(index))

    def _burst_group_for_index(self, index: int) -> tuple[int, ...]:
        return self._burst_group_members_by_index.get(index, ())

    def _can_cycle_burst(self, index: int) -> bool:
        return len(self._burst_group_for_index(index)) > 1

    def _cycle_burst(self, index: int, step: int) -> None:
        group = self._burst_group_for_index(index)
        if len(group) <= 1:
            return
        try:
            group_position = group.index(index)
        except ValueError:
            return
        next_index = group[(group_position + step) % len(group)]
        if self._tool_checkbox_mode:
            self._set_current_index(next_index)
            self._selection_anchor = next_index
            return
        self._set_single_selection(next_index)

    def _normalize_index_for_display(self, index: int) -> int:
        if not self._burst_stack_mode or not 0 <= index < len(self._items):
            return index
        anchor = self._burst_group_anchor_by_index.get(index)
        if anchor is None:
            return index
        current_display = self._burst_display_member_by_anchor.get(anchor, anchor)
        if current_display != index:
            self._burst_display_member_by_anchor[anchor] = index
            self._rebuild_visible_items()
            self._refresh_layout_after_visible_items_changed()
            self._schedule_visible_thumbnail_requests(immediate=True)
            self.viewport().update()
        return index

    def _visible_slot_for_index(self, index: int) -> int | None:
        normalized = self._displayed_index_for_slot(index)
        return self._visible_slot_by_item_index.get(normalized)

    def _displayed_index_for_slot(self, index: int) -> int:
        if not self._burst_stack_mode:
            return index
        anchor = self._burst_group_anchor_by_index.get(index)
        if anchor is None:
            return index
        return self._burst_display_member_by_anchor.get(anchor, anchor)

    def _normalize_burst_stack_selection(self) -> None:
        if not self._burst_stack_mode:
            return
        for anchor, members in self._burst_group_members_by_anchor.items():
            if self._current_index in members:
                self._burst_display_member_by_anchor[anchor] = self._current_index
                continue
            selected_member = next((index for index in self._selected_indexes if index in members), None)
            if selected_member is not None:
                self._burst_display_member_by_anchor[anchor] = selected_member

        if self._selected_indexes:
            self._selected_indexes = {self._displayed_index_for_slot(index) for index in self._selected_indexes}
        if self._selection_anchor >= 0:
            self._selection_anchor = self._displayed_index_for_slot(self._selection_anchor)
        if self._current_index >= 0:
            self._current_index = self._displayed_index_for_slot(self._current_index)

    def _rebuild_visible_items(self) -> None:
        if not self._burst_stack_mode:
            self._visible_item_indexes = list(range(len(self._items)))
        else:
            visible: list[int] = []
            skip_members: set[int] = set()
            for index in range(len(self._items)):
                if index in skip_members:
                    continue
                anchor = self._burst_group_anchor_by_index.get(index)
                if anchor is None or anchor != index:
                    visible.append(index)
                    continue
                members = self._burst_group_members_by_anchor.get(anchor, (index,))
                display_member = self._burst_display_member_by_anchor.get(anchor, anchor)
                if display_member not in members:
                    display_member = members[0]
                    self._burst_display_member_by_anchor[anchor] = display_member
                visible.append(display_member)
                skip_members.update(members)
            self._visible_item_indexes = visible
        if self._adapter_review_mode:
            self._visible_item_indexes = [
                index
                for index in self._visible_item_indexes
                if self._record_in_adapter_review(index)
            ]
        self._visible_slot_by_item_index = {
            item_index: slot
            for slot, item_index in enumerate(self._visible_item_indexes)
        }
        self._sync_adapter_label_controls()

    def _record_in_adapter_review(self, index: int) -> bool:
        if not 0 <= index < len(self._items):
            return False
        record = self._items[index]
        if _fast_path_key(record.path) in self._adapter_review_paths:
            return True
        return any(_fast_path_key(variant.path) in self._adapter_review_paths for variant in record.display_variants)

    def _hide_adapter_label_controls(self) -> None:
        for combo in self._adapter_label_combos.values():
            combo.hide()

    def _hide_adapter_reason_controls(self) -> None:
        for button in self._adapter_reason_buttons.values():
            button.hide()

    def _delete_adapter_label_controls(self) -> None:
        for combo in self._adapter_label_combos.values():
            combo.deleteLater()
        self._adapter_label_combos.clear()
        for button in self._adapter_reason_buttons.values():
            button.deleteLater()
        self._adapter_reason_buttons.clear()

    def _sync_adapter_label_controls(self) -> None:
        if not self._adapter_review_mode or not self._adapter_review_label_controls_enabled:
            self._hide_adapter_label_controls()
            self._sync_adapter_reason_controls()
            return
        self._hide_adapter_reason_controls()
        visible = set(self._visible_indexes())
        for index, combo in list(self._adapter_label_combos.items()):
            if index not in visible or not self._record_in_adapter_review(index):
                combo.hide()
        for index in visible:
            if not self._record_in_adapter_review(index):
                continue
            combo = self._adapter_label_combos.get(index)
            if combo is None:
                combo = self._build_adapter_label_combo(index)
                self._adapter_label_combos[index] = combo
            record = self._items[index]
            label = self._adapter_labels_by_path.get(record.path, "")
            current = combo.currentData()
            if current != label:
                was_blocked = combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(label)))
                combo.blockSignals(was_blocked)
            combo.setGeometry(self._adapter_label_rect(self._item_rect(index)))
            combo.show()
            combo.raise_()

    def _sync_adapter_reason_controls(self) -> None:
        if not self._adapter_review_mode or not self._adapter_review_reason_controls_enabled:
            self._hide_adapter_reason_controls()
            return
        visible = set(self._visible_indexes())
        for index, button in list(self._adapter_reason_buttons.items()):
            if index not in visible or not self._record_in_adapter_review(index):
                button.hide()
        for index in visible:
            if not self._record_in_adapter_review(index):
                continue
            button = self._adapter_reason_buttons.get(index)
            if button is None:
                button = self._build_adapter_reason_button(index)
                self._adapter_reason_buttons[index] = button
            record = self._items[index]
            tags = self._adapter_reason_tags_by_path.get(record.path, ())
            self._set_adapter_reason_button_state(button, tags)
            button.setGeometry(self._adapter_label_rect(self._item_rect(index)))
            button.show()
            button.raise_()

    def _build_adapter_label_combo(self, index: int) -> QComboBox:
        combo = QComboBox(self.viewport())
        combo.setObjectName("adapterLabelCombo")
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setStyleSheet(
            "QComboBox#adapterLabelCombo {"
            " padding: 1px 4px 1px 8px;"
            " margin: 0px;"
            " font-size: 11px;"
            " min-height: 20px;"
            "} "
            "QComboBox#adapterLabelCombo::drop-down { width: 16px; border-left: none; } "
            "QComboBox#adapterLabelCombo QAbstractItemView { font-size: 12px; }"
        )
        for label, text in (
            ("", "Label..."),
            ("hero", "1 Best"),
            ("strong", "2 Strong"),
            ("maybe", "3 Maybe"),
            ("weak", "4 Weak"),
            ("reject", "5 Reject"),
        ):
            combo.addItem(text, label)
        combo.currentIndexChanged.connect(lambda _row, item_index=index, widget=combo: self._handle_adapter_label_changed(item_index, widget))
        return combo

    def _build_adapter_reason_button(self, index: int) -> QToolButton:
        button = QToolButton(self.viewport())
        button.setObjectName("adapterReasonButton")
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet(
            "QToolButton#adapterReasonButton {"
            " padding: 1px 8px; margin: 0px; font-size: 11px; min-height: 20px;"
            " background: #0f1622; color: #f0f6ff;"
            " border: 1px solid #33445f; border-radius: 5px;"
            "} "
            "QToolButton#adapterReasonButton:hover { border-color: #4f7edb; }"
        )
        menu = QMenu(button)
        for key, label in self._adapter_reason_options:
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setProperty("reasonKey", key)
            action.toggled.connect(
                lambda checked, item_index=index, reason_key=key: self._handle_adapter_reason_toggled(
                    item_index,
                    reason_key,
                    checked,
                )
            )
            menu.addAction(action)
        menu.addSeparator()
        clear_action = QAction("Clear reasons", menu)
        clear_action.triggered.connect(lambda _checked=False, item_index=index: self._set_adapter_reason_tags_for_index(item_index, (), emit=True))
        menu.addAction(clear_action)
        button.setMenu(menu)
        return button

    def _set_adapter_reason_button_state(self, button: QToolButton, tags: tuple[str, ...]) -> None:
        selected = set(tags)
        for action in button.menu().actions() if button.menu() is not None else ():
            reason_key = action.property("reasonKey")
            if not reason_key:
                continue
            with QSignalBlocker(action):
                action.setChecked(str(reason_key) in selected)
        button.setText(f"{len(selected)} reason(s)" if selected else "Reasons...")

    def _handle_adapter_reason_toggled(self, index: int, reason_key: str, checked: bool) -> None:
        if not 0 <= index < len(self._items):
            return
        record = self._items[index]
        current = list(self._adapter_reason_tags_by_path.get(record.path, ()))
        if checked and reason_key not in current:
            current.append(reason_key)
        elif not checked:
            current = [tag for tag in current if tag != reason_key]
        ordered = tuple(key for key, _label in self._adapter_reason_options if key in set(current))
        self._set_adapter_reason_tags_for_index(index, ordered, emit=True)

    def _set_adapter_reason_tags_for_index(self, index: int, tags: tuple[str, ...], *, emit: bool) -> None:
        if not 0 <= index < len(self._items):
            return
        record = self._items[index]
        if tags:
            self._adapter_reason_tags_by_path[record.path] = tags
        else:
            self._adapter_reason_tags_by_path.pop(record.path, None)
        button = self._adapter_reason_buttons.get(index)
        if button is not None:
            self._set_adapter_reason_button_state(button, tags)
        if emit:
            self.adapter_reasons_requested.emit(record.path, tags)

    def _handle_adapter_label_changed(self, index: int, combo: QComboBox) -> None:
        label = str(combo.currentData() or "")
        self._set_adapter_label_for_index(index, label, emit=True)

    def _set_adapter_label_for_index(self, index: int, label: str, *, emit: bool) -> None:
        if not 0 <= index < len(self._items):
            return
        logger = perf_logger()
        total_start = time.perf_counter() if logger.enabled else 0.0
        record = self._items[index]
        normalized = label.strip().lower()
        if normalized:
            self._adapter_labels_by_path[record.path] = normalized
        else:
            self._adapter_labels_by_path.pop(record.path, None)
        combo = self._adapter_label_combos.get(index)
        if combo is not None and combo.currentData() != normalized:
            was_blocked = combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(normalized)))
            combo.blockSignals(was_blocked)
        if emit:
            emit_start = time.perf_counter() if logger.enabled else 0.0
            self.adapter_label_requested.emit(record.path, normalized)
            if logger.enabled:
                logger.duration(
                    "adapter_review.grid.emit_wait",
                    (time.perf_counter() - emit_start) * 1000.0,
                    path=record.path,
                    label=normalized,
                    review_mode=self._adapter_review_mode,
                )
            if normalized and self._adapter_review_mode:
                self._advance_to_next_adapter_candidate(index)
        if logger.enabled:
            logger.duration(
                "adapter_review.grid.label_total",
                (time.perf_counter() - total_start) * 1000.0,
                path=record.path,
                label=normalized,
                emitted=emit,
                review_mode=self._adapter_review_mode,
            )

    def _advance_to_next_adapter_candidate(self, from_index: int) -> None:
        if not self._items:
            return
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        total = len(self._items)
        scanned = 0
        for offset in range(1, total + 1):
            candidate_index = (from_index + offset) % total
            if candidate_index == from_index:
                break
            scanned += 1
            if not self._record_in_adapter_review(candidate_index):
                continue
            record = self._items[candidate_index]
            if record.path in self._adapter_labels_by_path:
                continue
            select_start = time.perf_counter() if logger.enabled else 0.0
            self.set_current_index(candidate_index)
            if logger.enabled:
                logger.duration(
                    "adapter_review.grid.advance_select",
                    (time.perf_counter() - select_start) * 1000.0,
                    from_index=from_index,
                    candidate_index=candidate_index,
                    scanned=scanned,
                    path=record.path,
                )
            scroll_start = time.perf_counter() if logger.enabled else 0.0
            self._ensure_index_visible(candidate_index)
            if logger.enabled:
                logger.duration(
                    "adapter_review.grid.advance_scroll",
                    (time.perf_counter() - scroll_start) * 1000.0,
                    from_index=from_index,
                    candidate_index=candidate_index,
                    scanned=scanned,
                    path=record.path,
                )
                logger.duration(
                    "adapter_review.grid.advance_total",
                    (time.perf_counter() - start) * 1000.0,
                    from_index=from_index,
                    candidate_index=candidate_index,
                    scanned=scanned,
                    path=record.path,
                    found=True,
                )
            return
        if logger.enabled:
            logger.duration(
                "adapter_review.grid.advance_total",
                (time.perf_counter() - start) * 1000.0,
                from_index=from_index,
                scanned=scanned,
                found=False,
            )

    def _ensure_index_visible(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        try:
            rect = self._item_rect(index)
        except Exception:
            return
        if rect.isNull():
            return
        viewport = self.viewport().rect()
        if rect.top() < 0:
            self.verticalScrollBar().setValue(max(0, self.verticalScrollBar().value() + rect.top() - 12))
        elif rect.bottom() > viewport.height():
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + (rect.bottom() - viewport.height()) + 12)

    def _refresh_layout_after_visible_items_changed(self) -> None:
        self._recalculate_metrics()
        self._update_scrollbar()

    def _should_fit_single_visible_tile(self) -> bool:
        return self._columns == 1 and len(self._visible_item_indexes) == 1

    def _single_visible_item(self) -> tuple[int, ImageRecord, ImageVariant] | None:
        if not self._should_fit_single_visible_tile():
            return None
        index = self._visible_item_indexes[0]
        if not 0 <= index < len(self._items):
            return None
        record = self._items[index]
        return index, record, self._current_variant(record)

    def _single_visible_item_matches_path(self, path: str) -> bool:
        single_item = self._single_visible_item()
        if single_item is None:
            return False
        _, record, variant = single_item
        return path == variant.path or path == record.path

    def _single_visible_item_aspect_ratio(self) -> float | None:
        single_item = self._single_visible_item()
        if single_item is None:
            return None
        _, record, variant = single_item
        return self._record_aspect_ratio(record, variant)

    def _record_aspect_ratio(self, record: ImageRecord, variant: ImageVariant) -> float | None:
        for candidate in (variant.path, record.path):
            aspect_ratio = self._display_aspect_ratio_by_path.get(candidate)
            if aspect_ratio is not None and aspect_ratio > 0:
                return aspect_ratio
            cached_metadata = self.metadata_manager.get_cached(variant if candidate == variant.path else record)
            if cached_metadata is not None and cached_metadata.width > 0 and cached_metadata.height > 0:
                aspect_ratio = cached_metadata.width / cached_metadata.height
                self._display_aspect_ratio_by_path[candidate] = aspect_ratio
                return aspect_ratio
        return None

    def _current_loupe_aspect_ratio(self) -> float | None:
        if not 0 <= self._current_index < len(self._items):
            return None
        record = self._items[self._current_index]
        if record.is_folder:
            return None
        return self._record_aspect_ratio(record, self._current_variant(record))

    def _loupe_fitted_tile_height(self, tile_width: int, max_tile_height: int) -> int:
        """Trim the single-column card so it hugs the current photo instead of
        leaving a dead band under it (the pane is usually taller than a 3:2
        landscape frame). Falls back to the full pane height when the aspect
        ratio is unknown or the photo is height-limited (portrait)."""
        aspect = self._current_loupe_aspect_ratio()
        if not aspect or aspect <= 0:
            return max_tile_height
        if tile_width <= 0:
            return max_tile_height
        if self._loupe_card_style == "detailed":
            footer_height = self._review_text_block_height(QRect(0, 0, tile_width, max(1, max_tile_height)))
            if aspect >= 1.0:
                needed = int(round(tile_width * 2 / 3)) + footer_height
                return max(160, needed)
            needed = int(round(tile_width / aspect)) + footer_height
            return max(160, min(max_tile_height, needed))
        needed = int(round(tile_width / aspect))
        return max(160, min(max_tile_height, needed))

    def _loupe_tile_height_stale(self) -> bool:
        if not self._use_loupe_card_style():
            return False
        max_tile_height = max(160, self.viewport().height() - (self._margin * 2))
        return self._loupe_fitted_tile_height(self._tile_width_value, max_tile_height) != self._tile_height_value

    def _current_item_matches_path(self, path: str) -> bool:
        if not 0 <= self._current_index < len(self._items):
            return False
        record = self._items[self._current_index]
        variant = self._current_variant(record)
        return path == variant.path or path == record.path

    def _current_visible_slot(self) -> int:
        if not self._visible_item_indexes:
            return -1
        if self._current_index < 0:
            return 0
        return self._visible_slot_by_item_index.get(self._displayed_index_for_slot(self._current_index), 0)

    def _set_single_selection(self, index: int) -> None:
        index = self._normalize_index_for_display(index)
        if not 0 <= index < len(self._items):
            return
        previous_selection = set(self._selected_indexes)
        self._selected_indexes = {index}
        self._selection_anchor = index
        self._set_current_index(index)
        self._update_selection_tiles(previous_selection | {index})
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def _toggle_selection(self, index: int) -> None:
        index = self._normalize_index_for_display(index)
        if not 0 <= index < len(self._items):
            return
        previous_selection = set(self._selected_indexes)
        if index in self._selected_indexes and (len(self._selected_indexes) > 1 or self._tool_checkbox_mode):
            self._selected_indexes.remove(index)
        else:
            self._selected_indexes.add(index)
        self._selection_anchor = index
        self._set_current_index(index)
        self._update_selection_tiles(previous_selection | self._selected_indexes | {index})
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def _select_range(self, index: int) -> None:
        index = self._normalize_index_for_display(index)
        if not 0 <= index < len(self._items):
            return
        anchor = self._selection_anchor if 0 <= self._selection_anchor < len(self._items) else self._current_index
        anchor = self._normalize_index_for_display(anchor)
        if anchor < 0:
            self._set_single_selection(index)
            return
        previous_selection = set(self._selected_indexes)
        start_slot = self._visible_slot_for_index(anchor)
        end_slot = self._visible_slot_for_index(index)
        if start_slot is None or end_slot is None:
            self._set_single_selection(index)
            return
        start = min(start_slot, end_slot)
        end = max(start_slot, end_slot)
        self._selected_indexes = {self._visible_item_indexes[slot] for slot in range(start, end + 1)}
        self._set_current_index(index)
        self._update_selection_tiles(previous_selection | self._selected_indexes)
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def _select_all(self) -> None:
        if not self._visible_item_indexes:
            return
        previous_selection = set(self._selected_indexes)
        self._selected_indexes = set(self._visible_item_indexes)
        if self._current_index < 0:
            self._current_index = self._visible_item_indexes[0]
        self._selection_anchor = self._current_index
        self._update_selection_tiles(previous_selection | self._selected_indexes)
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def _set_marquee_rect(self, rect: QRect) -> None:
        previous_rect = QRect(self._marquee_rect)
        self._marquee_rect = rect
        update_rect = previous_rect.united(self._marquee_rect).adjusted(-2, -2, 2, 2)
        if not update_rect.isNull():
            self.viewport().update(update_rect)

    def _apply_marquee_selection(self) -> None:
        if self._marquee_origin is None or self._marquee_rect.isNull():
            return
        previous_selection = set(self._selected_indexes)
        hit_indexes = {
            index
            for index in self._visible_item_indexes
            if self._item_rect(index).intersects(self._marquee_rect)
        }
        self._selected_indexes = set(self._marquee_base_selection)
        self._selected_indexes.update(hit_indexes)
        if hit_indexes:
            focus_index = max(hit_indexes)
            self._selection_anchor = min(hit_indexes)
            self._set_current_index(focus_index)
        self._update_selection_tiles(previous_selection | self._selected_indexes)
        if previous_selection != self._selected_indexes:
            self.selection_changed.emit()

    def _start_internal_drag(self) -> bool:
        selected_indexes = self._drag_indexes()
        if not selected_indexes:
            self._reset_pointer_interaction(clear_marquee=True)
            return False

        mime_data = QMimeData()
        dragged_paths = [self._items[index].path for index in selected_indexes if 0 <= index < len(self._items)]
        payload = "\n".join(dragged_paths).encode("utf-8")
        mime_data.setData(self.INTERNAL_RECORD_MIME, payload)
        mime_data.setText("\n".join(Path(path).name for path in dragged_paths))

        drag = QDrag(self.viewport())
        drag.setMimeData(mime_data)
        self._reset_pointer_interaction(clear_marquee=True)
        default_action = (
            Qt.DropAction.CopyAction
            if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            else Qt.DropAction.MoveAction
        )
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, default_action)
        return True

    def _drag_indexes(self) -> list[int]:
        selected_indexes = self.selected_indexes()
        if self._press_index >= 0 and self._press_index not in selected_indexes:
            return [self._press_index]
        return selected_indexes

    def _clear_marquee_selection(self) -> None:
        previous_rect = QRect(self._marquee_rect)
        self._marquee_origin = None
        self._marquee_base_selection = set()
        self._marquee_active = False
        self._marquee_rect = QRect()
        if not previous_rect.isNull():
            self.viewport().update(previous_rect.adjusted(-2, -2, 2, 2))

    def _reset_pointer_interaction(self, *, clear_marquee: bool) -> None:
        self._press_pos = None
        self._press_index = -1
        self._press_on_interactive_control = False
        self._pending_single_selection_index = -1
        self._pending_clear_selection = False
        if clear_marquee:
            self._clear_marquee_selection()

    def _update_selection_tiles(self, indexes: set[int]) -> None:
        for tile_index in indexes:
            if 0 <= tile_index < len(self._items):
                self.viewport().update(self._item_rect(tile_index))

    def _set_current_index(self, index: int) -> None:
        index = self._normalize_index_for_display(index)
        if not 0 <= index < len(self._items):
            return
        previous = self._current_index
        self._current_index = index
        if self._loupe_tile_height_stale():
            # The loupe card hugs the current photo, so navigating between
            # different aspect ratios changes the row height.
            self._refresh_layout_after_visible_items_changed()
            self._schedule_visible_thumbnail_requests(immediate=True)
        self._ensure_visible(index)
        if previous >= 0:
            self.viewport().update(self._item_rect(previous))
        self.viewport().update(self._item_rect(index))
        self.current_changed.emit(index)

    def _ensure_visible(self, index: int) -> None:
        rect = self._content_rect(index)
        scroll_value = self.verticalScrollBar().value()
        top = rect.top()
        bottom = rect.bottom()
        viewport_height = self.viewport().height()
        if top < scroll_value:
            self.verticalScrollBar().setValue(max(0, top - self._spacing))
        elif bottom > scroll_value + viewport_height:
            self.verticalScrollBar().setValue(bottom - viewport_height + self._spacing)

    def _wheel_row_steps(self, event: QWheelEvent, row_height: int) -> int:
        angle_y = event.angleDelta().y()
        if angle_y:
            if self._wheel_angle_remainder and (self._wheel_angle_remainder > 0) != (angle_y > 0):
                self._wheel_angle_remainder = 0
            self._wheel_angle_remainder += angle_y
            steps = int(abs(self._wheel_angle_remainder) // 120)
            if steps <= 0:
                return 0
            direction = -1 if self._wheel_angle_remainder > 0 else 1
            self._wheel_angle_remainder -= (120 * steps) if self._wheel_angle_remainder > 0 else (-120 * steps)
            self._wheel_pixel_remainder = 0
            return direction * steps

        pixel_y = event.pixelDelta().y()
        if not pixel_y:
            return 0
        if self._wheel_pixel_remainder and (self._wheel_pixel_remainder > 0) != (pixel_y > 0):
            self._wheel_pixel_remainder = 0
        self._wheel_pixel_remainder += pixel_y
        threshold = max(32, min(row_height, row_height // 2))
        steps = int(abs(self._wheel_pixel_remainder) // threshold)
        if steps <= 0:
            return 0
        direction = -1 if self._wheel_pixel_remainder > 0 else 1
        self._wheel_pixel_remainder -= threshold * steps if self._wheel_pixel_remainder > 0 else -threshold * steps
        self._wheel_angle_remainder = 0
        return direction * steps

    def _wheel_scroll_delta_pixels(self, event: QWheelEvent, row_height: int) -> int:
        pixel_y = event.pixelDelta().y()
        if pixel_y:
            self._wheel_angle_remainder = 0
            return -pixel_y

        angle_y = event.angleDelta().y()
        if not angle_y:
            return 0
        self._wheel_pixel_remainder = 0
        free_step = max(90, int(row_height * 0.55))
        return int(round((-angle_y / 120.0) * free_step))

    def _scroll_by_pixels(self, delta: int) -> None:
        if delta == 0:
            return
        scrollbar = self.verticalScrollBar()
        current = self._smooth_scroll_target if self._smooth_scroll_target is not None else scrollbar.value()
        target = current + int(delta)
        self._animate_scroll_to(target)

    def _scroll_by_aligned_rows(self, row_delta: int) -> None:
        row_height = self._row_height()
        if row_delta == 0 or row_height <= 0:
            return
        scrollbar = self.verticalScrollBar()
        current = self._smooth_scroll_target if self._smooth_scroll_target is not None else scrollbar.value()
        if row_delta > 0:
            row = current // row_height + row_delta
        else:
            row = math.ceil(current / row_height) + row_delta
        row = max(0, min(max(0, self._row_count() - 1), row))
        target = max(scrollbar.minimum(), min(scrollbar.maximum(), row * row_height))
        self._animate_scroll_to(target)

    def _animate_scroll_to(self, target: int) -> None:
        scrollbar = self.verticalScrollBar()
        target = max(scrollbar.minimum(), min(scrollbar.maximum(), int(target)))
        if target == scrollbar.value():
            self._stop_smooth_scroll()
            return
        self._smooth_scroll_target = target
        if self._smooth_scroll_animation.state() == QAbstractAnimation.State.Running:
            self._smooth_scroll_animation.stop()
        self._smooth_scroll_animation.setStartValue(scrollbar.value())
        self._smooth_scroll_animation.setEndValue(target)
        self._smooth_scroll_animation.start()

    def _stop_smooth_scroll(self) -> None:
        if self._smooth_scroll_animation.state() == QAbstractAnimation.State.Running:
            self._smooth_scroll_animation.stop()
        self._smooth_scroll_target = None

    def _handle_smooth_scroll_finished(self) -> None:
        self._smooth_scroll_target = None

    def _update_scrollbar(self) -> None:
        rows = self._row_count()
        total_height = self._margin * 2
        if rows:
            total_height += rows * self._tile_height() + max(0, rows - 1) * self._spacing
        max_value = max(0, total_height - self.viewport().height())
        self.verticalScrollBar().setRange(0, max_value)
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.verticalScrollBar().setSingleStep(max(40, self._row_height()))

    def _request_visible_thumbnails(self) -> None:
        if not self._items:
            self.thumbnail_manager.set_wanted_keys(set())
            return

        target = self._thumbnail_target_size()
        visible = self._visible_indexes()
        if not visible:
            self.thumbnail_manager.set_wanted_keys(set())
            return

        center = (visible[0] + visible[-1]) // 2
        thumbnail_requests: list[tuple[ImageRecord, ThumbnailKey, int]] = []
        for index in visible:
            distance = abs(index - center)
            priority = max(1, 10_000 - distance)
            if self._items[index].is_folder:
                continue
            variant = self._current_variant(self._items[index])
            key = self.thumbnail_manager.make_key(variant, target)
            thumbnail_requests.append((variant, key, priority))

        self.thumbnail_manager.set_wanted_keys({key for _, key, _ in thumbnail_requests})
        for variant, key, priority in thumbnail_requests:
            if variant.path in self._failed_paths:
                continue
            if self._cached_pixmap_for_key(key) is None:
                self.thumbnail_manager.request_thumbnail(variant, target, priority=priority)
            self.metadata_manager.request_metadata(variant, priority=priority)

    def _schedule_visible_thumbnail_requests(self, immediate: bool = False) -> None:
        if immediate:
            self._thumbnail_request_timer.stop()
            self._request_visible_thumbnails()
            return
        self._thumbnail_request_timer.start()

    def _visible_indexes(self) -> list[int]:
        if not self._visible_item_indexes:
            return []

        row_height = self._row_height()
        if row_height <= 0:
            return []

        scroll_value = self.verticalScrollBar().value()
        viewport_height = self.viewport().height()
        start_row = max(0, (scroll_value - self._margin) // row_height - self._buffer_rows)
        row_count = self._row_count()
        if row_count <= 0:
            return []
        end_row = min(
            row_count - 1,
            (scroll_value + viewport_height - self._margin) // row_height + self._buffer_rows + 1,
        )
        start = start_row * self._columns
        end = min(len(self._visible_item_indexes), (end_row + 1) * self._columns)
        return list(self._visible_item_indexes[start:end])

    def _row_count(self) -> int:
        if not self._visible_item_indexes:
            return 0
        return math.ceil(len(self._visible_item_indexes) / self._columns)

    def _tile_width(self) -> int:
        return self._tile_width_value

    def _image_height(self) -> int:
        return self._image_height_value

    def _tile_height(self) -> int:
        return self._tile_height_value

    def _row_height(self) -> int:
        return self._row_height_value

    def _thumbnail_target_size(self) -> QSize:
        return self._thumbnail_target_size_value

    def _recalculate_metrics(self) -> None:
        self._margin = 10 if self._compact_card_mode else 12
        self._spacing = 10 if self._compact_card_mode else 12
        self._image_padding = 6 if self._compact_card_mode else 7
        self._caption_height = 20 if self._compact_card_mode else 22
        self._capture_height = 0 if self._compact_card_mode else 16
        self._meta_height = 0 if self._compact_card_mode else 16
        minimum_image_height = 72 if self._compact_card_mode else 88
        image_ratio = 0.62 if self._compact_card_mode else 0.68
        footer_height = 10 if self._compact_card_mode else 16
        card_chrome_height = (
            self._image_padding * 2
            + self._caption_height
            + self._action_height
            + self._capture_height
            + self._meta_height
            + footer_height
        )
        inner = max(320, self.viewport().width() - (self._margin * 2))
        if self._zoom_mode == "tile":
            # Continuous zoom: tiles are a fixed target width; derive how many
            # whole columns fit so the grid reflows smoothly as the slider moves.
            target = max(self.MIN_TILE_WIDTH, min(self.MAX_TILE_WIDTH, int(self._zoom_tile_width)))
            columns = max(1, int((inner + self._spacing) // (target + self._spacing)))
        else:
            # Discrete zoom: a whole number of columns that fill the row width.
            columns = max(1, min(8, self._zoom_columns))
            target = max(
                self.MIN_TILE_WIDTH,
                (inner - ((columns - 1) * self._spacing)) // columns,
            )
        self._columns = columns
        if columns == 1:
            if not self._compact_card_mode:
                # Frameless loupe: the photo owns the whole tile.
                card_chrome_height = 0
            self._tile_width_value = inner
            max_tile_height = max(160, self.viewport().height() - (self._margin * 2))
            if not self._compact_card_mode:
                max_tile_height = self._loupe_fitted_tile_height(inner, max_tile_height)
            self._image_height_value = max(64, max_tile_height - card_chrome_height)
            self._row_x_offset = 0
        else:
            self._tile_width_value = target
            self._image_height_value = max(minimum_image_height, int(target * image_ratio))
            used = columns * target + ((columns - 1) * self._spacing)
            self._row_x_offset = max(0, (inner - used) // 2)
        self._tile_height_value = card_chrome_height + self._image_height_value
        if self._use_new_grid_card():
            compact_tiles = self._use_compact_grid_card()
            if compact_tiles:
                # Barebones compact card: the tile is the bare 3:2 photo with
                # the chrome overlaid, so the photo owns the full cell.
                self._tile_height_value = max(80, grid_card_height_for_width(target, compact=True))
                self._image_height_value = self._tile_height_value
            else:
                # Detailed card at low column counts: the tuned 11:8 review
                # tile with a full-width 3:2 photo pane and the text footer
                # over its lower edge.
                self._tile_height_value = max(96, grid_card_height_for_width(target, compact=False))
                self._image_height_value = max(64, round(target * 2 / 3))
        self._row_height_value = self._tile_height_value + self._spacing
        thumbnail_width = self._tile_width_value
        if not (columns == 1 and not self._compact_card_mode) and not self._use_new_grid_card():
            thumbnail_width -= self._image_padding * 2
        self._thumbnail_target_size_value = QSize(
            max(64, thumbnail_width),
            max(64, self._image_height_value),
        )
        self._clear_pixmap_cache()

    def _pixmap_for(self, key: ThumbnailKey, image: QImage | None) -> QPixmap | None:
        entry = self._pixmap_cache.get(key)
        if entry is not None:
            self._pixmap_cache.move_to_end(key)
            return entry[0]
        if image is None or image.isNull():
            return None
        return self._cache_pixmap(key, image)

    def _cached_pixmap_for_key(self, key: ThumbnailKey) -> QPixmap | None:
        entry = self._pixmap_cache.get(key)
        if entry is None:
            return None
        self._pixmap_cache.move_to_end(key)
        return entry[0]

    def _cache_pixmap(self, key: ThumbnailKey, image: QImage) -> QPixmap | None:
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        cost = max(1, pixmap.width() * pixmap.height() * 4)
        existing = self._pixmap_cache.pop(key, None)
        if existing is not None:
            self._pixmap_cache_bytes -= existing[1]
        self._pixmap_cache[key] = (pixmap, cost)
        self._pixmap_cache.move_to_end(key)
        self._pixmap_cache_bytes += cost
        while self._pixmap_cache_bytes > self._pixmap_cache_limit and self._pixmap_cache:
            _, (_, removed_cost) = self._pixmap_cache.popitem(last=False)
            self._pixmap_cache_bytes -= removed_cost
        return pixmap

    def _clear_pixmap_cache(self, *, paths: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        if not paths:
            self._pixmap_cache.clear()
            self._pixmap_cache_bytes = 0
            return

        normalized_paths = {normalized_path_key(path) for path in paths if path}
        if not normalized_paths:
            return
        removed_cost = 0
        for key in list(self._pixmap_cache.keys()):
            if normalized_path_key(key.path) in normalized_paths:
                _, cost = self._pixmap_cache.pop(key)
                removed_cost += cost
        if removed_cost:
            self._pixmap_cache_bytes = max(0, self._pixmap_cache_bytes - removed_cost)

    def _content_rect(self, index: int) -> QRect:
        slot = self._visible_slot_for_index(index)
        if slot is None:
            return QRect()
        row = slot // self._columns
        column = slot % self._columns
        x = self._margin + self._row_x_offset + column * (self._tile_width() + self._spacing)
        y = self._margin + row * (self._tile_height() + self._spacing)
        return QRect(x, y, self._tile_width(), self._tile_height())

    def _item_rect(self, index: int) -> QRect:
        rect = self._content_rect(index)
        rect.translate(0, -self.verticalScrollBar().value())
        return rect

    def _index_at(self, x: int, y: int) -> int:
        content_y = y + self.verticalScrollBar().value()
        left = self._margin + self._row_x_offset
        if x < left:
            return -1
        tile_width = self._tile_width()
        tile_height = self._tile_height()
        column_span = tile_width + self._spacing
        row_span = tile_height + self._spacing

        column = (x - left) // column_span
        row = (content_y - self._margin) // row_span
        if column < 0 or column >= self._columns or row < 0:
            return -1

        x_in_tile = (x - left) % column_span
        y_in_tile = (content_y - self._margin) % row_span
        if x_in_tile >= tile_width or y_in_tile >= tile_height:
            return -1

        slot = row * self._columns + column
        if slot >= len(self._visible_item_indexes):
            return -1
        return self._visible_item_indexes[slot]
