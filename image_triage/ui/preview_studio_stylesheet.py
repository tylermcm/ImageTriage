"""Stylesheet for the Studio popout preview dialog (``FullScreenPreview``).

``build_studio_dialog_stylesheet`` returns the whole text. Most of it is
fixed colours; only the mockup rules scale their type and row sizes with the
dialog's height, which is why the stylesheet is rebuilt when the dialog is
resized (see ``FullScreenPreview._studio_theme_key``).

The text is built from small section functions, one per widget family, joined
in a fixed order: the cascade is order-sensitive and the exact text is pinned
by ``tests/test_stylesheet_golden.py``. Each section starts with a newline and
ends right after its last ``}``, so the pieces join without a separator.

Note that every selector is rewritten by ``preview_studio.scope_stylesheet``,
which splits the text on braces and commas. A comment that sits before a rule
is therefore part of that rule's selector as far as the scoping is concerned,
so a comment containing ``{`` or ``}`` would break it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import popout_layout_ratios as popout_ratios
from . import preview_studio as studio


@dataclass(frozen=True, slots=True)
class _StudioSizes:
    """Pixel sizes that scale with the dialog height.

    ``height`` is the dialog's current height; each method converts one of the
    popout layout ratios to pixels against it, applying the given floor.
    """

    height: int

    def type_px(self, ratio: float, minimum: int | None = None) -> int:
        """Text size: ``ratio`` of the height, floored at ``MIN_TEXT_PX`` by default."""
        if minimum is None:
            minimum = popout_ratios.MIN_TEXT_PX
        return popout_ratios.ratio_px(ratio, self.height, minimum=minimum)

    def px(self, ratio: float, *, minimum: int = 0) -> int:
        """Any other size: ``ratio`` of the height, floored at ``minimum``."""
        return popout_ratios.ratio_px(ratio, self.height, minimum=minimum)


def build_studio_dialog_stylesheet(scope: str, height: int) -> str:
    """The Studio dialog stylesheet, every selector prefixed with ``scope``.

    ``height`` is the dialog's height in pixels (clamped to at least 1, since
    a dialog that has not been shown yet reports 0). The result is the scope's
    own surface and font rules, the shared Studio sheet, then this module's
    dialog-specific rules, scoped as a single block.
    """
    sizes = _StudioSizes(max(1, height))
    fixed = "".join(section() for section in _FIXED_SECTIONS)
    scaled = "".join(section(sizes) for section in _SCALED_SECTIONS)
    return (
        f"{scope} {{ background-color: #0e0f11; color: #e8eaed; }}\n"
        f"{scope} QWidget {{ font-family: 'Segoe UI'; font-size: 11px; }}\n"
        + studio.studio_stylesheet(scope=scope)
        + studio.scope_stylesheet(fixed + "\n        " + scaled + "\n        ", scope)
    )


# --- Fixed sections ----------------------------------------------------------
# Rules that do not depend on the dialog height.


def _studio_control_css() -> str:
    """Studio tool buttons, the preview-controls cards and their labels."""
    return f"""
            QToolButton#studioToolButton {{
                background: {studio.SURFACE_3}; border: 1px solid {studio.LINE}; color: {studio.TEXT};
                padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 500;
                min-height: 0px;
            }}
            QToolButton#studioToolButton:hover {{ background: {studio.SURFACE_HOVER}; border-color: {studio.LINE_STRONG}; }}
            QToolButton#studioToolButton:checked {{ background: {studio.ACCENT}; color: #ffffff; border-color: {studio.ACCENT}; }}
            QToolButton#studioToolButton:disabled {{ color: {studio.TEXT_MUTE}; border-color: {studio.LINE}; }}
            QComboBox:disabled {{ color: {studio.TEXT_MUTE}; border-color: {studio.LINE}; background: {studio.SURFACE_2}; }}
            QFrame#previewControlsCard {{ background: {studio.SURFACE_2}; border: 1px solid {studio.LINE}; border-radius: {studio.CARD_RADIUS}px; }}
            QLabel#previewControlsTitle {{ color: {studio.TEXT}; font-size: 13px; font-weight: 600; }}
            QLabel#previewControlsSummary {{ color: {studio.TEXT_MUTE}; font-size: 11px; }}
            QLabel#previewControlLabel {{ color: {studio.TEXT_DIM}; font-size: 12px; font-weight: 500; }}
            QLabel#previewAnalysisValue {{ color: {studio.TEXT}; font-size: 12px; }}
            QLabel#previewAnalysisHint {{ color: {studio.TEXT_MUTE}; font-size: 11px; }}"""


