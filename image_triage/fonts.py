"""The interface font.

Kept at the top level, free of the ``ui`` package (whose ``__init__`` pulls in
every dialog), so low-level modules such as ``thumbnails`` can build a label
font without importing the whole interface.
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

# Windows 11's UI family, the one the design was drawn in; Segoe UI is the
# fallback on older Windows.
UI_FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI")
UI_FONT_STACK = ", ".join(f'"{family}"' for family in UI_FONT_FAMILIES)

_resolved_families: tuple[str, ...] | None = None


def interface_families() -> tuple[str, ...]:
    """The preferred families this platform can actually serve.

    Headless platforms (the offscreen plugin the tests run under) report an
    empty font database, and asking those for a family they do not have hangs
    inside Qt's fallback search, so the list is filtered once and cached.
    """
    global _resolved_families
    if _resolved_families is not None:
        return _resolved_families
    if QGuiApplication.instance() is None:
        return UI_FONT_FAMILIES
    installed = set(QFontDatabase.families())
    available = tuple(family for family in UI_FONT_FAMILIES if family in installed)
    _resolved_families = available or (UI_FONT_FAMILIES[-1],)
    return _resolved_families


def ui_font(point_size: int = -1, weight: "QFont.Weight | None" = None) -> QFont:
    """A UI font in the interface family, for painters that build their own."""
    families = interface_families()
    font = QFont(families[0])
    font.setFamilies(list(families))
    if point_size > 0:
        font.setPointSize(point_size)
    if weight is not None:
        font.setWeight(weight)
    return font
