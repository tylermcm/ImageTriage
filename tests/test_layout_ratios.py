from __future__ import annotations

from image_triage.ui import layout_ratios


def test_default_side_panes_leave_most_of_the_window_to_the_grid() -> None:
    defaults = (layout_ratios.DEFAULT_LIBRARY_PANEL_W, layout_ratios.DEFAULT_INSPECTOR_W)
    for ratio in defaults:
        assert layout_ratios.PANE_MIN_W <= ratio <= layout_ratios.PANE_MAX_W
    assert sum(defaults) <= 0.5


def test_dragged_pane_widths_replace_the_live_values_within_bounds() -> None:
    try:
        layout_ratios.set_pane_widths(0.2, 0.9)
        assert layout_ratios.LIBRARY_PANEL_W == 0.2
        assert layout_ratios.INSPECTOR_W == layout_ratios.PANE_MAX_W
        layout_ratios.set_pane_widths(inspector=0.0)
        assert layout_ratios.LIBRARY_PANEL_W == 0.2
        assert layout_ratios.INSPECTOR_W == layout_ratios.PANE_MIN_W
    finally:
        layout_ratios.reset_pane_widths()
    assert layout_ratios.LIBRARY_PANEL_W == layout_ratios.DEFAULT_LIBRARY_PANEL_W
    assert layout_ratios.INSPECTOR_W == layout_ratios.DEFAULT_INSPECTOR_W
