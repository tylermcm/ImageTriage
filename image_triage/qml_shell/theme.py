"""Theme tokens for the QML shell, derived from three inputs (base, accent, contrast) the way the HTML mockup does.

The mockup's CSS uses ``oklch()`` and ``color-mix()``, which QML does not have, so the same derivation is done here once and
exposed as plain colours. Mixing is done in OKLab (OKLCH without the hue step), which is identical for the achromatic mixes that
make up the surfaces and text and a close approximation for the accent mixes.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QColor


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def oklab_from_srgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def srgb_from_oklab(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        _linear_to_srgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        _linear_to_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        _linear_to_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    )


def oklch(lightness: float, chroma: float, hue_degrees: float) -> tuple[float, float, float]:
    hue = math.radians(hue_degrees)
    return srgb_from_oklab(lightness, chroma * math.cos(hue), chroma * math.sin(hue))


def mix(first: tuple[float, float, float], second: tuple[float, float, float], percent_second: float) -> tuple[float, float, float]:
    """CSS ``color-mix(in oklab, first, second P%)`` for opaque sRGB triples in 0..1."""
    t = max(0.0, min(1.0, percent_second / 100.0))
    a = oklab_from_srgb(*first)
    b = oklab_from_srgb(*second)
    return srgb_from_oklab(*(a[i] + (b[i] - a[i]) * t for i in range(3)))


def oklch_from_srgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    lightness, a, b2 = oklab_from_srgb(r, g, b)
    return lightness, math.hypot(a, b2), math.degrees(math.atan2(b2, a)) % 360.0


def _color(triple: tuple[float, float, float]) -> QColor:
    return QColor.fromRgbF(*(max(0.0, min(1.0, v)) for v in triple))


WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)

DARK = {"base": (0.165, 0.006, 265.0), "accent": (0.70, 0.15, 255.0), "contrast": 90.0, "lift": WHITE, "star": "#f0b84a"}
# The dark theme comes in three tones (the same hue, lifted): Slate is the original near-black (#0d0e12); Soft (#1d1f23) and Dim (#2c2e32) are for
# laptop and office screens that show the original as almost black. The surfaces, lines and text are derived from the base, so they keep their steps.
DARK_TONES = {"slate": (0.165, 0.006, 0.52), "soft": (0.24, 0.008, 0.46), "dim": (0.30, 0.010, 0.40)}      # base lightness, chroma, the dialogs' dim
LIGHT = {"base": (0.975, 0.003, 265.0), "accent": (0.56, 0.17, 258.0), "contrast": 84.0, "lift": BLACK, "star": "#c98a14"}


def derive(spec: dict) -> dict[str, QColor]:
    """Every token the QML uses, from base / accent / contrast / lift."""
    base = oklch(*spec["base"])
    lift = spec["lift"]
    contrast = spec["contrast"]
    accent = oklch(*spec["accent"])
    tokens = {
        "base": base,
        "s1": mix(base, lift, 3.5),
        "s2": mix(base, lift, 6.5),
        "s3": mix(base, lift, 11),
        "line": mix(base, lift, 15),
        "lineSoft": mix(base, lift, 9),
        "fg": mix(base, lift, contrast),
        "fg2": mix(base, lift, contrast * 0.74),
        "fg3": mix(base, lift, contrast * 0.64),
        "lift": lift,
        "accent": accent,
        "accentFg": mix(accent, WHITE, 30) if lift == WHITE else mix(accent, BLACK, 30),
        "onAccent": oklch(0.2, 0.03, 262.0) if lift == WHITE else WHITE,     # text on a filled accent button
    }
    colors = {name: _color(value) for name, value in tokens.items()}
    for name, value in (("win", "#3fb97a"), ("keep", "#4b8ff0"), ("rev", "#e0b04a"), ("rej", "#e5635b"), ("star", spec["star"])):
        colors[name] = QColor(value)
    return colors


def _palette_spec(mode: str, system_dark: bool, tone: str = "slate") -> dict:
    """base / accent / contrast of one of the Widgets app's appearance modes, read from its real palette. Slate and Light are the
    mockup's own two palettes exactly."""
    if mode == "auto":
        mode = "slate" if system_dark else "light"
    if mode == "slate":
        spec = dict(DARK)
        lightness, chroma, _veil = DARK_TONES.get(tone, DARK_TONES["slate"])
        spec["base"] = (lightness, chroma, 265.0)
        return spec
    if mode == "light":
        return dict(LIGHT)
    from ..ui.theme import AppearanceMode, resolve_theme

    palette = resolve_theme(AppearanceMode(mode), None)  # type: ignore[arg-type]  # only AUTO needs an application
    lift = WHITE if palette.is_dark else BLACK

    def lch(token) -> tuple[float, float, float]:
        return oklch_from_srgb(token.red / 255.0, token.green / 255.0, token.blue / 255.0)

    base = lch(palette.window_bg)
    text = lch(palette.text_primary)
    span = (1.0 - base[0]) if palette.is_dark else base[0]
    pull = (text[0] - base[0]) if palette.is_dark else (base[0] - text[0])
    contrast = max(40.0, min(100.0, 100.0 * pull / span)) if span > 0 else 90.0
    return {"base": base, "accent": lch(palette.accent), "contrast": contrast, "lift": lift, "star": "#f0b84a" if palette.is_dark else "#c98a14"}


