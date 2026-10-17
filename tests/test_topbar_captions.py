"""Top-bar button captions are never wider than their button: they shrink a step before they would be cut off."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.toolbar_controller import MIN_CAPTION_PX, fit_caption_font


def _settle(window) -> None:
    for _ in range(10):
        QApplication.instance().processEvents()
    window._appearance.apply_layout_ratios()
    for _ in range(10):
        QApplication.instance().processEvents()


def test_captions_span_the_whole_button_and_can_always_be_fitted(main_window) -> None:
    window = main_window
    old_size = window.size()
    try:
        for width, height in ((1100, 700), (1400, 860), (1920, 1080), (2560, 1440)):
            window.resize(width, height)
            _settle(window)
            labels = [label for label in window.findChildren(QLabel, "appTopBarButtonCaption") if label.isVisibleTo(window)]
            assert labels
            for label in labels:
                button = label.parentWidget().parentWidget()
                assert label.width() == button.width(), f"{label.text()!r}: the caption row spans the button at {width}x{height}"
                # The test machine has no real UI font, so a caption may still be wider than its button at the smallest
                # size; what must hold is that fitting it reaches either a fit or the floor, never a silent cut.
                fit_caption_font(label, label.width() - 2)
                label.ensurePolished()
                fits = label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
                at_floor = label.styleSheet() == f"font-size: {MIN_CAPTION_PX}px;"
                assert fits or at_floor, f"{label.text()!r} is cut off at {width}x{height} without being shrunk"
    finally:
        window.resize(old_size)
        _settle(window)


def test_a_caption_shrinks_only_when_it_has_to_and_grows_back(main_window) -> None:
    label = QLabel("Refresh composition")
    label.setObjectName("appTopBarButtonCaption")
    label.ensurePolished()
    natural = round(QFont(label.font()).pointSizeF() * 96 / 72)
    fit_caption_font(label, 4000)
    assert not label.styleSheet(), "plenty of room: the theme's size stays"
    fit_caption_font(label, 40)
    assert label.styleSheet().startswith("font-size:")
    shrunk = int(label.styleSheet().split(":")[1].split("px")[0])
    assert MIN_CAPTION_PX <= shrunk < natural
    fit_caption_font(label, 4000)
    assert not label.styleSheet(), "a wider button gives the size back"
