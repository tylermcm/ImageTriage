from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .ai_results import AIBundle, AIImageResult, find_ai_result_for_record
from .cache import ThumbnailKey
from .models import ImageRecord, SessionAnnotation
from .thumbnails import ThumbnailManager


@dataclass(slots=True, frozen=True)
class ClusterCardItem:
    index: int
    record: ImageRecord
    ai_result: AIImageResult | None
    normalized_score: float | None


class AIImageCard(QFrame):
    clicked = Signal(int)
    activated = Signal(int)
    context_menu_requested = Signal(int, object)

    def __init__(self, item: ClusterCardItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._selected = False
        self._annotation = SessionAnnotation()
        self._source_pixmap = QPixmap()
        self.setObjectName("aiImageCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.image_label = QLabel("Loading preview...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(160)
        self.image_label.setStyleSheet(
            """
            QLabel {
                background-color: #090c11;
                border-radius: 10px;
                color: #aab7ca;
                padding: 10px;
            }
            """
        )
        layout.addWidget(self.image_label)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(6)
        self.rank_badge = self._build_badge("")
        self.top_pick_badge = self._build_badge("Model Top Pick", visible=False)
        self.annotation_badge = self._build_badge("", visible=False)
        badge_row.addWidget(self.rank_badge)
        badge_row.addWidget(self.top_pick_badge)
        badge_row.addWidget(self.annotation_badge)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

        self.name_label = QLabel(self.item.ai_result.file_name if self.item.ai_result else self.item.record.name)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("color: #f4f7fb; font-size: 15px; font-weight: 700;")
        layout.addWidget(self.name_label)

        self.score_label = QLabel("")
        self.score_label.setStyleSheet("color: #dce8ff; font-size: 13px;")
        layout.addWidget(self.score_label)

        self.timestamp_label = QLabel("")
        self.timestamp_label.setWordWrap(True)
        self.timestamp_label.setStyleSheet("color: #c6d2e0; font-size: 12px;")
        layout.addWidget(self.timestamp_label)

        self.path_label = QLabel(self.item.record.name)
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color: #98a6bb; font-size: 12px;")
        layout.addWidget(self.path_label)

        layout.addStretch(1)
        self._refresh_text()
        self._apply_style()

    def sizeHint(self) -> QSize:
        return QSize(250, 340)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def set_annotation(self, annotation: SessionAnnotation | None) -> None:
        self._annotation = annotation or SessionAnnotation()
        if self._annotation.winner:
            self.annotation_badge.setText("Accepted")
            self.annotation_badge.setVisible(True)
            self.annotation_badge.setStyleSheet(self._badge_style("#1c5c38", "#e8fff1"))
        elif self._annotation.reject:
            self.annotation_badge.setText("Rejected")
            self.annotation_badge.setVisible(True)
            self.annotation_badge.setStyleSheet(self._badge_style("#6e1c2a", "#ffe8ea"))
        else:
            self.annotation_badge.setVisible(False)

    def set_thumbnail(self, image: QImage) -> None:
        if image.isNull():
            self.image_label.setText("Preview unavailable")
            self._source_pixmap = QPixmap()
            return
        self._source_pixmap = QPixmap.fromImage(image)
        self._update_thumbnail_pixmap()

    def clear_thumbnail(self, message: str = "Preview unavailable") -> None:
        self._source_pixmap = QPixmap()
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(message)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        target_height = max(150, min(240, int((self.width() - 24) * 0.62)))
        self.image_label.setMinimumHeight(target_height)
        self.image_label.setMaximumHeight(target_height)
        self._update_thumbnail_pixmap()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item.index)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.item.index)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu_requested.emit(self.item.index, event.globalPos())
        event.accept()

    def _refresh_text(self) -> None:
        ai_result = self.item.ai_result
        if ai_result is None:
            self.rank_badge.setText("No AI")
            self.top_pick_badge.setVisible(False)
            self.score_label.setText("score: unavailable")
            self.timestamp_label.setText("")
            self.path_label.setText(self.item.record.name)
            self.setToolTip(self.item.record.path)
            return

        self.rank_badge.setText(f"Rank #{ai_result.rank_in_group}")
        self.top_pick_badge.setVisible(ai_result.is_top_pick)
        score_text = "score: unavailable"
        if ai_result.normalized_score is not None:
            score_text = f"score: {ai_result.display_score_with_scale_text}"
        self.score_label.setText(score_text)
        timestamp = ai_result.capture_timestamp.strip()
        self.timestamp_label.setText(f"timestamp: {timestamp}" if timestamp else "")
        self.path_label.setText(ai_result.file_path)
        self.setToolTip(f"Raw score: {ai_result.score:.4f}\nPath: {ai_result.file_path}")

    def _update_thumbnail_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            return
        target_size = self.image_label.contentsRect().size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return
        scaled = self._source_pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.setText("")

    def _apply_style(self) -> None:
        border = "#63a0ff" if self._selected else "#2b3442"
        background = "#171f2c" if self._selected else "#14181f"
        self.setStyleSheet(
            f"""
            QFrame#aiImageCard {{
                background-color: {background};
                border: 2px solid {border};
                border-radius: 14px;
            }}
            """
        )

    def _build_badge(self, text: str, *, visible: bool = True) -> QLabel:
        badge = QLabel(text)
        badge.setVisible(visible)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(self._badge_style("#1d5aa3", "#f4f8ff"))
        return badge

    def _badge_style(self, background: str, foreground: str) -> str:
        return (
            "QLabel {"
            f"background-color: {background};"
            f"color: {foreground};"
            "border-radius: 11px;"
            "padding: 4px 10px;"
            "font-size: 12px;"
            "font-weight: 700;"
            "}"
        )