def _studio_editor_frame_css() -> str:
    """The photo editor panel frame, its tool rail, segment buttons and scroll area."""
    return f"""
            QFrame#photoEditorPanel {{
                background: #2e2e2e; border: 1px solid #1c1c1c;
                border-radius: 6px;
            }}
            QFrame#editorToolRail {{
                background: #232323; border: none; border-right: 1px solid #1a1a1a;
                border-top-left-radius: 6px; border-bottom-left-radius: 6px;
            }}
            QToolButton#editorToolRailButton {{
                background: transparent; border: none; border-radius: 5px;
                min-height: 0px; padding: 0px;
            }}
            QToolButton#editorToolRailButton:hover {{ background: #363636; }}
            QToolButton#editorToolRailButton:checked {{
                background: #2e2e2e; border-left: 2px solid #1473e6;
                border-top-left-radius: 0px; border-bottom-left-radius: 0px;
            }}
            QToolButton#editorToolRailButton:disabled {{ background: transparent; }}
            QWidget#photoEditorColumn {{ background: #2e2e2e; }}
            QPushButton#editorSegmentButton {{
                background: #3a3a3a; border: 1px solid #303030; color: #b8b8b8;
                padding: 4px 2px; border-radius: 4px; font-size: 10px;
                min-height: 0px;
            }}
            QPushButton#editorSegmentButton:hover {{ background: #444444; color: #e6e6e6; }}
            QPushButton#editorSegmentButton:checked {{
                background: #1473e6; border-color: #1473e6; color: #ffffff;
            }}
            QPushButton#editorSegmentButton:disabled {{ color: #6a6a6a; }}
            QWidget#colorWheel {{ background: transparent; }}
            QStackedWidget#photoEditorStack {{ background: #2e2e2e; }}
            QScrollArea#photoEditorScrollArea {{ background: #2e2e2e; border: none; }}
            QScrollArea#photoEditorScrollArea QWidget {{ background: #2e2e2e; }}
            QWidget#photoEditorBody {{ background: #2e2e2e; }}
            QScrollArea#photoEditorScrollArea QScrollBar:vertical {{
                background: transparent; width: 9px; margin: 2px 1px;
            }}
            QScrollArea#photoEditorScrollArea QScrollBar::handle:vertical {{
                background: #464646; border-radius: 4px; min-height: 24px;
            }}
            QScrollArea#photoEditorScrollArea QScrollBar::handle:vertical:hover {{ background: #5a5a5a; }}
            QScrollArea#photoEditorScrollArea QScrollBar::add-line:vertical,
            QScrollArea#photoEditorScrollArea QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollArea#photoEditorScrollArea QScrollBar::add-page:vertical,
            QScrollArea#photoEditorScrollArea QScrollBar::sub-page:vertical {{ background: transparent; }}"""


def _studio_mask_tool_css() -> str:
    """Mask pane: title, tool rows, AI-selection chips, layer cards and component rows."""
    return f"""
            QLabel#maskPaneTitle {{ color: #ececec; font-size: 12px; font-weight: 600; }}
            QFrame#maskHairline {{ background: #333333; border: none; max-height: 1px; }}
            /* Full-width tool rows: glyph, label, shortcut chip. The transparent
               left border reserves the checked row's accent bar, so nothing
               shifts when a tool is armed. */
            QFrame#photoEditorPanel QPushButton#maskToolRow {{
                background: transparent; border: none; border-left: 2px solid transparent;
                border-radius: 4px; text-align: left; padding: 0px;
                min-height: 32px; max-height: 32px;
            }}
            QFrame#photoEditorPanel QPushButton#maskToolRow:hover {{ background: #3e3e3e; }}
            QFrame#photoEditorPanel QPushButton#maskToolRow:checked {{
                background: #2b4460; border-left: 2px solid #1473e6;
                border-top-left-radius: 0px; border-bottom-left-radius: 0px;
            }}
            QLabel#maskToolRowLabel {{ color: #e2e2e2; font-size: 12px; background: transparent; }}
            QLabel#maskToolRowLabel:disabled {{ color: #767676; }}
            /* The scroll area paints every plain widget and label with the pane
               ground, which would cut dark blocks through a checked tool row. */
            QFrame#photoEditorPanel QPushButton#maskToolRow QLabel,
            QFrame#photoEditorPanel QPushButton#maskToolRow .QWidget {{ background: transparent; }}
            QFrame#photoEditorPanel QLabel#maskShortcutChip {{
                color: #a8a8a8; font-size: 10px; font-weight: 600; background: transparent;
                border: 1px solid #505050; border-radius: 3px; padding: 0px;
            }}
            /* AI selections as rounded chips with a leading glyph. */
            QFrame#photoEditorPanel QPushButton#semanticMaskButton {{
                background: #3d3d3d; border: 1px solid #4b4b4b; color: #e6e6e6;
                border-radius: 14px; padding: 5px 12px; font-size: 11px; min-height: 18px;
            }}
            QFrame#photoEditorPanel QPushButton#semanticMaskButton:hover {{
                background: #474747; border-color: #5c5c5c; color: #ffffff;
            }}
            QFrame#photoEditorPanel QPushButton#semanticMaskButton:checked {{
                background: #2b4460; border-color: #1473e6; color: #ffffff;
            }}
            QFrame#photoEditorPanel QPushButton#semanticMaskButton:disabled {{
                background: #363636; border-color: #404040; color: #7a7a7a;
            }}
            /* Masks overview: one card per mask group. */
            QFrame#photoEditorPanel QWidget#maskLayerCard {{
                background: #343434; border: 1px solid #3f3f3f; border-radius: 6px;
            }}
            QFrame#photoEditorPanel QWidget#maskLayerCard:hover {{ border-color: #4d4d4d; }}
            QFrame#photoEditorPanel QWidget#maskLayerCard[expanded="true"] {{ border-color: #1473e6; }}
            QFrame#photoEditorPanel QWidget#maskLayerCard .QWidget,
            QFrame#photoEditorPanel QWidget#maskLayerCard QLabel {{ background: transparent; }}
            QFrame#photoEditorPanel QWidget#maskLayerCard QLabel#maskLayerThumb {{
                background: #1c1c1c; border: 1px solid #2a2a2a; border-radius: 3px;
            }}
            QLabel#maskLayerTitle {{ color: #ececec; font-size: 12px; font-weight: 600; }}
            QLabel#maskLayerSubtitle {{ color: #9c9c9c; font-size: 11px; }}
            QFrame#photoEditorPanel QWidget#maskLayerCard[layerHidden="true"] QLabel#maskLayerTitle,
            QFrame#photoEditorPanel QWidget#maskLayerCard[layerHidden="true"] QLabel#maskLayerSubtitle {{
                color: #767676;
            }}
            QFrame#photoEditorPanel QToolButton#maskLayerAction {{
                background: transparent; border: none; border-radius: 3px; padding: 1px;
            }}
            QFrame#photoEditorPanel QToolButton#maskLayerAction:hover {{ background: #454545; }}
            QFrame#photoEditorPanel QWidget#maskComponentRow {{
                background: #2b2b2b; border: 1px solid #3a3a3a; border-radius: 4px;
            }}
            QFrame#photoEditorPanel QWidget#maskComponentRow:hover {{ border-color: #525252; }}
            QLabel#maskComponentName {{ color: #dcdcdc; font-size: 11px; }}
            QLabel#maskComponentBase {{ color: #8a8a8a; font-size: 10px; padding-right: 6px; }}
            QFrame#photoEditorPanel QComboBox#maskCombineCombo {{
                padding: 1px 6px; min-height: 18px; font-size: 11px;
            }}
            QFrame#photoEditorPanel QPushButton#maskAddComponent {{
                background: transparent; border: none; color: #a8a8a8;
                padding: 2px 2px; font-size: 11px; text-align: left;
            }}
            QFrame#photoEditorPanel QPushButton#maskAddComponent:hover,
            QFrame#photoEditorPanel QPushButton#maskAddComponent:pressed {{
                background: transparent; color: #ffffff;
            }}"""


