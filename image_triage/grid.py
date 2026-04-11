from __future__ import annotations

import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QCursor, QDrag, QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen, QPixmap, QTextOption
from PySide6.QtWidgets import QApplication, QAbstractScrollArea

from .ai_results import AIImageResult
from .cache import ThumbnailKey
from .metadata import CaptureMetadata, MetadataKey, MetadataManager
from .models import ImageRecord, ImageVariant, SessionAnnotation
from .review_workflows import review_round_short_label
from .scanner import normalized_path_key
from .thumbnails import ThumbnailManager
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

    current_changed = Signal(int)
    preview_requested = Signal(int)
    delete_requested = Signal(int)
    move_requested = Signal(int)
    keep_requested = Signal(int)
    rate_requested = Signal(int, int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)
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
        self._normalized_path_cache: dict[str, str] = {}
        self._failed_paths: set[str] = set()
        self._empty_message = "Choose a folder to start triaging images."
        self._meta_cache: dict[str, str] = {}
        self._meta_with_ai_cache: dict[str, str] = {}
        self._capture_cache: dict[str, str] = {}
        self._pixmap_cache: OrderedDict[ThumbnailKey, tuple[QPixmap, int]] = OrderedDict()
        self._pixmap_cache_bytes = 0
        self._pixmap_cache_limit = 192 * 1024 * 1024
        self._current_index = -1
        self._selected_indexes: set[int] = set()
        self._selection_anchor = -1
        self._tool_checkbox_mode = False
        self._action_mode = "normal"
        self._columns = 3
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
        self._title_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._meta_font = QFont("Segoe UI", 9)
        self._placeholder_font = QFont("Segoe UI", 11)
        self._empty_font = QFont("Segoe UI", 14)
        self._border_active = QColor("#63a0ff")
        self._border_selected = QColor("#4e6d94")
        self._border_idle = QColor("#364152")
        self._background_active = QColor("#1d232d")
        self._background_selected = QColor("#18202c")
        self._background_idle = QColor("#141922")
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

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().valueChanged.connect(self._handle_scroll_value_changed)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.apply_theme(default_theme())
        self._recalculate_metrics()

    def apply_theme(self, theme: ThemePalette) -> None:
        self._border_active = theme.accent.qcolor()
        self._border_selected = theme.selection_outline.qcolor()
        self._border_idle = theme.border.qcolor()
        self._background_active = theme.raised_bg.qcolor()
        self._background_selected = theme.panel_bg.qcolor()
        self._background_idle = theme.panel_alt_bg.qcolor()
        self._title_color = theme.text_primary.qcolor()
        self._capture_color = theme.text_secondary.qcolor()
        self._meta_color = theme.text_muted.qcolor()
        self._placeholder_color = theme.input_hover_bg.qcolor()
        self._placeholder_text_color = theme.text_secondary.qcolor()
        self._failed_text_color = theme.danger.qcolor()
        self._badge_background = theme.badge_bg.qcolor()
        self._badge_text_color = theme.badge_text.qcolor()
        self._winner_color = theme.danger.qcolor()
        self._winner_button_fill = theme.danger_soft.with_alpha(42).qcolor()
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
        palette.setColor(QPalette.ColorRole.Base, theme.panel_alt_bg.qcolor())
        palette.setColor(QPalette.ColorRole.Window, theme.panel_bg.qcolor())
        palette.setColor(QPalette.ColorRole.Text, theme.text_primary.qcolor())
        palette.setColor(QPalette.ColorRole.Mid, theme.border.qcolor())
        self.setPalette(palette)
        self.viewport().update()

    def set_items(self, items: list[ImageRecord]) -> None:
        previous_variant_indexes = dict(self._variant_indexes)
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
        self._variant_indexes = {
            item.path: min(previous_variant_indexes.get(item.path, 0), max(0, item.stack_count - 1))
            for item in items
        }
        self._failed_paths.clear()
        self._meta_cache = {
            variant.path: self._format_meta_line(item, variant)
            for item in items
            for variant in item.display_variants
        }
        self._capture_cache = {
            variant.path: self._format_capture_line(self.metadata_manager.get_cached(variant))
            for item in items
            for variant in item.display_variants
        }
        self._ai_result_cache.clear()
        self._normalized_path_cache.clear()
        self._meta_with_ai_cache.clear()
        self._clear_pixmap_cache()
        self._current_index = 0 if items else -1
        self._selected_indexes = {0} if items else set()
        self._selection_anchor = self._current_index
        self._reset_pointer_interaction(clear_marquee=True)
        self._rebuild_visible_items()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.current_changed.emit(self._current_index)
        self.selection_changed.emit()

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._annotations = annotations
        self.viewport().update()

    def update_annotations(self, changed_paths: list[str] | tuple[str, ...] | set[str]) -> None:
        if not changed_paths:
            return
        normalized_index_lookup = {normalized_path_key(path): index for path, index in self._path_to_index.items()}
        dirty_indexes: set[int] = set()
        for path in changed_paths:
            if not path:
                continue
            index = self._path_to_index.get(path)
            if index is None:
                index = normalized_index_lookup.get(normalized_path_key(path))
            if index is not None:
                dirty_indexes.add(index)
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

        normalized_index_lookup = {normalized_path_key(path): index for path, index in self._path_to_index.items()}
        dirty_indexes: set[int] = set()
        for path in changed_paths:
            index = self._path_to_index.get(path)
            if index is None:
                index = normalized_index_lookup.get(normalized_path_key(path))
            if not 0 <= index < len(self._items):
                continue
            record = self._items[index]
            for variant in record.display_variants:
                self._meta_cache[variant.path] = self._format_meta_line(record, variant)
                self._capture_cache[variant.path] = self._format_capture_line(self.metadata_manager.get_cached(variant))
                self._meta_with_ai_cache.pop(variant.path, None)
                self._failed_paths.discard(variant.path)
            self._ai_result_cache.pop(record.path, None)
            dirty_indexes.add(index)

        if patch.selection_anchor is not None and 0 <= patch.selection_anchor < len(self._items):
            self._selection_anchor = patch.selection_anchor
        if not patch.preserve_pixmap_cache:
            self._clear_pixmap_cache(paths=changed_paths)

        if dirty_indexes:
            self._update_selection_tiles(dirty_indexes)
            self._schedule_visible_thumbnail_requests(immediate=True)

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

    def set_burst_groups(
        self,
        burst_groups_by_path: dict[str, BurstVisualInfo],
        burst_groups: list[tuple[int, ...]] | None = None,
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
        self._update_scrollbar()
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update()

    def set_burst_stack_mode(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self._burst_stack_mode == normalized:
            return
        self._burst_stack_mode = normalized
        self._normalize_burst_stack_selection()
        self._rebuild_visible_items()
        self._update_scrollbar()
        self._schedule_visible_thumbnail_requests(immediate=True)
        self.viewport().update()

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

    def set_review_insights(self, insights_by_path: dict[str, object]) -> None:
        self._review_insights_by_path = dict(insights_by_path)
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
        self._columns = max(1, min(8, columns))
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def set_action_mode(self, mode: str) -> None:
        normalized = mode if mode in {"normal", "accepted_only", "rejected_only", "recycle_only"} else "normal"
        if self._action_mode == normalized:
            return
        self._action_mode = normalized
        self.viewport().update()

    def set_tool_checkbox_mode(self, enabled: bool, *, clear_selection: bool = False) -> None:
        normalized = bool(enabled)
        changed = self._tool_checkbox_mode != normalized
        self._tool_checkbox_mode = normalized
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
        target = self._thumbnail_target_size()
        return self.thumbnail_manager.get_cached(self._current_variant(self._items[index]), target)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recalculate_metrics()
        self._update_scrollbar()
        self.viewport().update()
        self._schedule_visible_thumbnail_requests(immediate=True)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.viewport().update()
        self._schedule_visible_thumbnail_requests()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self.palette().color(QPalette.ColorRole.Base))

        if not self._items:
            self._paint_empty_state(painter)
            return

        target_size = self._thumbnail_target_size()
        for index in self._visible_indexes():
            rect = self._item_rect(index)
            if not rect.intersects(event.rect()):
                continue
            record = self._items[index]
            variant = self._current_variant(record)
            key = self.thumbnail_manager.make_key(variant, target_size)
            image = self.thumbnail_manager.get_cached(variant, target_size)
            pixmap = self._pixmap_for(key, image)
            self._paint_tile(painter, index, rect, record, pixmap)

        if self._marquee_active and not self._marquee_rect.isNull():
            overlay_fill = QColor(self._border_active)
            overlay_fill.setAlpha(46)
            overlay_border = QColor(self._border_active)
            overlay_border.setAlpha(185)
            painter.setPen(QPen(overlay_border, 1, Qt.PenStyle.DashLine))
            painter.setBrush(overlay_fill)
            painter.drawRect(self._marquee_rect)

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
            if self._winner_button_rect(rect).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self.winner_requested.emit(index)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if self._reject_button_rect(rect).contains(point):
                if self._tool_checkbox_mode:
                    self._set_current_index(index)
                else:
                    self._set_single_selection(index)
                self.reject_requested.emit(index)
                self._press_on_interactive_control = True
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return
            if self._tool_checkbox_mode:
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
            self.delete_requested.emit(index)
            return
        if key == Qt.Key.Key_K and review_shortcut_allowed:
            self.keep_requested.emit(index)
            return
        if key == Qt.Key.Key_M and review_shortcut_allowed:
            self.move_requested.emit(index)
            return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_5 and review_shortcut_allowed:
            self.rate_requested.emit(index, key - Qt.Key.Key_0)
            return
        if key == Qt.Key.Key_T and review_shortcut_allowed:
            self.tag_requested.emit(index)
            return
        if key == Qt.Key.Key_W and review_shortcut_allowed:
            self.winner_requested.emit(index)
            return
        if key == Qt.Key.Key_X and review_shortcut_allowed:
            self.reject_requested.emit(index)
            return
        super().keyPressEvent(event)

    def _handle_scroll_value_changed(self) -> None:
        self._schedule_visible_thumbnail_requests()

    def _handle_thumbnail_ready(self, key: ThumbnailKey, _image: QImage) -> None:
        self._failed_paths.discard(key.path)
        self._cache_pixmap(key, _image)
        target = self._thumbnail_target_size()
        if key.width != target.width() or key.height != target.height():
            return

        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return

        rect = self._item_rect(index)
        if rect.intersects(self.viewport().rect()):
            self.viewport().update(rect)

    def _handle_thumbnail_failed(self, key: ThumbnailKey, _message: str) -> None:
        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return
        self._failed_paths.add(key.path)
        rect = self._item_rect(index)
        if rect.intersects(self.viewport().rect()):
            self.viewport().update(rect)

    def _handle_metadata_ready(self, key: MetadataKey, metadata: CaptureMetadata) -> None:
        index = self._variant_path_to_index.get(key.path)
        if index is None:
            return
        self._capture_cache[key.path] = self._format_capture_line(metadata)
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
        painter.save()
        if is_rejected:
            border_color = self._reject_color
            background_color = QColor("#191317")
        elif is_winner:
            border_color = self._accepted_color
            background_color = QColor("#142018")
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
        painter.setPen(QPen(border_color, 2))
        painter.setBrush(background_color)
        painter.drawRoundedRect(QRectF(rect), 12, 12)

        image_rect = self._image_rect(rect)
        if burst_info is not None:
            if self._burst_stack_mode:
                self._paint_burst_stack_layers(painter, image_rect, highlighted=is_current or is_selected)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._placeholder_color)
        painter.drawRoundedRect(QRectF(image_rect), 10, 10)

        if pixmap is not None and not pixmap.isNull():
            draw_rect = QRect(0, 0, pixmap.width(), pixmap.height())
            draw_rect.moveCenter(image_rect.center())
            painter.drawPixmap(draw_rect, pixmap)
        elif variant.path in self._failed_paths:
            painter.setPen(self._failed_text_color)
            painter.setFont(self._placeholder_font)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "Failed")
        else:
            painter.setPen(self._placeholder_text_color)
            painter.setFont(self._placeholder_font)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "Loading...")

        if annotation and (annotation.rating or annotation.tags):
            badge = self._annotation_badge(annotation)
            badge_rect = QRect(image_rect.right() - 160, image_rect.bottom() - 30, 150, 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._badge_background)
            painter.drawRoundedRect(QRectF(badge_rect), 8, 8)
            painter.setPen(self._badge_text_color)
            painter.setFont(self._meta_font)
            painter.drawText(badge_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, badge)

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

        workflow_insight = self._workflow_insight_for(record)
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
        if workflow_insight is not None and getattr(workflow_insight, "best_in_group", False):
            self._paint_state_badge(
                painter,
                QRect(left_badge_x, left_badge_y, 94, 24),
                "Best Frame",
                self._workflow_best_badge_fill,
                self._workflow_best_badge_text,
            )
            left_badge_y += 30
        if workflow_insight is not None and getattr(workflow_insight, "disagreement_badge", ""):
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
        if record.has_edits:
            self._paint_state_badge(
                painter,
                QRect(image_rect.right() - 94, badge_y, 84, 24),
                "Edited",
                self._edited_badge_fill,
                self._edited_badge_text,
            )
            badge_y += 30

        if ai_result is not None and ai_result.is_top_pick:
            self._paint_state_badge(
                painter,
                QRect(image_rect.right() - 104, badge_y, 94, 24),
                "AI Pick",
                self._ai_pick_badge_fill,
                self._ai_pick_badge_text,
            )
            badge_y += 30

        if ai_result is not None and ai_result.confidence_bucket_short_label != "Review":
            fill, text = self._confidence_badge_palette(ai_result.confidence_bucket_short_label)
            self._paint_state_badge(
                painter,
                QRect(image_rect.right() - 116, badge_y, 106, 24),
                ai_result.confidence_bucket_short_label,
                fill,
                text,
            )

        if ai_result is not None:
            score_badge_rect = QRect(image_rect.right() - 92, image_rect.bottom() - 30, 82, 24)
            self._paint_state_badge(
                painter,
                score_badge_rect,
                f"AI {ai_result.display_score_text}",
                self._ai_score_badge_fill,
                self._ai_score_badge_text,
            )

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

        painter.setPen(self._title_color)
        painter.setFont(self._title_font)
        title_option = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title_option.setWrapMode(QTextOption.WrapMode.WordWrap)
        painter.drawText(QRectF(title_text_rect), variant.name if record.has_variant_stack else record.name, title_option)

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
            parts.append(f"{annotation.rating}/5")
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
        capture = self._capture_cache.get(variant.path, "")
        if capture or variant.path == record.path:
            return capture
        return self._capture_cache.get(record.path, "")

    def _format_capture_line(self, metadata: CaptureMetadata | None) -> str:
        if metadata is None:
            return ""
        return metadata.summary

    def _format_meta_line(self, record: ImageRecord, variant: ImageVariant | None = None) -> str:
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
        if workflow_insight is not None and getattr(workflow_insight, "summary_text", ""):
            parts.append(getattr(workflow_insight, "summary_text", ""))
        if ai_result is not None:
            ai_parts = [f"AI {ai_result.display_score_text}", ai_result.confidence_bucket_label]
            if ai_result.group_id:
                ai_parts.append(ai_result.group_id)
            if ai_result.group_size > 1:
                ai_parts.append(ai_result.rank_text)
            parts.extend(part for part in ai_parts if part)
        return "  |  ".join(part for part in parts if part)

    def _review_insight_for(self, record: ImageRecord):
        return self._review_insights_by_path.get(record.path) or self._review_insights_by_path.get(normalized_path_key(record.path))

    def _workflow_insight_for(self, record: ImageRecord):
        return self._workflow_insights_by_path.get(record.path) or self._workflow_insights_by_path.get(normalized_path_key(record.path))

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
        return QColor(118, 54, 48, 220), QColor("#fff0ee")

    def _workflow_disagreement_palette(self, level: str) -> tuple[QColor, QColor]:
        if level == "strong":
            return self._workflow_miss_badge_fill, self._workflow_miss_badge_text
        return self._workflow_review_badge_fill, self._workflow_review_badge_text

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
                self._ai_result_cache[item.path] = result
                return result
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
        return QRect(
            tile_rect.x() + 12,
            tile_rect.y() + self._image_padding + self._image_height() + 8,
            tile_rect.width() - 24,
            self._caption_height,
        )

    def _image_rect(self, tile_rect: QRect) -> QRect:
        return QRect(
            tile_rect.x() + self._image_padding,
            tile_rect.y() + self._image_padding,
            tile_rect.width() - (self._image_padding * 2),
            self._image_height(),
        )

    def _capture_rect(self, tile_rect: QRect) -> QRect:
        title_rect = self._title_rect(tile_rect)
        return QRect(
            tile_rect.x() + 12,
            title_rect.bottom() + 2,
            tile_rect.width() - 24,
            self._capture_height,
        )

    def _meta_rect(self, tile_rect: QRect) -> QRect:
        capture_rect = self._capture_rect(tile_rect)
        return QRect(
            tile_rect.x() + 12,
            capture_rect.bottom() + 2,
            tile_rect.width() - 24,
            self._meta_height,
        )

    def _action_rect(self, tile_rect: QRect) -> QRect:
        meta_rect = self._meta_rect(tile_rect)
        return QRect(
            tile_rect.x() + 12,
            meta_rect.bottom() + 6,
            tile_rect.width() - 24,
            self._action_height,
        )

    def _winner_button_rect(self, tile_rect: QRect) -> QRect:
        if self._action_mode in {"rejected_only", "recycle_only"}:
            return QRect()
        action_rect = self._action_rect(tile_rect)
        rect = QRect(0, 0, self._winner_button_size.width(), self._winner_button_size.height())
        rect.moveLeft(action_rect.left())
        rect.moveTop(action_rect.top() + max(0, (action_rect.height() - rect.height()) // 2))
        return rect

    def _reject_button_rect(self, tile_rect: QRect) -> QRect:
        if self._action_mode in {"accepted_only", "recycle_only"}:
            return QRect()
        action_rect = self._action_rect(tile_rect)
        rect = QRect(0, 0, self._winner_button_size.width(), self._winner_button_size.height())
        if self._action_mode == "rejected_only":
            rect.moveLeft(action_rect.left())
        else:
            rect.moveRight(action_rect.right())
        rect.moveTop(action_rect.top() + max(0, (action_rect.height() - rect.height()) // 2))
        return rect

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
            self._update_scrollbar()
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
        self._visible_slot_by_item_index = {
            item_index: slot
            for slot, item_index in enumerate(self._visible_item_indexes)
        }

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

    def _update_scrollbar(self) -> None:
        rows = self._row_count()
        total_height = self._margin * 2
        if rows:
            total_height += rows * self._tile_height() + max(0, rows - 1) * self._spacing
        max_value = max(0, total_height - self.viewport().height())
        self.verticalScrollBar().setRange(0, max_value)
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.verticalScrollBar().setSingleStep(max(40, self._row_height() // 4))

    def _request_visible_thumbnails(self) -> None:
        if not self._items:
            return

        target = self._thumbnail_target_size()
        visible = self._visible_indexes()
        if not visible:
            return

        center = (visible[0] + visible[-1]) // 2
        for index in visible:
            distance = abs(index - center)
            priority = max(1, 10_000 - distance)
            variant = self._current_variant(self._items[index])
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
        available = max(320, self.viewport().width() - (self._margin * 2) - ((self._columns - 1) * self._spacing))
        minimum_tile_width = 220 if self._columns <= 4 else 120
        minimum_image_height = 180 if self._columns <= 4 else 90
        self._tile_width_value = max(minimum_tile_width, available // self._columns)
        self._image_height_value = max(minimum_image_height, int(self._tile_width_value * 0.72))
        self._tile_height_value = (
            self._image_padding * 2
            + self._image_height_value
            + self._caption_height
            + self._action_height
            + self._capture_height
            + self._meta_height
            + 20
        )
        self._row_height_value = self._tile_height_value + self._spacing
        self._thumbnail_target_size_value = QSize(
            max(64, self._tile_width_value - (self._image_padding * 2)),
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
        x = self._margin + column * (self._tile_width() + self._spacing)
        y = self._margin + row * (self._tile_height() + self._spacing)
        return QRect(x, y, self._tile_width(), self._tile_height())

    def _item_rect(self, index: int) -> QRect:
        rect = self._content_rect(index)
        rect.translate(0, -self.verticalScrollBar().value())
        return rect

    def _index_at(self, x: int, y: int) -> int:
        content_y = y + self.verticalScrollBar().value()
        if x < self._margin:
            return -1
        tile_width = self._tile_width()
        tile_height = self._tile_height()
        column_span = tile_width + self._spacing
        row_span = tile_height + self._spacing

        column = (x - self._margin) // column_span
        row = (content_y - self._margin) // row_span
        if column < 0 or column >= self._columns or row < 0:
            return -1

        x_in_tile = (x - self._margin) % column_span
        y_in_tile = (content_y - self._margin) % row_span
        if x_in_tile >= tile_width or y_in_tile >= tile_height:
            return -1

        slot = row * self._columns + column
        if slot >= len(self._visible_item_indexes):
            return -1
        return self._visible_item_indexes[slot]
