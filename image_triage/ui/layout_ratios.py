"""Shell proportions as fractions of the window.

The layout was specified as percentages of a 2560x1440 window so it keeps the
same proportions on any monitor: every value below is a share of the window's
width (``*_W``) or height (``*_H``), resolved to pixels on each resize.
"""
from __future__ import annotations

# Widths (share of window width). Rail + drives pane + grid + inspector = 100%.
RAIL_W = 0.035           # 4.5% as drawn, less the 1 point asked for
LIBRARY_PANE_W = 0.120          # drives/folders pane beside the rail
INSPECTOR_W = 0.15
FLOATING_TOOLBAR_W = 0.43
SEARCH_W = 0.25
AI_BOX_W = 0.164

# Heights (share of window height).
TOP_BAR_H = 0.041
DRIVE_ROW_H = 0.04              # top of one drive's name to the next
FLOATING_TOOLBAR_H = 0.05
SETTINGS_BAR_H = 0.045
AI_BOX_H = 0.13

# Text sizes are shares of the window height too. The design was drawn at 1.5x,
# so its 15px/16px type is 22px/24px of screen at 1440. A floor keeps a small
# window legible.
FOLDER_TEXT_H = 0.0104
DRIVE_TEXT_H = 0.0111
MIN_TEXT_PX = 11

# The library panel holds the rail and the drives pane side by side.
LIBRARY_PANEL_W = RAIL_W + LIBRARY_PANE_W


def ratio_px(ratio: float, extent: int, *, minimum: int = 0) -> int:
    """Pixels for ``ratio`` of ``extent``, never below ``minimum``."""
    return max(minimum, round(ratio * max(0, extent)))