class AIClusterSection(QFrame):
    def __init__(self, title: str, subtitle: str, cards: list[AIImageCard], parent=None) -> None:
        super().__init__(parent)
        self._cards = cards
        self._current_columns = 0
        self.setObjectName("aiClusterSection")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            """
            QFrame#aiClusterSection {
                background-color: #11161f;
                border: 1px solid #2b3442;
                border-radius: 16px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #f2f6fb; font-size: 28px; font-weight: 700;")
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet("color: #c4d0df; font-size: 13px;")
        layout.addWidget(self.subtitle_label)

        self.cards_widget = QWidget()
        self.cards_layout = QGridLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(14)
        self.cards_layout.setVerticalSpacing(14)
        layout.addWidget(self.cards_widget)
        self._reflow_cards()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow_cards()

    def set_current_index(self, index: int) -> None:
        for card in self._cards:
            card.set_selected(card.item.index == index)

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        for card in self._cards:
            card.set_annotation(annotations.get(card.item.record.path))

    def _reflow_cards(self) -> None:
        available = max(1, self.cards_widget.width())
        columns = max(1, min(6, available // 250))
        if columns == self._current_columns and self.cards_layout.count() == len(self._cards):
            return
        self._current_columns = columns
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.cards_widget)
        for idx, card in enumerate(self._cards):
            row = idx // columns
            column = idx % columns
            self.cards_layout.addWidget(card, row, column)
        for column in range(columns):
            self.cards_layout.setColumnStretch(column, 1)


class AIClusterView(QScrollArea):
    current_requested = Signal(int)
    preview_requested = Signal(int)
    context_menu_requested = Signal(int, object)

    def __init__(self, thumbnail_manager: ThumbnailManager, parent=None) -> None:
        super().__init__(parent)
        self.thumbnail_manager = thumbnail_manager
        self.thumbnail_manager.thumbnail_ready.connect(self._handle_thumbnail_ready)
        self.thumbnail_manager.thumbnail_failed.connect(self._handle_thumbnail_failed)

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: #0f141b;")

        self._cards_by_index: dict[int, AIImageCard] = {}
        self._cards_by_key: dict[ThumbnailKey, list[AIImageCard]] = {}
        self._sections: list[AIClusterSection] = []
        self._current_index = -1

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(20, 20, 20, 20)
        self._content_layout.setSpacing(18)
        self._empty_label = QLabel("Run AI culling or load a saved AI report to see grouped ranking results here.")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #c1cddd; font-size: 16px; padding: 48px;")
        self._content_layout.addWidget(self._empty_label)
        self._content_layout.addStretch(1)
        self.setWidget(self._content)

    def clear(self, message: str) -> None:
        self._cards_by_index.clear()
        self._cards_by_key.clear()
        self._sections.clear()
        self._current_index = -1
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._empty_label = QLabel(message)
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #c1cddd; font-size: 16px; padding: 48px;")
        self._content_layout.addWidget(self._empty_label)
        self._content_layout.addStretch(1)

    def set_records(
        self,
        records: list[ImageRecord],
        annotations: dict[str, SessionAnnotation],
        bundle: AIBundle | None,
    ) -> None:
        if bundle is None:
            self.clear("Load AI results to switch this viewport into grouped ranking view.")
            return

        ordered_groups: list[str] = []
        grouped_items: dict[str, list[ClusterCardItem]] = {}
        unmatched_items: list[ClusterCardItem] = []
        self._cards_by_key.clear()
        self._cards_by_index.clear()
        self._sections.clear()

        for index, record in enumerate(records):
            ai_result = find_ai_result_for_record(bundle, record)
            normalized_score = bundle.normalized_score_for_result(ai_result) if ai_result is not None else None
            item = ClusterCardItem(index=index, record=record, ai_result=ai_result, normalized_score=normalized_score)
            if ai_result is None:
                unmatched_items.append(item)
                continue
            if ai_result.group_id not in grouped_items:
                grouped_items[ai_result.group_id] = []
                ordered_groups.append(ai_result.group_id)
            grouped_items[ai_result.group_id].append(item)

        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not ordered_groups and not unmatched_items:
            self.clear("The current folder has no AI-ranked images to display in grouped view.")
            return

        for group_id in ordered_groups:
            items = grouped_items[group_id]
            items.sort(
                key=lambda item: (
                    item.ai_result.rank_in_group if item.ai_result is not None else 999999,
                    -(item.ai_result.score if item.ai_result is not None else 0.0),
                    item.record.name.casefold(),
                )
            )
            cards = [self._build_card(item, annotations) for item in items]
            top_pick = next((item.ai_result.file_name for item in items if item.ai_result and item.ai_result.is_top_pick), "")
            top_result = items[0].ai_result if items else None
            group_size = top_result.group_size if top_result is not None else len(items)
            visible_count = len(items)
            subtitle_parts = [f"size {group_size}"]
            if visible_count != group_size:
                subtitle_parts.append(f"showing {visible_count} visible")
            if top_result and top_result.cluster_reason:
                subtitle_parts.append(f"reason: {top_result.cluster_reason}")
            if top_pick:
                subtitle_parts.append(f"top pick: {top_pick}")
            section = AIClusterSection(group_id, " | ".join(subtitle_parts), cards)
            section.set_annotations(annotations)
            section.set_current_index(self._current_index)
            self._sections.append(section)
            self._content_layout.addWidget(section)

        if unmatched_items:
            cards = [self._build_card(item, annotations) for item in unmatched_items]
            section = AIClusterSection(
                "unmatched",
                f"{len(unmatched_items)} visible image(s) have no AI-ranked export row.",
                cards,
            )
            section.set_annotations(annotations)
            section.set_current_index(self._current_index)
            self._sections.append(section)
            self._content_layout.addWidget(section)

        self._content_layout.addStretch(1)
        self.set_current_index(self._current_index)

    def set_current_index(self, index: int) -> None:
        self._current_index = index
        selected_card = self._cards_by_index.get(index)
        for section in self._sections:
            section.set_current_index(index)
        if selected_card is not None:
            self.ensureWidgetVisible(selected_card, 24, 24)

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        for section in self._sections:
            section.set_annotations(annotations)

    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)

    def _build_card(self, item: ClusterCardItem, annotations: dict[str, SessionAnnotation]) -> AIImageCard:
        card = AIImageCard(item)
        card.set_annotation(annotations.get(item.record.path))
        card.clicked.connect(self.current_requested.emit)
        card.activated.connect(self.preview_requested.emit)
        card.context_menu_requested.connect(self.context_menu_requested.emit)
        self._cards_by_index[item.index] = card
        key = self.thumbnail_manager.request_thumbnail(item.record, self._thumbnail_target_size())
        self._cards_by_key.setdefault(key, []).append(card)
        return card

    def _thumbnail_target_size(self) -> QSize:
        return QSize(640, 420)

    def _handle_thumbnail_ready(self, key: ThumbnailKey, image: QImage) -> None:
        cards = self._cards_by_key.get(key)
        if not cards:
            return
        for card in cards:
            card.set_thumbnail(image)

    def _handle_thumbnail_failed(self, key: ThumbnailKey, _message: str) -> None:
        cards = self._cards_by_key.get(key)
        if not cards:
            return
        for card in cards:
            card.clear_thumbnail()
