"""Popout workstation proportions, resolved against its logical window size.

As in ``layout_ratios``, *_W is a share of window width and *_H a share of
window height. The values reproduce the approved 2048 x 1191 composition.
Small readability floors are applied at the call sites.
For example, ``EDITOR_W = 0.1625`` means 16.25% of the popout's width.
"""

from .layout_ratios import ratio_px

# Width ratios: each value is a fraction of the popout window's width.
EDITOR_W = 0.1625             # Editor and tool rail together; leaves 83.75% for the photo.
TOOL_RAIL_W = 0.0203          # Outer button rail, about 2% of the window width.
PHOTO_MAX_W = 0.576           # Maximum single-photo width at the reference size.
STAGE_SIDE_W = 0.0            # No inset: the photo canvas reaches both side edges.
SETTINGS_BUTTON_W = 0.0264   # Gear cell at the far right of the path bar.
ZOOM_SLIDER_W = 0.0465        # Zoom track in the bar below the photo.
ADJUSTMENT_SLIDER_W = 0.0688 # Width of an adjustment slider in the editor.
ADJUSTMENT_VALUE_W = 0.0184  # Width of the adjustment numeric readout.
SWATCH_W = 0.0088            # One Color Mixer swatch width.
PATH_GUTTER_W = 0.006         # Left and right padding in the breadcrumb bar.
ACTION_GUTTER_W = 0.004       # Left and right padding around the lower controls row.
ACTION_BUTTON_W = 0.0137      # Width of each icon-only control below the photo.
METADATA_GUTTER_W = 0.007     # Right padding after the rating and flag buttons.
HISTOGRAM_GUTTER_W = 0.006    # Left and right padding around the histogram.
FILMSTRIP_MARGIN_W = 0.0078  # Space at each end of the thumbnail reel.
FILMSTRIP_GAP_W = 0.0029     # Gap between filmstrip thumbnails.
FILMSTRIP_GRIP_W = 0.0176   # Width of the drag grip above the filmstrip.

# Height ratios: each value is a fraction of the popout window's height.
PATH_BAR_H = 0.025            # Breadcrumb bar across the full window.
ACTION_BAR_H = 0.03           # Zoom and rating controls below the photo.
STATUS_BAR_H = 0.0118         # Thin details line inside the bottom of the filmstrip.
EDITOR_HEADER_H = 0.0403      # Editor heading above the histogram.
EDITOR_SECTION_H = 0.0294     # Light / Color / Effects section heading row.
HISTOGRAM_FRAME_H = 0.1276   # Whole histogram area including its captions.
HISTOGRAM_PLOT_H = 0.0772    # Height of the histogram graph itself.
STAGE_TOP_H = 0.0             # No inset between the path bar and photo canvas.
STAGE_BOTTOM_H = 0.0          # No inset between the photo canvas and button bar.
ADJUSTMENT_ROW_H = 0.0252    # One inline label, slider, and value row.
ADJUSTMENT_VALUE_H = 0.0146  # Height of a numeric adjustment readout.
SWATCH_H = 0.0118            # One Color Mixer swatch height.
FILMSTRIP_THUMB_H = 0.0605   # Thumb share; with chrome, filmstrip totals about 8.2%.
FILMSTRIP_MIN_THUMB_H = 0.037  # Shortest thumbnail allowed when dragging.
FILMSTRIP_MAX_THUMB_H = 0.141  # Tallest thumbnail allowed when dragging.
FILMSTRIP_HANDLE_H = 0.0067  # Draggable strip above the thumbnails.
FILMSTRIP_ARROW_H = 0.0285   # Left and right filmstrip arrow button size.
FILMSTRIP_TOP_PAD_H = 0.0008  # Space above thumbnails inside the reel.
FILMSTRIP_BOTTOM_PAD_H = 0.0017  # Space below thumbnails before the details line.
TOOL_BUTTON_H = 0.042        # Slightly tighter, equal-height buttons in the outer rail.
TOOL_ICON_H = 0.0143         # Icon inside an editor tool-rail button.
ACTION_ICON_H = 0.01         # Toolbar and star icon, about 1% high.
ACTION_BUTTON_H = 0.025      # Clickable height of lower controls-row buttons.
RATING_STAR_H = 0.018        # Height of each clickable rating star.
RATING_ACTION_W = 0.0105    # Heart and reject button width.
RATING_ACTION_H = 0.0188    # Heart and reject button height.
FOOTER_TOP_H = 0.0134        # Padding above Reset / Save buttons.
FOOTER_BOTTOM_H = 0.0101     # Padding below Reset / Save buttons.

# Text ratios also use window height, like the main window's typography.
PATH_TEXT_H = 0.0101         # Breadcrumb path and photo position in the top bar.
ACTION_TEXT_H = 0.0101       # Labels on the controls below the photo.
INFO_TEXT_H = 0.0084         # Filename text at the foot of the filmstrip.
SECONDARY_TEXT_H = 0.0084    # EXIF and histogram captions.
STATUS_TEXT_H = 0.0084       # Pixels, zoom, and status text in the filmstrip.
EDITOR_TITLE_TEXT_H = 0.0118  # Editor title above the histogram.
EDITOR_SECTION_TEXT_H = 0.0092  # Light / Color / Effects section headings.
EDITOR_CONTROL_TEXT_H = 0.0092  # Labels beside editor adjustment sliders.
TOOL_TEXT_H = 0.0076         # Small labels under the outer-rail icons.
MIN_TEXT_PX = 10             # Readability floor in pixels, not a ratio.
