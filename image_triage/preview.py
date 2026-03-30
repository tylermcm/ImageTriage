from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue

from PySide6.QtCore import QEvent, QPoint, QRect, QRunnable, QSize, QSignalBlocker, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .ai_results import AIImageResult
from .imaging import load_image_for_display
from .metadata import CaptureMetadata, EMPTY_METADATA, load_capture_metadata
from .models import ImageRecord, JPEG_SUFFIXES
from .scanner import discover_edited_paths
from .ui.theme import ThemePalette, default_theme

COMPARE_COUNTS = (2, 3, 5, 7, 9)


@dataclass(slots=True, frozen=True)
class PreviewRequest:
    path: str
    token: int
    slot: int
    target_size: QSize
    prefer_embedded: bool = False
    load_metadata: bool = True


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
        )
        if image.isNull():
            self.result_queue.put(
                ("failed", self.request.token, self.request.slot, self.request.path, error or "Could not decode image.")
            )
            return
        metadata = load_capture_metadata(self.request.path) if self.request.load_metadata else None
        self.result_queue.put(("ready", self.request.token, self.request.slot, self.request.path, image, metadata))


class PreviewPane(QWidget):
    HEART_SYMBOL = "\u2665"
    HEART_OUTLINE_SYMBOL = "\u2661"
    REJECT_SYMBOL = "\u2715"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("previewPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = default_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._apply_style(False)

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

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
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
        layout.addWidget(footer)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.scroll_area.setStyleSheet(f"background-color: {theme.image_bg.css};")
        self.image_label.setStyleSheet(f"background-color: {theme.image_bg.css};")
        self.caption_label.setStyleSheet(f"font-size: 12px; color: {theme.text_primary.css};")
        self.metadata_label.setStyleSheet(f"font-size: 11px; color: {theme.text_secondary.css};")
        self.heart_button.setStyleSheet(self._badge_button_style(theme, theme.danger))
        self.reject_button.setStyleSheet(self._badge_button_style(theme, theme.danger))
        self._apply_style(False)

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
        self._apply_style(active)

    def _apply_style(self, active: bool) -> None:
        if active:
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


class FullScreenPreview(QDialog):
    navigation_requested = Signal(int)
    compare_mode_changed = Signal(bool)
    auto_bracket_mode_changed = Signal(bool)
    compare_count_changed = Signal(int)
    photoshop_requested = Signal(str)
    winner_requested = Signal(str)
    reject_requested = Signal(str)
    keep_requested = Signal(str)
    delete_requested = Signal(str)
    move_requested = Signal(str)
    rate_requested = Signal(str, int)
    tag_requested = Signal(str)

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
        self._compare_mode = False
        self._before_after_enabled = False
        self._compare_count = 3
        self._edited_variant_index = 0
        self._focused_slot = 0
        self._photoshop_available = False
        self._manual_zoom = False
        self._zoom_scale = 1.0
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
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._result_queue: SimpleQueue = SimpleQueue()
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(12)
        self._drain_timer.timeout.connect(self._drain_results)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1200)
        self._refresh_timer.timeout.connect(self._poll_source_updates)
        self._panes: list[PreviewPane] = []
        self._watched_widgets: dict[object, int] = {}
        self._theme = default_theme()

        self.setWindowTitle("Preview")
        self.setModal(False)
        self.setStyleSheet("background-color: #111; color: white;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(12)

        self.header_widget = QWidget()
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

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
        self.compare_count_combo.currentIndexChanged.connect(self._handle_compare_count_changed)
        self.compare_count_combo.setEnabled(False)

        self.photoshop_button = QPushButton("Open In Photoshop")
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

        header_layout.addStretch(1)
        header_layout.addWidget(self.compare_toggle_button)
        header_layout.addWidget(self.auto_bracket_button)
        header_layout.addWidget(self.before_after_button)
        header_layout.addWidget(self.next_edit_button)
        header_layout.addWidget(self.photoshop_button)
        header_layout.addWidget(self.compare_count_combo)

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

        self.info_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet("font-size: 14px; color: #ddd;")
        self.info_label.setWordWrap(True)

        root_layout.addWidget(self.header_widget)
        root_layout.addWidget(self.content_widget, 1)
        root_layout.addWidget(self.info_label)
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

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(f"background-color: {theme.image_bg.css}; color: {theme.text_primary.css};")
        self.header_widget.setStyleSheet(
            f"background-color: {theme.panel_bg.css}; border: 1px solid {theme.border.css}; border-radius: 12px;"
        )
        self.content_widget.setStyleSheet("background-color: transparent;")
        self.info_label.setStyleSheet(f"font-size: 14px; color: {theme.text_secondary.css};")
        self.compare_toggle_button.setStyleSheet(self._toggle_button_style())
        self.auto_bracket_button.setStyleSheet(self._toggle_button_style())
        self.before_after_button.setStyleSheet(self._toggle_button_style())
        self.photoshop_button.setStyleSheet(self._action_button_style())
        self.next_edit_button.setStyleSheet(self._action_button_style())
        self.compare_count_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {theme.input_bg.css};
                border: 1px solid {theme.border.css};
                border-radius: 8px;
                color: {theme.text_primary.css};
                padding: 4px 8px;
            }}
            QComboBox:disabled {{
                color: {theme.text_disabled.css};
            }}
            """
        )
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
        if enabled and self._before_after_enabled:
            self._before_after_enabled = False
            with QSignalBlocker(self.before_after_button):
                self.before_after_button.setChecked(False)
        self.compare_count_combo.setEnabled(enabled)
        with QSignalBlocker(self.compare_toggle_button):
            self.compare_toggle_button.setChecked(enabled)
        self._rebuild_entries()
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

    def set_photoshop_available(self, available: bool) -> None:
        self._photoshop_available = available
        self.photoshop_button.setEnabled(available)
        if available:
            self.photoshop_button.setText("Open In Photoshop")
        else:
            self.photoshop_button.setText("Photoshop Not Found")

    def show_entries(self, entries: list[PreviewEntry]) -> None:
        self._source_entries = list(entries)
        self._focused_slot = 0
        self._manual_zoom = False
        self._zoom_scale = 1.0
        self._dragging = False
        self._pending_right_close = False
        self._edited_variant_index = 0
        self._rebuild_entries()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._refresh_timer.start()
        QTimer.singleShot(25, self._request_preview_loads)

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
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.close()
            event.accept()
            return
        if key == Qt.Key.Key_Tab and self._compare_mode and len(self._entries) > 1:
            step = -1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self._set_focused_slot((self._focused_slot + step) % len(self._entries))
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
        if key == Qt.Key.Key_0:
            self._set_fit_mode()
            event.accept()
            return
        if key == Qt.Key.Key_W:
            path = self._focused_path()
            if path:
                self.winner_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_X:
            path = self._focused_path()
            if path:
                self.reject_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_K:
            path = self._focused_path()
            if path:
                self.keep_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_Delete:
            path = self._focused_path()
            if path:
                self.delete_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_M:
            path = self._focused_path()
            if path:
                self.move_requested.emit(path)
                event.accept()
                return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_5:
            path = self._focused_path()
            if path:
                self.rate_requested.emit(path, key - Qt.Key.Key_0)
                event.accept()
                return
        if key == Qt.Key.Key_T:
            path = self._focused_path()
            if path:
                self.tag_requested.emit(path)
                event.accept()
                return
        if key == Qt.Key.Key_C:
            self.compare_mode_changed.emit(not self._compare_mode)
            event.accept()
            return
        super().keyPressEvent(event)

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
        self._request_preview_loads()

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
        self._current_images = [QImage() for _ in self._entries]
        self._current_metadata = [self._metadata_cache.get(entry.source_path, EMPTY_METADATA) for entry in self._entries]
        self._source_versions = [_file_signature(entry.source_path) for entry in self._entries]
        self._pending_requests = 0
        self._focused_slot = min(self._focused_slot, max(0, len(self._entries) - 1))
        self._update_before_after_controls()
        self._update_layout()
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
            ),
        ]

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

    def _update_layout(self) -> None:
        visible_count = max(1, len(self._entries))
        self._ensure_panes(visible_count)

        while self.panes_layout.count():
            item = self.panes_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()

        columns = self._column_count_for(visible_count)
        for index, pane in enumerate(self._panes[:visible_count]):
            row = index // columns
            column = index % columns
            self.panes_layout.addWidget(pane, row, column)
            pane.caption_label.setVisible((self._compare_mode and visible_count > 1) or self._before_after_enabled)
            pane.set_active(index == self._focused_slot)
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
            self._pending_requests += 1
            task = PreviewTask(
                PreviewRequest(
                    path=entry.source_path,
                    token=token,
                    slot=slot,
                    target_size=self._decode_target_size(slot),
                    prefer_embedded=not self._manual_zoom,
                    load_metadata=force_metadata or entry.source_path not in self._metadata_cache,
                ),
                self._result_queue,
            )
            self._pool.start(task)
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def _drain_results(self) -> None:
        processed = 0
        while processed < 16:
            try:
                item = self._result_queue.get_nowait()
            except Empty:
                break

            state, token, slot, path, *payload = item

            if token == self._load_token and self._pending_requests > 0:
                self._pending_requests -= 1
            if token == self._load_token and 0 <= slot < len(self._entries) and path == self._entries[slot].source_path:
                if state == "ready":
                    image = payload[0]
                    metadata = payload[1] if len(payload) > 1 else None
                    self._current_images[slot] = image
                    if slot < len(self._source_versions):
                        self._source_versions[slot] = _file_signature(path)
                    if metadata is not None:
                        self._metadata_cache[path] = metadata
                        self._current_metadata[slot] = metadata
                    self._render_pane(slot)
                else:
                    if self._current_images[slot].isNull():
                        self._show_failed(slot, payload[0])
            processed += 1

        if processed == 0 and self._pending_requests == 0:
            self._drain_timer.stop()

    def _render_all(self) -> None:
        for slot in range(len(self._entries)):
            self._render_pane(slot)
        self._update_focus_styles()
        self._update_cursor()
        self._update_info_label()

    def _poll_source_updates(self) -> None:
        if not self.isVisible() or not self._entries or self._pending_requests > 0:
            return

        if not self._compare_mode and len(self._source_entries) == 1:
            source_entry = self._source_entries[0]
            if not self._edited_candidates_for_entry(source_entry):
                discovered = discover_edited_paths(source_entry.record)
                if discovered:
                    self.set_edited_candidates(source_entry.record.path, tuple(discovered))

        changed_slots: list[int] = []
        for slot, entry in enumerate(self._entries):
            current_signature = self._source_versions[slot] if slot < len(self._source_versions) else None
            latest_signature = _file_signature(entry.source_path)
            if latest_signature is None or latest_signature == current_signature:
                continue
            self._metadata_cache.pop(entry.source_path, None)
            changed_slots.append(slot)

        if changed_slots:
            self._request_preview_loads(changed_slots, force_metadata=True)

    def _render_pane(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        pane = self._panes[slot]
        entry = self._entries[slot]
        image = self._current_images[slot]
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

        if image.isNull():
            pane.image_label.resize(1, 1)
            pane.image_label.clear()
            pane.image_label.setText("Loading full preview...")
            return

        if self._manual_zoom:
            scaled_size = QSize(
                max(1, int(round(image.width() * self._zoom_scale))),
                max(1, int(round(image.height() * self._zoom_scale))),
            )
            if scaled_size == image.size():
                pixmap = QPixmap.fromImage(image)
            else:
                pixmap = QPixmap.fromImage(
                    image.scaled(
                        scaled_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            pane.image_label.setPixmap(pixmap)
            pane.image_label.resize(pixmap.size())
        else:
            target = self._fit_target_size(pane)
            pixmap = QPixmap.fromImage(
                image.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            pane.image_label.setPixmap(pixmap)
            pane.image_label.resize(pixmap.size())
            pane.scroll_area.horizontalScrollBar().setValue(0)
            pane.scroll_area.verticalScrollBar().setValue(0)

        if self._manual_zoom:
            self._apply_zoom_to_all()

    def _show_failed(self, slot: int, message: str) -> None:
        if not 0 <= slot < len(self._entries):
            return
        pane = self._panes[slot]
        pane.image_label.resize(1, 1)
        pane.image_label.clear()
        pane.image_label.setText(f"Failed\n{message}")

    def _fit_target_size(self, pane: PreviewPane) -> QSize:
        viewport = pane.scroll_area.viewport().size()
        width = max(1, viewport.width() - 8)
        height = max(1, viewport.height() - 8)
        return QSize(width, height)

    def _decode_target_size(self, slot: int) -> QSize:
        if self._manual_zoom and not self._compare_mode and slot == self._focused_slot:
            return QSize()
        if 0 <= slot < len(self._panes):
            fit_target = self._fit_target_size(self._panes[slot])
        else:
            fit_target = QSize(max(1, self.width()), max(1, self.height()))
        if fit_target.width() < 400 or fit_target.height() < 400:
            window_size = self.size()
            if window_size.width() < 400 or window_size.height() < 400:
                screen = self.screen()
                if screen is not None:
                    window_size = screen.availableGeometry().size()
            columns = 2 if self._before_after_enabled and len(self._entries) == 2 else max(1, self._column_count_for(max(1, len(self._entries))))
            fit_target = QSize(
                max(1, int(window_size.width() / columns) - 48),
                max(1, window_size.height() - 180),
            )
        return QSize(
            min(4096, max(1, fit_target.width() * 2)),
            min(4096, max(1, fit_target.height() * 2)),
        )

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
        if entered_manual and self._entries:
            target_slots = list(range(len(self._entries))) if (self._before_after_enabled or self._compare_mode) else [self._focused_slot]
            self._request_preview_loads(target_slots)

    def _handle_wheel_zoom(self, delta: int) -> None:
        if delta == 0 or not self._entries:
            return
        current_scale = self._zoom_scale if self._manual_zoom else self._fit_scale_threshold()
        step = 1.15 if delta > 0 else 1 / 1.15
        self._set_manual_zoom(current_scale * step)

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
        self._update_info_label()

    def _update_focus_styles(self) -> None:
        for index, pane in enumerate(self._panes[: len(self._entries)]):
            pane.set_active(index == self._focused_slot)

    def _handle_heart_clicked(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        self._set_focused_slot(slot)
        self.winner_requested.emit(self._entries[slot].record.path)

    def _handle_reject_clicked(self, slot: int) -> None:
        if not 0 <= slot < len(self._entries):
            return
        self._set_focused_slot(slot)
        self.reject_requested.emit(self._entries[slot].record.path)

    def _focused_path(self) -> str:
        if not 0 <= self._focused_slot < len(self._entries):
            return ""
        return self._entries[self._focused_slot].record.path

    def _focused_photoshop_path(self) -> str:
        if not 0 <= self._focused_slot < len(self._entries):
            return ""
        entry = self._entries[self._focused_slot]
        if entry.source_path:
            return entry.source_path
        return entry.record.path

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
        self.info_label.setText(
            f"{prefix}  |  {mode}{focus_hint}{loupe_hint}  |  {nav_hint}, wheel/Z zoom, L loupe, Alt+L cycle loupe, drag to pan, Tab focus, W/X/K/Delete/M/0-5/T actions, C compare, 0 to fit"
        )


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
    if ai_result.group_id:
        parts.append(ai_result.group_id)
    if ai_result.group_size > 1:
        parts.append(ai_result.rank_text)
        if ai_result.is_top_pick:
            parts.append("recommended keeper")
    return "  |  ".join(part for part in parts if part)