def _studio_mask_action_css() -> str:
    """Mask pane: the subject choice panel and the new/combine/link/delete buttons."""
    return f"""
            QFrame#photoEditorPanel QFrame#subjectChoicePanel {{
                background: #292929; border: 1px solid #414141; border-radius: 4px;
            }}
            QFrame#photoEditorPanel QPushButton#newMaskButton {{
                background: {studio.ACCENT}; border: 1px solid {studio.ACCENT}; color: #ffffff;
                border-radius: 4px; min-height: 20px; max-height: 20px;
                font-size: 11px; font-weight: 600; padding: 0px 8px;
            }}
            QFrame#photoEditorPanel QPushButton#newMaskButton:hover {{ background: #5aa9ff; border-color: #5aa9ff; }}
            QFrame#photoEditorPanel QPushButton#newMaskButton:disabled {{
                background: #333333; border-color: #333333; color: #6d6d6d;
            }}
            QFrame#photoEditorPanel QPushButton#maskCombineButton {{
                background: #333333; border: 1px solid #414141; color: #e2e2e2;
                border-radius: 4px; min-height: 20px; max-height: 20px;
                font-size: 11px; padding: 0px 8px;
            }}
            QFrame#photoEditorPanel QPushButton#maskCombineButton:hover {{
                background: #414141; border-color: #555555; color: #ffffff;
            }}
            QFrame#photoEditorPanel QPushButton#maskCombineButton:disabled {{
                background: #2d2d2d; border-color: #383838; color: #6d6d6d;
            }}
            QFrame#photoEditorPanel QPushButton#maskLinkButton {{
                background: transparent; border: none; color: #9a9a9a; font-size: 11px;
                padding: 0px 2px;
            }}
            QFrame#photoEditorPanel QPushButton#maskLinkButton:hover {{ color: #ffffff; }}
            QFrame#photoEditorPanel QPushButton#deleteMaskButton {{
                background: transparent; border: 1px solid #4d3636; color: #e59a9a;
            }}
            QFrame#photoEditorPanel QPushButton#deleteMaskButton:hover {{
                background: #3a2828; color: #ffb0b0; border-color: #6a3a3a;
            }}"""


