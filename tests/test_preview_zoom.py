from PySide6.QtCore import QRect, QSize

from image_triage.preview import (
    _normalized_viewport_center,
    _scroll_value_for_viewport_center,
    _source_rect_for_scaled_view,
)


def test_zoom_preserves_panned_viewport_center() -> None:
    center = _normalized_viewport_center(
        scroll_value=750,
        image_extent=2000,
        viewport_extent=1000,
        scroll_maximum=1000,
    )

    assert center == 0.625
    assert _scroll_value_for_viewport_center(center, image_extent=4000, viewport_extent=1000) == 2000


def test_first_zoom_uses_image_center_when_axis_was_not_scrollable() -> None:
    center = _normalized_viewport_center(
        scroll_value=0,
        image_extent=800,
        viewport_extent=1000,
        scroll_maximum=0,
    )

    assert center == 0.5
    assert _scroll_value_for_viewport_center(center, image_extent=1600, viewport_extent=1000) == 300


def test_zoomed_paint_maps_only_exposed_viewport_to_source() -> None:
    source = _source_rect_for_scaled_view(
        QRect(24_000, 12_000, 1_920, 1_080),
        source_size=QSize(6_000, 4_000),
        target_size=QSize(48_000, 32_000),
    )

    assert source.x() == 3_000
    assert source.y() == 1_500
    assert source.width() == 240
    assert source.height() == 135
