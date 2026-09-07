from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.ui.grid_card_renderer import (
    DETAILED_CARD_MAX_CHROME_SCALE,
    DETAILED_CARD_REFERENCE_HEIGHT,
    DETAILED_CARD_REFERENCE_WIDTH,
    GridCardData,
    GridCardInteractionColors,
    THUMBNAIL_SELECTION_TINT_ALPHA,
    grid_card_action_rects,
    grid_card_action_hit_rects,
    grid_card_filename_is_elided,
    grid_card_height_for_width,
    gallery_card_filename_is_elided,
    grid_gallery_action_hit_rects,
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
            effective_scale = min(rect.width() / reference.width(), DETAILED_CARD_MAX_CHROME_SCALE)
            for source, scaled in (
                (reference_hits.favorite, hits.favorite),
                (reference_hits.reject, hits.reject),
            ):
                with self.subTest(factor=factor, button=source):
                    self.assertAlmostEqual(scaled.width(), source.width() * effective_scale, delta=1.0)
                    self.assertAlmostEqual(scaled.height(), source.height() * effective_scale, delta=1.0)
                    reference_right_inset = reference.right() - source.right()
                    actual_right_inset = rect.right() - scaled.right()
                    self.assertAlmostEqual(actual_right_inset, reference_right_inset * effective_scale, delta=1.5)

    def test_logical_action_targets_are_30px_and_visual_rects_are_unchanged(self) -> None:
        for rect, compact in (
            (QRect(0, 0, 560, 407), False),
            (QRect(0, 0, 180, 120), True),
        ):
            visual = grid_card_action_rects(rect, compact=compact, compact_actions="right")
            logical = grid_card_action_hit_rects(rect, compact=compact, compact_actions="right")
            for name in ("favorite", "reject"):
                painted = getattr(visual, name)
                target = getattr(logical, name)
                with self.subTest(compact=compact, action=name):
                    self.assertTrue(target.contains(painted))
                    self.assertGreaterEqual(target.width(), painted.width())
                    self.assertGreaterEqual(target.height(), 30)
                    self.assertTrue(rect.contains(target))
                    if not compact:
                        self.assertGreaterEqual(target.width(), 30)
            self.assertFalse(logical.favorite.intersects(logical.reject))

    def test_gallery_logical_targets_are_30px_and_do_not_overlap(self) -> None:
        rect = QRect(0, 0, 356, 277)
        logical = grid_gallery_action_hit_rects(rect)
        self.assertEqual(logical.favorite.size(), QSize(30, 30))
        self.assertEqual(logical.reject.size(), QSize(30, 30))
        self.assertFalse(logical.favorite.intersects(logical.reject))

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

    def test_filename_elision_helpers_only_flag_truncated_names(self) -> None:
        rect = QRect(0, 0, DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_REFERENCE_HEIGHT)
        self.assertFalse(grid_card_filename_is_elided(rect, GridCardData(filename="IMG_001.jpg")))
        self.assertTrue(
            grid_card_filename_is_elided(
                rect,
                GridCardData(filename="a_very_long_filename_that_cannot_fit_in_the_available_footer_space.jpg"),
            )
        )
        gallery_rect = QRect(0, 0, 356, 277)
        self.assertFalse(gallery_card_filename_is_elided(gallery_rect, GridCardData(filename="IMG_001.jpg")))
        self.assertTrue(
            gallery_card_filename_is_elided(
                gallery_rect,
                GridCardData(filename="a_very_long_filename_that_cannot_fit_in_the_gallery_footer.jpg"),
            )
        )

    def test_active_outline_is_preserved_over_selected_and_decision_states(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(120, 130, 140))
        active = QColor(14, 240, 86)
        colors = GridCardInteractionColors(
            active_outline=active,
            selection_outline=QColor(230, 30, 200),
            selection_fill=QColor(230, 30, 200, 50),
            hover_outline=QColor(240, 220, 20),
            contrast_outline=QColor(0, 0, 0),
        )
        image = render_grid_card_pixmap(
            QSize(DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_REFERENCE_HEIGHT),
            source,
            GridCardData(active=True, selected=True, hovered=True, favorite=True, rejected=True),
            interaction_colors=colors,
        ).toImage()
        matching_pixels = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.green() > 210 and color.red() < 70 and color.blue() < 130:
                    matching_pixels += 1
        self.assertGreater(matching_pixels, 250)

    def test_hover_boundary_remains_visible_on_a_bright_photo(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(245, 245, 245))
        size = QSize(DETAILED_CARD_REFERENCE_WIDTH, DETAILED_CARD_REFERENCE_HEIGHT)
        normal = render_grid_card_pixmap(
            size,
            source,
            GridCardData(duplicate_visible=False, ai_visible=False),
            corner_radius=8,
        ).toImage()
        hovered = render_grid_card_pixmap(
            size,
            source,
            GridCardData(hovered=True, duplicate_visible=False, ai_visible=False),
            corner_radius=8,
        ).toImage()

        edge_y = 100
        self.assertGreater(
            _luminance(normal.pixelColor(0, edge_y)) - _luminance(hovered.pixelColor(0, edge_y)),
            100,
        )
        self.assertEqual(normal.pixelColor(10, edge_y), hovered.pixelColor(10, edge_y))
        self.assertEqual(THUMBNAIL_SELECTION_TINT_ALPHA, 16)


if __name__ == "__main__":
    unittest.main()