def _studio_editor_section_css() -> str:
    """Editor sections: headers, control labels, numeric readouts, curve buttons, footer."""
    return f"""
            QFrame#editorSection {{ background: transparent; border-bottom: 1px solid #262626; }}
            /* One caption style for every section on every page: small, grey,
               uppercase and letter-spaced (the font is set in code) on a
               lighter full-width bar that marks where each category starts.
               Scoped to the panel so it out-specifies the generic QPushButton
               rule below, which used to paint the bar by accident (and turn
               the caption white). */
            QFrame#photoEditorPanel QPushButton#editorSectionHeader {{
                background: #383838; border: none; color: #8f8f8f;
                text-align: left; padding: 6px 10px; border-radius: 0px;
                font-size: 10px; font-weight: 600;
            }}
            QFrame#photoEditorPanel QPushButton#editorSectionHeader:hover {{
                background: #3e3e3e; color: #e6e6e6;
            }}
            QLabel#editorControlLabel {{ color: #c4c4c4; font-size: 11px; }}
            /* A slider label that doubles as a disclosure — reads as the plain
               label until hovered, so the row keeps its normal height. */
            QFrame#photoEditorPanel QPushButton#editorExpanderLabel {{
                color: #c4c4c4; font-size: 11px; text-align: left;
                background: transparent; border: none; padding: 0px;
            }}
            QFrame#photoEditorPanel QPushButton#editorExpanderLabel:hover,
            QFrame#photoEditorPanel QPushButton#editorExpanderLabel:checked {{
                color: #ffffff;
            }}
            QFrame#photoEditorPanel QWidget#vignetteOptions {{
                background: transparent; border-left: 1px solid #333333;
            }}
            /* Compact, editable numeric readout (QSpinBox/QDoubleSpinBox with
               no buttons). Explicit per-class rules so they out-specify the
               generic spin-box styling below. */
            QFrame#photoEditorPanel QSpinBox#editorNumber,
            QFrame#photoEditorPanel QDoubleSpinBox#editorNumber {{
                color: #e6e6e6; background: #232323;
                border: 1px solid #191919; border-radius: 3px;
                min-height: 18px; max-height: 18px;
                min-width: 48px; max-width: 48px;
                padding: 0 2px; font-size: 11px;
                selection-background-color: #1473e6;
            }}
            QFrame#photoEditorPanel QSpinBox#editorNumber:hover,
            QFrame#photoEditorPanel QDoubleSpinBox#editorNumber:hover {{
                border-color: #3a3a3a;
            }}
            QFrame#photoEditorPanel QSpinBox#editorNumber:focus,
            QFrame#photoEditorPanel QDoubleSpinBox#editorNumber:focus {{
                border-color: #1473e6; background: #1e1e1e;
            }}
            QFrame#photoEditorPanel QSpinBox#editorNumber:disabled,
            QFrame#photoEditorPanel QDoubleSpinBox#editorNumber:disabled {{
                color: #6f6f6f; background: #262626; border-color: #202020;
            }}
            QFrame#photoEditorPanel QPushButton#curveChannelButton {{
                color: #cfcfcf; background: #232323; border: 1px solid #191919;
                border-radius: 3px; padding: 2px 8px; font-size: 10px; font-weight: 600;
                min-height: 18px; min-width: 0px;
            }}
            QFrame#photoEditorPanel QPushButton#curveChannelButton:hover {{
                background: #303030; color: #ffffff;
            }}
            QFrame#photoEditorPanel QPushButton#curveChannelButton:checked {{
                background: #1473e6; border-color: #1473e6; color: #ffffff;
            }}
            QFrame#photoEditorPanel QPushButton#curveResetButton {{
                color: #cfcfcf; background: #303030; border: 1px solid #232323;
                border-radius: 3px; padding: 2px 10px; font-size: 10px; font-weight: 500;
                min-height: 18px; max-height: 18px; min-width: 0px;
            }}
            QFrame#photoEditorPanel QPushButton#curveResetButton:hover {{
                background: #3c3c3c; color: #ffffff; border-color: #4a4a4a;
            }}
            QFrame#photoEditorPanel QPushButton#curveResetButton:disabled {{
                color: #6f6f6f; background: #2a2a2a; border-color: #232323;
            }}
            QFrame#photoEditorFooter {{
                background: #232323; border: none; border-top: 1px solid #1a1a1a;
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
            }}"""


