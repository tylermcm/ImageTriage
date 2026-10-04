"""WI-8.1: the numpy alpha-bounds rewrite of ``MainWindow._trim_icon_transparency``
must crop exactly like the per-pixel ``pixelColor`` loop it replaced.

``_legacy_trim_icon_transparency`` below is a verbatim copy of the old
implementation. Every assertion compares the new code against it, so a future
"simplification" that changes a crop edge case fails here rather than shifting
toolbar glyphs by a pixel.
"""
from __future__ import annotations

import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap

from image_triage.window import MainWindow

_PROBE = QSize(64, 64)


def _legacy_trim_icon_transparency(icon: QIcon, *, padding: int = 3) -> QIcon:
    """The pre-WI-8.1 implementation, kept as the oracle."""
    if icon.isNull():
        return icon
    source = icon.pixmap(QSize(64, 64))
    image = source.toImage()
    left, top = image.width(), image.height()
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() <= 0:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if right < left or bottom < top:
        return icon
    inset = max(0, int(padding))
    bounds = QRect(left, top, right - left + 1, bottom - top + 1)
    bounds = bounds.adjusted(-inset, -inset, inset, inset).intersected(image.rect())
    return QIcon(source.copy(bounds))


def _signature(original: QIcon, result: QIcon) -> tuple:
    """Everything observable about a trim result: untouched-vs-cropped, the
    cropped size, and the cropped pixels."""
    if result is original:
        return ("unchanged",)
    image = result.pixmap(_PROBE).toImage()
    return ("cropped", image.width(), image.height(), image.format().value, bytes(image.constBits()))


def _assert_same_as_legacy(icon: QIcon, *, padding: int = 3, label: str = "") -> None:
    legacy = _legacy_trim_icon_transparency(icon, padding=padding)
    current = MainWindow._trim_icon_transparency(icon, padding=padding)
    assert _signature(icon, current) == _signature(icon, legacy), f"trim differs from legacy for {label or icon}"


def _argb_pixmap(width: int, height: int, pixels: dict[tuple[int, int], QColor]) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    for (x, y), colour in pixels.items():
        image.setPixelColor(x, y, colour)
    return QPixmap.fromImage(image)


OPAQUE = QColor(240, 240, 240, 255)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_every_workspace_toolbar_icon_trims_identically(main_window) -> None:
    colour = main_window._theme.text_primary.qcolor()
    item_ids = list(MainWindow.WORKSPACE_TOOLBAR_FLUENT_ICONS)
    assert len(item_ids) >= 50, "icon table unexpectedly small; the equivalence sweep would prove little"
    for item_id in item_ids:
        icon = main_window._toolbar.workspace_toolbar_icon(item_id, color=colour)
        _assert_same_as_legacy(icon, label=item_id)
        # The drive-glyph path trims with a tighter inset.
        _assert_same_as_legacy(icon, padding=2, label=f"{item_id} padding=2")


def test_every_topbar_nav_icon_trims_identically(main_window) -> None:
    colour = main_window._theme.text_primary.qcolor()
    for item_id, (primary, secondary) in MainWindow.TOPBAR_NAV_FLUENT_ICONS.items():
        icon = main_window._toolbar.fluent_toolbar_icon(primary, secondary, color=colour)
        _assert_same_as_legacy(icon, label=item_id)


def test_trim_matches_legacy_in_the_other_theme_variants(main_window) -> None:
    for colour in (QColor(10, 10, 10), QColor(255, 255, 255), QColor(90, 120, 200, 128)):
        for item_id in list(MainWindow.WORKSPACE_TOOLBAR_FLUENT_ICONS)[:12]:
            _assert_same_as_legacy(main_window._toolbar.workspace_toolbar_icon(item_id, color=colour), label=item_id)


def test_null_icon_is_returned_untouched() -> None:
    icon = QIcon()
    assert MainWindow._trim_icon_transparency(icon) is icon
    assert _legacy_trim_icon_transparency(icon) is icon


def test_fully_transparent_icon_is_returned_untouched() -> None:
    pixmap = _argb_pixmap(64, 64, {})
    icon = QIcon(pixmap)
    assert MainWindow._trim_icon_transparency(icon) is icon
    _assert_same_as_legacy(icon, label="fully transparent")


@pytest.mark.parametrize(
    "x,y",
    [(0, 0), (63, 0), (0, 63), (63, 63), (31, 31), (0, 31), (63, 31), (31, 0), (31, 63), (7, 40)],
)
def test_single_opaque_pixel_anywhere(x: int, y: int) -> None:
    icon = QIcon(_argb_pixmap(64, 64, {(x, y): OPAQUE}))
    _assert_same_as_legacy(icon, label=f"single pixel at {x},{y}")
    # Concretely: the crop is the pixel plus up to 3px of padding, clipped to the canvas.
    cropped = MainWindow._trim_icon_transparency(icon).pixmap(_PROBE)
    expected = QRect(x, y, 1, 1).adjusted(-3, -3, 3, 3).intersected(QRect(0, 0, 64, 64))
    assert (cropped.width(), cropped.height()) == (expected.width(), expected.height())


