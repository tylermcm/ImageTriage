"""The AI-analysis placeholder's dimension chips (Focus, Exposure, ...) keep whole words at any box width."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def test_chips_wrap_to_fewer_columns_instead_of_cutting_words(main_window) -> None:
    panel = main_window.inspector_panel
    placeholder = panel.ai_stack.widget(0)
    layout = placeholder.layout()
    columns_by_width = []
    try:
        # Narrowest real box: the widest chip plus the placeholder's own margins.
        for box_width in (170, 200, 240, 280, 320, 380, 440, 520):
            placeholder.setFixedWidth(box_width)
            margins = layout.contentsMargins()
            panel._fit_ai_chips(box_width - margins.left() - margins.right())
            layout.activate()
            QApplication.instance().processEvents()
            columns_by_width.append(panel._ai_chip_columns)
            for chip in panel._ai_chips:
                assert chip.width() >= chip.sizeHint().width(), f"{chip.text()!r} is cut off in a {box_width}px box"
        assert columns_by_width == sorted(columns_by_width), "a wider box never has fewer chips per row"
        assert columns_by_width[0] < columns_by_width[-1], "a narrow box stacks the chips"
    finally:
        main_window._appearance.apply_layout_ratios()