def _studio_editor_slider_css() -> str:
    """Editor sliders, including the colour-gradient grooves (temperature, tint, vibrance,
    saturation)."""
    return f"""
            /* Round handle sitting centred on the track: the groove is 4px and
               the handle 10px, so a -3px vertical margin puts the handle's
               centre exactly on the groove's centre. */
            QFrame#photoEditorPanel QSlider {{ min-height: 14px; }}
            QFrame#photoEditorPanel QSlider::groove:horizontal {{
                height: 4px; background: #4f4f4f; border-radius: 2px;
            }}
            QFrame#photoEditorPanel QSlider::sub-page:horizontal {{
                background: #4f4f4f; border-radius: 2px;
            }}
            QFrame#photoEditorPanel QSlider::handle:horizontal {{
                width: 10px; height: 10px; margin: -3px 0;
                border-radius: 5px; border: none; background: #e8e8e8;
            }}
            QFrame#photoEditorPanel QSlider::handle:horizontal:hover {{ background: #ffffff; }}
            QFrame#photoEditorPanel QSlider::handle:horizontal:pressed {{ background: #1473e6; }}
            QFrame#photoEditorPanel QSlider::handle:horizontal:disabled {{ background: #6a6a6a; }}
            QFrame#photoEditorPanel QSlider#slider_temperature::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f69ff, stop:0.5 #9a9a9a, stop:1 #ffd94f);
            }}
            QFrame#photoEditorPanel QSlider#slider_tint::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #52d46b, stop:0.5 #9a9a9a, stop:1 #d64bd5);
            }}
            QFrame#photoEditorPanel QSlider#slider_vibrance::groove:horizontal,
            QFrame#photoEditorPanel QSlider#slider_saturation::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #65b7ff, stop:0.5 #d9cb60, stop:1 #ff5a5a);
            }}
            QFrame#photoEditorPanel QSlider#slider_temperature::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_tint::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_vibrance::sub-page:horizontal,
            QFrame#photoEditorPanel QSlider#slider_saturation::sub-page:horizontal {{
                background: transparent;
            }}"""


def _studio_editor_button_and_field_css() -> str:
    """Editor push buttons, text fields, combo boxes and the mask list."""
    return f"""
            QFrame#photoEditorPanel QPushButton {{
                background: #3d3d3d; border: 1px solid #2a2a2a;
                color: #e6e6e6; padding: 5px 10px; border-radius: 3px;
                font-size: 11px; font-weight: 500;
            }}
            QFrame#photoEditorPanel QPushButton#editorActionButton {{
                min-height: 24px; padding: 5px 10px;
            }}
            QFrame#photoEditorPanel QPushButton#editorToolToggle {{
                min-height: 26px; padding: 5px 10px; font-weight: 600;
            }}
            QFrame#photoEditorPanel QPushButton#editorToolToggle:checked {{
                background: #1473e6; border-color: #1473e6; color: #ffffff;
            }}
            QLabel#editorHint {{ color: #8f8f8f; font-size: 10px; }}
            QFrame#photoEditorPanel QPushButton:hover {{
                background: #4a4a4a; border-color: #5a5a5a;
            }}
            QFrame#photoEditorPanel QPushButton:pressed {{ background: #333333; }}
            QFrame#photoEditorPanel QPushButton:disabled {{
                color: #767676; background: #333333; border-color: #282828;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton {{
                background: #1473e6; border: 1px solid #1473e6; color: #ffffff; font-weight: 600;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton:hover {{
                background: #2b84f0; border-color: #2b84f0;
            }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton:disabled {{
                background: #2c4a6e; border-color: #2c4a6e; color: #8fa5bf;
            }}
            QFrame#photoEditorPanel QListWidget#editorList,
            QFrame#photoEditorPanel QPlainTextEdit#editorText,
            QFrame#photoEditorPanel QLineEdit,
            QFrame#photoEditorPanel QSpinBox,
            QFrame#photoEditorPanel QDoubleSpinBox,
            QFrame#photoEditorPanel QComboBox {{
                background: #1e1e1e; color: #e0e0e0;
                border: 1px solid #161616; border-radius: 3px;
                padding: 4px 6px; selection-background-color: #1473e6;
            }}
            QFrame#photoEditorPanel QLineEdit:focus,
            QFrame#photoEditorPanel QSpinBox:focus,
            QFrame#photoEditorPanel QDoubleSpinBox:focus,
            QFrame#photoEditorPanel QComboBox:focus {{ border-color: #1473e6; }}
            QFrame#photoEditorPanel QPlainTextEdit#editorText {{
                font-family: 'Consolas'; font-size: 10px; color: #c8c8c8;
            }}
            QFrame#photoEditorPanel QFrame#maskListViewport {{
                background: #2a2a2a; border: 1px solid #292929;
                border-radius: 4px;
            }}
            QFrame#photoEditorPanel QFrame#maskListViewport QWidget#maskPaneHeader,
            QFrame#photoEditorPanel QFrame#maskListViewport QLabel {{
                border: none;
            }}
            /* The outer frame is the viewport; the list itself stays flush and
               compact inside it. */
            QFrame#photoEditorPanel QListWidget#editorList {{
                background: transparent; border: none; padding: 0px; outline: 0;
            }}
            /* Rows are custom widgets (name + trash), so the item itself is just
               the selection backing — no padding, and no focus rectangle box. */
            QFrame#photoEditorPanel QListWidget#editorList::item {{
                padding: 0px; border-radius: 3px; color: #d6d6d6; outline: 0;
            }}
            QFrame#photoEditorPanel QListWidget#editorList::item:hover {{ background: transparent; }}
            QFrame#photoEditorPanel QListWidget#editorList::item:selected {{
                background: transparent; color: #ffffff; border: none;
            }}"""


