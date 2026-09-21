"""Shell proportions as fractions of the window.

Every value here is a share of the window, so the layout keeps its shape on any
monitor. ``*_W`` values are a share of the window's width, ``*_H`` a share of
its height. They are resolved to pixels on every resize.

To adjust: pick the value below and change the number.

    pixels = ratio x window size

On a maximized 2560x1440 screen the window is 2560 wide and about 1362 tall
(full height less the title bar and taskbar), so:

    width  ratio 0.01 -> 26px          to get N px across: N / 2560
    height ratio 0.01 -> 14px          to get N px down:   N / 1362

Rail internals (its buttons, icons and labels) are not here: they are shares of
the rail's own width, in nav_rail.py.
"""
from __future__ import annotations

# --- Widths (share of window width) -----------------------------------------
# Rail + folder pane + grid + inspector together make up the window.
RAIL_W = 0.035                  # left icon rail
LIBRARY_PANE_W = 0.85          # drives/folders pane beside the rail
INSPECTOR_W = 0.11              # right-hand inspector pane
FLOATING_TOOLBAR_W = 0.426       # floating button bar over the grid
SEARCH_W = 0.22                 # search bar in the top bar
AI_BOX_W = 0.164                # AI analysis box in the inspector
ZOOM_SLIDER_W = 0.0461          # thumbnail zoom slider in the status bar
STATUS_FILTER_W = 0.1641        # max width of "Filters: ..." in the status bar
STATUS_CATALOG_W = 0.1016       # max width of "Load: ..."
STATUS_PIPELINE_W = 0.1328      # max width of "Review: ... | Workflow: ..."

# --- Heights (share of window height) ---------------------------------------
TOP_BAR_H = 0.041               # top bar holding Menu, breadcrumb and search
SEARCH_H = 0.025               # search bar height
DRIVE_ROW_H = 0.034             # one drive row, top of its name to the next
FLOATING_TOOLBAR_H = 0.057       # floating button bar height
SETTINGS_BAR_H = 0.045          # settings strip at the foot of the left pane
AI_BOX_H = 0.13                 # AI analysis box in the inspector

# --- Text sizes (share of window height) ------------------------------------
# Raise a value to enlarge that text. MIN_TEXT_PX stops any of them going
# unreadable on a short window.
BREADCRUMB_TEXT_H = 0.01      # folder path in the top bar
SEARCH_TEXT_H = 0.01          # text typed into the search bar
SECTION_TITLE_H = 0.011        # "Drives" and "Folders" headings
DRIVE_TEXT_H = 0.011           # drive names in the Drives list
FOLDER_TEXT_H = 0.01          # folder names in the Folders tree
INSPECTOR_TITLE_H = 0.011      # "Preview", "Histogram", "Culling" headings
INSPECTOR_ASIDE_H = 0.0086      # the grey note beside a heading
INSPECTOR_KEY_H = 0.009        # row labels: Decision, Rating, Camera
INSPECTOR_VALUE_H = 0.0086      # row values beside them
INSPECTOR_PREVIEW_H = 0.009    # filename and "10 / 48" under the preview
INSPECTOR_CHIP_TEXT_H = 0.0079  # Focus / Exposure / Noise chips in the AI box
MENU_ITEM_TEXT_H = 0.0092       # items in the dropdown under the menu icon
STATUS_TEXT_H = 0.0095         # all status bar text: the message on the left
                                # and Load / Review / Filters on the right
MIN_TEXT_PX = 11                # floor for every text size above

# --- Icons, buttons and spacing (share of window height) --------------------
# Each value is the pixel size at a 1392px-tall window divided by 1392, so the
# defaults reproduce the layout exactly. Raise one to grow that piece.
# Glyphs and gaps use MIN_GLYPH_PX as their floor instead of MIN_TEXT_PX,
# since several of them are meant to be smaller than readable text.

