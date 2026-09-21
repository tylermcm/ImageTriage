from __future__ import annotations

from image_triage.ui import layout_ratios


def test_default_side_panes_leave_most_of_the_window_to_the_grid() -> None:
    # Stored drags outside 0.05-0.5 are discarded, so the defaults must sit in
    # that range too or a fresh install boots with the grid squeezed out.
    for ratio in (layout_ratios.LIBRARY_PANEL_W, layout_ratios.INSPECTOR_W):
        assert 0.05 <= ratio <= 0.5
    assert layout_ratios.LIBRARY_PANEL_W + layout_ratios.INSPECTOR_W <= 0.5
