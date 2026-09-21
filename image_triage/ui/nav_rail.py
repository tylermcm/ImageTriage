"""The left navigation rail.

A vertical column of labelled destinations beside the library panel. Choosing
one swaps the whole pane beside it (Folders, Faces, Collections, ...) instead of
splitting one pane between several lists, so each list gets the full height.

HOW SIZES WORK HERE
-------------------
The rail's own width comes from ``RAIL_W`` in layout_ratios.py (a share of the
window). Everything *inside* the rail is a share of that rail width, so the
column keeps its shape whatever width it is given:

    pixels = ratio x rail_width

Each ratio is written as ``N / DESIGN_RAIL_PX``, where N is the size in the
78px-wide rail the design was drawn at. To change something, change N. The
comments show what each one currently resolves to on a 115px rail (a maximized
2560-wide window), so N x 1.47 is roughly the pixel result.

WHAT EACH VALUE CHANGES
-----------------------
::

    |<---------- rail_width ---------->|   RAIL_W (layout_ratios.py)
    +----------------------------------+
    |                                  |   RAIL_PAD_RATIO      top padding
    |      +--------------------+      |
    |      |                    |      |   BUTTON_W_RATIO      pill width
    |      |       [icon]       |      |   ICON_RATIO          mark size
    |      |                    |      |   ICON_TEXT_GAP_RATIO icon -> text
    |      |      Library       |      |   LABEL_RATIO         label size
    |      +--------------------+      |   BUTTON_H_RATIO      pill height
    |                                  |   BUTTON_GAP_RATIO    pill -> pill
    |      +--------------------+      |   BUTTON_RADIUS_RATIO pill corners
    |      |       [icon]       |      |
    |      |       Faces        |      |
    |      +--------------------+      |
    |                                  |   SECTION_GAP_RATIO   above divider
    |         ---------------          |   DIVIDER_RATIO       divider width
    |             PINNED               |   SECTION_LABEL_RATIO caption size
    |            +------+              |   TOOL_W/H_RATIO      tool button box
    |            | tool |              |   TOOL_ICON_RATIO     tool mark size
    |            +------+              |   TOOL_RADIUS_RATIO   tool corners
    |                                  |   TOOL_GAP_RATIO      tool -> tool
    |            +------+              |
    |            | tool |              |
    |            +------+              |
    |                                  |
    |              (+)                 |   ADD_RATIO / ADD_ICON_RATIO
    |                                  |   RAIL_PAD_RATIO      bottom padding
    +----------------------------------+

The space above the icon and below the label is not set anywhere: the pair is
centred in the pill, so it is whatever is left over. See ``_NavButton``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# (icon id, selected) -> icon. Supplied by the window so icons follow its theme.
IconFactory = Callable[[str, bool], QIcon]

# The rail as the design drew it. Every ratio below is a share of this width.
DESIGN_RAIL_PX = 78


# --- The rail itself ---------------------------------------------------------

RAIL_PAD_RATIO = 10 / DESIGN_RAIL_PX        # 15px  space above the first button
                                            #       and below the last one


# --- Destination buttons (Library, Faces, Collections) -----------------------

BUTTON_W_RATIO = 64 / DESIGN_RAIL_PX        # 94px  pill width. Capped by the
                                            #       rail: the leftover becomes
                                            #       the side margin, so this
                                            #       cannot exceed rail_width.
BUTTON_H_RATIO = 50 / DESIGN_RAIL_PX        # 86px  pill height. Also sets how
                                            #       far apart the icons sit,
                                            #       more than BUTTON_GAP does.
BUTTON_RADIUS_RATIO = 10 / DESIGN_RAIL_PX   # 15px  pill corner rounding
BUTTON_GAP_RATIO = 6 / DESIGN_RAIL_PX      # 24px  space between pills only;
                                            #       pinned tools use TOOL_GAP
ICON_RATIO = 20 / DESIGN_RAIL_PX            # 37px  icon size. Icon + label must
                                            #       fit BUTTON_H or the label
                                            #       gets squeezed.
ICON_TEXT_GAP_RATIO = 5 / DESIGN_RAIL_PX    # 12px  clear space from the icon to
                                            #       the top of the label's
                                            #       capitals
LABEL_RATIO = 11 / DESIGN_RAIL_PX           # 16px  label text size


# --- Pinned tools block ------------------------------------------------------

SECTION_GAP_RATIO = 8 / DESIGN_RAIL_PX      # 12px  space above the divider
DIVIDER_RATIO = 44 / DESIGN_RAIL_PX         # 65px  divider line width
SECTION_LABEL_RATIO = 10 / DESIGN_RAIL_PX   # 16px  "PINNED" caption size
TOOL_GAP_RATIO = 6 / DESIGN_RAIL_PX        # 15px  space between tool buttons
TOOL_W_RATIO = 34 / DESIGN_RAIL_PX          # 53px  tool button width
TOOL_H_RATIO = 30 / DESIGN_RAIL_PX          # 50px  tool button height
TOOL_ICON_RATIO = 16 / DESIGN_RAIL_PX       # 29px  tool mark size. Clamped to
                                            #       the button, so raise TOOL_H
                                            #       to go much past this.
TOOL_RADIUS_RATIO = 8 / DESIGN_RAIL_PX      # 12px  tool button corners
ADD_RATIO = 40 / DESIGN_RAIL_PX             # 59px  the "+" button at the foot
ADD_ICON_RATIO = 24 / DESIGN_RAIL_PX        # 35px  the "+" mark itself


# --- Floors ------------------------------------------------------------------
# Below roughly a 1400px-wide window these take over and the rail stops
# shrinking. They are what keeps a small window legible rather than faithful,
# so lowering them is usually not what you want.

MIN_RAIL_PX = 48
MIN_BUTTON_H_PX = 44
MIN_ICON_PX = 16
MIN_LABEL_PX = 9
MIN_TOOL_PX = 24

# Fallbacks for callers that need a size before the rail has been given a width.
RAIL_WIDTH_PX = DESIGN_RAIL_PX
BUTTON_WIDTH_PX = 64
BUTTON_MIN_HEIGHT_PX = 52
ICON_PX = 20


# --- Resolving the ratios ----------------------------------------------------


def _scaled(ratio: float, width: int, *, minimum: int = 0) -> int:
    return max(minimum, round(ratio * width))


@dataclass(frozen=True)
class RailMetrics:
    """Every rail size resolved against one rail width, in pixels."""

    rail_width: int
    # Destination buttons
    button_width: int
    button_height: int
    button_radius: int
    button_gap: int
    icon_px: int
    icon_text_gap: int
    label_px: int
    # The rail's own padding, and the margin either side of a centred button
    pad_y: int
    side_inset: int
    # Pinned tools block
    section_gap: int
    section_label_px: int
    divider_width: int
    tool_gap: int
    tool_width: int
    tool_height: int
    tool_icon_px: int
    tool_radius: int
    add_size: int
    add_icon_px: int


def metrics_for(width: int) -> RailMetrics:
    """Resolve the design's proportions against a rail ``width`` in pixels."""
    width = max(MIN_RAIL_PX, int(width))
    button_width = _scaled(BUTTON_W_RATIO, width, minimum=32)
    return RailMetrics(
        rail_width=width,
        button_width=button_width,
        button_height=_scaled(BUTTON_H_RATIO, width, minimum=MIN_BUTTON_H_PX),
        button_radius=_scaled(BUTTON_RADIUS_RATIO, width, minimum=6),
        button_gap=_scaled(BUTTON_GAP_RATIO, width, minimum=2),
        icon_px=_scaled(ICON_RATIO, width, minimum=MIN_ICON_PX),
        icon_text_gap=_scaled(ICON_TEXT_GAP_RATIO, width, minimum=0),
        label_px=_scaled(LABEL_RATIO, width, minimum=MIN_LABEL_PX),
        pad_y=_scaled(RAIL_PAD_RATIO, width, minimum=6),
        # The button is centred, so whatever it does not use becomes the margin.
        side_inset=max(2, (width - button_width) // 2),
        section_gap=_scaled(SECTION_GAP_RATIO, width, minimum=4),
        section_label_px=_scaled(SECTION_LABEL_RATIO, width, minimum=MIN_LABEL_PX - 1),
        divider_width=_scaled(DIVIDER_RATIO, width, minimum=24),
        tool_gap=_scaled(TOOL_GAP_RATIO, width, minimum=1),
        tool_width=_scaled(TOOL_W_RATIO, width, minimum=MIN_TOOL_PX),
        tool_height=_scaled(TOOL_H_RATIO, width, minimum=MIN_TOOL_PX),
        tool_icon_px=_scaled(TOOL_ICON_RATIO, width, minimum=14),
        tool_radius=_scaled(TOOL_RADIUS_RATIO, width, minimum=5),
        add_size=_scaled(ADD_RATIO, width, minimum=MIN_TOOL_PX),
        add_icon_px=_scaled(ADD_ICON_RATIO, width, minimum=16),
    )


# --- The destination button --------------------------------------------------


class _NavButton(QToolButton):
    """A destination button that places its own icon and label.

    Qt's text-under-icon layout centres the pair and chooses the gap between
    them itself, with no setting for either. Drawing the contents here makes the
    gap a ratio like every other size, and lets the pair sit so the space above
    the icon looks equal to the space below the label.

    Only the contents are drawn here. The pill behind them -- its colour, hover
    and checked states -- is still the stylesheet's, painted by the style.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_box = ICON_PX
        self._gap = 0
        self._label_colors: tuple[QColor, QColor] | None = None

    # -- what the rail tells it ------------------------------------------------

    def set_content_metrics(self, icon_box: int, gap: int) -> None:
        """``gap`` is clear space from the icon to the label's capitals."""
        if (icon_box, gap) == (self._icon_box, self._gap):
            return
        self._icon_box = max(0, int(icon_box))
        self._gap = max(0, int(gap))
        self.update()

    def set_label_colors(self, normal: QColor, selected: QColor) -> None:
        """The label is painted here, so its colour is passed in.

        A stylesheet's ``:checked`` colour never reaches the style option, so
        the selected state has to come from the theme the same way the icons do.
        """
        self._label_colors = (QColor(normal), QColor(selected))
        self.update()

    # -- placing the contents --------------------------------------------------

    def _content_layout(self, metrics: QFontMetrics) -> tuple[int, int, int]:
        """Where the icon top and the label's line box sit inside the pill.

        Returns ``(icon_top, text_top, line_height)``. Neither the top nor the
        bottom padding is configurable: both fall out of centring the pair.
        """
        line_height = metrics.height()
        ascent = metrics.ascent()
        # Capitals start below the line box's top; the requested gap is to them,
        # not to the box, so the box is pulled up by that lead.
        lead = max(0, ascent - metrics.capHeight())
        layout_gap = max(0, self._gap - lead)
        # Slack left after the pair, split so the visible space matches: what
        # sits below the baseline is dead room, so the bottom gets that much
        # less. (A descender such as the "y" in Library eats into it.)
        descent = max(0, line_height - ascent)
        slack = self.height() - self._icon_box - layout_gap - line_height
        top = max(0, (slack + descent) // 2)
        text_top = top + self._icon_box + layout_gap
        return top, text_top, line_height

    def paintEvent(self, event) -> None:  # noqa: D102 - Qt override
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        # Let the style paint the pill alone, then place the contents by hand.
        option.text = ""
        option.icon = QIcon()
        option.toolButtonStyle = Qt.ToolButtonStyle.ToolButtonIconOnly
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)

        # The label size comes from the stylesheet, so read it off the widget.
        metrics = self.fontMetrics()
        top, text_top, line_height = self._content_layout(metrics)

        mark = self.icon()
        if not mark.isNull() and self._icon_box > 0:
            painter.drawPixmap(
                QPoint((self.width() - self._icon_box) // 2, top),
                mark.pixmap(self._icon_box, self._icon_box),
            )

        label = self.text()
        if label:
            if self._label_colors is not None:
                painter.setPen(self._label_colors[1 if self.isChecked() else 0])
            else:
                painter.setPen(option.palette.buttonText().color())
            painter.drawText(
                QRect(0, text_top, self.width(), line_height),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(label, Qt.TextElideMode.ElideRight, self.width()),
            )


# --- The rail ----------------------------------------------------------------


class NavRail(QWidget):
    """Exclusive, labelled destination buttons that report the chosen key."""

    current_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftNavRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._metrics = metrics_for(DESIGN_RAIL_PX)
        self._sized = False
        self.setFixedWidth(self._metrics.rail_width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            self._metrics.side_inset,
            self._metrics.pad_y,
            self._metrics.side_inset,
            self._metrics.pad_y,
        )
        self._layout.setSpacing(self._metrics.button_gap)
        # Destinations go above this stretch, the footer below it.
        self._layout.addStretch(1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        self._icon_ids: dict[str, str] = {}
        self._icon_factory: IconFactory | None = None
        self._label_colors: tuple[QColor, QColor] | None = None
        self._current = ""

    # -- building the column ---------------------------------------------------

    def add_destination(self, key: str, label: str, icon_id: str, *, tooltip: str = "") -> QToolButton:
        button = _NavButton(self)
        button.setObjectName("leftNavButton")
        button.setProperty("navKey", key)
        button.setText(label)
        button.setToolTip(tooltip or label)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.clicked.connect(lambda _checked=False, target=key: self.set_current(target))
        self._group.addButton(button)
        self._buttons[key] = button
        self._icon_ids[key] = icon_id
        if self._label_colors is not None:
            button.set_label_colors(*self._label_colors)
        self._style_button(button)
        # Destinations stack from the top, ahead of any added sections.
        self._layout.insertWidget(len(self._buttons) - 1, button, 0, Qt.AlignmentFlag.AlignHCenter)
        if not self._current:
            self.set_current(key, emit=False)
        else:
            self._refresh_icon(key)
        return button

    def add_section(self, widget: QWidget) -> None:
        """Add a block (such as pinned tools) below the destinations."""
        self._layout.insertWidget(self._stretch_index(), widget, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_footer(self, widget: QWidget) -> None:
        """Pin a widget to the bottom of the rail."""
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

    def _stretch_index(self) -> int:
        for index in range(self._layout.count()):
            if self._layout.itemAt(index).spacerItem() is not None:
                return index
        return self._layout.count()

    # -- sizing ----------------------------------------------------------------

    def metrics(self) -> RailMetrics:
        """The sizes the rail is currently drawn at.

        The window reads these to size the pinned-tool block, which it owns, to
        match the destinations above it.
        """
        return self._metrics

    def apply_width(self, width: int) -> None:
        """Fit the rail and its destinations to ``width`` pixels, keeping the
        design's proportions. Called on every window resize."""
        metrics = metrics_for(width)
        if metrics == self._metrics and self._sized:
            # Restyling re-polishes each button, which lays the rail out again
            # and calls straight back here; settle instead of looping.
            return
        self._sized = True
        self._metrics = metrics
        self.setFixedWidth(metrics.rail_width)
        self._layout.setContentsMargins(
            metrics.side_inset, metrics.pad_y, metrics.side_inset, metrics.pad_y
        )
        self._layout.setSpacing(metrics.button_gap)
        for button in self._buttons.values():
            self._style_button(button)
        self.refresh_icons()

    def _style_button(self, button: QToolButton) -> None:
        """Size one destination to the current metrics.

        The label size goes through the button's own stylesheet: a widget's
        sheet beats the application sheet, which pins ``font-size`` for this
        object name, and setFont would lose to both.
        """
        metrics = self._metrics
        button.setFixedSize(metrics.button_width, metrics.button_height)
        button.setIconSize(QSize(metrics.icon_px, metrics.icon_px))
        if isinstance(button, _NavButton):
            button.set_content_metrics(metrics.icon_px, metrics.icon_text_gap)
        # No padding: the button places its own icon and label.
        sheet = (
            "QToolButton#leftNavButton {"
            f" font-size: {metrics.label_px}px;"
            f" border-radius: {metrics.button_radius}px; }}"
        )
        if button.styleSheet() != sheet:
            button.setStyleSheet(sheet)

    # -- colours and icons -----------------------------------------------------

    def set_label_colors(self, normal: QColor, selected: QColor) -> None:
        """Colour the destination labels, matching how their icons are tinted."""
        self._label_colors = (QColor(normal), QColor(selected))
        for button in self._buttons.values():
            button.set_label_colors(normal, selected)

    def set_icon_factory(self, factory: IconFactory) -> None:
        self._icon_factory = factory
        self.refresh_icons()

    def refresh_icons(self) -> None:
        for key in self._buttons:
            self._refresh_icon(key)

    def _refresh_icon(self, key: str) -> None:
        if self._icon_factory is None:
            return
        button = self._buttons[key]
        button.setIcon(self._icon_factory(self._icon_ids[key], key == self._current))

    # -- selection -------------------------------------------------------------

    def keys(self) -> tuple[str, ...]:
        return tuple(self._buttons)

    def button(self, key: str) -> QToolButton | None:
        return self._buttons.get(key)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, *, emit: bool = True) -> None:
        button = self._buttons.get(key)
        if button is None:
            return
        changed = key != self._current
        self._current = key
        button.setChecked(True)
        self.refresh_icons()
        if changed and emit:
            self.current_changed.emit(key)