# Top bar
MENU_ICON_H = 0.02            # hamburger icon beside "Menu"
TOP_GEAR_ICON_H = 0.021        # settings gear beside the window buttons
TOP_GEAR_BOX_H = 0.027          # its clickable box
UPDATE_ICON_H = 0.0201          # update arrow beside the gear (shows when an
                                # update is available)
UPDATE_BOX_W_H = 0.0345         # its clickable box, width
UPDATE_BOX_H = 0.027           # its clickable box, height
SEARCH_GLYPH_H =0.01         # magnifier inside the search bar
SEARCH_HINT_H = 0.008          # the "Ctrl K" hint in the search bar
BREADCRUMB_CHEVRON_H = 0.0057   # the ">" between breadcrumb folders
WINDOW_BUTTON_GLYPH_H = 0.008  # minimize / maximize / close glyphs

# Drives and Folders headings
SECTION_HEADER_H = 0.0287       # height of the heading row
SECTION_CHEVRON_H = 0.0086      # the collapse arrow before the title
SECTION_BUTTON_BOX_H = 0.0172   # the refresh / + button box
SECTION_BUTTON_ICON_H = 0.0101  # the refresh / + icon

# Drives list and folder tree
DRIVE_METER_H = 0.0029          # thickness of a drive's usage bar
DRIVE_METER_GAP_H = 0.0036      # space between a drive's name and its bar
FOLDER_ICON_H = 0.0144          # folder icon size in the tree
FOLDER_INDENT_H = 0.0158        # indent per nested folder level

# Settings strip at the foot of the left pane
SETTINGS_BUTTON_BOX_H = 0.0244  # each button's clickable box
SETTINGS_ICON_H = 0.0144        # each button's icon
SETTINGS_PAD_H = 0.0086         # padding around the row of buttons
SETTINGS_GAP_H = 0.0101         # space between the buttons

# Floating button bar
FLOATING_TOOLBAR_BOTTOM_H = 0.018   # gap below the bar to the grid's edge
FLOATING_TOOLBAR_PAD_X_H = 0.0057   # padding inside the bar, left and right
FLOATING_TOOLBAR_PAD_Y_H = 0.0029   # padding inside the bar, top and bottom
FLOATING_TOOLBAR_ROW_GAP_H = 0.0086 # clear space kept above the bar for the
                                    # last row of cards
FLOATING_TOOLBAR_FADE_H = 0.0862    # height of the fade behind the bar

# Inspector
INSPECTOR_NAV_H = 0.0108        # the < > arrows beside "10 / 48"
ANALYZE_BUTTON_H = 0.0201       # "Analyze folder" button height

# Status bar (bottom right: zoom slider and the pane show/hide buttons).
# These apply while the toolbar floats; docked, they ride on the toolbar and
# take its button sizes instead.
STATUS_TOGGLE_BOX_H = 0.0244    # each pane show/hide button's box
STATUS_TOGGLE_ICON_H = 0.0158   # its icon
ZOOM_ICON_SMALL_H = 0.0129      # small magnifier left of the slider
ZOOM_ICON_LARGE_H = 0.0172      # large magnifier right of the slider
ZOOM_TRACK_H = 0.0018           # thickness of the slider's line
ZOOM_HANDLE_H = 0.0063          # the slider's round handle
ZOOM_ICON_GAP_H = 0.005         # space between the slider and its magnifiers
STATUS_CONTROLS_GAP_H = 0.005  # space between the zoom cluster and toggles

MIN_GLYPH_PX = 2                # floor for every icon, glyph and gap above

# --- Inspector internals (share of the inspector pane's own width) -----------
# Keyed to the pane, not the window, so the label column keeps its share as
# INSPECTOR_W changes instead of crowding out the values.
INSPECTOR_KEY_W = 0.34          # width of the row label column

# The library panel holds the rail and the drives pane side by side.
LIBRARY_PANEL_W = RAIL_W + LIBRARY_PANE_W


def ratio_px(ratio: float, extent: int, *, minimum: int = 0) -> int:
    """Pixels for ``ratio`` of ``extent``, never below ``minimum``."""
    return max(minimum, round(ratio * max(0, extent)))