def _studio_mask_row_and_touchup_css() -> str:
    """Mask list rows and the mask touch-up page."""
    return f"""
            QWidget#maskRow {{ background: transparent; border: none; border-radius: 4px; }}
            QWidget#maskRow:hover {{ background: #333333; border-radius: 4px; }}
            QWidget#maskRow[selected="true"] {{
                background: #264f78; border: none; border-radius: 4px;
            }}
            QWidget#maskRow[separated="true"] {{ border-top: 1px solid #3a3a3a; }}
            QLabel#maskRowLabel {{ color: #dcdcdc; font-size: 12px; background: transparent; }}
            QLabel#maskRowMarker {{ color: #8f8f8f; font-size: 12px; background: transparent; }}
            QFrame#photoEditorPanel QToolButton#maskRowTrash,
            QFrame#photoEditorPanel QToolButton#maskRowTouchup {{
                background: transparent; border: none; border-radius: 3px; padding: 1px;
            }}
            QFrame#photoEditorPanel QToolButton#maskRowTouchup:hover {{ background: #3f5368; }}
            QFrame#photoEditorPanel QToolButton#maskRowTrash:hover {{ background: #5a2a2a; }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage {{
                background: #242424; color: #dedede;
            }}
            QFrame#photoEditorPanel QFrame#maskTouchupHeader {{
                background: #1f1f1f; border-bottom: 1px solid #151515;
            }}
            QFrame#photoEditorPanel QLabel#maskTouchupTitle {{
                color: #f0f0f0; font-size: 13px; font-weight: 600;
            }}
            QFrame#photoEditorPanel QLabel#maskTouchupSubtitle {{
                color: #a7a7a7; font-size: 11px;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QFrame#editorSection {{
                background: #2d2d2d; border: none; border-bottom: 1px solid #1c1c1c;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#editorSectionHeader {{
                background: #363636; color: #e2e2e2; border: none;
                border-radius: 0px; text-align: left; padding: 5px 8px;
                font-weight: 600;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QLabel#editorControlLabel {{
                color: #d4d4d4; font-size: 11px;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QSpinBox,
            QFrame#photoEditorPanel QWidget#maskTouchupPage QDoubleSpinBox {{
                background: #1e1e1e; color: #e0e0e0;
                border: 1px solid #161616; border-radius: 3px;
                padding: 3px 5px; selection-background-color: #1473e6;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#maskTouchupSecondaryButton {{
                background: #333333; border: 1px solid #484848;
                border-radius: 3px; color: #dedede; padding: 5px 8px;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#maskTouchupSecondaryButton:hover,
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#maskTouchupSecondaryButton:checked {{
                background: #414141; border-color: #626262; color: #ffffff;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#maskTouchupOkButton {{
                background: #1473e6; border: 1px solid #1473e6;
                border-radius: 3px; color: white; padding: 5px 12px;
            }}
            QFrame#photoEditorPanel QWidget#maskTouchupPage QPushButton#maskTouchupOkButton:hover {{
                background: #2b84f0; border-color: #2b84f0;
            }}"""


def _studio_editor_check_box_and_overlay_css() -> str:
    """Editor check boxes and the overlay menu button."""
    return f"""
            QFrame#photoEditorPanel QCheckBox {{ color: #d6d6d6; spacing: 6px; font-size: 11px; }}
            QFrame#photoEditorPanel QCheckBox::indicator {{
                width: 13px; height: 13px; background: #1e1e1e;
                border: 1px solid #3a3a3a; border-radius: 2px;
            }}
            QFrame#photoEditorPanel QCheckBox::indicator:hover {{ border-color: #5a5a5a; }}
            QFrame#photoEditorPanel QCheckBox::indicator:checked {{
                background: #1473e6; border-color: #1473e6;
            }}
            QFrame#photoEditorPanel QToolButton#overlayMenuButton {{
                background: #333333; border: 1px solid #414141;
                border-radius: 4px; padding: 0px 3px;
            }}
            QFrame#photoEditorPanel QToolButton#overlayMenuButton:hover {{
                background: #414141; border-color: #555555;
            }}
            QFrame#photoEditorPanel QToolButton#overlayMenuButton::menu-indicator {{
                subcontrol-position: right center; subcontrol-origin: padding;
            }}"""


# --- Height-scaled sections --------------------------------------------------
# Rules whose type and row sizes follow the dialog height.