def test_faintest_non_zero_alpha_counts_as_ink() -> None:
    # alpha == 1 is visible to the old ``alpha() <= 0`` test, so it must be here too.
    icon = QIcon(_argb_pixmap(64, 64, {(10, 12): QColor(255, 0, 0, 1), (50, 44): QColor(0, 255, 0, 2)}))
    _assert_same_as_legacy(icon, label="alpha 1/2 pixels")


def test_icon_touching_every_edge_is_cropped_to_the_canvas() -> None:
    pixels = {(x, y): OPAQUE for x in range(64) for y in range(64) if x in (0, 63) or y in (0, 63)}
    icon = QIcon(_argb_pixmap(64, 64, pixels))
    _assert_same_as_legacy(icon, label="border ring")
    cropped = MainWindow._trim_icon_transparency(icon).pixmap(_PROBE)
    assert (cropped.width(), cropped.height()) == (64, 64)


@pytest.mark.parametrize("size", [(20, 50), (50, 20), (1, 1), (3, 64), (64, 3), (37, 21)])
def test_non_square_and_tiny_canvases(size: tuple[int, int]) -> None:
    width, height = size
    rng = random.Random(width * 1000 + height)
    pixels = {
        (rng.randrange(width), rng.randrange(height)): QColor(rng.randrange(256), 40, 80, rng.randrange(1, 256))
        for _ in range(max(1, width * height // 25))
    }
    _assert_same_as_legacy(QIcon(_argb_pixmap(width, height, pixels)), label=f"{width}x{height}")


def test_canvas_larger_than_the_probe_is_downscaled_the_same_way() -> None:
    pixels = {(x, y): OPAQUE for x in range(30, 70) for y in range(20, 80)}
    _assert_same_as_legacy(QIcon(_argb_pixmap(100, 100, pixels)), label="100x100")


def test_opaque_pixmap_without_an_alpha_channel_keeps_its_whole_canvas() -> None:
    pixmap = QPixmap(18, 18)
    pixmap.fill(QColor("white"))
    icon = QIcon(pixmap)
    assert not pixmap.hasAlphaChannel()
    _assert_same_as_legacy(icon, label="opaque RGB pixmap")


def test_existing_block_case_keeps_its_documented_size() -> None:
    block = _argb_pixmap(64, 64, {(x, y): OPAQUE for x in range(20, 38) for y in range(22, 38)})
    trimmed = MainWindow._trim_icon_transparency(QIcon(block), padding=3)
    assert [trimmed.availableSizes()[0].width(), trimmed.availableSizes()[0].height()] == [24, 22]


def test_padding_variants_match_legacy() -> None:
    pixels = {(x, y): OPAQUE for x in range(24, 40) for y in range(10, 50)}
    icon = QIcon(_argb_pixmap(64, 64, pixels))
    for padding in (0, 1, 2, 3, 8, 40, -5):
        _assert_same_as_legacy(icon, padding=padding, label=f"padding={padding}")


def test_randomised_sparse_icons_match_legacy() -> None:
    rng = random.Random(20260930)
    for case in range(40):
        width, height = rng.choice([(64, 64), (64, 64), (48, 64), (64, 33), (9, 9)])
        count = rng.choice([1, 2, 5, 40, 400])
        pixels = {
            (rng.randrange(width), rng.randrange(height)): QColor(
                rng.randrange(256), rng.randrange(256), rng.randrange(256), rng.randrange(1, 256)
            )
            for _ in range(count)
        }
        _assert_same_as_legacy(QIcon(_argb_pixmap(width, height, pixels)), label=f"random case {case}")


def test_trim_is_much_faster_than_the_per_pixel_loop() -> None:
    """Guard against reintroducing a per-pixel Python loop. Measured on a dev box:
    ~2 ms per icon for the legacy loop vs ~0.05 ms now. Compared against the
    legacy copy in the same process, so machine load cancels out; the 5x floor
    leaves an order of magnitude of slack."""
    import time

    icon = QIcon(_argb_pixmap(64, 64, {(x, y): OPAQUE for x in range(12, 50) for y in range(9, 55)}))
    start = time.perf_counter()
    for _ in range(4):
        _legacy_trim_icon_transparency(icon)
    legacy_each = (time.perf_counter() - start) / 4
    start = time.perf_counter()
    for _ in range(40):
        MainWindow._trim_icon_transparency(icon)
    current_each = (time.perf_counter() - start) / 40
    assert current_each * 5 < legacy_each, (
        f"trim now takes {current_each * 1000:.3f} ms vs legacy {legacy_each * 1000:.3f} ms; "
        "the per-pixel loop is back?"
    )