class Theme(QObject):
    """``theme.base``, ``theme.s2``, ... for QML, plus ``theme.light`` to flip the whole palette."""

    changed = Signal()

    def __init__(self, light: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._light = bool(light)
        self._colors = derive(LIGHT if light else DARK)
        self._scrim = QColor(20, 22, 28, 87) if light else QColor(6, 7, 9, 133)

    def _get(self, name: str) -> QColor:
        return self._colors[name]

    def _get_light(self) -> bool:
        return self._light

    def _set_light(self, value: bool) -> None:
        value = bool(value)
        if value != self._light:
            self._light = value
            self._colors = derive(LIGHT if value else DARK)
            self.changed.emit()

    light = Property(bool, _get_light, _set_light, notify=changed)
    base = Property(QColor, lambda self: self._get("base"), notify=changed)
    s1 = Property(QColor, lambda self: self._get("s1"), notify=changed)
    s2 = Property(QColor, lambda self: self._get("s2"), notify=changed)
    s3 = Property(QColor, lambda self: self._get("s3"), notify=changed)
    line = Property(QColor, lambda self: self._get("line"), notify=changed)
    lineSoft = Property(QColor, lambda self: self._get("lineSoft"), notify=changed)
    fg = Property(QColor, lambda self: self._get("fg"), notify=changed)
    fg2 = Property(QColor, lambda self: self._get("fg2"), notify=changed)
    fg3 = Property(QColor, lambda self: self._get("fg3"), notify=changed)
    lift = Property(QColor, lambda self: self._get("lift"), notify=changed)
    accent = Property(QColor, lambda self: self._get("accent"), notify=changed)
    accentFg = Property(QColor, lambda self: self._get("accentFg"), notify=changed)
    onAccent = Property(QColor, lambda self: self._get("onAccent"), notify=changed)
    win = Property(QColor, lambda self: self._get("win"), notify=changed)
    keep = Property(QColor, lambda self: self._get("keep"), notify=changed)
    rev = Property(QColor, lambda self: self._get("rev"), notify=changed)
    rej = Property(QColor, lambda self: self._get("rej"), notify=changed)
    star = Property(QColor, lambda self: self._get("star"), notify=changed)
    scrim = Property(QColor, lambda self: self._scrim, notify=changed)

    @Slot(str, int, int, bool, str)
    def applyMode(self, mode: str, accent_hue: int, contrast: int, system_dark: bool = True, tone: str = "slate") -> None:
        """Re-derive every colour for an appearance mode, an optional accent hue (-1 keeps the mode's own) and the contrast slider
        (90 = the mode's own contrast; each step moves it one point)."""
        spec = _palette_spec(mode, system_dark, tone)
        lifted_light = spec["lift"] == BLACK
        if accent_hue >= 0:
            spec["accent"] = (0.56, 0.17, float(accent_hue)) if lifted_light else (0.70, 0.15, float(accent_hue))
        spec["contrast"] = max(40.0, min(100.0, spec["contrast"] + (contrast - 90)))
        self._light = lifted_light
        dark_slate = not lifted_light and (mode == "slate" or (mode == "auto" and system_dark))
        veil = DARK_TONES.get(tone, DARK_TONES["slate"])[2] if dark_slate else 0.52
        self._scrim = QColor(20, 22, 28, 87) if lifted_light else QColor(6, 7, 9, round(veil * 255))      # what dims the window behind a dialog
        self._colors = derive(spec)
        self.changed.emit()

    @Slot(float, float, float, result=QColor)
    def oklch(self, lightness: float, chroma: float, hue: float) -> QColor:
        """An OKLCH colour as a QColor (CSS ``oklch()``), for gradients the mockup defines in OKLCH."""
        return _color(oklch(lightness, chroma, hue))
