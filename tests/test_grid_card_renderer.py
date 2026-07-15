from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.ui.grid_card_renderer import (
    DETAILED_CARD_REFERENCE_HEIGHT,
    DETAILED_CARD_REFERENCE_WIDTH,
    GridCardData,
    grid_card_action_rects,
    grid_card_height_for_width,
    paint_grid_card,
    render_grid_card_pixmap,
    _metadata_text_top,
    _right_stack_vertical_positions,
    _scale_for,
)


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


class GridCardRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()

    def test_bottom_scrim_keeps_top_bright_and_bottom_dark(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(230, 230, 230))

        card = render_grid_card_pixmap(
            QSize(560, 330),
            source,
            GridCardData(duplicate_visible=False, ai_visible=False, immersive=True),
        ).toImage()

        image_top = 7
        image_height = 316
        image_bottom = image_top + image_height - 1
        x = card.width() // 2

        upper = _luminance(card.pixelColor(x, image_top + round(image_height * 0.18)))
        mid = _luminance(card.pixelColor(x, image_top + round(image_height * 0.65)))
        lower = _luminance(card.pixelColor(x, image_bottom - 4))

        self.assertGreater(upper, 205)
        self.assertGreater(mid, lower)
        self.assertLess(lower, 60)

    def test_compact_card_renders_at_small_sizes(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(120, 130, 140))

        for size in (QSize(300, 218), QSize(180, 131), QSize(120, 87)):
            card = render_grid_card_pixmap(
                QSize(size),
                source,
                GridCardData(selected=True, favorite=True),
                compact=True,
            )
            self.assertFalse(card.isNull())
            self.assertEqual(card.size(), size)

    def test_action_rects_stay_inside_card_for_both_layouts(self) -> None:
        for width, height, compact in ((560, 407, False), (385, 280, False), (300, 218, True), (180, 131, True)):
            rect = QRect(0, 0, width, height)
            hits = grid_card_action_rects(rect, compact=compact)
            for name, button in (("favorite", hits.favorite), ("reject", hits.reject)):
                with self.subTest(width=width, compact=compact, button=name):
                    self.assertTrue(button.isValid())
                    self.assertTrue(rect.contains(button), f"{button} outside {rect}")
        self.assertLess(
            grid_card_action_rects(QRect(0, 0, 300, 218), compact=True).favorite.right(),
            grid_card_action_rects(QRect(0, 0, 300, 218), compact=True).reject.left(),
        )

    def test_compact_buttons_pin_to_bottom_corners_with_equal_padding(self) -> None:
        for width in (180, 300, 420):
            rect = QRect(0, 0, width, round(width * 2 / 3))
            hits = grid_card_action_rects(rect, compact=True)
            left_pad = hits.favorite.left() - rect.left()
            right_pad = rect.right() - hits.reject.right()
            bottom_pad = rect.bottom() - hits.favorite.bottom()
            with self.subTest(width=width):
                self.assertLessEqual(abs(left_pad - right_pad), 1)
                self.assertLessEqual(abs(left_pad - bottom_pad), 1)
                self.assertEqual(hits.favorite.top(), hits.reject.top())

    def test_detailed_actions_scale_uniformly_from_reference_canvas(self) -> None:
        reference = QRect(
            0,
            0,
            DETAILED_CARD_REFERENCE_WIDTH,
            DETAILED_CARD_REFERENCE_HEIGHT,
        )
        reference_hits = grid_card_action_rects(reference)

        for factor in (0.75, 1.5):
            width = round(DETAILED_CARD_REFERENCE_WIDTH * factor)
            rect = QRect(17, 29, width, grid_card_height_for_width(width))
            hits = grid_card_action_rects(rect)
            effective_scale = min(
                rect.width() / reference.width(),
                rect.height() / reference.height(),
            )
            for source, scaled in (
                (reference_hits.favorite, hits.favorite),
                (reference_hits.reject, hits.reject),
            ):
                with self.subTest(factor=factor, button=source):
                    self.assertAlmostEqual(scaled.width(), source.width() * effective_scale, delta=1.0)
                    self.assertAlmostEqual(scaled.height(), source.height() * effective_scale, delta=1.0)
                    reference_offset_x = source.center().x() - reference.center().x()
                    actual_offset_x = scaled.center().x() - rect.center().x()
                    self.assertAlmostEqual(actual_offset_x, reference_offset_x * effective_scale, delta=1.5)

    def test_detailed_card_renders_at_reference_and_scaled_sizes(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(120, 130, 140))
        for width in (
            round(DETAILED_CARD_REFERENCE_WIDTH * 0.75),
            DETAILED_CARD_REFERENCE_WIDTH,
            round(DETAILED_CARD_REFERENCE_WIDTH * 1.5),
        ):
            size = QSize(width, grid_card_height_for_width(width))
            card = render_grid_card_pixmap(size, source, GridCardData(selected=True, favorite=True))
            with self.subTest(width=width):
                self.assertFalse(card.isNull())
                self.assertEqual(card.size(), size)

    def test_detailed_painted_and_clickable_action_rects_match(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(120, 130, 140))
        for width in (267, DETAILED_CARD_REFERENCE_WIDTH, 534):
            size = QSize(width, grid_card_height_for_width(width))
            output = QPixmap(size)
            output.fill(QColor(0, 0, 0, 0))
            painter = QPainter(output)
            painted = paint_grid_card(painter, output.rect(), source, GridCardData())
            painter.end()
            with self.subTest(width=width):
                self.assertEqual(painted, grid_card_action_rects(output.rect()))

    def test_detailed_position_text_baseline_matches_filename(self) -> None:
        rect = QRect(0, 0, DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_REFERENCE_HEIGHT)
        scale = _scale_for(rect)
        position_top, _, _ = _right_stack_vertical_positions(rect, scale)
        name_font = QFont("Segoe UI", max(11, round(13 * scale)), QFont.Weight.DemiBold)
        position_font = QFont("Segoe UI", max(8, round(9 * scale)), QFont.Weight.DemiBold)

        filename_baseline = _metadata_text_top(rect, scale) + QFontMetrics(name_font).ascent()
        position_baseline = position_top + QFontMetrics(position_font).ascent()
        self.assertEqual(position_baseline, filename_baseline)


if __name__ == "__main__":
    unittest.main()