def _mockup_path_bar_and_action_bar_css(sizes: _StudioSizes) -> str:
    """The breadcrumb path bar, navigation pill and the action bar under the photo."""
    return f"""
            QFrame#mockupPathBar {{ background: #15171a; border: none; border-bottom: 1px solid #25282d; }}
            QPushButton#mockupLibraryButton, QPushButton#mockupWindowButton,
            QToolButton#mockupWindowButton,
            QPushButton#mockupEditorMenu {{ background: transparent; border: none; color: #b4bac2; padding: 2px 7px; }}
            QToolButton#mockupWindowButton {{ border-left: 1px solid #272a2f; border-radius: 0px; }}
            QPushButton#mockupLibraryButton:hover, QPushButton#mockupWindowButton:hover,
            QToolButton#mockupWindowButton:hover,
            QPushButton#mockupEditorMenu:hover {{ background: #292b2f; }}
            QLabel#mockupBreadcrumb {{ color: #b4bac2; font-size: {sizes.type_px(popout_ratios.PATH_TEXT_H)}px; font-weight: 400; }}
            QFrame#navPill {{ background: transparent; border: none; border-radius: 0px; }}
            QLabel#navCount {{ color: #c5cad0; font-size: {sizes.type_px(popout_ratios.PATH_TEXT_H)}px; min-width: 42px; }}
            QPushButton#navArrow {{ background: transparent; color: #aeb5c0; border: none; font-size: 14px; }}
            QFrame#mockupActionBar {{ background: #141619; border: none; }}
            QFrame#mockupActionBar QToolButton#studioToolButton {{
                background: transparent; border: none; border-radius: 3px;
                color: #c6cbd1; padding: 0px; font-size: {sizes.type_px(popout_ratios.ACTION_TEXT_H)}px;
                min-width: 0px; min-height: 0px;
            }}
            QFrame#mockupActionBar QToolButton#studioToolButton:hover {{ background: #292c32; }}
            QFrame#mockupActionBar QToolButton#studioToolButton:checked {{ background: #223453; color: #fff; }}
            QFrame#mockupActionBar QToolButton#studioToolButton:disabled {{ color: #747b87; }}
            QPushButton#mockupZoomButton {{ background: #25282d; border: 1px solid #3a3f46; border-radius: 4px;
                color: #d9dde1; padding: 1px; font-size: {sizes.type_px(popout_ratios.ACTION_TEXT_H)}px; }}
            QPushButton#mockupZoomButton:hover {{ background: #363a41; }}
            QSlider#mockupZoomSlider::groove:horizontal {{ height: 2px; background: #454b53; border-radius: 1px; }}
            QSlider#mockupZoomSlider::sub-page:horizontal {{ background: #4c87ec; }}
            QSlider#mockupZoomSlider::handle:horizontal {{ background: #dce0e5; width: 9px;
                height: 9px; margin: -4px 0; border-radius: 5px; }}
            QToolButton#mockupMoreButton, QPushButton#mockupEditButton {{
                background: transparent; border: none; border-radius: 4px;
                color: #dbe0e9; padding: 1px; font-size: {sizes.type_px(popout_ratios.ACTION_TEXT_H)}px;
                min-width: 0px; min-height: 0px;
            }}
            QToolButton#mockupMoreButton:hover, QPushButton#mockupEditButton:hover {{ background: #292c32; }}
            QToolButton#mockupMoreButton::menu-indicator,
            QToolButton#mockupWindowButton::menu-indicator {{ image: none; width: 0px; }}
            QPushButton#mockupEditButton:checked {{ color: #7eb0ff; background: transparent; }}"""


def _mockup_metadata_and_status_css(sizes: _StudioSizes) -> str:
    """The layout buttons, metadata bar, rating buttons, filmstrip and status bar."""
    return f"""
            QPushButton#mockupLayoutButton {{ background: transparent; color: #97a4b8;
                border: none; padding: 4px; font-size: 17px; min-width: 22px; }}
            QPushButton#mockupLayoutButton:hover {{ background: #2a2e34; }}
            QPushButton#mockupLayoutButton:checked {{ color: #74a8ff; }}
            QFrame#mockupMetadataBar {{ background: #141619; border: none;
                border-top: 1px solid #24272c; border-bottom: 1px solid #24272c; min-height: 0px; }}
            QLabel#mockupFilename {{ color: #74808c; font-size: {sizes.type_px(popout_ratios.INFO_TEXT_H)}px; font-weight: 400; }}
            QLabel#mockupCapture {{ color: #74808c; font-size: {sizes.type_px(popout_ratios.SECONDARY_TEXT_H)}px; }}
            QLabel#mockupEdited {{ color: #75a4f5; font-size: {sizes.type_px(popout_ratios.SECONDARY_TEXT_H)}px; }}
            QFrame#mockupRating {{ background: transparent; border: none; }}
            QPushButton#mockupKeep, QPushButton#mockupReject {{ background: transparent;
                border: 1px solid #4f5660; border-radius: 11px; color: #aab0bb;
                font-size: {sizes.type_px(popout_ratios.EDITOR_TITLE_TEXT_H)}px; padding: 0px; }}
            QPushButton#mockupKeep:checked {{ color: #f1749e; border-color: #8a3b5a; }}
            QPushButton#mockupReject:checked {{ color: #ef7777; border-color: #8c4444; }}
            QFrame#filmstrip {{ background: #1b1e21; border: none; border-radius: 0px; }}
            QFrame#mockupStatusBar {{ background: #1b1e21; border: none;
                min-height: 0px; }}
            QFrame#mockupStatusBar QLabel {{ color: #74808c; font-size: {sizes.type_px(popout_ratios.STATUS_TEXT_H)}px; }}"""


def _mockup_editor_column_css(sizes: _StudioSizes) -> str:
    """The right-hand editor column: header, histogram, tool rail, sections and sliders."""
    return f"""
            QFrame#rail {{ background: #17191c; border: none; border-left: 1px solid #2a2d32; border-radius: 0px; }}
            QFrame#photoEditorPanel {{ background: #17191c; border: none; border-radius: 0px; }}
            QWidget#photoEditorColumn, QStackedWidget#photoEditorStack,
            QWidget#photoEditorBody, QScrollArea#photoEditorScrollArea,
            QScrollArea#photoEditorScrollArea QWidget {{ background: #17191c; }}
            QFrame#mockupEditorHeader {{ background: #17191c; border: none;
                border-bottom: 1px solid #2a2d32; min-height: 0px; }}
            QLabel#mockupEditorTitle {{ color: #e8eaed; font-size: {sizes.type_px(popout_ratios.EDITOR_TITLE_TEXT_H)}px; font-weight: 600; }}
            QFrame#mockupHistogram {{ background: #17191c; border: none;
                border-bottom: 1px solid #24282d; }}
            QFrame#mockupHistogram QLabel {{ color: #7f8790; font-size: {sizes.type_px(popout_ratios.SECONDARY_TEXT_H)}px; }}
            QFrame#editorToolRail {{ background: #15171a; border: none; border-left: 1px solid #111214;
                border-radius: 0px; }}
            QFrame#photoEditorPanel QToolButton#editorToolRailButton {{ background: transparent;
                color: #aab0b8; border: none; border-radius: 0px;
                padding: 0px; font-size: {sizes.type_px(popout_ratios.TOOL_TEXT_H, minimum=9)}px; }}
            QFrame#photoEditorPanel QToolButton#editorToolRailButton:hover {{ background: #292e37; color: #fff; }}
            QFrame#photoEditorPanel QToolButton#editorToolRailButton:checked {{
                background: #223453; color: #ffffff; border: none; border-left: 2px solid #7eb0ff; border-radius: 0px; }}
            QFrame#photoEditorPanel QPushButton#editorSectionHeader {{
                background: #17191c; color: #d9dce0; border: none;
                border-top: 1px solid #24282d; border-radius: 0px; padding: 0px 14px;
                min-height: {sizes.px(popout_ratios.EDITOR_SECTION_H, minimum=28)}px;
                max-height: {sizes.px(popout_ratios.EDITOR_SECTION_H, minimum=28)}px; text-align: left;
                font-size: {sizes.type_px(popout_ratios.EDITOR_SECTION_TEXT_H)}px; font-weight: 700; }}
            QFrame#photoEditorPanel QLabel#editorControlLabel {{ color: #c4c8cd; font-size: {sizes.type_px(popout_ratios.EDITOR_CONTROL_TEXT_H)}px; }}
            QFrame#photoEditorPanel QSpinBox#editorNumber,
            QFrame#photoEditorPanel QDoubleSpinBox#editorNumber {{
                color: #b7bdc5; background: #202328; border: 1px solid #30343a;
                border-radius: 3px; padding: 0px 2px; min-width: 0px; max-width: 9999px;
                min-height: 0px; max-height: 9999px; font-size: {sizes.type_px(popout_ratios.EDITOR_CONTROL_TEXT_H)}px; }}
            QFrame#photoEditorPanel QSlider::groove:horizontal {{ height: 2px; background: #50555c; border-radius: 0px; }}
            QFrame#photoEditorPanel QSlider::sub-page:horizontal {{ background: #6f767f; }}
            QFrame#photoEditorPanel QSlider::handle:horizontal {{ width: 8px; height: 8px;
                margin: -3px 0; border-radius: 4px; background: #e1e4e7; }}
            QFrame#photoEditorPanel QComboBox {{ background: #202328; border: 1px solid #30343a;
                border-radius: 3px; color: #c8cdd3; font-size: {sizes.type_px(popout_ratios.EDITOR_CONTROL_TEXT_H)}px; }}
            QFrame#photoEditorFooter {{ background: #16181b; border: none;
                border-top: 1px solid #2a2d32; border-radius: 0px; }}
            QFrame#photoEditorPanel QPushButton#editorPrimaryButton {{
                background: #4d8df7; border: 1px solid #4d8df7; border-radius: 4px; color: white; }}"""


# --- Section order -----------------------------------------------------------
_FIXED_SECTIONS: tuple[Callable[[], str], ...] = (
    _studio_control_css,
    _studio_editor_frame_css,
    _studio_mask_tool_css,
    _studio_mask_action_css,
    _studio_editor_section_css,
    _studio_editor_slider_css,
    _studio_editor_button_and_field_css,
    _studio_mask_row_and_touchup_css,
    _studio_editor_check_box_and_overlay_css,
)

_SCALED_SECTIONS: tuple[Callable[[_StudioSizes], str], ...] = (
    _mockup_path_bar_and_action_bar_css,
    _mockup_metadata_and_status_css,
    _mockup_editor_column_css,
)
